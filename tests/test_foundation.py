from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


SUPPORTED_CLIENT = "0.146.0-alpha.3.1"
POWERSHELLS = [
    value
    for value in (shutil.which("pwsh"), shutil.which("powershell.exe"))
    if value
]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _package(
    path: Path,
    *,
    version: str = "1.0.0",
    foundation_engine_version: str = "0.1.0",
    wrong_hash: bool = False,
    traversal: bool = False,
    protected: bool = False,
    reverse_policy: bool = False,
    omit_replace_row: bool = False,
    empty_exact_directory: bool = False,
    nested_managed_root: bool = False,
    casefold_preserved: bool = False,
) -> Path:
    entries = {
        ".codex/AGENTS.md": b"# candidate\n",
        ".codex/config.toml": b"[features]\nhooks = true\n",
        ".codex/hooks.json": b'{"hooks":{}}\n',
        ".codex/agents/auditor.toml": b'name = "auditor"\n',
        ".agents/skills/alpha/SKILL.md": b"---\nname: alpha\ndescription: test\n---\n",
        ".agents/skills/sync-base/SKILL.md": b"---\nname: sync-base\ndescription: sync\n---\n",
        ".codex/base/VERSION": (version + "\n").encode(),
        ".codex/base/components.lock.json": b'{"components":{}}\n',
        ".codex/base/cold/reference.md": b"# cold\n",
        ".codex/base/runtime/hooks/check.ps1": b"exit 0\n",
        ".codex/base/foundation/0.1.0/VERSION": b"0.1.0\n",
    }
    replace_files = [
        ".codex/AGENTS.md",
        ".codex/base/VERSION",
        ".codex/base/components.lock.json",
        ".codex/config.toml",
        ".codex/hooks.json",
    ]
    if protected:
        entries[".codex/auth.json"] = b'{"token":"must-not-install"}\n'
        replace_files.append(".codex/auth.json")
        replace_files.sort()
    exact_directories = [
        ".agents/skills",
        ".codex/agents",
        *([".codex/agents/nested"] if nested_managed_root else []),
        ".codex/base/cold",
        ".codex/base/foundation",
        ".codex/base/runtime",
    ]
    if casefold_preserved:
        entries.pop(".codex/agents/auditor.toml")
        entries[".CODEX/SESSIONS/managed.txt"] = b"must-not-install\n"
        exact_directories.remove(".codex/agents")
        exact_directories.append(".CODEX/SESSIONS")
        exact_directories.sort()
    rows = []
    for name, payload in sorted(entries.items()):
        if omit_replace_row and name == ".codex/AGENTS.md":
            continue
        if empty_exact_directory and name.startswith(".codex/agents/"):
            continue
        digest = "0" * 64 if wrong_hash and name == ".codex/AGENTS.md" else _sha256(payload)
        rows.append(
            {
                "path": name,
                "sha256": digest,
                "bytes": len(payload),
            }
        )
    manifest = {
        "schema_version": 1,
        "target": "codex",
        "version": version,
        "client": {
            "id": "codex-cli",
            "supported_version": SUPPORTED_CLIENT,
        },
        "foundation_engine_version": foundation_engine_version,
        "managed_surface": {
            "exact_directories": [
                *exact_directories,
            ],
            "replace_files": [
                *replace_files,
            ],
            "preserved_paths": [
                ".codex/archived_sessions",
                ".codex/auth.json",
                ".codex/browser",
                ".codex/computer-use",
                ".codex/imports",
                ".codex/memories",
                ".codex/sessions",
                ".codex/state",
                ".codex/state.sqlite",
            ],
        },
        "sync_policy": {
            "direction": "hub-to-consumer",
            "consumer_feedback_upload": False,
            "consumer_push": reverse_policy,
            "consumer_session_upload": False,
            "credentials_included": False,
        },
        "files": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
        archive.writestr(
            "package-manifest.json",
            json.dumps(manifest, sort_keys=True).encode() + b"\n",
        )
        if traversal:
            archive.writestr("../escaped.txt", b"escape")
    return path


@pytest.fixture(scope="session")
def engine_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run(
    executable: str,
    engine_root: Path,
    command: str,
    home: Path,
    *,
    package: Path | None = None,
    target: str | None = None,
    client: str = SUPPORTED_CLIENT,
    client_id: str = "codex-cli",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    arguments = [
        executable,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(engine_root / "src" / "foundation.ps1"),
        command,
        "-Home",
        str(home),
        "-Json",
    ]
    if package is not None:
        arguments.extend(["-Package", str(package)])
    if target is not None:
        arguments.extend(["-Target", target])
    if client:
        arguments.extend(["-ClientId", client_id])
        arguments.extend(["-ClientVersion", client])
    environment = os.environ.copy()
    environment["FOUNDATION_ACCEPTANCE_MODE"] = "1"
    if extra_env:
        environment.update(extra_env)
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


def _json(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.stdout.strip(), result.stderr
    return json.loads(result.stdout)


def _seed_home(home: Path) -> dict[Path, bytes]:
    sentinels = {
        home / ".codex" / "auth.json": b'{"token":"preserve"}\n',
        home / ".codex" / "sessions" / "one.json": b"session\n",
        home / ".codex" / "archived_sessions" / "old.json": b"archive\n",
        home / ".codex" / "memories" / "memory.md": b"memory\n",
        home / ".codex" / "state.sqlite": b"sqlite\n",
        home / ".codex" / "browser" / "state.json": b"browser\n",
        home / "project" / "work.txt": b"project\n",
    }
    for path, payload in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    (home / ".codex" / "AGENTS.md").write_text("# previous\n", encoding="utf-8")
    local_skill = home / ".agents" / "skills" / "local-personal" / "SKILL.md"
    local_skill.parent.mkdir(parents=True)
    local_skill.write_text("# local\n", encoding="utf-8")
    return sentinels


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_plan_install_doctor_inventory_and_rollback_preserve_user_data(
    engine_root, tmp_path, executable
):
    home = tmp_path / Path(executable).stem
    home.mkdir()
    sentinels = _seed_home(home)
    package = _package(tmp_path / f"{Path(executable).stem}.zip")

    before_plan = sorted(
        path.relative_to(home).as_posix()
        for path in home.rglob("*")
        if path.is_file()
    )
    plan = _run(executable, engine_root, "plan", home, package=package)
    assert plan.returncode == 0, plan.stderr
    assert _json(plan)["status"] == "READY"
    after_plan = sorted(
        path.relative_to(home).as_posix()
        for path in home.rglob("*")
        if path.is_file()
    )
    assert after_plan == before_plan

    install = _run(executable, engine_root, "install", home, package=package)
    assert install.returncode == 0, install.stderr
    assert (home / ".codex" / "AGENTS.md").read_text() == "# candidate\n"
    assert not (home / ".agents" / "skills" / "local-personal").exists()
    assert (home / ".agents" / "skills" / "alpha" / "SKILL.md").is_file()
    for path, payload in sentinels.items():
        assert path.read_bytes() == payload

    doctor = _run(
        executable, engine_root, "doctor", home, package=package
    )
    assert doctor.returncode == 0, doctor.stderr
    assert _json(doctor)["status"] == "HEALTHY"

    inventory = _run(
        executable, engine_root, "inventory", home, target="codex"
    )
    assert inventory.returncode == 0, inventory.stderr
    inventory_data = _json(inventory)
    assert inventory_data["release_version"] == "1.0.0"
    assert inventory_data["quarantined_unknown"] == [
        ".agents/skills/local-personal"
    ]

    rollback = _run(
        executable, engine_root, "rollback", home, target="codex"
    )
    assert rollback.returncode == 0, rollback.stderr
    assert (home / ".codex" / "AGENTS.md").read_text() == "# previous\n"
    assert (home / ".agents" / "skills" / "local-personal" / "SKILL.md").is_file()
    assert not (home / ".agents" / "skills" / "alpha").exists()
    for path, payload in sentinels.items():
        assert path.read_bytes() == payload


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_unsupported_client_and_downgrade_fail_closed(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"version-{Path(executable).stem}"
    home.mkdir()
    package_v2 = _package(tmp_path / f"v2-{Path(executable).stem}.zip", version="2.0.0")
    unsupported = _run(
        executable,
        engine_root,
        "plan",
        home,
        package=package_v2,
        client="0.145.0",
    )
    assert unsupported.returncode == 10
    assert _json(unsupported)["code"] == "UNSUPPORTED_CLIENT"

    installed = _run(executable, engine_root, "install", home, package=package_v2)
    assert installed.returncode == 0, installed.stderr
    package_v1 = _package(tmp_path / f"v1-{Path(executable).stem}.zip", version="1.0.0")
    downgrade = _run(
        executable, engine_root, "plan", home, package=package_v1
    )
    assert downgrade.returncode == 10
    assert _json(downgrade)["code"] == "DOWNGRADE_BLOCKED"


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        ("corrupt", "INVALID_PACKAGE"),
        ("wrong_hash", "INVALID_PACKAGE"),
        ("traversal", "UNSAFE_PATH"),
        ("protected", "UNSAFE_PATH"),
        ("reverse_policy", "INVALID_PACKAGE"),
    ],
)
def test_malformed_packages_fail_before_home_mutation(
    engine_root, tmp_path, executable, kind, expected_code
):
    home = tmp_path / f"{kind}-{Path(executable).stem}"
    home.mkdir()
    marker = home / "marker.txt"
    marker.write_text("unchanged", encoding="utf-8")
    package = tmp_path / f"{kind}-{Path(executable).stem}.zip"
    if kind == "corrupt":
        package.write_bytes(b"not a zip")
    else:
        _package(
            package,
            wrong_hash=kind == "wrong_hash",
            traversal=kind == "traversal",
            protected=kind == "protected",
            reverse_policy=kind == "reverse_policy",
        )

    result = _run(executable, engine_root, "install", home, package=package)
    assert result.returncode in {30, 40}
    assert _json(result)["code"] == expected_code
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert not (home / ".llm-foundation").exists()


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_injected_interruption_rolls_back_and_hard_crash_is_recoverable(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"interrupt-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    package = _package(tmp_path / f"interrupt-{Path(executable).stem}.zip")

    injected = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
        extra_env={"FOUNDATION_FAIL_AFTER": "1"},
    )
    assert injected.returncode == 30
    assert (home / ".codex" / "AGENTS.md").read_text() == "# previous\n"
    assert (home / ".agents" / "skills" / "local-personal" / "SKILL.md").is_file()

    crashed = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
        extra_env={"FOUNDATION_CRASH_AFTER": "1"},
    )
    assert crashed.returncode == 99
    doctor = _run(
        executable, engine_root, "doctor", home, target="codex"
    )
    assert doctor.returncode == 20
    assert _json(doctor)["code"] == "RECOVERY_REQUIRED"
    rollback = _run(
        executable, engine_root, "rollback", home, target="codex"
    )
    assert rollback.returncode == 0, rollback.stderr
    assert (home / ".codex" / "AGENTS.md").read_text() == "# previous\n"


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_reparse_point_in_managed_ancestor_is_rejected(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"junction-{Path(executable).stem}"
    outside = tmp_path / f"outside-{Path(executable).stem}"
    home.mkdir()
    outside.mkdir()
    link_parent = home / ".agents"
    create = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-Command",
            (
                "New-Item -ItemType Junction -Path "
                f"'{link_parent}' -Target '{outside}' | Out-Null"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if create.returncode != 0:
        pytest.skip(f"junction unavailable: {create.stderr}")
    package = _package(tmp_path / f"junction-{Path(executable).stem}.zip")

    result = _run(executable, engine_root, "plan", home, package=package)
    assert result.returncode == 40
    assert _json(result)["code"] == "UNSAFE_PATH"
    assert not list(outside.iterdir())


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_doctor_detects_active_drift(engine_root, tmp_path, executable):
    home = tmp_path / f"drift-{Path(executable).stem}"
    home.mkdir()
    package = _package(tmp_path / f"drift-{Path(executable).stem}.zip")
    installed = _run(
        executable, engine_root, "install", home, package=package
    )
    assert installed.returncode == 0, installed.stderr
    (home / ".codex" / "AGENTS.md").write_text(
        "# local drift\n", encoding="utf-8"
    )

    doctor = _run(
        executable, engine_root, "doctor", home, target="codex"
    )
    assert doctor.returncode == 30
    assert _json(doctor)["code"] == "ACTIVE_DRIFT"


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("command", ["doctor", "inventory", "rollback"])
def test_package_less_commands_reject_target_path_traversal_before_state_access(
    engine_root, tmp_path, executable, command
):
    home = tmp_path / f"target-{command}-{Path(executable).stem}"
    home.mkdir()
    outside = tmp_path / "escaped-state"

    result = _run(
        executable,
        engine_root,
        command,
        home,
        target="../../escaped-state",
    )

    assert result.returncode in {2, 40}
    assert _json(result)["code"] in {"INVALID_ARGUMENT", "UNSAFE_PATH"}
    assert not outside.exists()
    assert not (home / ".llm-foundation").exists()


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize(
    ("variant", "expected_message"),
    [
        ("engine", "engine"),
        ("missing_replace", "replace"),
        ("empty_exact", "exact"),
        ("nested_root", "overlap"),
    ],
)
def test_incompatible_engine_and_incomplete_managed_surface_fail_before_mutation(
    engine_root, tmp_path, executable, variant, expected_message
):
    home = tmp_path / f"coverage-{variant}-{Path(executable).stem}"
    home.mkdir()
    marker = home / "marker.txt"
    marker.write_text("unchanged", encoding="utf-8")
    package = _package(
        tmp_path / f"coverage-{variant}-{Path(executable).stem}.zip",
        foundation_engine_version="9.0.0" if variant == "engine" else "0.1.0",
        omit_replace_row=variant == "missing_replace",
        empty_exact_directory=variant == "empty_exact",
        nested_managed_root=variant == "nested_root",
    )

    result = _run(executable, engine_root, "install", home, package=package)

    assert result.returncode == 30
    assert _json(result)["code"] == "INVALID_PACKAGE"
    assert expected_message in str(_json(result)["message"]).lower()
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert not (home / ".llm-foundation").exists()


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_preserved_paths_cannot_be_bypassed_by_windows_case_folding(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"casefold-{Path(executable).stem}"
    home.mkdir()
    sentinels = _seed_home(home)
    session = home / ".codex" / "sessions" / "one.json"
    package = _package(
        tmp_path / f"casefold-{Path(executable).stem}.zip",
        casefold_preserved=True,
    )

    result = _run(executable, engine_root, "install", home, package=package)

    assert result.returncode == 40
    assert _json(result)["code"] == "UNSAFE_PATH"
    assert session.read_bytes() == sentinels[session]
    assert not (home / ".llm-foundation").exists()


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_rollback_accepts_same_windows_home_with_different_casing(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"case-home-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    package = _package(tmp_path / f"case-home-{Path(executable).stem}.zip")
    installed = _run(executable, engine_root, "install", home, package=package)
    assert installed.returncode == 0, installed.stderr

    same_home_different_case = home.parent / home.name.upper()
    rollback = _run(
        executable,
        engine_root,
        "rollback",
        same_home_different_case,
        target="codex",
    )

    assert rollback.returncode == 0, rollback.stderr
    assert (home / ".codex" / "AGENTS.md").read_text() == "# previous\n"


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("tamper", ["metadata", "missing_backup"])
def test_rollback_preflights_hash_bound_snapshot_before_destination_mutation(
    engine_root, tmp_path, executable, tamper
):
    home = tmp_path / f"snapshot-{tamper}-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    package = _package(tmp_path / f"snapshot-{tamper}-{Path(executable).stem}.zip")
    installed = _run(executable, engine_root, "install", home, package=package)
    assert installed.returncode == 0, installed.stderr

    active_path = home / ".llm-foundation" / "state" / "codex" / "active.json"
    active = json.loads(active_path.read_text(encoding="utf-8"))
    snapshot_path = Path(active["snapshot_path"])
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if tamper == "metadata":
        snapshot["target"] = "other"
        snapshot_path.write_text(
            json.dumps(snapshot, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        backup_row = snapshot["backup_files"][0]
        (snapshot_path.parent / backup_row["backup_path"]).unlink()

    candidate_before = (home / ".codex" / "AGENTS.md").read_bytes()
    rollback = _run(
        executable, engine_root, "rollback", home, target="codex"
    )

    assert rollback.returncode == 30
    assert _json(rollback)["code"] == "INVALID_PACKAGE"
    assert (home / ".codex" / "AGENTS.md").read_bytes() == candidate_before
    assert active_path.is_file()


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_interrupted_rollback_is_journaled_and_idempotently_recoverable(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"rollback-crash-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    package = _package(tmp_path / f"rollback-crash-{Path(executable).stem}.zip")
    installed = _run(executable, engine_root, "install", home, package=package)
    assert installed.returncode == 0, installed.stderr

    crashed = _run(
        executable,
        engine_root,
        "rollback",
        home,
        target="codex",
        extra_env={"FOUNDATION_ROLLBACK_CRASH_AFTER": "1"},
    )
    assert crashed.returncode == 98
    journal = (
        home / ".llm-foundation" / "state" / "codex" / "rollback.json"
    )
    assert journal.is_file()

    recovered = _run(
        executable, engine_root, "rollback", home, target="codex"
    )
    assert recovered.returncode == 0, recovered.stderr
    assert (home / ".codex" / "AGENTS.md").read_text() == "# previous\n"
    assert not journal.exists()


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize(
    "stage",
    ["after_active", "after_pending", "before_journal_delete"],
)
def test_late_rollback_crash_recovers_from_journal_not_mutated_state(
    engine_root, tmp_path, executable, stage
):
    home = tmp_path / f"rollback-{stage}-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    package = _package(
        tmp_path / f"rollback-{stage}-{Path(executable).stem}.zip"
    )
    installed = _run(executable, engine_root, "install", home, package=package)
    assert installed.returncode == 0, installed.stderr

    crashed = _run(
        executable,
        engine_root,
        "rollback",
        home,
        target="codex",
        extra_env={"FOUNDATION_ROLLBACK_CRASH_STAGE": stage},
    )
    assert crashed.returncode == 97
    journal = (
        home / ".llm-foundation" / "state" / "codex" / "rollback.json"
    )
    assert journal.is_file()
    doctor = _run(
        executable, engine_root, "doctor", home, target="codex"
    )
    assert doctor.returncode == 20
    assert _json(doctor)["code"] == "RECOVERY_REQUIRED"

    recovered = _run(
        executable, engine_root, "rollback", home, target="codex"
    )
    assert recovered.returncode == 0, recovered.stderr
    assert (home / ".codex" / "AGENTS.md").read_text() == "# previous\n"
    assert not journal.exists()


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_concurrent_destructive_operation_is_locked(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"lock-{Path(executable).stem}"
    home.mkdir()
    package = _package(tmp_path / f"lock-{Path(executable).stem}.zip")
    arguments = [
        executable,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(engine_root / "src" / "foundation.ps1"),
        "install",
        "-Home",
        str(home),
        "-Package",
        str(package),
        "-ClientId",
        "codex-cli",
        "-ClientVersion",
        SUPPORTED_CLIENT,
        "-Json",
    ]
    environment = os.environ.copy()
    environment["FOUNDATION_ACCEPTANCE_MODE"] = "1"
    environment["FOUNDATION_HOLD_LOCK_MS"] = "2500"
    first = subprocess.Popen(
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    lock_path = home / ".llm-foundation" / "locks" / "codex.lock"
    for _ in range(100):
        if lock_path.exists():
            break
        import time

        time.sleep(0.025)
    assert lock_path.exists()

    second = _run(
        executable, engine_root, "install", home, package=package
    )
    assert second.returncode == 20
    assert _json(second)["code"] == "LOCKED"
    stdout, stderr = first.communicate(timeout=15)
    assert first.returncode == 0, stderr or stdout
    after_owner_exit = _run(
        executable, engine_root, "install", home, package=package
    )
    assert after_owner_exit.returncode == 0, after_owner_exit.stderr


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_lock_file_reparse_point_cannot_redirect_lock_write(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"lock-link-{Path(executable).stem}"
    home.mkdir()
    locks = home / ".llm-foundation" / "locks"
    locks.mkdir(parents=True)
    outside_root = tmp_path / f"outside-lock-{Path(executable).stem}"
    outside_root.mkdir()
    outside = outside_root / "sentinel.txt"
    outside.write_text("untouched\n", encoding="utf-8")
    link = locks / "codex.lock"
    created = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-Command",
            (
                "New-Item -ItemType SymbolicLink -Path "
                f"'{link}' -Target '{outside}' | Out-Null"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if created.returncode != 0:
        created = subprocess.run(
            [
                executable,
                "-NoProfile",
                "-Command",
                (
                    "New-Item -ItemType Junction -Path "
                    f"'{link}' -Target '{outside_root}' | Out-Null"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    if created.returncode != 0:
        pytest.skip(f"lock reparse point unavailable: {created.stderr}")
    package = _package(tmp_path / f"lock-link-{Path(executable).stem}.zip")

    result = _run(executable, engine_root, "install", home, package=package)

    assert result.returncode == 40
    assert _json(result)["code"] == "UNSAFE_PATH"
    assert outside.read_text(encoding="utf-8") == "untouched\n"


def test_engine_bundle_is_deterministic_across_ps7_and_ps51(
    engine_root, tmp_path
):
    outputs = []
    for executable in POWERSHELLS:
        output = tmp_path / Path(executable).stem
        result = subprocess.run(
            [
                executable,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(engine_root / "tools" / "build-engine.ps1"),
                "-OutputRoot",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
        outputs.append(output)

    assert len(outputs) == 2
    first = {
        path.relative_to(outputs[0]).as_posix(): path.read_bytes()
        for path in outputs[0].rglob("*")
        if path.is_file()
    }
    second = {
        path.relative_to(outputs[1]).as_posix(): path.read_bytes()
        for path in outputs[1].rglob("*")
        if path.is_file()
    }
    assert first == second
    assert set(first) == {"VERSION", "engine-manifest.json", "foundation.ps1"}
    manifest = json.loads(first["engine-manifest.json"])
    assert manifest["engine_version"] == "0.1.0"
    assert manifest["commands"] == [
        "doctor",
        "install",
        "inventory",
        "plan",
        "rollback",
    ]
    assert manifest["foundation_ps1_sha256"] == _sha256(
        first["foundation.ps1"]
    )


def test_foundation_engine_has_no_network_or_secret_material(engine_root):
    payload = (engine_root / "src" / "foundation.ps1").read_text(
        encoding="utf-8"
    )
    lowered = payload.lower()
    for forbidden in (
        "invoke-restmethod",
        "invoke-webrequest",
        "start-bitstransfer",
        "http://",
        "https://",
        "telemetry",
        "feedback-pending",
        "session-report",
        "-----begin private key-----",
        "codex",
        ".claude",
        "opencode",
    ):
        assert forbidden not in lowered
