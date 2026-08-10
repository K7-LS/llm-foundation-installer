import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import textwrap
import ctypes
import time

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


def _build_fault_injection_launcher(output: Path) -> None:
    compiler = _find_csharp_compiler()
    assert compiler is not None, "C# compiler is unavailable"
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(compiler), "/nologo", "/target:exe", "/platform:anycpu", "/optimize+",
            "/checked+", "/deterministic+", "/codepage:65001", "/utf8output",
            "/define:FOUNDATION_RECOVERY_FAULT_INJECTION",
            f"/out:{output}", "/reference:System.Web.Extensions.dll",
            str(SOURCE), str(RECOVERY),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


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


def _fingerprint(path: Path) -> str:
    if not path.exists():
        return "absent"
    if path.is_file():
        return _sha256(path)
    canonical = bytearray()
    for file in sorted((item for item in path.rglob("*") if item.is_file()), key=str):
        relative = file.relative_to(path).as_posix()
        canonical.extend(f"{relative}\0{_sha256(file)}\n".encode("utf-8"))
    return hashlib.sha256(canonical).hexdigest()


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
    recovery_fault_injection: bool = False,
) -> tuple[Path, Path, dict[str, str]]:
    build = tmp_path / "build"
    launcher = build / f"{target}-managed.exe"
    if recovery_fault_injection:
        _build_fault_injection_launcher(launcher)
    else:
        host = _powershells()[0]
        result = _build(host, build)
        assert result.returncode == 0, result.stderr
    updater = tmp_path / "update-session-tools.ps1"
    updater.write_text(updater_body, encoding="utf-8")
    vendor = tmp_path / "vendor.exe"
    _compile_probe(
        vendor,
        r'''
        using System;
        using System.Diagnostics;
        using System.IO;
        class Vendor
        {
            static int Main(string[] args)
            {
                File.WriteAllLines(Environment.GetEnvironmentVariable("MANAGED_LAUNCHER_VENDOR_LOG"), args);
                string tickLog = Environment.GetEnvironmentVariable("MANAGED_LAUNCHER_VENDOR_TICK_LOG");
                if (!String.IsNullOrEmpty(tickLog))
                    File.WriteAllText(tickLog, Stopwatch.GetTimestamp().ToString());
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


def _staged_journal_fixture(
    tmp_path: Path, recovery_fault_injection: bool = False,
) -> tuple[Path, Path, dict[str, str], Path, dict]:
    launcher, vendor_log, environment = _install_runtime(
        tmp_path, "codex", recovery_fault_injection=recovery_fault_injection,
    )
    receipt = launcher.with_suffix(".receipt.json")
    profile = Path(environment["USERPROFILE"])
    state_root = profile / ".llm-foundation" / "state" / "session-tools" / "codex"
    transaction_id = "12345678-1234-1234-1234-123456789abc"
    transaction_root = state_root / "transactions" / transaction_id
    staging = transaction_root / "staging"
    staging.mkdir(parents=True)
    (staging / "payload.txt").write_text("new", encoding="utf-8")
    ticks = _stopwatch_contract()
    value = {
        "schema_version": 1, "target": "codex", "transaction_id": transaction_id,
        "phase": "staged", "receipt_sha256": _sha256(receipt),
        "start_tick": ticks[0], "mutation_cutoff_tick": ticks[1],
        "kill_tick": ticks[2], "hard_deadline_tick": ticks[3],
        "stopwatch_frequency": ticks[4], "previous_destination_sha256": "absent",
        "previous_state_sha256": "absent", "expected_staging_sha256": _fingerprint(staging),
        "expected_destination_sha256": _fingerprint(staging), "expected_state_sha256": "absent",
        "staging_path": str(staging), "previous_path": str(transaction_root / "previous"),
        "destination_path": str(profile / ".agents" / "skills" / "fixture-skill"),
        "state_path": str(state_root / "state.json"),
        "operations": {
            "move_destination_to_previous": {"intent": False, "applied": False},
            "move_staging_to_destination": {"intent": False, "applied": False},
            "write_state": {"intent": False, "applied": False},
        },
    }
    return launcher, vendor_log, environment, state_root / "active-transaction.json", value


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
    frequency = int(tokens[12])
    assert frequency > 0
    assert ticks[1] - ticks[0] == 22 * frequency
    assert ticks[2] - ticks[0] == 25 * frequency
    assert ticks[3] - ticks[0] == 30 * frequency
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


def test_unknown_recovery_phase_blocks_without_deleting_transaction_data(tmp_path: Path) -> None:
    """An unknown phase must not be treated like a safe pre-mutation journal."""
    launcher, vendor_log, environment = _install_runtime(tmp_path, "codex")
    receipt = launcher.with_suffix(".receipt.json")
    state_root = Path(environment["USERPROFILE"]) / ".llm-foundation" / "state" / "session-tools" / "codex"
    transaction_id = "12345678-1234-1234-1234-123456789abc"
    transaction_root = state_root / "transactions" / transaction_id
    staging = transaction_root / "staging"
    previous = transaction_root / "previous"
    destination = Path(environment["USERPROFILE"]) / ".agents" / "skills" / "phase-test"
    staging.mkdir(parents=True)
    ticks = _stopwatch_contract()
    journal = state_root / "active-transaction.json"
    journal.write_text(json.dumps({
        "schema_version": 1, "target": "codex",
        "transaction_id": transaction_id,
        "phase": "unknown", "receipt_sha256": _sha256(receipt),
        "start_tick": ticks[0], "mutation_cutoff_tick": ticks[1],
        "kill_tick": ticks[2], "hard_deadline_tick": ticks[3],
        "stopwatch_frequency": ticks[4], "previous_destination_sha256": "absent",
        "previous_state_sha256": "absent", "expected_staging_sha256": _fingerprint(staging),
        "expected_destination_sha256": "absent", "expected_state_sha256": "absent",
        "staging_path": str(staging), "previous_path": str(previous),
        "destination_path": str(destination), "state_path": str(state_root / "state.json"),
        "operations": {
            "move_destination_to_previous": {"intent": False, "applied": False},
            "move_staging_to_destination": {"intent": False, "applied": False},
            "write_state": {"intent": False, "applied": False},
        },
    }), encoding="utf-8")

    invocation = subprocess.run([str(launcher)], check=False, capture_output=True, text=True,
                                encoding="utf-8", env=environment, timeout=10)
    assert invocation.returncode != 41
    assert "BLOCKED_SESSION_RECOVERY" in invocation.stderr
    assert staging.exists()
    assert not previous.exists()
    assert not destination.exists()
    assert journal.exists()
    assert not vendor_log.exists()


def test_transaction_staging_cannot_alias_an_existing_managed_skill(tmp_path: Path) -> None:
    """A journal may not delete a managed skill by naming it as transaction staging."""
    launcher, vendor_log, environment = _install_runtime(tmp_path, "codex")
    receipt = launcher.with_suffix(".receipt.json")
    profile = Path(environment["USERPROFILE"])
    state_root = profile / ".llm-foundation" / "state" / "session-tools" / "codex"
    state_root.mkdir(parents=True)
    transaction_id = "12345678-1234-1234-1234-123456789abc"
    transaction_root = state_root / "transactions" / transaction_id
    transaction_root.mkdir(parents=True)
    victim = profile / ".agents" / "skills" / "neighbor-skill"
    victim.mkdir(parents=True)
    marker = victim / "SKILL.md"
    marker.write_text("local-neighbor", encoding="utf-8")
    previous = transaction_root / "previous"
    destination = profile / ".agents" / "skills" / "managed-test"
    ticks = _stopwatch_contract()
    journal = state_root / "active-transaction.json"
    journal.write_text(json.dumps({
        "schema_version": 1, "target": "codex",
        "transaction_id": transaction_id,
        "phase": "staged", "receipt_sha256": _sha256(receipt),
        "start_tick": ticks[0], "mutation_cutoff_tick": ticks[1],
        "kill_tick": ticks[2], "hard_deadline_tick": ticks[3],
        "stopwatch_frequency": ticks[4], "previous_destination_sha256": "absent",
        "previous_state_sha256": "absent", "expected_staging_sha256": _fingerprint(victim),
        "expected_destination_sha256": "absent", "expected_state_sha256": "absent",
        "staging_path": str(victim), "previous_path": str(previous),
        "destination_path": str(destination), "state_path": str(state_root / "state.json"),
        "operations": {
            "move_destination_to_previous": {"intent": False, "applied": False},
            "move_staging_to_destination": {"intent": False, "applied": False},
            "write_state": {"intent": False, "applied": False},
        },
    }), encoding="utf-8")

    invocation = subprocess.run([str(launcher)], check=False, capture_output=True, text=True,
                                encoding="utf-8", env=environment, timeout=10)
    assert invocation.returncode != 41
    assert marker.read_text(encoding="utf-8") == "local-neighbor"
    assert journal.exists()
    assert not vendor_log.exists()


def test_journal_rejects_unknown_operations_fields_hashes_and_tick_intervals(tmp_path: Path) -> None:
    """Every malformed transition variant blocks without consuming the journal."""
    launcher, vendor_log, environment, journal, valid = _staged_journal_fixture(tmp_path)
    invalid_values: list[dict] = []

    unknown_operation = json.loads(json.dumps(valid))
    unknown_operation["operations"]["unexpected"] = {"intent": False, "applied": False}
    invalid_values.append(unknown_operation)

    unknown_operation_field = json.loads(json.dumps(valid))
    unknown_operation_field["operations"]["write_state"]["extra"] = False
    invalid_values.append(unknown_operation_field)

    unknown_top_level_field = json.loads(json.dumps(valid))
    unknown_top_level_field["unexpected"] = False
    invalid_values.append(unknown_top_level_field)

    wrong_tick_type = json.loads(json.dumps(valid))
    wrong_tick_type["start_tick"] = str(wrong_tick_type["start_tick"])
    invalid_values.append(wrong_tick_type)

    wrong_operation_type = json.loads(json.dumps(valid))
    wrong_operation_type["operations"]["write_state"]["intent"] = 0
    invalid_values.append(wrong_operation_type)

    wrong_transition = json.loads(json.dumps(valid))
    wrong_transition["operations"]["move_destination_to_previous"] = {
        "intent": True,
        "applied": False,
    }
    invalid_values.append(wrong_transition)

    bad_hash = json.loads(json.dumps(valid))
    bad_hash["expected_staging_sha256"] = "0" * 64
    invalid_values.append(bad_hash)

    bad_ticks = json.loads(json.dumps(valid))
    bad_ticks["mutation_cutoff_tick"] += 1
    invalid_values.append(bad_ticks)

    empty_committed = json.loads(json.dumps(valid))
    empty_committed["phase"] = "committed"
    empty_committed["operations"] = {}
    invalid_values.append(empty_committed)

    for value in invalid_values:
        journal.write_text(json.dumps(value), encoding="utf-8")
        invocation = subprocess.run([str(launcher)], check=False, capture_output=True, text=True,
                                    encoding="utf-8", env=environment, timeout=10)
        assert invocation.returncode != 41
        assert "BLOCKED_SESSION_RECOVERY" in invocation.stderr
        assert journal.exists()
        assert not vendor_log.exists()

    duplicated_record = json.dumps(valid).replace(
        '"intent": false', '"intent": false, "intent": false', 1,
    )
    journal.write_text(duplicated_record, encoding="utf-8")
    invocation = subprocess.run([str(launcher)], check=False, capture_output=True, text=True,
                                encoding="utf-8", env=environment, timeout=10)
    assert invocation.returncode != 41
    assert "BLOCKED_SESSION_RECOVERY" in invocation.stderr
    assert journal.exists()
    assert not vendor_log.exists()

    escaped_duplicate = json.dumps(valid).replace(
        '"target": "codex"', r'"\u0074arget": "codex", "target": "codex"', 1,
    )
    journal.write_text(escaped_duplicate, encoding="utf-8")
    invocation = subprocess.run([str(launcher)], check=False, capture_output=True, text=True,
                                encoding="utf-8", env=environment, timeout=10)
    assert invocation.returncode != 41
    assert "BLOCKED_SESSION_RECOVERY" in invocation.stderr
    assert journal.exists()
    assert not vendor_log.exists()


def test_every_supported_journal_transition_recovers_to_a_verified_state(tmp_path: Path) -> None:
    """Each exact intent/applied phase either restores old bytes or proves committed new bytes."""
    launcher, vendor_log, environment = _install_runtime(tmp_path, "codex")
    receipt = launcher.with_suffix(".receipt.json")
    profile = Path(environment["USERPROFILE"])
    state_root = profile / ".llm-foundation" / "state" / "session-tools" / "codex"
    journal = state_root / "active-transaction.json"
    phases = [
        "created", "staged", "move_destination_intent", "move_destination_applied",
        "move_staging_intent", "move_staging_applied", "state_write_intent",
        "state_write_applied", "committed",
    ]
    for phase_index, phase in enumerate(phases):
        transaction_id = f"12345678-1234-1234-1234-{phase_index:012d}"
        transaction_root = state_root / "transactions" / transaction_id
        staging = transaction_root / "staging"
        previous = transaction_root / "previous"
        destination = profile / ".agents" / "skills" / f"phase-{phase_index}"
        state_path = state_root / "state.json"
        transaction_root.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
        (staging / "payload.txt").write_text("new", encoding="utf-8")
        new_hash = _fingerprint(staging)
        old_template = transaction_root / "old-template"
        old_template.mkdir()
        (old_template / "payload.txt").write_text("old", encoding="utf-8")
        old_hash = _fingerprint(old_template)
        state_hash = hashlib.sha256(b"new-state").hexdigest()
        transition = max(-1, phase_index - 2)
        flags = [index <= transition for index in range(6)]
        if phase == "committed":
            flags = [True] * 6
        moved_old, moved_new, wrote_state = flags[1], flags[3], flags[5]

        if moved_old:
            old_template.rename(previous)
        else:
            old_template.rename(destination)
        if moved_new:
            if destination.exists():
                shutil.rmtree(destination)
            staging.rename(destination)
        if phase == "created" and staging.exists():
            shutil.rmtree(staging)
        if wrote_state:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_bytes(b"new-state")
        elif state_path.exists():
            state_path.unlink()

        ticks = _stopwatch_contract()
        value = {
            "schema_version": 1, "target": "codex", "transaction_id": transaction_id,
            "phase": phase, "receipt_sha256": _sha256(receipt),
            "start_tick": ticks[0], "mutation_cutoff_tick": ticks[1],
            "kill_tick": ticks[2], "hard_deadline_tick": ticks[3],
            "stopwatch_frequency": ticks[4], "previous_destination_sha256": old_hash,
            "previous_state_sha256": "absent",
            "expected_staging_sha256": "absent" if phase == "created" else new_hash,
            "expected_destination_sha256": new_hash, "expected_state_sha256": state_hash,
            "staging_path": str(staging), "previous_path": str(previous),
            "destination_path": str(destination), "state_path": str(state_path),
            "operations": {
                "move_destination_to_previous": {"intent": flags[0], "applied": flags[1]},
                "move_staging_to_destination": {"intent": flags[2], "applied": flags[3]},
                "write_state": {"intent": flags[4], "applied": flags[5]},
            },
        }
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(json.dumps(value), encoding="utf-8")
        invocation = subprocess.run([str(launcher)], check=False, capture_output=True, text=True,
                                    encoding="utf-8", env=environment, timeout=10)
        assert invocation.returncode == 41, (phase, invocation.stdout + invocation.stderr)
        assert not journal.exists()
        if wrote_state:
            assert _fingerprint(destination) == new_hash
            assert _fingerprint(state_path) == state_hash
        else:
            assert _fingerprint(destination) == old_hash
            assert not state_path.exists()
        assert not previous.exists()


@pytest.mark.parametrize(
    ("has_previous", "phase", "actual_step"),
    [
        (False, "move_destination_applied", 1),
        (False, "move_staging_intent", 2),
        (True, "move_destination_intent", 1),
        (True, "move_staging_intent", 2),
    ],
)
def test_recovery_reconciles_move_before_durable_applied(
    tmp_path: Path, has_previous: bool, phase: str, actual_step: int,
) -> None:
    """A kill between a filesystem move and applied must restore the verified old layout."""
    launcher, vendor_log, environment = _install_runtime(tmp_path, "codex")
    receipt = launcher.with_suffix(".receipt.json")
    profile = Path(environment["USERPROFILE"])
    state_root = profile / ".llm-foundation" / "state" / "session-tools" / "codex"
    transaction_id = "12345678-1234-1234-1234-123456789abc"
    transaction_root = state_root / "transactions" / transaction_id
    staging = transaction_root / "staging"
    previous = transaction_root / "previous"
    destination = profile / ".agents" / "skills" / "crash-window"
    staging.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    (staging / "payload.txt").write_text("new", encoding="utf-8")
    expected_hash = _fingerprint(staging)
    previous_hash = "absent"
    if has_previous:
        destination.mkdir(parents=True)
        (destination / "payload.txt").write_text("old", encoding="utf-8")
        previous_hash = _fingerprint(destination)
    if actual_step >= 1 and has_previous:
        destination.rename(previous)
    if actual_step >= 2:
        staging.rename(destination)

    phases = [
        "created", "staged", "move_destination_intent", "move_destination_applied",
        "move_staging_intent", "move_staging_applied", "state_write_intent",
        "state_write_applied", "committed",
    ]
    transition = max(-1, phases.index(phase) - 2)
    flags = [index <= transition for index in range(6)]
    ticks = _stopwatch_contract()
    journal = state_root / "active-transaction.json"
    journal.write_text(json.dumps({
        "schema_version": 1, "target": "codex", "transaction_id": transaction_id,
        "phase": phase, "receipt_sha256": _sha256(receipt),
        "start_tick": ticks[0], "mutation_cutoff_tick": ticks[1],
        "kill_tick": ticks[2], "hard_deadline_tick": ticks[3],
        "stopwatch_frequency": ticks[4], "previous_destination_sha256": previous_hash,
        "previous_state_sha256": "absent", "expected_staging_sha256": expected_hash,
        "expected_destination_sha256": expected_hash, "expected_state_sha256": "absent",
        "staging_path": str(staging), "previous_path": str(previous),
        "destination_path": str(destination), "state_path": str(state_root / "state.json"),
        "operations": {
            "move_destination_to_previous": {"intent": flags[0], "applied": flags[1]},
            "move_staging_to_destination": {"intent": flags[2], "applied": flags[3]},
            "write_state": {"intent": flags[4], "applied": flags[5]},
        },
    }), encoding="utf-8")

    invocation = subprocess.run([str(launcher)], check=False, capture_output=True, text=True,
                                encoding="utf-8", env=environment, timeout=10)
    assert invocation.returncode == 41, invocation.stdout + invocation.stderr
    assert _fingerprint(destination) == previous_hash
    assert not staging.exists()
    assert not previous.exists()
    assert not journal.exists()
    assert vendor_log.exists()


def test_nonzero_updater_without_journal_launches_verified_vendor(tmp_path: Path) -> None:
    """A pre-mutation updater failure keeps the last verified copy available."""
    launcher, vendor_log, environment = _install_runtime(tmp_path, "claude", "exit 17\n")
    invocation = subprocess.run([str(launcher), "safe"], check=False, capture_output=True,
                                text=True, encoding="utf-8", env=environment, timeout=10)
    assert invocation.returncode == 41, invocation.stdout + invocation.stderr
    assert vendor_log.read_text(encoding="utf-8").splitlines() == ["safe"]


def test_job_kills_updater_tree_then_launches_verified_vendor(tmp_path: Path) -> None:
    """A pre-mutation timeout kills the updater tree and keeps the verified vendor available."""
    updater_body = r'''
    [IO.File]::WriteAllLines($env:MANAGED_LAUNCHER_UPDATER_TIMING_LOG, @($args[4], $args[12]))
    $child = $env:MANAGED_LAUNCHER_CHILD_SCRIPT
    $systemPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    Start-Process -FilePath $systemPowerShell -ArgumentList @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-File', ('"' + $child + '"')
    ) -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 60
    '''
    launcher, vendor_log, environment = _install_runtime(tmp_path, "claude", updater_body)
    child_marker = tmp_path / "escaped-child.txt"
    updater_timing_log = tmp_path / "updater-timing.txt"
    vendor_tick_log = tmp_path / "vendor-tick.txt"
    child_script = tmp_path / "child.ps1"
    child_script.write_text(
        "Start-Sleep -Seconds 27\n[IO.File]::WriteAllText($env:MANAGED_LAUNCHER_CHILD_MARKER, 'escaped')\n",
        encoding="utf-8",
    )
    environment["MANAGED_LAUNCHER_CHILD_SCRIPT"] = str(child_script)
    environment["MANAGED_LAUNCHER_CHILD_MARKER"] = str(child_marker)
    environment["MANAGED_LAUNCHER_UPDATER_TIMING_LOG"] = str(updater_timing_log)
    environment["MANAGED_LAUNCHER_VENDOR_TICK_LOG"] = str(vendor_tick_log)
    started = time.monotonic()
    invocation = subprocess.run([str(launcher)], check=False, capture_output=True, text=True,
                                encoding="utf-8", env=environment, timeout=35)
    elapsed = time.monotonic() - started
    assert invocation.returncode == 41, invocation.stdout + invocation.stderr
    launcher_start_tick, frequency = map(
        int, updater_timing_log.read_text(encoding="utf-8-sig").splitlines(),
    )
    vendor_tick = int(vendor_tick_log.read_text(encoding="utf-8"))
    launcher_elapsed = (vendor_tick - launcher_start_tick) / frequency
    assert 24 <= launcher_elapsed < 30
    assert elapsed < 33
    assert vendor_log.exists()
    time.sleep(4)
    assert not child_marker.exists()


def test_hung_recovery_is_bounded_by_shared_hard_deadline(tmp_path: Path) -> None:
    """A stuck filesystem recovery cannot delay the block beyond the launcher's 30-second budget."""
    launcher, vendor_log, environment, journal, value = _staged_journal_fixture(
        tmp_path, recovery_fault_injection=True,
    )
    journal.write_text(json.dumps(value), encoding="utf-8")
    environment["FOUNDATION_RECOVERY_FAULT_DELAY_MS"] = "60000"
    started = time.monotonic()
    invocation = subprocess.run([str(launcher)], check=False, capture_output=True, text=True,
                                encoding="utf-8", env=environment, timeout=35)
    elapsed = time.monotonic() - started
    assert invocation.returncode == 70, invocation.stdout + invocation.stderr
    assert "BLOCKED_SESSION_RECOVERY" in invocation.stderr
    assert 29 <= elapsed < 32
    assert journal.exists()
    assert not vendor_log.exists()


def test_reparse_staging_path_blocks_and_preserves_neighbor(tmp_path: Path) -> None:
    """A transaction staging reparse point cannot redirect cleanup into local data."""
    launcher, vendor_log, environment, journal, value = _staged_journal_fixture(tmp_path)
    staging = Path(value["staging_path"])
    shutil.rmtree(staging)
    neighbor = tmp_path / "neighbor"
    neighbor.mkdir()
    marker = neighbor / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    try:
        os.symlink(neighbor, staging, target_is_directory=True)
    except OSError:
        junction = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(staging), str(neighbor)],
            check=False, capture_output=True, text=True, encoding="utf-8",
        )
        assert junction.returncode == 0, junction.stdout + junction.stderr
    value["expected_staging_sha256"] = _fingerprint(neighbor)
    journal.write_text(json.dumps(value), encoding="utf-8")

    invocation = subprocess.run([str(launcher)], check=False, capture_output=True, text=True,
                                encoding="utf-8", env=environment, timeout=10)
    assert invocation.returncode != 41
    assert marker.read_text(encoding="utf-8") == "keep"
    assert journal.exists()
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
    transaction_id = "12345678-1234-1234-1234-123456789abc"
    transaction_root = state_root / "transactions" / transaction_id
    staging = transaction_root / "staging"
    previous = transaction_root / "previous"
    destination = Path(environment["USERPROFILE"]) / ".agents" / "skills" / "managed-test"
    transaction_root.mkdir(parents=True)
    previous.mkdir()
    (previous / "payload.txt").write_text("old", encoding="utf-8")
    destination.mkdir(parents=True)
    (destination / "payload.txt").write_text("new", encoding="utf-8")
    previous_hash = _fingerprint(previous)
    expected_hash = _fingerprint(destination)
    journal = state_root / "active-transaction.json"
    start_tick, mutation_tick, kill_tick, deadline_tick, frequency = _stopwatch_contract()
    journal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": "codex",
                "transaction_id": transaction_id,
                "phase": "move_staging_applied",
                "receipt_sha256": _sha256(receipt),
                "start_tick": start_tick,
                "mutation_cutoff_tick": mutation_tick,
                "kill_tick": kill_tick,
                "hard_deadline_tick": deadline_tick,
                "stopwatch_frequency": frequency,
                "previous_destination_sha256": previous_hash,
                "previous_state_sha256": "absent",
                "expected_staging_sha256": expected_hash,
                "expected_destination_sha256": expected_hash,
                "expected_state_sha256": "absent",
                "staging_path": str(staging),
                "previous_path": str(previous),
                "destination_path": str(destination),
                "state_path": str(state_root / "state.json"),
                "operations": {
                    "move_destination_to_previous": {"intent": True, "applied": True},
                    "move_staging_to_destination": {"intent": True, "applied": True},
                    "write_state": {"intent": False, "applied": False},
                },
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
