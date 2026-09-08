"""Actual built-engine transactions; no GUI, model or real consumer is launched.

The engine-only runner supplies both immutable build roots and a receipt root.
Ordinary source-only pytest runs do not silently build/download dependencies.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import uuid
import zipfile
from pathlib import Path

import pytest

from test_foundation import (
    POWERSHELLS, _package, _read_acceptance_environment,
    _write_acceptance_environment,
)

SCENARIOS = (
    "fresh_install_rollback", "existing_install_rollback", "late_failure_rollback",
    "interrupted_recovery", "snapshot_tamper_rejected", "receipt_drift_rejected",
    "foreign_generation_rejected",
)
SHARED = (
    ".llm-foundation/bin/officecli.exe",
    ".llm-foundation/libexec/officecli/officecli-command-policy.json",
    ".llm-foundation/libexec/officecli/officecli.exe",
    ".llm-foundation/libexec/officecli/officecli_csv_batch.py",
    ".llm-foundation/libexec/officecli/plugins/exporter/pdf/plugin.exe",
    ".llm-foundation/state/shared-tools/officecli/current.json",
)
ENV_NAMES = ("OFFICECLI_NO_AUTO_INSTALL", "OFFICECLI_SKIP_UPDATE", "PATH")


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _write(path, value):
    path.write_bytes(_bytes(value))


def _real_environment():
    # Read only these three HKCU values, hash them in memory, never expose values.
    import winreg
    result = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", access=winreg.KEY_READ) as key:
        for name in ENV_NAMES:
            try:
                value, kind = winreg.QueryValueEx(key, name)
                state = {"exists": True, "value": value, "kind": kind}
            except FileNotFoundError:
                state = {"exists": False}
            result[name] = _sha(_bytes(state))
    return result


def _state(home):
    paths = {home / relative for relative in SHARED}
    for relative in (".codex", ".agents"):
        root = home / relative
        if root.exists():
            paths.update(p for p in root.rglob("*") if p.is_file())
    return {
        "files": {p.relative_to(home).as_posix(): _sha(p.read_bytes())
                  for p in sorted(paths) if p.is_file()},
        "fake_environment": {k: _sha(_bytes(v)) for k, v in
                             sorted(_read_acceptance_environment(home).items())},
    }


def _seed(home):
    for index, relative in enumerate(SHARED):
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"previous-owned-{index}\r\n".encode())
    path = home / ".codex/AGENTS.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"# previous user instructions\r\n")
    _write_acceptance_environment(home, {
        "PATH": "C:\\existing-tool;C:\\another-tool",
        "OFFICECLI_NO_AUTO_INSTALL": "previous-no-install",
        "OFFICECLI_SKIP_UPDATE": "previous-no-update",
        "UNRELATED_SENTINEL": "untouched",
    })


def _assert_installed(home, engine):
    tool = json.loads((engine / "shared-tools.lock.json").read_text())["tools"][0]
    mapping = {
        SHARED[0]: tool["shim"], SHARED[1]: tool["policy"], SHARED[2]: tool["private_exe"],
        SHARED[3]: tool["csv_batch_adapter"], SHARED[4]: tool["pdf_exporter"],
    }
    for relative, record in mapping.items():
        data = (home / relative).read_bytes()
        assert len(data) == record["bytes"] and _sha(data) == record["sha256"]
        assert data == (engine / record["path"]).read_bytes()
    receipt = json.loads((home / SHARED[5]).read_text(encoding="utf-8-sig"))
    assert receipt["schema_version"] == 2 and receipt["owner_target"] == "codex"
    assert {r["path"]: (r["sha256"], r["bytes"]) for r in receipt["files"]} == {
        p: (r["sha256"], r["bytes"]) for p, r in mapping.items()}
    env = _read_acceptance_environment(home)
    assert env["OFFICECLI_NO_AUTO_INSTALL"] == env["OFFICECLI_SKIP_UPDATE"] == "1"
    assert str(home / ".llm-foundation/bin").replace("/", "\\") in env["PATH"].split(";")
    return _state(home)


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_actual_built_engine_lifecycle(tmp_path, executable, scenario_id):
    shell = "ps51" if Path(executable).stem.lower() == "powershell" else "ps7"
    configured = os.environ.get(f"K7_ENGINE_{shell.upper()}_ROOT")
    if not configured:
        pytest.skip("requires explicit isolated engine build roots")
    engine = Path(configured).resolve(strict=True)
    workspace = Path(os.environ["K7_ENGINE_ACCEPTANCE_WORKSPACE"]).resolve(strict=True)
    assert tmp_path.resolve().is_relative_to(workspace)
    assert engine.is_relative_to(workspace)
    # .NET Framework in PS5.1 still has MAX_PATH-sensitive atomic temp files.
    # Keep the actual home short inside the authorized workspace, and retain it
    # for inspection. This does not claim long-path support.
    home = workspace / "h" / uuid.uuid4().hex[:8]
    home.mkdir(parents=True)
    temporary = tmp_path / "temp"
    temporary.mkdir()
    package = _package(tmp_path / "fixture.zip")
    if scenario_id != "fresh_install_rollback":
        _seed(home)
    before = _state(home)
    actual_before = _real_environment()
    commands = []

    def run(command, *, code=0, status=None, target="codex", extra=None, selected_package=None):
        args = [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(engine / "foundation.ps1"), command, "-Home", str(home),
                "-Target", target, "-ClientId", "codex-cli", "-ClientVersion", "0.153.1", "-Json"]
        if command in ("install", "plan"):
            args.extend(["-Package", str(selected_package or package)])
        env = os.environ.copy()
        env.update(FOUNDATION_ACCEPTANCE_MODE="1", USERPROFILE=str(home),
                   APPDATA=str(home / "AppData/Roaming"), LOCALAPPDATA=str(home / "AppData/Local"),
                   TEMP=str(temporary), TMP=str(temporary),
                   PSModuleAnalysisCachePath=str(temporary / "ps-module-cache"))
        for key in list(env):
            if key.startswith("FOUNDATION_") and key != "FOUNDATION_ACCEPTANCE_MODE":
                del env[key]
        env.update(extra or {})
        result = subprocess.run(args, env=env, capture_output=True, text=True,
                                encoding="utf-8", timeout=90)
        commands.append({"command": args, "returncode": result.returncode,
                         "stdout": result.stdout, "stderr": result.stderr})
        assert result.returncode == code, commands[-1]
        data = json.loads(result.stdout) if result.stdout.strip() else None
        if status:
            assert data["status"] == status, data
        return data

    installed = None
    try:
        plan = run("plan", status="READY")
        assert set(SHARED[:5]) <= {r["path"] for r in plan["actions"]}
        assert _state(home) == before
        if scenario_id == "late_failure_rollback":
            failed = run("install", code=30, extra={"FOUNDATION_OFFICECLI_FAIL_STAGE": "after_mutations"})
            assert failed["code"] == "INSTALL_FAILED"
            assert _state(home) == before
        elif scenario_id == "interrupted_recovery":
            run("install", code=99, extra={"FOUNDATION_OFFICECLI_CRASH_STAGE": "after_mutations"})
            installed = _assert_installed(home, engine)
            assert (home / ".llm-foundation/state/codex/pending.json").is_file()
            run("rollback", code=97, extra={"FOUNDATION_ROLLBACK_CRASH_STAGE": "before_journal_delete"})
            assert _state(home) == before
            run("rollback", status="ROLLED_BACK")
            assert _state(home) == before
        else:
            run("install", status="CANONICAL")
            installed = _assert_installed(home, engine)
            run("doctor", status="HEALTHY")
            if scenario_id == "snapshot_tamper_rejected":
                active_path = home / ".llm-foundation/state/codex/active.json"
                active_bytes = active_path.read_bytes()
                active = json.loads(active_bytes)
                snapshot_path = Path(active["snapshot_path"])
                snapshot_bytes = snapshot_path.read_bytes()
                snapshot = json.loads(snapshot_bytes)
                backup = snapshot_path.parent / snapshot["bundled_officecli"]["files"][0]["backup_path"]
                backup_bytes = backup.read_bytes()
                backup.write_bytes(b"tampered bytes")
                rejected = run("rollback", code=30)
                assert rejected["message"] == "Bundled OfficeCLI backup bytes differ"
                assert _state(home) == installed
                backup.write_bytes(backup_bytes)
                for mutation in ("path", "hash", "extra"):
                    altered = copy.deepcopy(snapshot)
                    row = altered["bundled_officecli"]["files"][0]
                    if mutation == "path": row["backup_path"] = "../outside.bin"
                    elif mutation == "hash": row["sha256"] = "0" * 64
                    else: altered["bundled_officecli"]["files"].append(copy.deepcopy(row))
                    _write(snapshot_path, altered)
                    active["snapshot_sha256"] = _sha(snapshot_path.read_bytes())
                    _write(active_path, active)
                    rejected = run("rollback", code=30)
                    assert "Bundled OfficeCLI" in rejected["message"]
                    assert _state(home) == installed
                snapshot_path.write_bytes(snapshot_bytes)
                active_path.write_bytes(active_bytes)
                # A directory junction is available without symlink privilege on
                # Windows. It must be refused before any managed rollback write.
                backup_root = snapshot_path.parent / 'bundled-officecli'
                saved_root = snapshot_path.parent / 'bundled-officecli-saved'
                backup_root.rename(saved_root)
                arguments = [executable, '-NoProfile', '-NonInteractive', '-Command',
                             "New-Item -ItemType Junction -Path '" + str(backup_root) +
                             "' -Target '" + str(saved_root) + "' | Out-Null"]
                junction_environment = {**os.environ, 'USERPROFILE': str(home),
                    'APPDATA': str(home / 'AppData/Roaming'), 'LOCALAPPDATA': str(home / 'AppData/Local'),
                    'TEMP': str(temporary), 'TMP': str(temporary),
                    'PSModuleAnalysisCachePath': str(temporary / 'ps-module-cache')}
                created = subprocess.run(arguments, env=junction_environment, capture_output=True,
                                         text=True, encoding='utf-8', timeout=30)
                commands.append({'command': arguments, 'returncode': created.returncode,
                                 'stdout': created.stdout, 'stderr': created.stderr})
                assert created.returncode == 0, commands[-1]
                try:
                    rejected = run('rollback', code=40)
                    assert rejected['code'] == 'UNSAFE_PATH'
                    assert _state(home) == installed
                finally:
                    assert backup_root.is_junction() and backup_root.resolve() == saved_root.resolve()
                    os.rmdir(backup_root)
                    saved_root.rename(backup_root)
            elif scenario_id == "receipt_drift_rejected":
                receipt_path = home / SHARED[5]
                receipt_bytes = receipt_path.read_bytes()
                receipt = json.loads(receipt_bytes)
                for mutation in ("missing", "empty", "extra", "duplicate", "hash"):
                    altered = copy.deepcopy(receipt)
                    if mutation == "missing": receipt_path.unlink()
                    else:
                        if mutation == "empty": altered["files"] = []
                        elif mutation == "extra": altered["files"].append(copy.deepcopy(altered["files"][0]))
                        elif mutation == "duplicate": altered["files"][1] = copy.deepcopy(altered["files"][0])
                        else: altered["files"][0]["sha256"] = "0" * 64
                        _write(receipt_path, altered)
                    rejected = run("doctor", code=30, status="FAILED_DOCTOR")
                    assert rejected["code"] == "ACTIVE_DRIFT"
                    receipt_path.write_bytes(receipt_bytes)
                run("doctor", status="HEALTHY")
            elif scenario_id == "foreign_generation_rejected":
                # A second synthetic target shares OfficeCLI but has its own journal.
                other = tmp_path / "other-target.zip"
                with zipfile.ZipFile(package) as archive:
                    entries = {n: archive.read(n) for n in archive.namelist()}
                manifest = json.loads(entries["package-manifest.json"])
                manifest["target"] = "codex-other"
                entries["package-manifest.json"] = _bytes(manifest)
                with zipfile.ZipFile(other, "w") as archive:
                    for name, data in entries.items(): archive.writestr(name, data)
                pending = home / '.llm-foundation/state/codex-other/pending.json'
                pending.parent.mkdir(parents=True)
                pending.write_bytes(b'{}\n')
                rejected = run('rollback', code=20)
                assert rejected['message'] == 'Another target has an unfinished shared-tool transaction'
                assert _state(home) == installed
                pending.unlink()
                run("install", status="CANONICAL", target="codex-other", selected_package=other)
                foreign_state = _state(home)
                rejected = run("rollback", code=20)
                assert rejected["message"] == "Bundled OfficeCLI generation changed; rollback refused"
                assert _state(home) == foreign_state
                run("rollback", status="ROLLED_BACK", target="codex-other")
                assert _state(home) == installed
            run("rollback", status="ROLLED_BACK")
            assert _state(home) == before
        after = _state(home)
    finally:
        actual_after = _real_environment()
        assert actual_after == actual_before, "real User environment changed"
    receipts = Path(os.environ["K7_ENGINE_LIFECYCLE_RECEIPTS"])
    assert receipts.resolve().is_relative_to(workspace)
    receipts.mkdir(parents=True, exist_ok=True)
    receipt = {
        "scenario_id": scenario_id, "shell": shell, "status": "PASS",
        "engine_sha256": _sha((engine / "foundation.ps1").read_bytes()),
        "engine_manifest_sha256": _sha((engine / "engine-manifest.json").read_bytes()),
        "commands": commands, "real_user_environment_before": actual_before,
        "real_user_environment_after": actual_after,
        "before": before, "installed": installed, "after": after,
    }
    destination = receipts / f"{shell}-{scenario_id}.json"
    with destination.open("xb") as stream:
        stream.write(_bytes(receipt))
