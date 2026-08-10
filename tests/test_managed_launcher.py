import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import textwrap
import ctypes

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPOSITORY_ROOT / "src" / "managed-launcher" / "Program.cs"
RECOVERY = REPOSITORY_ROOT / "src" / "managed-launcher" / "SessionRecovery.cs"
BUILD = REPOSITORY_ROOT / "tools" / "build-managed-launcher.ps1"
TARGETS = ("claude", "codex", "opencode")


def _powershells() -> list[str]:
    return [
        executable
        for executable in ("pwsh.exe", "powershell.exe")
        if shutil.which(executable)
    ]


def _find_csharp_compiler() -> Path | None:
    candidates: list[Path] = []
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(variable)
        if root:
            candidates.extend(
                Path(root).glob(
                    "Microsoft Visual Studio/*/*/MSBuild/Current/Bin/Roslyn/csc.exe"
                )
            )
    framework = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")
    if framework.is_file():
        candidates.append(framework)
    return sorted(candidates)[0] if candidates else None


def _build(host: str, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            host,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BUILD),
            "-OutputDirectory",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _compile_probe(path: Path, source: str) -> None:
    compiler = _find_csharp_compiler()
    assert compiler is not None, "C# compiler is unavailable"
    source_path = path.with_suffix(".cs")
    source_path.write_text(textwrap.dedent(source), encoding="utf-8")
    result = subprocess.run(
        [str(compiler), "/nologo", "/target:exe", f"/out:{path}", str(source_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stopwatch_contract() -> tuple[int, int, int, int, int]:
    frequency = ctypes.c_longlong()
    counter = ctypes.c_longlong()
    assert ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(frequency))
    assert ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(counter))
    start = counter.value - frequency.value
    return (
        start,
        start + frequency.value * 22,
        start + frequency.value * 25,
        start + frequency.value * 30,
        frequency.value,
    )


def _write_receipt(launcher: Path, updater: Path, vendor: Path, target: str) -> Path:
    receipt = launcher.with_suffix(".receipt.json")
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": target,
                "launcher_path": str(launcher),
                "launcher_sha256": _sha256(launcher),
                "updater_path": str(updater),
                "vendor_executable_path": str(vendor),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return receipt


def _install_runtime(
    tmp_path: Path, target: str, updater_body: str = "exit 0\n",
) -> tuple[Path, Path, dict[str, str]]:
    host = _powershells()[0]
    build = tmp_path / "build"
    result = _build(host, build)
    assert result.returncode == 0, result.stderr
    launcher = build / f"{target}-managed.exe"
    updater = tmp_path / "update-session-tools.ps1"
    updater.write_text(updater_body, encoding="utf-8")
    vendor = tmp_path / "vendor.exe"
    _compile_probe(
        vendor,
        r'''
        using System;
        using System.IO;
        class Vendor
        {
            static int Main(string[] args)
            {
                File.WriteAllLines(Environment.GetEnvironmentVariable("MANAGED_LAUNCHER_VENDOR_LOG"), args);
                return 41;
            }
        }
        ''',
    )
    _write_receipt(launcher, updater, vendor, target)
    vendor_log = tmp_path / "vendor-argv.txt"
    environment = dict(os.environ)
    environment["MANAGED_LAUNCHER_VENDOR_LOG"] = str(vendor_log)
    environment["USERPROFILE"] = str(tmp_path / "home")
    return launcher, vendor_log, environment


def test_sources_define_launcher_contract_before_build() -> None:
    """Removing receipt, job, cutoff, or recovery guards must fail this contract."""
    source = SOURCE.read_text(encoding="utf-8")
    recovery = RECOVERY.read_text(encoding="utf-8")
    assert "claude-managed.exe" in source
    assert "codex-managed.exe" in source
    assert "opencode-managed.exe" in source
    assert "WindowsPowerShell\\\\v1.0\\\\powershell.exe" in source
    assert "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in source
    assert "TimeSpan.FromSeconds(22)" in source
    assert "TimeSpan.FromSeconds(25)" in source
    assert "TimeSpan.FromSeconds(30)" in source
    assert "-ManagedPreflight" in source
    assert "WindowsArgv.Serialize" in source
    assert "BLOCKED_SESSION_RECOVERY" in source
    assert "created" in recovery and "staged" in recovery
    assert "intent" in recovery and "applied" in recovery


def test_recovery_contract_is_fail_closed_for_real_skill_destinations_and_deadlines() -> None:
    """Unknown JSON, escaped aliases, reparse points, and expired recovery must block."""
    source = SOURCE.read_text(encoding="utf-8")
    recovery = RECOVERY.read_text(encoding="utf-8")
    assert '".agents", "skills"' in recovery
    assert '".claude", "skills"' in recovery
    assert '".config", "opencode", "skills"' in recovery
    assert "Stopwatch.GetTimestamp() >= hardDeadlineTick" in recovery
    assert "ContainsUnicodeEscape" in recovery
    assert "expected_destination_sha256" in recovery
    assert "previous_state_sha256" in recovery
    assert "CREATE_SUSPENDED" in source
    assert "ResumeThread" in source
    assert "UpdaterFailed" in source


def test_build_emits_deterministic_target_specific_launchers(tmp_path: Path) -> None:
    """Changing the build inputs or target filenames must fail this contract."""
    hosts = _powershells()
    assert {Path(host).stem.lower() for host in hosts} >= {"pwsh", "powershell"}
    outputs: list[Path] = []
    for host in hosts:
        output = tmp_path / Path(host).stem
        result = _build(host, output)
        assert result.returncode == 0, result.stderr
        for target in TARGETS:
            assert (output / f"{target}-managed.exe").is_file()
        outputs.append(output)
    for target in TARGETS:
        assert (outputs[0] / f"{target}-managed.exe").read_bytes() == (
            outputs[1] / f"{target}-managed.exe"
        ).read_bytes()


def test_launcher_forwards_original_windows_argv_only_to_vendor(tmp_path: Path) -> None:
    """Shell metacharacters and Unicode must reach the vendor byte-for-byte."""
    launcher, vendor_log, environment = _install_runtime(tmp_path, "codex")
    arguments = [
        "", "two words", "tab\tvalue", 'quote"value', "trailing\\", "%", "!",
        "^", "&", "|", "<", ">", "Привет, мир",
    ]
    invocation = subprocess.run(
        [str(launcher), *arguments], check=False, capture_output=True, text=True,
        encoding="utf-8", env=environment, timeout=35,
    )
    assert invocation.returncode == 41, invocation.stdout + invocation.stderr
    assert vendor_log.read_text(encoding="utf-8").splitlines() == arguments


def test_launcher_passes_only_fixed_preflight_tokens_to_updater(tmp_path: Path) -> None:
    """User argv must not reach PowerShell; ticks and GUID come from the launcher."""
    launcher, vendor_log, environment = _install_runtime(
        tmp_path,
        "claude",
        "[IO.File]::WriteAllLines($env:MANAGED_LAUNCHER_UPDATER_LOG, [string[]]$args)\nexit 0\n",
    )
    updater_log = tmp_path / "updater-argv.txt"
    environment["MANAGED_LAUNCHER_UPDATER_LOG"] = str(updater_log)
    user_arguments = ["--untrusted", "&", "Привет", 'quote"value']
    invocation = subprocess.run(
        [str(launcher), *user_arguments], check=False, capture_output=True, text=True,
        encoding="utf-8", env=environment, timeout=35,
    )
    assert invocation.returncode == 41, invocation.stdout + invocation.stderr
    tokens = updater_log.read_text(encoding="utf-8").splitlines()
    assert tokens[:7] == [
        "-ManagedPreflight", "-TransactionId", tokens[2], "-StartTick", tokens[4],
        "-MutationCutoffTick", tokens[6],
    ]
    assert tokens[7:14] == [
        "-KillTick", tokens[8], "-HardDeadlineTick", tokens[10],
        "-StopwatchFrequency", tokens[12],
    ]
    assert len(tokens) == 13
    assert all(value not in tokens for value in user_arguments)
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        tokens[2],
    )
    ticks = [int(tokens[index]) for index in (4, 6, 8, 10)]
    assert 0 < ticks[0] < ticks[1] < ticks[2] < ticks[3]
    assert int(tokens[12]) > 0
    assert vendor_log.read_text(encoding="utf-8").splitlines() == user_arguments


def test_tampered_receipt_blocks_before_vendor_launch(tmp_path: Path) -> None:
    """Receipt hash or target mismatch must block the managed entrypoint."""
    launcher, vendor_log, environment = _install_runtime(tmp_path, "claude")
    receipt = launcher.with_suffix(".receipt.json")
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["target"] = "codex"
    receipt.write_text(json.dumps(value), encoding="utf-8")
    invocation = subprocess.run(
        [str(launcher), "--test"], check=False, capture_output=True, text=True,
        encoding="utf-8", env=environment, timeout=10,
    )
    assert invocation.returncode != 41
    assert not vendor_log.exists()


def test_active_unsafe_journal_blocks_before_vendor_launch(tmp_path: Path) -> None:
    """Unsafe or incomplete recovery must never start the vendor process."""
    launcher, vendor_log, environment = _install_runtime(tmp_path, "opencode")
    journal = (
        Path(environment["USERPROFILE"])
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
        / "active-transaction.json"
    )
    journal.parent.mkdir(parents=True)
    journal.write_text('{"schema_version":1,"phase":"created"}', encoding="utf-8")
    invocation = subprocess.run(
        [str(launcher)], check=False, capture_output=True, text=True,
        encoding="utf-8", env=environment, timeout=10,
    )
    assert invocation.returncode != 41
    assert "BLOCKED_SESSION_RECOVERY" in invocation.stderr
    assert not vendor_log.exists()


def test_recovery_rolls_back_applied_operation_and_is_idempotent(tmp_path: Path) -> None:
    """A killed apply restores its previous destination before the vendor starts."""
    launcher, vendor_log, environment = _install_runtime(tmp_path, "codex")
    receipt = launcher.with_suffix(".receipt.json")
    state_root = (
        Path(environment["USERPROFILE"])
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "codex"
    )
    staging = state_root / "transaction-staging"
    previous = state_root / "transaction-previous"
    destination = state_root / "destination"
    state_root.mkdir(parents=True)
    staging.mkdir()
    previous.mkdir()
    (previous / "payload.txt").write_text("old", encoding="utf-8")
    destination.mkdir()
    (destination / "payload.txt").write_text("new", encoding="utf-8")
    journal = state_root / "active-transaction.json"
    start_tick, mutation_tick, kill_tick, deadline_tick, frequency = _stopwatch_contract()
    journal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": "codex",
                "transaction_id": "12345678-1234-1234-1234-123456789abc",
                "phase": "applying",
                "receipt_sha256": _sha256(receipt),
                "start_tick": start_tick,
                "mutation_cutoff_tick": mutation_tick,
                "kill_tick": kill_tick,
                "hard_deadline_tick": deadline_tick,
                "stopwatch_frequency": frequency,
                "previous_state_sha256": "absent",
                "expected_destination_sha256": "absent",
                "expected_state_sha256": "absent",
                "staging_path": str(staging),
                "previous_path": str(previous),
                "destination_path": str(destination),
                "state_path": str(state_root / "state.json"),
                "operations": {"replace": {"intent": True, "applied": True}},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    first = subprocess.run(
        [str(launcher)], check=False, capture_output=True, text=True,
        encoding="utf-8", env=environment, timeout=10,
    )
    assert first.returncode == 41, first.stdout + first.stderr
    assert (destination / "payload.txt").read_text(encoding="utf-8") == "old"
    assert not staging.exists()
    assert not previous.exists()
    assert not journal.exists()

    second = subprocess.run(
        [str(launcher)], check=False, capture_output=True, text=True,
        encoding="utf-8", env=environment, timeout=10,
    )
    assert second.returncode == 41, second.stdout + second.stderr
