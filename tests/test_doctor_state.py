"""Doctor checks canonical TOML requirements without changing installed homes."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from test_foundation import POWERSHELLS, _json, _package, _run, engine_root


def _state_path(home: Path) -> Path:
    return home / ".llm-foundation/state/codex/active.json"


def _read_state(home: Path) -> dict:
    return json.loads(_state_path(home).read_text(encoding="utf-8"))


def _write_state(home: Path, state: dict) -> None:
    _state_path(home).write_text(json.dumps(state), encoding="utf-8")


def _tree(root: Path) -> dict[str, tuple[str, int] | str]:
    return {
        path.relative_to(root).as_posix(): (
            (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            if path.is_file() else "directory"
        )
        for path in root.rglob("*")
    }


def _installed(tmp_path: Path, executable: str, engine_root: Path, *, keep=False, config_payload=None):
    home = tmp_path / "home"
    config = home / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_bytes(
        b'model = "old-user-model"\r\nmodel_reasoning_effort = "high"\r\n'
        b'project_doc_max_bytes = 123\r\n'
        b'[projects."C:/my-project"]\r\ntrust_level = "trusted"\r\n'
        + (b'[mcp_servers.local]\r\ncommand = "kept.exe"\r\n' if keep else b"")
    )
    package = _package(tmp_path / "package.zip", desired_state=True, config_payload=config_payload)
    exceptions = ["toml:.codex/config.toml#mcp_servers.local"] if keep else None
    result = _run(executable, engine_root, "install", home, package=package,
                  local_exceptions=exceptions)
    assert result.returncode == 0, result.stdout + result.stderr
    return home, config, package


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_doctor_canonical_contract_preserves_user_settings_read_only(engine_root, tmp_path, executable):
    home, config, package = _installed(tmp_path, executable, engine_root)
    state = _read_state(home)
    contracts = state["toml_contracts"]
    assert len(contracts) == 1
    assert contracts[0]["schema_version"] == 1
    assert contracts[0]["path"] == ".codex/config.toml"
    import zipfile
    with zipfile.ZipFile(package) as archive:
        assert contracts[0]["source_sha256"] == hashlib.sha256(
            archive.read(".codex/config.toml")).hexdigest()
    paths = [row["path_segments"] for row in contracts[0]["requirements"]]
    assert ["project_doc_max_bytes"] in paths
    assert ["model"] not in paths
    assert ["model_reasoning_effort"] not in paths
    assert not any(row[0] == "projects" for row in paths)
    content = config.read_text(encoding="utf-8").replace('"old-user-model"', '"new-user-model"')
    content = content.replace('"high"', '"ultra"')
    config.write_text(content, encoding="utf-8")
    before = _tree(home)
    for original in (None, package):
        result = _run(executable, engine_root, "doctor", home, package=original,
                      target="codex", strict=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert _json(result)["status"] == "CANONICAL"
        assert _tree(home) == before


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("mutation", ["managed", "invalid-toml", "exact-file", "new-unknown"])
def test_doctor_keeps_managed_drift_and_unknown_gates(engine_root, tmp_path, executable, mutation):
    home, config, _ = _installed(tmp_path, executable, engine_root)
    if mutation == "managed":
        config.write_text(config.read_text().replace("hooks = true", "hooks = false"), encoding="utf-8")
    elif mutation == "invalid-toml":
        config.write_text(config.read_text() + '\n[broken\nsecret = "not-for-logs"\n', encoding="utf-8")
    elif mutation == "exact-file":
        (home / ".codex/AGENTS.md").write_text("changed\n", encoding="utf-8")
    else:
        config.write_text(config.read_text() + '\n[mcp_servers.unapproved]\ncommand = "new.exe"\n', encoding="utf-8")
    before = _tree(home)
    result = _run(executable, engine_root, "doctor", home, target="codex", strict=True)
    assert result.returncode == 30, result.stdout + result.stderr
    assert _json(result)["code"] == "ACTIVE_DRIFT"
    assert "not-for-logs" not in result.stdout + result.stderr
    assert _tree(home) == before


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_doctor_kept_registration_stays_separate_from_toml_contract(engine_root, tmp_path, executable):
    home, config, package = _installed(tmp_path, executable, engine_root, keep=True)
    config.write_text(config.read_text().replace("kept.exe", "locally-updated.exe"), encoding="utf-8")
    before = _tree(home)
    result = _run(executable, engine_root, "doctor", home, package=package, strict=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _json(result)["status"] == "CANONICAL_WITH_LOCAL_EXCEPTIONS"
    assert _tree(home) == before
    config.write_text(config.read_text().replace("mcp_servers.local", "mcp_servers.other"), encoding="utf-8")
    result = _run(executable, engine_root, "doctor", home, target="codex", strict=True)
    assert result.returncode == 30
    assert _json(result)["message"] == "Local exception inventory differs"


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("mutation", ["null", "empty", "object", "empty-requirements", "duplicate", "bad-kind", "bad-source", "source-lf", "value-lf"])
def test_present_malformed_contract_never_falls_back(engine_root, tmp_path, executable, mutation):
    home, _, _ = _installed(tmp_path, executable, engine_root)
    state = _read_state(home)
    if mutation == "null":
        state["toml_contracts"] = None
    elif mutation == "empty":
        state["toml_contracts"] = []
    elif mutation == "object":
        state["toml_contracts"] = state["toml_contracts"][0]
    elif mutation == "empty-requirements":
        state["toml_contracts"][0]["requirements"] = []
    elif mutation == "duplicate":
        state["toml_contracts"][0]["requirements"].append(state["toml_contracts"][0]["requirements"][0])
    elif mutation == "bad-kind":
        state["toml_contracts"][0]["requirements"][0]["kind"] = "unknown"
    elif mutation == "source-lf":
        state["toml_contracts"][0]["source_sha256"] += "\n"
    elif mutation == "value-lf":
        state["toml_contracts"][0]["requirements"][0]["value_sha256"] += "\n"
    else:
        state["toml_contracts"][0]["source_sha256"] = "malformed"
    _write_state(home, state)
    before = _tree(home)
    result = _run(executable, engine_root, "doctor", home, target="codex")
    assert result.returncode == 30, result.stdout + result.stderr
    assert _json(result)["code"] == "INVALID_PACKAGE"
    assert _tree(home) == before


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("mode", ["changed-with-package", "managed-with-package", "unchanged-no-package", "changed-no-package", "wrong-package", "tampered-backup", "tampered-snapshot", "tampered-installed-hash"])
def test_legacy_baseline_is_reconstructed_not_taken_from_preinstall_backup(engine_root, tmp_path, executable, mode):
    home, config, package = _installed(tmp_path, executable, engine_root, keep=True)
    state = _read_state(home)
    state.pop("toml_contracts")
    _write_state(home, state)
    installed = next(row for row in state["installed_files"] if row["path"] == ".codex/config.toml")
    snapshot_path = Path(state["snapshot_path"])
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    backed_up = next(row for row in snapshot["backup_files"] if row["path"] == ".codex/config.toml")
    assert backed_up["sha256"] != installed["sha256"]
    installed_bytes = config.read_bytes()
    config.write_text(config.read_text().replace('"old-user-model"', '"new-user-model"'), encoding="utf-8")
    source = package
    if mode == "managed-with-package":
        config.write_text(config.read_text().replace("hooks = true", "hooks = false"), encoding="utf-8")
    elif mode == "unchanged-no-package":
        # Exact legacy bytes remain eligible for the old read-only path.
        config.write_bytes(installed_bytes)
        source = None
    elif mode == "changed-no-package":
        source = None
    elif mode == "wrong-package":
        source = _package(tmp_path / "other.zip", desired_state=True, version="1.0.1")
    elif mode == "tampered-backup":
        (snapshot_path.parent / backed_up["backup_path"]).write_text("changed backup", encoding="utf-8")
    elif mode == "tampered-snapshot":
        snapshot_path.write_text("{}", encoding="utf-8")
    elif mode == "tampered-installed-hash":
        installed["sha256"] = "0" * 64
        _write_state(home, state)
    before = _tree(home)
    temp_before = _tree(home.parent / "_foundation-temp")
    result = _run(executable, engine_root, "doctor", home, package=source, target="codex", strict=True)
    expected_pass = mode in {"changed-with-package", "unchanged-no-package"}
    assert result.returncode == (0 if expected_pass else 30), result.stdout + result.stderr
    if mode == "changed-no-package":
        assert _json(result)["code"] == "LEGACY_TOML_BASELINE_REQUIRED"
    elif mode == "managed-with-package":
        assert _json(result)["code"] == "ACTIVE_DRIFT"
    elif not expected_pass:
        assert _json(result)["code"] == "INVALID_PACKAGE"
    assert _tree(home) == before
    assert _tree(home.parent / "_foundation-temp") == temp_before


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("mutation", ["source-hash", "missing-requirement", "desired-rule", "client"])
def test_doctor_original_package_binds_canonical_state_contract(engine_root, tmp_path, executable, mutation):
    home, _, package = _installed(tmp_path, executable, engine_root)
    state = _read_state(home)
    if mutation == "source-hash":
        state["toml_contracts"][0]["source_sha256"] = "0" * 64
    elif mutation == "missing-requirement":
        state["toml_contracts"][0]["requirements"].pop()
    elif mutation == "desired-rule":
        state["desired_state"]["toml_reconcile"][0]["exact_tables"].pop()
    else:
        state["client"]["supported_version"] = "0.100.0"
    _write_state(home, state)
    before = _tree(home)
    result = _run(executable, engine_root, "doctor", home, package=package, strict=True)
    assert result.returncode == 30, result.stdout + result.stderr
    assert _json(result)["code"] == "INVALID_PACKAGE"
    assert _tree(home) == before


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_utf8_bom_canonical_source_retains_raw_hash_and_legacy_replay(engine_root, tmp_path, executable):
    canonical = (b"\xef\xbb\xbfproject_doc_max_bytes = 8192\n"
                 b"check_for_update_on_startup = false\n[features]\nhooks = true\n"
                 b"[agents]\nenabled = true\n")
    home, config, package = _installed(tmp_path, executable, engine_root, config_payload=canonical)
    state = _read_state(home)
    assert state["toml_contracts"][0]["source_sha256"] == hashlib.sha256(canonical).hexdigest()
    config.write_text(config.read_text().replace('"old-user-model"', '"new-user-model"'), encoding="utf-8")
    for legacy in (False, True):
        if legacy:
            state.pop("toml_contracts")
            _write_state(home, state)
        before = _tree(home)
        result = _run(executable, engine_root, "doctor", home, package=package, strict=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert _tree(home) == before


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_only_doctor_accepts_hash_bound_historical_engine_package(engine_root, tmp_path, executable):
    historical = tmp_path / "historical-engine"
    shutil.copytree(engine_root / "src", historical / "src")
    engine = historical / "src/foundation.ps1"
    text = engine.read_text(encoding="utf-8-sig")
    import re
    text = re.sub(r"\$script:EngineVersion = '[0-9.]+'", "$script:EngineVersion = '0.5.4'", text, count=1)
    engine.write_text(text, encoding="utf-8-sig")
    home = tmp_path / "home"
    home.mkdir()
    package = _package(tmp_path / "historical.zip", desired_state=True, foundation_engine_version="0.5.4")
    result = _run(executable, historical, "install", home, package=package)
    assert result.returncode == 0, result.stdout + result.stderr
    state = _read_state(home)
    state.pop("toml_contracts")
    _write_state(home, state)
    config = home / ".codex/config.toml"
    config.write_text('model = "user-model"\n' + config.read_text(), encoding="utf-8")
    before = _tree(home)
    result = _run(executable, engine_root, "doctor", home, package=package, strict=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _tree(home) == before
    result = _run(executable, engine_root, "plan", home, package=package)
    assert result.returncode == 30, result.stdout + result.stderr
    assert _json(result)["code"] == "INVALID_PACKAGE"
    assert _tree(home) == before
