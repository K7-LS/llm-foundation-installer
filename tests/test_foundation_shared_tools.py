from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

from test_foundation import (
    POWERSHELLS,
    SUPPORTED_CLIENT,
    _json,
    _package,
    _run,
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_release_manifest(
    package: Path,
    *,
    destination: Path | None = None,
    channel: str = "stable",
    mutate=None,
) -> Path:
    with zipfile.ZipFile(package) as archive:
        package_manifest_bytes = archive.read("package-manifest.json")
        package_manifest = json.loads(package_manifest_bytes)
    release = {
        "schema_version": 1,
        "target": package_manifest["target"],
        "version": package_manifest["version"],
        "tag": (
            f"{package_manifest['target']}-v{package_manifest['version']}"
        ),
        "channel": channel,
        "client": package_manifest["client"],
        "foundation_engine_version": package_manifest[
            "foundation_engine_version"
        ],
        "foundation_engine_manifest_sha256": "f" * 64,
        "source": {
            "repository": "https://github.com/example/codex-base",
            "commit": "a" * 40,
            "tree": "b" * 40,
            "transformation": "codex-native-v1",
        },
        "asset": {
            "name": package.name,
            "sha256": _sha256(package.read_bytes()),
            "bytes": package.stat().st_size,
        },
        "package_manifest_sha256": _sha256(package_manifest_bytes),
        "components_lock_sha256": "c" * 64,
        "requires": {
            "immutable_release": True,
            "release_attestation": True,
        },
    }
    if mutate is not None:
        mutate(release)
    path = destination or package.with_name("release-manifest.json")
    path.write_bytes(_json_bytes(release))
    return path


@pytest.fixture(scope="session")
def engine_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _modern_package(
    path: Path,
    *,
    version: str = "1.1.0",
    include_shared_tool: bool = False,
    baseline_payload: bytes | None = None,
    mutate_manifest=None,
) -> Path:
    legacy = _package(path.with_name(path.stem + "-legacy.zip"), version=version)
    with zipfile.ZipFile(legacy) as archive:
        entries = {
            name: archive.read(name)
            for name in archive.namelist()
            if name != "package-manifest.json"
        }
        manifest = json.loads(archive.read("package-manifest.json"))

    exact = manifest["managed_surface"]["exact_directories"]
    exact.remove(".agents/skills")
    exact.extend([".agents/skills/alpha", ".agents/skills/sync-base"])
    exact.sort()

    skill_payload = baseline_payload or (
        b"---\nname: ru-writing-style\ndescription: Russian writing\n---\n"
    )
    tool_record = {
        "id": "ru-writing-style",
        "files": [
            {
                "path": "SKILL.md",
                "sha256": _sha256(skill_payload),
                "bytes": len(skill_payload),
            }
        ],
    }
    session_manifest = {
        "schema_version": 1,
        "target": "codex",
        "release_tag": f"codex-v{version}",
        "base_version": version,
        "tools": [tool_record],
    }
    session_manifest_bytes = _json_bytes(session_manifest)
    manifest_path = "session-tools-baseline/session-tools-manifest.json"
    entries[manifest_path] = session_manifest_bytes
    entries[
        "session-tools-baseline/tools/ru-writing-style/SKILL.md"
    ] = skill_payload
    manifest["retired_managed_paths"] = [".agents/skills/retired-owned"]
    manifest["session_tools_baseline"] = {
        "manifest_path": manifest_path,
        "manifest_sha256": _sha256(session_manifest_bytes),
        "tools": [tool_record],
        "retired_tool_ids": [],
    }

    if include_shared_tool:
        private_payload = b"fake-officecli-private\n"
        shim_payload = b"fake-officecli-shim\n"
        policy_payload = _json_bytes(
            {
                "schema_version": 1,
                "officecli_version": "1.0.143",
                "allowed_commands": ["open"],
                "managed_install_commands": ["install"],
            }
        )
        entries["shared-tools/officecli/officecli.exe"] = private_payload
        entries["support/officecli-shim.exe"] = shim_payload
        entries["support/officecli-command-policy.json"] = policy_payload
        process_environment = [
            {"name": "OFFICECLI_NO_AUTO_INSTALL", "value": "1"},
            {"name": "OFFICECLI_SKIP_UPDATE", "value": "1"},
        ]
        manifest["shared_tools"] = [
            {
                "id": "officecli",
                "version": "1.0.143",
                "bundle_version": "1.0.0",
                "compatibility_epoch": "officecli-managed-v1",
                "minimum_compatible_version": "1.0.143",
                "maximum_exclusive_version": "2.0.0",
                "payload_path": "shared-tools/officecli/officecli.exe",
                "sha256": _sha256(private_payload),
                "bytes": len(private_payload),
                "install_path": ".llm-foundation/libexec/officecli/officecli.exe",
                "version_arguments": ["--version"],
                "version_pattern": (
                    r"\A(?:officecli[ \t]+)?v?(?<version>(?:0|[1-9][0-9]*)"
                    r"\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))\z"
                ),
                "timeout_seconds": 10,
                "path_entry": ".llm-foundation/bin",
                "environment": {
                    "scope": "current-user",
                    "set": process_environment,
                },
                "shim": {
                    "schema_version": 1,
                    "payload_path": "support/officecli-shim.exe",
                    "sha256": _sha256(shim_payload),
                    "bytes": len(shim_payload),
                    "command_path": ".llm-foundation/bin/officecli.exe",
                    "policy_payload_path": (
                        "support/officecli-command-policy.json"
                    ),
                    "policy_install_path": (
                        ".llm-foundation/libexec/officecli/"
                        "officecli-command-policy.json"
                    ),
                    "policy_sha256": _sha256(policy_payload),
                    "policy_bytes": len(policy_payload),
                    "process_environment": process_environment,
                },
            }
        ]

    if mutate_manifest is not None:
        mutate_manifest(manifest)

    manifest["files"] = [
        {"path": name, "sha256": _sha256(payload), "bytes": len(payload)}
        for name, payload in sorted(entries.items())
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
        archive.writestr("package-manifest.json", _json_bytes(manifest))
    _write_release_manifest(path)
    return path


def _legacy_package_with_retired_skill(path: Path) -> Path:
    package = _package(path)
    with zipfile.ZipFile(package) as archive:
        entries = {
            name: archive.read(name)
            for name in archive.namelist()
            if name != "package-manifest.json"
        }
        manifest = json.loads(archive.read("package-manifest.json"))
    retired_path = ".agents/skills/retired-owned/SKILL.md"
    retired_payload = b"# retired package-owned skill\n"
    entries[retired_path] = retired_payload
    manifest["files"] = [
        {"path": name, "sha256": _sha256(payload), "bytes": len(payload)}
        for name, payload in sorted(entries.items())
    ]
    with zipfile.ZipFile(package, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
        archive.writestr("package-manifest.json", _json_bytes(manifest))
    return package


def _rewrite_package(
    package: Path,
    mutate,
) -> Path:
    with zipfile.ZipFile(package) as archive:
        entries = {
            name: archive.read(name)
            for name in archive.namelist()
            if name != "package-manifest.json"
        }
        manifest = json.loads(archive.read("package-manifest.json"))
    mutate(manifest, entries)
    manifest["files"] = [
        {"path": name, "sha256": _sha256(payload), "bytes": len(payload)}
        for name, payload in sorted(entries.items())
    ]
    with zipfile.ZipFile(package, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
        archive.writestr("package-manifest.json", _json_bytes(manifest))
    _write_release_manifest(package)
    return package


def _overlap_session_with_package_skill(
    manifest: dict[str, object],
    entries: dict[str, bytes],
) -> None:
    for name in list(entries):
        if name.startswith("session-tools-baseline/"):
            del entries[name]
    payload = entries[".agents/skills/alpha/SKILL.md"]
    tool = {
        "id": "alpha",
        "files": [
            {
                "path": "SKILL.md",
                "sha256": _sha256(payload),
                "bytes": len(payload),
            }
        ],
    }
    internal = {
        "schema_version": 1,
        "target": manifest["target"],
        "release_tag": f"{manifest['target']}-v{manifest['version']}",
        "base_version": manifest["version"],
        "tools": [tool],
    }
    internal_bytes = _json_bytes(internal)
    manifest_path = "session-tools-baseline/session-tools-manifest.json"
    entries[manifest_path] = internal_bytes
    entries["session-tools-baseline/tools/alpha/SKILL.md"] = payload
    manifest["session_tools_baseline"] = {
        "manifest_path": manifest_path,
        "manifest_sha256": _sha256(internal_bytes),
        "tools": [tool],
        "retired_tool_ids": [],
    }


def _remove_session_baseline(
    manifest: dict[str, object],
    entries: dict[str, bytes],
) -> None:
    manifest.pop("session_tools_baseline")
    manifest.pop("retired_managed_paths")
    for name in list(entries):
        if name.startswith("session-tools-baseline/"):
            del entries[name]


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_schema_one_accepts_legacy_and_strict_optional_contracts(
    engine_root: Path, tmp_path: Path, executable: str
):
    legacy = _package(tmp_path / f"legacy-{Path(executable).stem}.zip")
    modern = _modern_package(
        tmp_path / f"modern-{Path(executable).stem}.zip",
        include_shared_tool=True,
    )
    legacy_home = tmp_path / f"legacy-{Path(executable).stem}"
    modern_home = tmp_path / f"modern-{Path(executable).stem}"
    legacy_home.mkdir()
    modern_home.mkdir()

    legacy_plan = _run(
        executable, engine_root, "plan", legacy_home, package=legacy
    )
    modern_plan = _run(
        executable, engine_root, "plan", modern_home, package=modern
    )

    assert legacy_plan.returncode == 0, legacy_plan.stderr
    assert _json(legacy_plan)["status"] == "READY"
    assert modern_plan.returncode == 0, modern_plan.stderr
    assert _json(modern_plan)["status"] == "READY"
    assert not (modern_home / "shared-tools").exists()


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("home_kind", ["clean", "legacy", "legacy-plus-local"])
def test_granular_migration_installs_baseline_and_preserves_local_skills(
    engine_root: Path,
    tmp_path: Path,
    executable: str,
    home_kind: str,
):
    stem = f"{home_kind}-{Path(executable).stem}"
    home = tmp_path / stem
    home.mkdir()
    local = home / ".agents" / "skills" / "local-personal" / "SKILL.md"
    local_payload = b"# local \xd0\xbd\xd0\xb0\xd0\xb2\xd1\x8b\xd0\xba\n"
    if home_kind != "clean":
        legacy = _package(tmp_path / f"{stem}-legacy.zip", version="1.0.0")
        installed = _run(
            executable, engine_root, "install", home, package=legacy
        )
        assert installed.returncode == 0, installed.stderr
    if home_kind == "legacy-plus-local":
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(local_payload)

    modern = _modern_package(tmp_path / f"{stem}-modern.zip")
    installed = _run(executable, engine_root, "install", home, package=modern)

    assert installed.returncode == 0, installed.stderr
    if home_kind == "legacy-plus-local":
        assert local.read_bytes() == local_payload
    assert (
        home / ".agents" / "skills" / "ru-writing-style" / "SKILL.md"
    ).is_file()
    active = json.loads(
        (
            home / ".llm-foundation" / "state" / "codex" / "active.json"
        ).read_text(encoding="utf-8")
    )
    package_owned_paths = {row["path"] for row in active["installed_files"]}
    assert not any(path.startswith("session-tools-baseline/") for path in package_owned_paths)
    assert not any(
        path.startswith(".agents/skills/ru-writing-style/")
        for path in package_owned_paths
    )
    session_state_path = (
        home
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "codex"
        / "state.json"
    )
    session_state = json.loads(session_state_path.read_text(encoding="utf-8"))
    assert session_state["schema_version"] == 1
    assert session_state["target"] == "codex"
    assert session_state["release_tag"] == "codex-v1.1.0"
    assert session_state["release_manifest_sha256"] == _sha256(
        modern.with_name("release-manifest.json").read_bytes()
    )
    assert session_state["tools"][0]["id"] == "ru-writing-style"
    assert session_state["tools"][0]["ownership_marker"]

    doctor = _run(
        executable, engine_root, "doctor", home, package=modern
    )
    assert doctor.returncode == 0, doctor.stderr
    if home_kind == "legacy-plus-local":
        assert local.read_bytes() == local_payload

    rollback = _run(executable, engine_root, "rollback", home, target="codex")
    assert rollback.returncode == 0, rollback.stderr
    if home_kind == "legacy-plus-local":
        assert local.read_bytes() == local_payload
    assert not session_state_path.exists()
    assert not (
        home / ".agents/skills/ru-writing-style"
    ).exists()


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_baseline_release_binding_accepts_explicit_pair_and_sibling_fallback(
    engine_root: Path, tmp_path: Path, executable: str
):
    sibling_home = tmp_path / f"sibling-{Path(executable).stem}"
    explicit_home = tmp_path / f"explicit-{Path(executable).stem}"
    sibling_home.mkdir()
    explicit_home.mkdir()
    sibling_package = _modern_package(
        tmp_path / f"sibling-{Path(executable).stem}.zip"
    )
    sibling_manifest = sibling_package.with_name("release-manifest.json")
    sibling_manifest_hash = _sha256(sibling_manifest.read_bytes())
    sibling_result = _run(
        executable,
        engine_root,
        "install",
        sibling_home,
        package=sibling_package,
    )
    assert sibling_result.returncode == 0, sibling_result.stderr

    explicit_package = _modern_package(
        tmp_path / f"explicit-{Path(executable).stem}.zip"
    )
    explicit_manifest = _write_release_manifest(
        explicit_package,
        destination=tmp_path / f"bound-{Path(executable).stem}.json",
    )
    explicit_package.with_name("release-manifest.json").unlink()
    explicit_result = _run(
        executable,
        engine_root,
        "install",
        explicit_home,
        package=explicit_package,
        release_manifest=explicit_manifest,
        release_manifest_sha256=_sha256(explicit_manifest.read_bytes()),
    )
    assert explicit_result.returncode == 0, explicit_result.stderr
    for home, manifest_hash in (
        (sibling_home, sibling_manifest_hash),
        (explicit_home, _sha256(explicit_manifest.read_bytes())),
    ):
        state = json.loads(
            (
                home
                / ".llm-foundation/state/session-tools/codex/state.json"
            ).read_text(encoding="utf-8")
        )
        assert state["release_manifest_sha256"] == manifest_hash


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_candidate_release_manifest_is_accepted_only_in_acceptance_mode(
    engine_root: Path, tmp_path: Path, executable: str
):
    accepted_home = tmp_path / f"candidate-accepted-{Path(executable).stem}"
    rejected_home = tmp_path / f"candidate-rejected-{Path(executable).stem}"
    accepted_home.mkdir()
    rejected_home.mkdir()
    package = _modern_package(
        tmp_path / f"candidate-{Path(executable).stem}.zip"
    )
    _write_release_manifest(package, channel="candidate")

    accepted = _run(
        executable, engine_root, "plan", accepted_home, package=package
    )
    assert accepted.returncode == 0, accepted.stderr
    assert _json(accepted)["status"] == "READY"

    rejected = _run(
        executable,
        engine_root,
        "plan",
        rejected_home,
        package=package,
        extra_env={"FOUNDATION_ACCEPTANCE_MODE": "0"},
    )
    assert rejected.returncode == 30
    assert _json(rejected)["code"] == "INVALID_PACKAGE"


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_release_binding_accepts_valid_acceptance_evidence_hash(
    engine_root: Path, tmp_path: Path, executable: str
):
    home = tmp_path / f"evidence-hash-{Path(executable).stem}"
    home.mkdir()
    package = _modern_package(
        tmp_path / f"evidence-hash-{Path(executable).stem}.zip"
    )
    manifest = _write_release_manifest(
        package,
        mutate=lambda release: release.update(
            {"acceptance_evidence_sha256": "d" * 64}
        ),
    )

    accepted = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
        release_manifest=manifest,
        release_manifest_sha256=_sha256(manifest.read_bytes()),
    )

    assert accepted.returncode == 0, accepted.stderr


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_release_binding_accepts_valid_candidate_promotion_hash(
    engine_root: Path, tmp_path: Path, executable: str
):
    home = tmp_path / f"promotion-hash-{Path(executable).stem}"
    home.mkdir()
    package = _modern_package(
        tmp_path / f"promotion-hash-{Path(executable).stem}.zip"
    )
    manifest = _write_release_manifest(
        package,
        mutate=lambda release: release.update(
            {"promoted_from_candidate_manifest_sha256": "e" * 64}
        ),
    )

    accepted = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
        release_manifest=manifest,
        release_manifest_sha256=_sha256(manifest.read_bytes()),
    )

    assert accepted.returncode == 0, accepted.stderr


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "explicit-path-only",
        "explicit-hash-only",
        "malformed-hash",
        "digest-mismatch",
        "release-version-mismatch",
        "asset-mismatch",
        "package-manifest-mismatch",
        "same-path",
    ],
)
def test_baseline_release_binding_rejects_missing_mixed_tampered_or_unbound(
    engine_root: Path,
    tmp_path: Path,
    executable: str,
    case: str,
):
    stem = f"release-{case}-{Path(executable).stem}"
    home = tmp_path / stem
    home.mkdir()
    package = _modern_package(tmp_path / f"{stem}.zip")
    sibling = package.with_name("release-manifest.json")
    release_manifest = None
    release_hash = None
    if case == "missing":
        sibling.unlink()
    elif case == "explicit-path-only":
        release_manifest = sibling
    elif case == "explicit-hash-only":
        release_hash = _sha256(sibling.read_bytes())
    elif case == "malformed-hash":
        release_manifest = sibling
        release_hash = "A" * 64
    elif case == "digest-mismatch":
        release_manifest = sibling
        release_hash = "0" * 64
    elif case in {
        "release-version-mismatch",
        "asset-mismatch",
        "package-manifest-mismatch",
    }:
        def mutate(value):
            if case == "release-version-mismatch":
                value["version"] = "9.9.9"
            elif case == "asset-mismatch":
                value["asset"]["sha256"] = "0" * 64
            else:
                value["package_manifest_sha256"] = "0" * 64

        _write_release_manifest(package, mutate=mutate)
    elif case == "same-path":
        release_manifest = package
        release_hash = _sha256(package.read_bytes())

    result = _run(
        executable,
        engine_root,
        "install",
        home,
        package=package,
        release_manifest=release_manifest,
        release_manifest_sha256=release_hash,
    )

    assert result.returncode == 30
    assert _json(result)["code"] == "INVALID_PACKAGE"
    assert not (home / ".llm-foundation").exists()


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("existing", ["matching", "different"])
def test_baseline_adopts_only_exact_unowned_destination(
    engine_root: Path,
    tmp_path: Path,
    executable: str,
    existing: str,
):
    home = tmp_path / f"adopt-{existing}-{Path(executable).stem}"
    home.mkdir()
    package = _modern_package(tmp_path / f"adopt-{existing}.zip")
    with zipfile.ZipFile(package) as archive:
        expected = archive.read(
            "session-tools-baseline/tools/ru-writing-style/SKILL.md"
        )
    destination = home / ".agents/skills/ru-writing-style/SKILL.md"
    destination.parent.mkdir(parents=True)
    before = expected if existing == "matching" else b"# local collision\n"
    destination.write_bytes(before)

    result = _run(executable, engine_root, "install", home, package=package)

    if existing == "different":
        assert result.returncode == 30
        assert _json(result)["code"] == "INVALID_PACKAGE"
        assert destination.read_bytes() == before
        assert not (
            home / ".llm-foundation/state/session-tools/codex/state.json"
        ).exists()
        return
    assert result.returncode == 0, result.stderr
    assert destination.read_bytes() == before
    assert (
        home / ".llm-foundation/state/session-tools/codex/state.json"
    ).is_file()
    rollback = _run(executable, engine_root, "rollback", home, target="codex")
    assert rollback.returncode == 0, rollback.stderr
    assert destination.read_bytes() == before


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_foundation_preserves_verified_newer_session_state(
    engine_root: Path, tmp_path: Path, executable: str
):
    home = tmp_path / ("p" if Path(executable).stem == "pwsh" else "w")
    home.mkdir()
    first = _modern_package(tmp_path / "a.zip", version="1.1.0")
    installed = _run(executable, engine_root, "install", home, package=first)
    assert installed.returncode == 0, installed.stderr
    state_path = home / ".llm-foundation/state/session-tools/codex/state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["release_tag"] = "codex-v9.0.0"
    state["release_version"] = "9.0.0"
    state["release_manifest_sha256"] = "9" * 64
    legacy_payload = b"print('legacy session helper')\n"
    legacy_relative = "x.py"
    legacy_path = (
        home
        / ".agents"
        / "skills"
        / state["tools"][0]["id"]
        / legacy_relative
    )
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(legacy_payload)
    script_payload = b"console.log('legacy session helper')\n"
    script_relative = "y.js"
    script_path = legacy_path.parent / script_relative
    script_path.write_bytes(script_payload)
    cache_path = legacy_path.parent / "__pycache__" / "x.cpython-312.pyc"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"runtime cache\n")
    state["tools"][0]["files"].append(
        {
            "path": legacy_relative,
            "sha256": _sha256(legacy_payload),
            "bytes": len(legacy_payload),
        }
    )
    state["tools"][0]["files"].append(
        {
            "path": script_relative,
            "sha256": _sha256(script_payload),
            "bytes": len(script_payload),
        }
    )
    state["tools"][0]["files"].sort(key=lambda record: record["path"])
    state_path.write_bytes(_json_bytes(state))
    expected_state = state_path.read_bytes()

    second = _modern_package(tmp_path / "b.zip", version="1.2.0")
    upgraded = _run(executable, engine_root, "install", home, package=second)

    assert upgraded.returncode == 0, upgraded.stderr
    assert state_path.read_bytes() == expected_state
    assert cache_path.read_bytes() == b"runtime cache\n"
    doctor = _run(executable, engine_root, "doctor", home, package=second)
    assert doctor.returncode == 0, doctor.stderr


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_package_handle_blocks_swap_after_snapshot_and_installs_verified_bytes(
    engine_root: Path, tmp_path: Path, executable: str
):
    home = tmp_path / f"held-package-{Path(executable).stem}"
    home.mkdir()
    package = _modern_package(tmp_path / "held-package.zip")
    replacement = _modern_package(
        tmp_path / "replacement" / "held-package.zip",
        baseline_payload=b"# replacement must not install\n",
    )
    with zipfile.ZipFile(package) as archive:
        expected = archive.read(
            "session-tools-baseline/tools/ru-writing-style/SKILL.md"
        )
    arguments = [
        executable,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(engine_root / "src/foundation.ps1"),
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
    environment["FOUNDATION_HOLD_AFTER_SNAPSHOT_MS"] = "2000"
    acceptance_temp = tmp_path / "swap-temp"
    acceptance_temp.mkdir()
    environment["TEMP"] = str(acceptance_temp)
    environment["TMP"] = str(acceptance_temp)
    process = subprocess.Popen(
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    pending = home / ".llm-foundation/state/codex/pending.json"
    deadline = time.monotonic() + 20
    while not pending.exists() and process.poll() is None:
        if time.monotonic() >= deadline:
            process.kill()
            pytest.fail("Foundation did not reach the post-snapshot hold")
        time.sleep(0.02)
    assert pending.exists()
    with pytest.raises(OSError):
        os.replace(replacement, package)
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr or stdout
    assert (
        home / ".agents/skills/ru-writing-style/SKILL.md"
    ).read_bytes() == expected


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize(
    "history",
    ["owned-unchanged", "owned-modified", "unmanaged"],
)
def test_retired_path_deletes_only_unchanged_prior_package_ownership(
    engine_root: Path,
    tmp_path: Path,
    executable: str,
    history: str,
):
    home = tmp_path / f"retired-{history}-{Path(executable).stem}"
    home.mkdir()
    retired = home / ".agents/skills/retired-owned/SKILL.md"
    original = b"# retired package-owned skill\n"
    current = original
    if history.startswith("owned"):
        legacy = _legacy_package_with_retired_skill(
            tmp_path / f"retired-{history}-legacy.zip"
        )
        installed = _run(
            executable, engine_root, "install", home, package=legacy
        )
        assert installed.returncode == 0, installed.stderr
        if history == "owned-modified":
            current = b"# user modified retired skill\n"
            retired.write_bytes(current)
    else:
        retired.parent.mkdir(parents=True)
        current = b"# unmanaged retired name\n"
        retired.write_bytes(current)
    modern = _modern_package(
        tmp_path / f"retired-{history}-modern.zip"
    )

    upgraded = _run(executable, engine_root, "install", home, package=modern)

    assert upgraded.returncode == 0, upgraded.stderr
    if history == "owned-unchanged":
        assert not retired.exists()
    else:
        assert retired.read_bytes() == current
    rollback = _run(executable, engine_root, "rollback", home, target="codex")
    assert rollback.returncode == 0, rollback.stderr
    assert retired.read_bytes() == current


def _unknown_baseline_field(manifest: dict[str, object]) -> None:
    manifest["session_tools_baseline"]["unexpected"] = True


def _unsafe_retired_path(manifest: dict[str, object]) -> None:
    manifest["retired_managed_paths"] = ["../escape"]


def _baseline_hash_mismatch(manifest: dict[str, object]) -> None:
    manifest["session_tools_baseline"]["manifest_sha256"] = "0" * 64


def _baseline_tools_mismatch(manifest: dict[str, object]) -> None:
    manifest["session_tools_baseline"]["tools"] = []


def _unknown_shared_field(manifest: dict[str, object]) -> None:
    manifest["shared_tools"][0]["unexpected"] = True


def _unknown_shim_field(manifest: dict[str, object]) -> None:
    manifest["shared_tools"][0]["shim"]["unexpected"] = True


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize(
    "mutation,shared",
    [
        (_unknown_baseline_field, False),
        (_unsafe_retired_path, False),
        (_baseline_hash_mismatch, False),
        (_baseline_tools_mismatch, False),
        (_unknown_shared_field, True),
        (_unknown_shim_field, True),
    ],
)
def test_optional_contracts_reject_unknown_unsafe_or_unbound_values(
    engine_root: Path,
    tmp_path: Path,
    executable: str,
    mutation,
    shared: bool,
):
    home = tmp_path / f"invalid-{mutation.__name__}-{Path(executable).stem}"
    home.mkdir()
    package = _modern_package(
        tmp_path / f"invalid-{mutation.__name__}-{Path(executable).stem}.zip",
        include_shared_tool=shared,
        mutate_manifest=mutation,
    )

    result = _run(executable, engine_root, "plan", home, package=package)

    assert result.returncode == 30
    assert _json(result)["code"] in {"INVALID_PACKAGE", "UNSAFE_PATH"}
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_session_destination_cannot_also_be_package_owned(
    engine_root: Path,
    tmp_path: Path,
    executable: str,
):
    suffix = Path(executable).stem
    home = tmp_path / f"overlapping-session-destination-{suffix}"
    home.mkdir()
    package = _modern_package(tmp_path / f"overlap-{suffix}.zip")
    _rewrite_package(package, _overlap_session_with_package_skill)

    result = _run(executable, engine_root, "plan", home, package=package)

    assert result.returncode == 30
    assert _json(result)["code"] == "INVALID_PACKAGE"
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_shared_tools_require_bound_release_manifest_without_baseline(
    engine_root: Path,
    tmp_path: Path,
    executable: str,
):
    suffix = Path(executable).stem
    home = tmp_path / f"shared-release-binding-{suffix}"
    home.mkdir()
    package = _modern_package(
        tmp_path / f"shared-only-{suffix}.zip",
        include_shared_tool=True,
    )
    _rewrite_package(package, _remove_session_baseline)
    ready = _run(executable, engine_root, "plan", home, package=package)
    assert ready.returncode == 0, ready.stderr
    package.with_name("release-manifest.json").unlink()

    result = _run(executable, engine_root, "plan", home, package=package)

    assert result.returncode == 30
    assert _json(result)["code"] == "INVALID_PACKAGE"
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("executable", POWERSHELLS)
def test_rollback_accepts_snapshot_holding_session_tools_state(
    engine_root: Path, tmp_path: Path, executable: str
):
    """An upgrade snapshot lists the session-tools state, and may restore it."""
    home = tmp_path / ("rp" if Path(executable).stem == "pwsh" else "rw")
    home.mkdir()
    first = _modern_package(tmp_path / "rollback-a.zip", version="1.1.0")
    installed = _run(executable, engine_root, "install", home, package=first)
    assert installed.returncode == 0, installed.stderr
    state_path = home / ".llm-foundation/state/session-tools/codex/state.json"
    assert state_path.is_file(), "install must seed the session-tools state"

    second = _modern_package(tmp_path / "rollback-b.zip", version="1.2.0")
    upgraded = _run(executable, engine_root, "install", home, package=second)
    assert upgraded.returncode == 0, upgraded.stderr

    active_path = home / ".llm-foundation/state/codex/active.json"
    snapshot_path = Path(
        json.loads(active_path.read_text(encoding="utf-8"))["snapshot_path"]
    )
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert (
        ".llm-foundation/state/session-tools/codex/state.json"
        in snapshot["existed"]
    ), "the upgrade snapshot must record the session-tools state it replaced"

    rollback = _run(executable, engine_root, "rollback", home, target="codex")

    assert rollback.returncode == 0, rollback.stdout + rollback.stderr
    assert "UNSAFE_PATH" not in rollback.stdout + rollback.stderr
