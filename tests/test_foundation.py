from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


SUPPORTED_CLIENT = "0.146.0-alpha.3.1"
ENGINE_VERSION = (
    Path(__file__).resolve().parents[1] / "VERSION"
).read_text(encoding="utf-8").strip()
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
POWERSHELLS = [
    value
    for value in (shutil.which("pwsh"), shutil.which("powershell.exe"))
    if value
]


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_inventory_reports_unmanaged_profile_before_first_install(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"fresh-inventory-{Path(executable).stem}"
    skill = home / ".agents" / "skills" / "imported-from-claude"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("legacy\n", encoding="utf-8")
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[mcp_servers.imported]\ncommand = "legacy-mcp.exe"\n',
        encoding="utf-8",
    )

    result = _run(executable, engine_root, "inventory", home, target="codex")

    assert result.returncode == 0, result.stderr
    payload = _json(result)
    assert payload["status"] == "UNMANAGED_PROFILE"
    assert [row["path"] for row in payload["unknown_entries"]] == [
        ".agents/skills/imported-from-claude",
        "toml:.codex/config.toml#mcp_servers.imported",
    ]
    assert payload["unknown_entries"][1]["launch_command"] == "legacy-mcp.exe"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _package(
    path: Path,
    *,
    version: str = "1.0.0",
    foundation_engine_version: str = ENGINE_VERSION,
    wrong_hash: bool = False,
    traversal: bool = False,
    protected: bool = False,
    reverse_policy: bool = False,
    omit_replace_row: bool = False,
    empty_exact_directory: bool = False,
    nested_managed_root: bool = False,
    casefold_preserved: bool = False,
    compatibility_surface: bool = False,
    environment_set: list[dict[str, str]] | None = None,
    desired_state: bool = False,
    config_payload: bytes | None = None,
) -> Path:
    entries = {
        ".codex/AGENTS.md": b"# candidate\n",
        ".codex/config.toml": config_payload or (
            b"project_doc_max_bytes = 8192\n"
            b"check_for_update_on_startup = false\n"
            b"[features]\nhooks = true\n"
            b"[agents]\nenabled = true\n"
        ),
        ".codex/hooks.json": b'{"hooks":{}}\n',
        ".codex/agents/auditor.toml": b'name = "auditor"\n',
        ".agents/skills/alpha/SKILL.md": b"---\nname: alpha\ndescription: test\n---\n",
        ".agents/skills/sync-base/SKILL.md": b"---\nname: sync-base\ndescription: sync\n---\n",
        ".codex/base/VERSION": (version + "\n").encode(),
        ".codex/base/components.lock.json": b'{"components":{}}\n',
        ".codex/base/cold/reference.md": b"# cold\n",
        ".codex/base/runtime/hooks/check.ps1": b"exit 0\n",
        f".codex/base/foundation/{ENGINE_VERSION}/VERSION": (
            ENGINE_VERSION + "\n"
        ).encode(),
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
    if compatibility_surface:
        replace_files.remove(".codex/config.toml")
        exact_directories.remove(".agents/skills")
        exact_directories.remove(".codex/agents")
        replace_files.extend(
            [
                ".agents/skills/alpha/SKILL.md",
                ".agents/skills/sync-base/SKILL.md",
                ".codex/agents/auditor.toml",
            ]
        )
        replace_files.sort()
    if desired_state and ".codex/config.toml" in replace_files:
        replace_files.remove(".codex/config.toml")
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
            **(
                {
                    "merge_toml_files": [".codex/config.toml"],
                }
                if compatibility_surface or desired_state
                else {}
            ),
        },
        "sync_policy": {
            "direction": "hub-to-consumer",
            "consumer_feedback_upload": False,
            "consumer_push": reverse_policy,
            "consumer_session_upload": False,
            "credentials_included": False,
        },
        "environment": {
            "scope": "current-user",
            "set": environment_set or [],
        },
        "files": rows,
    }
    if desired_state:
        manifest["desired_state"] = {
            "schema_version": 1,
            "unknown_policy": "prompt-every-run",
            "local_exceptions": True,
            "strict_doctor": True,
            "inventory_roots": [
                ".agents/skills",
                ".codex/agents",
            ],
            "platform_owned": [],
            "toml_reconcile": [
                {
                    "path": ".codex/config.toml",
                    "exact_tables": [
                        "mcp_servers",
                        "plugin_marketplaces",
                        "plugins",
                    ],
                    "protected_tables": [
                        "mcp_servers.node_repl",
                    ],
                    "allowed_entries": [
                        "mcp_servers.k7-autocad-bridge",
                        "mcp_servers.k7-revit-bridge",
                        "plugins.documents",
                        "plugins.documents@openai-primary-runtime",
                        "plugins.spreadsheets",
                    ],
                }
            ],
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
    client: str | None = SUPPORTED_CLIENT,
    client_id: str = "codex-cli",
    release_manifest: Path | None = None,
    release_manifest_sha256: str | None = None,
    extra_env: dict[str, str] | None = None,
    local_exceptions: list[str] | None = None,
    confirm_remove_unknown: bool = False,
    strict: bool = False,
    plan_file: Path | None = None,
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
    if plan_file is not None:
        arguments.extend(["-Plan", str(plan_file)])
    if target is not None:
        arguments.extend(["-Target", target])
    if release_manifest is not None:
        arguments.extend(["-ReleaseManifest", str(release_manifest)])
    if release_manifest_sha256 is not None:
        arguments.extend(
            ["-ReleaseManifestSha256", release_manifest_sha256]
        )
    if client is not None:
        arguments.extend(["-ClientId", client_id])
        arguments.extend(["-ClientVersion", client])
    if local_exceptions:
        arguments.extend(["-LocalExceptionPath", "|".join(local_exceptions)])
    if confirm_remove_unknown:
        arguments.append("-ConfirmRemoveUnknown")
    if strict:
        arguments.append("-Strict")
    environment = os.environ.copy()
    environment["FOUNDATION_ACCEPTANCE_MODE"] = "1"
    acceptance_temp = home.parent / "_foundation-temp"
    acceptance_temp.mkdir(parents=True, exist_ok=True)
    environment["TEMP"] = str(acceptance_temp)
    environment["TMP"] = str(acceptance_temp)
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


def _write_acceptance_environment(
    home: Path,
    values: dict[str, str],
) -> Path:
    path = (
        home
        / ".llm-foundation"
        / "acceptance-user-environment.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "values": [
                    {"name": name, "value": value}
                    for name, value in sorted(values.items())
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _read_acceptance_environment(home: Path) -> dict[str, str]:
    path = (
        home
        / ".llm-foundation"
        / "acceptance-user-environment.json"
    )
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        row["name"]: row["value"]
        for row in payload["values"]
    }


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
    assert not (home / ".agents" / "skills" / "alpha" / "SKILL.md").exists()
    for path, payload in sentinels.items():
        assert path.read_bytes() == payload


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_desired_state_prompts_every_run_and_keeps_approved_local_exception(
    engine_root, tmp_path, executable
):
    """Removing the decision gate or exception restore must break this contract."""
    home = tmp_path / f"desired-state-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    package = _package(
        tmp_path / f"desired-state-{Path(executable).stem}.zip",
        desired_state=True,
    )
    local_path = ".agents/skills/local-personal"

    blocked = _run(executable, engine_root, "plan", home, package=package)
    assert blocked.returncode == 20, blocked.stderr
    blocked_payload = _json(blocked)
    assert blocked_payload["status"] == "BLOCKED_USER_DECISION"
    assert blocked_payload["unknown_entries"] == [
        {
            "path": local_path,
            "kind": "skill",
            "active": True,
            "registration_path": local_path,
            "launch_command": None,
            "duplicates": [],
            "source": "local-unmanaged",
            "risk": "UNREVIEWED_EXECUTABLE_INSTRUCTIONS",
        }
    ]

    install = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
        local_exceptions=[local_path],
        confirm_remove_unknown=True,
    )
    assert install.returncode == 0, install.stderr
    assert _json(install)["status"] == "CANONICAL_WITH_LOCAL_EXCEPTIONS"
    assert (home / local_path.replace("/", os.sep) / "SKILL.md").is_file()

    doctor = _run(
        executable,
        engine_root,
        "doctor",
        home,
        target="codex",
        strict=True,
    )
    assert doctor.returncode == 0, doctor.stderr
    assert _json(doctor)["status"] == "CANONICAL_WITH_LOCAL_EXCEPTIONS"

    inventory = _run(
        executable, engine_root, "inventory", home, target="codex"
    )
    assert inventory.returncode == 0, inventory.stderr
    assert _json(inventory)["local_exceptions"] == [local_path]

    blocked_again = _run(
        executable, engine_root, "plan", home, package=package
    )
    assert blocked_again.returncode == 20, blocked_again.stderr
    assert _json(blocked_again)["status"] == "BLOCKED_USER_DECISION"


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_desired_state_reconciles_owned_toml_tables_and_preserves_projects(
    engine_root, tmp_path, executable
):
    """Preserving a stale MCP table or deleting project state must fail."""
    home = tmp_path / f"desired-toml-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    config = home / ".codex" / "config.toml"
    config.write_text(
        "[features]\nmemories = true\n\n"
        "[mcp_servers.node_repl]\ncommand = \"platform-node-repl.exe\"\n\n"
        "[mcp_servers.exa]\nurl = \"https://example.invalid/mcp\"\n\n"
        "[plugin_marketplaces.claude-plugins-official]\n"
        "url = \"https://example.invalid/claude\"\n\n"
        "[plugins]\nclaude-md-management = true\n\n"
        "[projects.'C:\\\\work']\ntrust_level = \"trusted\"\n",
        encoding="utf-8",
    )
    required = (
        b"project_doc_max_bytes = 8192\n"
        b"check_for_update_on_startup = false\n\n"
        b"[features]\nhooks = true\n\n"
        b"[agents]\nenabled = true\n\n"
        b"[mcp_servers.k7-revit-bridge]\ncommand = \"k7-revit-bridge.exe\"\n\n"
        b"[mcp_servers.k7-autocad-bridge]\ncommand = \"k7-autocad-bridge.exe\"\n\n"
        b"[plugins]\ndocuments = true\nspreadsheets = true\n"
    )
    package = _package(
        tmp_path / f"desired-toml-{Path(executable).stem}.zip",
        desired_state=True,
        config_payload=required,
    )

    install = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
        confirm_remove_unknown=True,
    )
    assert install.returncode == 0, install.stderr
    text = config.read_text(encoding="utf-8")
    assert "[mcp_servers.exa]" not in text
    assert "[mcp_servers.node_repl]" in text
    assert 'command = "platform-node-repl.exe"' in text
    assert "claude-plugins-official" not in text
    assert "claude-md-management" not in text
    assert "[mcp_servers.k7-revit-bridge]" in text
    assert "[mcp_servers.k7-autocad-bridge]" in text
    assert "documents = true" in text
    assert "spreadsheets = true" in text
    assert "[projects.'C:\\\\work']" in text
    assert 'trust_level = "trusted"' in text
    assert "memories = true" in text


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_desired_state_inventory_roots_remove_granular_unmanaged_entries_and_rollback(
    engine_root, tmp_path, executable
):
    """A granular payload must still find, remove and restore sibling drift."""
    home = tmp_path / f"inventory-roots-{Path(executable).stem}"
    home.mkdir()
    sentinels = _seed_home(home)
    legacy_agent = home / ".codex" / "agents" / "claude-derived.toml"
    legacy_agent.parent.mkdir(parents=True)
    legacy_agent.write_text('name = "claude-derived"\n', encoding="utf-8")
    package = _package(
        tmp_path / f"inventory-roots-{Path(executable).stem}.zip",
        compatibility_surface=True,
        desired_state=True,
    )

    blocked = _run(executable, engine_root, "plan", home, package=package)
    assert blocked.returncode == 20, blocked.stderr
    unknown = {
        row["path"]: row for row in _json(blocked)["unknown_entries"]
    }
    assert set(unknown) == {
        ".agents/skills/local-personal",
        ".codex/agents/claude-derived.toml",
    }
    assert unknown[".agents/skills/local-personal"]["kind"] == "skill"
    assert unknown[".codex/agents/claude-derived.toml"]["kind"] == "agent"

    installed = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
        confirm_remove_unknown=True,
    )
    assert installed.returncode == 0, installed.stderr
    assert _json(installed)["status"] == "CANONICAL"
    assert not (home / ".agents" / "skills" / "local-personal").exists()
    assert not legacy_agent.exists()
    assert (home / ".agents" / "skills" / "alpha" / "SKILL.md").is_file()

    doctor = _run(
        executable,
        engine_root,
        "doctor",
        home,
        target="codex",
        strict=True,
    )
    assert doctor.returncode == 0, doctor.stderr
    assert _json(doctor)["status"] == "CANONICAL"

    rollback = _run(
        executable, engine_root, "rollback", home, target="codex"
    )
    assert rollback.returncode == 0, rollback.stderr
    assert (home / ".agents" / "skills" / "local-personal" / "SKILL.md").is_file()
    assert legacy_agent.is_file()
    for path, payload in sentinels.items():
        assert path.read_bytes() == payload


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_control_fixture_classifies_20_mcp_47_skills_and_18_agents(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"control-profile-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    skills = home / ".agents" / "skills"
    for index in range(44):
        path = skills / f"legacy-skill-{index:02d}" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("# legacy\n", encoding="utf-8")
    agents = home / ".codex" / "agents"
    agents.mkdir(parents=True)
    for index in range(17):
        (agents / f"legacy-agent-{index:02d}.toml").write_text(
            'name = "legacy"\n', encoding="utf-8"
        )
    config_rows = [
        "[mcp_servers.node_repl]\ncommand = \"platform.exe\"\n",
        "[mcp_servers.k7-revit-bridge]\ncommand = \"k7-revit-bridge.exe\"\n",
        "[mcp_servers.k7-autocad-bridge]\ncommand = \"k7-autocad-bridge.exe\"\n",
    ]
    config_rows.extend(
        f"[mcp_servers.legacy-{index:02d}]\ncommand = \"legacy.exe\"\n"
        for index in range(17)
    )
    config = home / ".codex" / "config.toml"
    config.write_text("\n".join(config_rows), encoding="utf-8")
    package = _package(
        tmp_path / f"control-profile-{Path(executable).stem}.zip",
        desired_state=True,
        config_payload=(
            b"[mcp_servers.k7-revit-bridge]\ncommand = \"k7-revit-bridge.exe\"\n\n"
            b"[mcp_servers.k7-autocad-bridge]\ncommand = \"k7-autocad-bridge.exe\"\n\n"
            b"[plugins]\ndocuments = true\nspreadsheets = true\n"
        ),
    )
    result = _run(executable, engine_root, "plan", home, package=package)
    assert result.returncode == 20, result.stderr
    unknown = _json(result)["unknown_entries"]
    counts = {
        kind: sum(row["kind"] == kind for row in unknown)
        for kind in ("mcp", "skill", "agent")
    }
    assert counts == {"mcp": 17, "skill": 45, "agent": 17}
    assert 17 + 3 == 20
    assert 45 + 2 == 47
    assert 17 + 1 == 18
    assert all(row["source"] == "codex-config-toml" for row in unknown if row["kind"] == "mcp")


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_toml_local_exception_is_explicit_and_reconfirmed_every_run(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"toml-exception-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    config = home / ".codex" / "config.toml"
    config.write_text(
        "[mcp_servers.local]\ncommand = \"local.exe\"\n",
        encoding="utf-8",
    )
    package = _package(
        tmp_path / f"toml-exception-{Path(executable).stem}.zip",
        desired_state=True,
    )
    identity = "toml:.codex/config.toml#mcp_servers.local"
    blocked = _run(executable, engine_root, "plan", home, package=package)
    assert blocked.returncode == 20
    assert identity in [row["path"] for row in _json(blocked)["unknown_entries"]]
    installed = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
        local_exceptions=[
            ".agents/skills/local-personal",
            identity,
        ],
    )
    assert installed.returncode == 0, installed.stderr
    assert "[mcp_servers.local]" in config.read_text(encoding="utf-8")
    doctor = _run(
        executable, engine_root, "doctor", home, target="codex", strict=True
    )
    assert doctor.returncode == 0, doctor.stderr
    assert _json(doctor)["status"] == "CANONICAL_WITH_LOCAL_EXCEPTIONS"
    blocked_again = _run(executable, engine_root, "plan", home, package=package)
    assert blocked_again.returncode == 20


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_quoted_plugin_identifier_is_canonical_and_manifest_owned(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"quoted-plugin-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    required = (
        b'[plugins."documents@openai-primary-runtime"]\n'
        b"enabled = true\n"
    )
    package = _package(
        tmp_path / f"quoted-plugin-{Path(executable).stem}.zip",
        desired_state=True,
        config_payload=required,
    )

    installed = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
        confirm_remove_unknown=True,
    )
    assert installed.returncode == 0, installed.stderr
    doctor = _run(
        executable, engine_root, "doctor", home, package=package, strict=True
    )
    assert doctor.returncode == 0, doctor.stderr
    assert _json(doctor)["status"] == "CANONICAL"


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_apply_uses_hash_bound_saved_plan_and_rechecks_live_state(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"saved-plan-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    package = _package(
        tmp_path / f"saved-plan-{Path(executable).stem}.zip",
        desired_state=True,
    )
    planned = _run(
        executable,
        engine_root,
        "plan",
        home,
        package=package,
        confirm_remove_unknown=True,
    )
    assert planned.returncode == 0, planned.stderr
    plan_file = tmp_path / f"plan-{Path(executable).stem}.json"
    plan_file.write_text(
        json.dumps(_json(planned), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    applied = _run(
        executable,
        engine_root,
        "apply",
        home,
        plan_file=plan_file,
    )
    assert applied.returncode == 0, applied.stderr
    assert _json(applied)["status"] == "CANONICAL"

@pytest.mark.parametrize("executable", POWERSHELLS)
def test_install_adopts_existing_codex_home_without_replacing_local_state(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"adopt-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    config = home / ".codex" / "config.toml"
    config.write_text(
        "[features]\n"
        "memories = true\n"
        "\n"
        "[mcp_servers.exa]\n"
        'url = "https://example.invalid/mcp"\n'
        "\n"
        "[projects.'C:\\\\work']\n"
        'trust_level = "trusted"\n',
        encoding="utf-8",
    )
    legacy_agent = home / ".codex" / "agents" / "legacy.toml"
    legacy_agent.parent.mkdir(parents=True)
    legacy_agent.write_text('name = "legacy"\n', encoding="utf-8")
    package = _package(
        tmp_path / f"adopt-{Path(executable).stem}.zip",
        compatibility_surface=True,
    )

    install = _run(executable, engine_root, "install", home, package=package)
    assert install.returncode == 0, install.stderr

    installed_config = config.read_text(encoding="utf-8")
    for retained in (
        "memories = true",
        "[mcp_servers.exa]",
        'url = "https://example.invalid/mcp"',
        "[projects.'C:\\\\work']",
        'trust_level = "trusted"',
    ):
        assert retained in installed_config
    for required in (
        "project_doc_max_bytes = 8192",
        "check_for_update_on_startup = false",
        "hooks = true",
        "enabled = true",
    ):
        assert required in installed_config
    assert (home / ".agents" / "skills" / "local-personal" / "SKILL.md").is_file()
    assert legacy_agent.read_text(encoding="utf-8") == 'name = "legacy"\n'
    assert (home / ".agents" / "skills" / "alpha" / "SKILL.md").is_file()
    assert (home / ".codex" / "agents" / "auditor.toml").is_file()

    doctor = _run(executable, engine_root, "doctor", home, package=package)
    assert doctor.returncode == 0, doctor.stderr
    assert _json(doctor)["status"] == "HEALTHY"

    inventory = _run(executable, engine_root, "inventory", home, target="codex")
    assert inventory.returncode == 0, inventory.stderr
    assert _json(inventory)["quarantined_unknown"] == []

    rollback = _run(executable, engine_root, "rollback", home, target="codex")
    assert rollback.returncode == 0, rollback.stderr
    assert config.read_text(encoding="utf-8").startswith("[features]\nmemories = true")
    assert (home / ".agents" / "skills" / "local-personal" / "SKILL.md").is_file()
    assert legacy_agent.is_file()
    assert not (home / ".agents" / "skills" / "alpha" / "SKILL.md").exists()
    assert not (home / ".codex" / "agents" / "auditor.toml").exists()

@pytest.mark.parametrize("executable", POWERSHELLS)
def test_rollback_accepts_existing_directory_and_case_variant_replace_file(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"ordinal-snapshot-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    legacy_agent = home / ".codex" / "agents" / "legacy.toml"
    legacy_agent.parent.mkdir(parents=True)
    legacy_agent.write_text('name = "legacy"\n', encoding="utf-8")
    package = _package(
        tmp_path / f"ordinal-snapshot-{Path(executable).stem}.zip"
    )

    install = _run(executable, engine_root, "install", home, package=package)
    assert install.returncode == 0, install.stderr

    rollback = _run(
        executable, engine_root, "rollback", home, target="codex"
    )
    assert rollback.returncode == 0, rollback.stderr
    assert legacy_agent.read_text(encoding="utf-8") == 'name = "legacy"\n'
    assert (home / ".codex" / "AGENTS.md").read_text(
        encoding="utf-8"
    ) == "# previous\n"


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_rollback_recovers_snapshot_written_by_legacy_culture_sort(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"legacy-sort-{Path(executable).stem}"
    home.mkdir()
    _seed_home(home)
    legacy_agent = home / ".codex" / "agents" / "legacy.toml"
    legacy_agent.parent.mkdir(parents=True)
    legacy_agent.write_text('name = "legacy"\n', encoding="utf-8")
    package = _package(tmp_path / f"legacy-sort-{Path(executable).stem}.zip")

    install = _run(executable, engine_root, "install", home, package=package)
    assert install.returncode == 0, install.stderr
    active_path = (
        home / ".llm-foundation" / "state" / "codex" / "active.json"
    )
    active = json.loads(active_path.read_text(encoding="utf-8"))
    snapshot_path = Path(active["snapshot_path"])
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    uppercase_index = snapshot["existed"].index(".codex/AGENTS.md")
    agent_index = snapshot["existed"].index(".codex/agents")
    snapshot["existed"][uppercase_index], snapshot["existed"][agent_index] = (
        snapshot["existed"][agent_index],
        snapshot["existed"][uppercase_index],
    )
    rows = {row["path"]: row for row in snapshot["backup_files"]}
    legacy_order = sorted(rows)
    uppercase_index = legacy_order.index(".codex/AGENTS.md")
    agent_index = legacy_order.index(".codex/agents/legacy.toml")
    legacy_order.insert(
        uppercase_index,
        legacy_order.pop(agent_index),
    )
    snapshot["backup_files"] = [rows[path] for path in legacy_order]
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    active["snapshot_sha256"] = _sha256(snapshot_path.read_bytes())
    active_path.write_text(
        json.dumps(active, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    rollback = _run(
        executable, engine_root, "rollback", home, target="codex"
    )
    assert rollback.returncode == 0, rollback.stderr
    assert legacy_agent.read_text(encoding="utf-8") == 'name = "legacy"\n'
    assert (home / ".codex" / "AGENTS.md").read_text(
        encoding="utf-8"
    ) == "# previous\n"


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_current_user_environment_is_planned_checked_and_rolled_back(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"environment-{Path(executable).stem}"
    home.mkdir()
    _write_acceptance_environment(
        home,
        {"OPENCODE_DISABLE_CLAUDE_CODE": "previous"},
    )
    package = _package(
        tmp_path / f"environment-{Path(executable).stem}.zip",
        environment_set=[
            {
                "name": "OPENCODE_DISABLE_CLAUDE_CODE",
                "value": "1",
            }
        ],
    )

    plan = _run(
        executable,
        engine_root,
        "plan",
        home,
        package=package,
    )
    assert plan.returncode == 0, plan.stderr
    assert _json(plan)["environment_actions"] == [
        {
            "name": "OPENCODE_DISABLE_CLAUDE_CODE",
            "action": "UPDATE",
            "value": "1",
        }
    ]

    install = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
    )
    assert install.returncode == 0, install.stderr
    assert _read_acceptance_environment(home) == {
        "OPENCODE_DISABLE_CLAUDE_CODE": "1"
    }

    doctor = _run(
        executable,
        engine_root,
        "doctor",
        home,
        target="codex",
    )
    assert doctor.returncode == 0, doctor.stderr

    _write_acceptance_environment(
        home,
        {"OPENCODE_DISABLE_CLAUDE_CODE": "drift"},
    )
    drift = _run(
        executable,
        engine_root,
        "doctor",
        home,
        target="codex",
    )
    assert drift.returncode == 30
    assert _json(drift)["code"] == "ACTIVE_DRIFT"

    _write_acceptance_environment(
        home,
        {"OPENCODE_DISABLE_CLAUDE_CODE": "1"},
    )
    rollback = _run(
        executable,
        engine_root,
        "rollback",
        home,
        target="codex",
    )
    assert rollback.returncode == 0, rollback.stderr
    assert _read_acceptance_environment(home) == {
        "OPENCODE_DISABLE_CLAUDE_CODE": "previous"
    }


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_environment_allowlist_fails_closed_before_mutation(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"environment-reject-{Path(executable).stem}"
    home.mkdir()
    package = _package(
        tmp_path / f"environment-reject-{Path(executable).stem}.zip",
        environment_set=[{"name": "PATH", "value": "untrusted"}],
    )

    result = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
    )
    assert result.returncode == 30
    assert _json(result)["code"] == "INVALID_PACKAGE"
    assert not (home / ".codex" / "AGENTS.md").exists()
    assert _read_acceptance_environment(home) == {}


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_environment_change_is_restored_after_install_failure(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"environment-failure-{Path(executable).stem}"
    home.mkdir()
    _write_acceptance_environment(
        home,
        {"OPENCODE_DISABLE_CLAUDE_CODE": "previous"},
    )
    package = _package(
        tmp_path / f"environment-failure-{Path(executable).stem}.zip",
        environment_set=[
            {
                "name": "OPENCODE_DISABLE_CLAUDE_CODE",
                "value": "1",
            }
        ],
    )

    result = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
        extra_env={"FOUNDATION_FAIL_AFTER": "17"},
    )
    assert result.returncode == 30
    assert _json(result)["code"] == "INSTALL_FAILED"
    assert _read_acceptance_environment(home) == {
        "OPENCODE_DISABLE_CLAUDE_CODE": "previous"
    }
    assert not (home / ".codex" / "AGENTS.md").exists()


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_external_client_versions_and_downgrade_fail_closed(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"version-{Path(executable).stem}"
    home.mkdir()
    package_v2 = _package(tmp_path / f"v2-{Path(executable).stem}.zip", version="2.0.0")
    external = _run(
        executable,
        engine_root,
        "plan",
        home,
        package=package_v2,
        client="2.0.0",
    )
    assert external.returncode == 0, external.stderr
    assert _json(external)["status"] == "READY"

    for client_id, client in (
        ("other-client", SUPPORTED_CLIENT),
        ("codex-cli", "2.0"),
        ("codex-cli", ""),
    ):
        unsupported = _run(
            executable,
            engine_root,
            "plan",
            home,
            package=package_v2,
            client_id=client_id,
            client=client,
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
def test_exact_legacy_active_state_is_read_only_planned_and_upgraded(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"legacy-{Path(executable).stem}"
    home.mkdir()
    package_v1 = _package(
        tmp_path / f"legacy-v1-{Path(executable).stem}.zip",
        version="1.0.0",
    )
    installed = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package_v1,
    )
    assert installed.returncode == 0, installed.stderr
    active_path = home / ".llm-foundation" / "state" / "codex" / "active.json"
    active = json.loads(active_path.read_text(encoding="utf-8"))
    active.pop("local_exceptions")
    active.pop("desired_state")
    active_path.write_text(
        json.dumps(active, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    legacy_bytes = active_path.read_bytes()
    package_v2 = _package(
        tmp_path / f"legacy-v2-{Path(executable).stem}.zip",
        version="2.0.0",
    )

    planned = _run(
        executable,
        engine_root,
        "plan",
        home,
        package=package_v2,
    )

    assert planned.returncode == 0, planned.stderr
    assert _json(planned)["status"] == "READY"
    assert active_path.read_bytes() == legacy_bytes

    upgraded = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package_v2,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    upgraded_active = json.loads(active_path.read_text(encoding="utf-8"))
    assert upgraded_active["local_exceptions"] == []
    assert upgraded_active["desired_state"] is False
    doctor = _run(
        executable,
        engine_root,
        "doctor",
        home,
        target="codex",
    )
    assert doctor.returncode == 0, doctor.stderr


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_partial_legacy_active_state_still_fails_closed(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"partial-{Path(executable).stem}"
    home.mkdir()
    package_v1 = _package(
        tmp_path / f"partial-v1-{Path(executable).stem}.zip",
        version="1.0.0",
    )
    installed = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package_v1,
    )
    assert installed.returncode == 0, installed.stderr
    active_path = home / ".llm-foundation" / "state" / "codex" / "active.json"
    active = json.loads(active_path.read_text(encoding="utf-8"))
    active.pop("desired_state")
    active_path.write_text(
        json.dumps(active, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    package_v2 = _package(
        tmp_path / f"partial-v2-{Path(executable).stem}.zip",
        version="2.0.0",
    )

    result = _run(
        executable,
        engine_root,
        "plan",
        home,
        package=package_v2,
    )

    assert result.returncode == 30
    assert _json(result)["code"] == "INVALID_PACKAGE"
    assert "active state properties differ" in _json(result)["message"]


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
def test_acceptance_crash_staging_is_scoped_to_pytest_home(
    engine_root, tmp_path, executable
):
    home = tmp_path / f"scoped-crash-{Path(executable).stem}"
    home.mkdir()
    package = _package(
        tmp_path / f"scoped-crash-{Path(executable).stem}.zip"
    )

    crashed = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
        extra_env={"FOUNDATION_CRASH_AFTER": "1"},
    )

    assert crashed.returncode == 99
    scoped_temp = tmp_path / "_foundation-temp"
    assert list(scoped_temp.glob("foundation-*"))


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
        foundation_engine_version=(
            "9.0.0" if variant == "engine" else ENGINE_VERSION
        ),
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
    assert set(first) == {
        "VERSION",
        "engine-manifest.json",
        "foundation.ps1",
        "shared-tools.lock.json",
        "shared-tools/officecli/officecli.exe",
        "shared-tools/officecli/officecli-shim.exe",
        "shared-tools/officecli/officecli-command-policy.json",
        "shared-tools/officecli/k7-officecli-pdf.exe",
        "shared-tools/officecli/officecli_csv_batch.py",
    }
    manifest = json.loads(first["engine-manifest.json"])
    assert manifest["engine_version"] == ENGINE_VERSION
    assert manifest["commands"] == [
        "apply",
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
    ):
        assert forbidden not in lowered


def test_engine_build_uses_a_local_officecli_cache_without_network(tmp_path):
    # Каждая сборка тянула officecli из сети: обрыв канала валил и локальные
    # прогоны, и CI. Кеш по SHA делает вторую сборку на машине автономной;
    # проверка хеша остаётся обязательной на любом пути.
    executable = POWERSHELLS[0]
    cache_root = tmp_path / "cache"
    pinned = REPOSITORY_ROOT.parent / ".officecli-cache" / "officecli.exe"
    if not pinned.is_file():
        pytest.skip("нет локальной копии officecli для наполнения кеша")
    environment = {
        **os.environ,
        "K7_BUILD_CACHE": str(cache_root),
        "K7_OFFICECLI_BINARY_PATH": str(pinned.resolve()),
    }
    seeded = subprocess.run(
        [
            executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(REPOSITORY_ROOT / "tools" / "build-engine.ps1"),
            "-OutputRoot", str(tmp_path / "seed"),
        ],
        check=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=environment,
    )
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    cached = list((cache_root / "officecli").glob("*.exe"))
    assert len(cached) == 1, "кеш должен содержать ровно один файл, названный по SHA"
    assert re.fullmatch(r"[0-9a-f]{64}\.exe", cached[0].name)

    offline = {**os.environ, "K7_BUILD_CACHE": str(cache_root)}
    offline.pop("K7_OFFICECLI_BINARY_PATH", None)
    restored = subprocess.run(
        [
            executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(REPOSITORY_ROOT / "tools" / "build-engine.ps1"),
            "-OutputRoot", str(tmp_path / "restored"),
        ],
        check=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=offline,
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert "restored from the local build cache" in restored.stdout
    assert (
        tmp_path / "restored" / "shared-tools" / "officecli" / "officecli.exe"
    ).is_file()
