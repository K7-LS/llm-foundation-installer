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
    wrong_hash: bool = False,
    traversal: bool = False,
    protected: bool = False,
    reverse_policy: bool = False,
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
    rows = []
    for name, payload in sorted(entries.items()):
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
        "foundation_engine_version": "0.1.0",
        "managed_surface": {
            "exact_directories": [
                ".agents/skills",
                ".codex/agents",
                ".codex/base/cold",
                ".codex/base/foundation",
                ".codex/base/runtime",
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
