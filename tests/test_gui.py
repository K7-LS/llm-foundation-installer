from __future__ import annotations

import ctypes
from datetime import datetime, timedelta, timezone
import hashlib
import http.server
import io
import json
import os
import shutil
import struct
import subprocess
import textwrap
import threading
import zipfile
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_VERSION = (REPOSITORY_ROOT / "VERSION").read_text(
    encoding="utf-8"
).strip()
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe")
POWERSHELLS = [
    value
    for value in (shutil.which("pwsh"), shutil.which("powershell.exe"))
    if value
]
DEFAULT_GUI_CONTRACT_ARGUMENTS = [
    "-Edition",
    "Owner",
    "-ProductRole",
    "Installer",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _find_csharp_compiler() -> Path | None:
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")),
        Path(os.environ.get("ProgramFiles", "C:/Program Files")),
    ]
    matches: list[Path] = []
    for root in candidates:
        matches.extend(
            root.glob(
                "Microsoft Visual Studio/*/*/MSBuild/Current/Bin/Roslyn/csc.exe"
            )
        )
    framework = Path(
        "C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    )
    if framework.is_file():
        matches.append(framework)
    return sorted(matches)[0] if matches else None


def _compile_versioned_codex(path: Path, version: str) -> None:
    compiler = _find_csharp_compiler()
    if compiler is None:
        pytest.skip("C# compiler is unavailable")
    source = path.with_suffix(".cs")
    source.write_text(
        textwrap.dedent(
            f"""
            using System;
            public static class FixtureCodex
            {{
                public static int Main(string[] args)
                {{
                    Console.WriteLine("codex {version}");
                    return 0;
                }}
            }}
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:exe",
            f"/out:{path}",
            str(source),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    payload = (
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _build_gui_bundle(
    output: Path,
    package_root: Path | None = None,
    provider_eligibility_evidence: Path | None = None,
    distribution_mode: str | None = None,
    client_sources_lock: Path | None = None,
    allow_local_test_sources: bool = False,
    foundation_package_root: Path | None = None,
    owner_candidate_root: Path | None = None,
    edition: str = "Owner",
    product_role: str = "Installer",
) -> Path:
    arguments = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
        "-Edition",
        edition,
        "-ProductRole",
        product_role,
        "-OutputRoot",
        str(output),
    ]
    if package_root is not None:
        arguments.extend(["-PackageRoot", str(package_root)])
        implicit_foundation = package_root / "foundation"
        if (
            foundation_package_root is None
            and implicit_foundation.is_dir()
        ):
            foundation_package_root = implicit_foundation
    if foundation_package_root is not None:
        arguments.extend(
            ["-FoundationPackageRoot", str(foundation_package_root)]
        )
    if owner_candidate_root is not None:
        arguments.extend(
            ["-OwnerCandidateRoot", str(owner_candidate_root)]
        )
    if provider_eligibility_evidence is not None:
        arguments.extend(
            [
                "-ProviderEligibilityEvidence",
                str(provider_eligibility_evidence),
            ]
        )
    if distribution_mode is not None:
        arguments.extend(["-DistributionMode", distribution_mode])
    if client_sources_lock is not None:
        arguments.extend(["-ClientSourcesLock", str(client_sources_lock)])
    if allow_local_test_sources:
        arguments.append("-AllowLocalTestSources")
    result = subprocess.run(
        arguments,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def _local_client_source_lock(
    path: Path,
    *,
    url: str = "http://127.0.0.1:8765/client.bin",
    sha256: str = "0" * 64,
    signature_required: bool = False,
    publisher: str | None = None,
    version: str = "1.0.0",
    artifact_kind: str = "portable-exe",
    install_mode: str = "download-only",
    detect_commands: list[str] | None = None,
    archive_entry: str | None = None,
) -> Path:
    _write_json(
        path,
        {
            "schema_version": 1,
            "official_only": False,
            "test_only": True,
            "platform": {
                "os": "windows",
                "architecture": "x64",
                "minimum_build": 19041,
            },
            "clients": [
                {
                    "id": "fixture-client",
                    "target": "fixture",
                    "display_name": "Fixture Client",
                    "role": "cli",
                    "required_for_base": True,
                    "required_for_employee": False,
                    "version": version,
                    "source_kind": "download",
                    "url": url,
                    "sha256": sha256,
                    "artifact_kind": artifact_kind,
                    "archive_entry": archive_entry,
                    "publisher": publisher,
                    "signature_required": signature_required,
                    "install_mode": install_mode,
                    "detect_commands": (
                        detect_commands
                        if detect_commands is not None
                        else ["fixture-client.exe"]
                    ),
                    "version_arguments": ["--version"],
                    "store_identity": None,
                    "store_publisher": None,
                    "store_signature_kind": None,
                }
            ],
        },
    )
    return path


def _codex_cli_source_lock(path: Path, version: str = "1.0.0") -> Path:
    source_lock = json.loads(
        (REPOSITORY_ROOT / "client-sources.lock.json").read_text(
            encoding="utf-8"
        )
    )
    source_lock["official_only"] = False
    source_lock["test_only"] = True
    codex = next(
        client
        for client in source_lock["clients"]
        if client["id"] == "codex-cli"
    )
    codex["version"] = version
    codex["url"] = "http://127.0.0.1:8765/codex.ps1"
    source_lock["clients"] = [codex]
    _write_json(path, source_lock)
    return path


def _codex_store_record(path: Path, store_location: Path) -> Path:
    _write_json(
        path,
        {
            "present": True,
            "name": "OpenAI.Codex",
            "publisher": "CN=50BDFD77-8903-4850-9FFE-6E8522F64D5B",
            "signature_kind": "Store",
            "architecture": "X64",
            "version": "26.721.4979.0",
            "package_full_name": (
                "OpenAI.Codex_26.721.4979.0_x64__2p2nqsd0c76g0"
            ),
            "package_family_name": "OpenAI.Codex_2p2nqsd0c76g0",
            "install_location": str(store_location),
            "application_id": "App",
            "executable": "app/ChatGPT.exe",
            "entry_point": "Windows.FullTrustApplication",
        },
    )
    return path


def _provider_eligibility_evidence(
    path: Path,
    *,
    reviewed_at: datetime | None = None,
    expires_at: datetime | None = None,
    extra_top_level: dict[str, object] | None = None,
) -> Path:
    reviewed = reviewed_at or datetime.now(timezone.utc).replace(
        microsecond=0
    )
    expires = expires_at or reviewed + timedelta(days=7)
    value: dict[str, object] = {
        "schema_version": 1,
        "reviewed_at_utc": reviewed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at_utc": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": {
            "supported_regions": (
                "https://www.anthropic.com/supported-countries"
            ),
            "usage_policy": "https://www.anthropic.com/legal/aup",
            "consumer_terms": (
                "https://www.anthropic.com/legal/consumer-terms"
            ),
            "safeguards_appeals": (
                "https://support.claude.com/en/articles/"
                "8241253-safeguards-warnings-and-appeals"
            ),
        },
        "claude": {
            "employee_location_eligibility_verified": True,
            "organization_eligibility_verified": True,
            "individual_accounts_only": True,
            "transport_not_used_for_region_or_ban_bypass": True,
            "unattended_consumer_automation": False,
        },
    }
    if extra_top_level:
        value.update(extra_top_level)
    _write_json(path, value)
    return path


def _accepted_foundation(root: Path) -> Path:
    package_root = root / "foundation"
    if package_root.exists():
        return package_root
    package_root.mkdir(parents=True)
    engine_files = {
        "foundation.ps1": (
            REPOSITORY_ROOT / "src" / "foundation.ps1"
        ).read_bytes(),
        "VERSION": (
            REPOSITORY_ROOT / "VERSION"
        ).read_bytes(),
    }
    script_hash = hashlib.sha256(
        engine_files["foundation.ps1"]
    ).hexdigest()
    engine_files["engine-manifest.json"] = (
        json.dumps(
            {
                "commands": [
                    "doctor",
                    "install",
                    "inventory",
                    "plan",
                    "rollback",
                ],
                "engine_version": FOUNDATION_VERSION,
                "foundation_ps1_sha256": script_hash,
                "network": "offline",
                "protocol_version": 1,
                "schema_version": 1,
                "supported_powershell": ["5.1", "7"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    asset = package_root / f"foundation-engine-{FOUNDATION_VERSION}.zip"
    with zipfile.ZipFile(asset, "w") as archive:
        for name, payload in sorted(engine_files.items()):
            archive.writestr(name, payload)
    engine_records = {
        name: {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
        for name, payload in sorted(engine_files.items())
    }
    evidence = package_root / "acceptance-evidence.json"
    evidence_value = {
        "schema_version": 1,
        "engine_version": FOUNDATION_VERSION,
        "installer_version": "0.3.0",
        "FOUNDATION_SYNTHETIC": "PASS",
        "deterministic_engine_bundle": "PASS",
    }
    evidence_value["evidence_body_sha256"] = _evidence_body_sha256(
        evidence_value
    )
    _write_json(evidence, evidence_value)
    release = package_root / "release-manifest.json"
    release_value = {
        "schema_version": 1,
        "target": "foundation",
        "version": FOUNDATION_VERSION,
        "tag": f"foundation-engine-v{FOUNDATION_VERSION}",
        "channel": "stable",
        "source": {
            "repository": (
                "https://github.com/daniileliseev1337/"
                "llm-foundation-installer"
            ),
            "commit": "a" * 40,
            "tree": "b" * 40,
        },
        "asset": {
            "name": asset.name,
            "sha256": _sha256(asset),
            "bytes": asset.stat().st_size,
        },
        "engine_files": engine_records,
        "acceptance_evidence_sha256": _sha256(evidence),
        "requires": {
            "immutable_release": True,
            "release_attestation": True,
        },
    }
    release_value["evidence_body_sha256"] = _evidence_body_sha256(
        release_value
    )
    _write_json(release, release_value)
    verification = package_root / "release-verification.json"
    verification_value = {
        "schema_version": 1,
        "repository": (
            "daniileliseev1337/llm-foundation-installer"
        ),
        "tag": f"foundation-engine-v{FOUNDATION_VERSION}",
        "release_state": {
            "draft": False,
            "prerelease": False,
            "immutable": True,
        },
        "release_attestation": "PASS",
        "assets": [
            {
                **release_value["asset"],
                "attestation": "PASS",
            }
        ],
        "RELEASE_INTEGRITY": "PASS",
    }
    verification_value["evidence_body_sha256"] = _evidence_body_sha256(
        verification_value
    )
    _write_json(verification, verification_value)
    _write_json(
        package_root / "package-acceptance.json",
        {
            "schema_version": 1,
            "target": "foundation",
            "engine_version": FOUNDATION_VERSION,
            "package_acceptance": "PASS",
            "asset": release_value["asset"],
            "engine_files": engine_records,
            "release_manifest": {
                "name": release.name,
                "sha256": _sha256(release),
                "bytes": release.stat().st_size,
            },
            "acceptance_evidence": {
                "name": evidence.name,
                "sha256": _sha256(evidence),
                "bytes": evidence.stat().st_size,
            },
            "release_verification": {
                "name": verification.name,
                "sha256": _sha256(verification),
                "bytes": verification.stat().st_size,
            },
            "immutable_release": True,
            "release_attestation": True,
        },
    )
    return package_root


def _accepted_package(
    root: Path,
    target: str = "codex",
    *,
    codex_flat_evidence: bool = True,
) -> Path:
    foundation = _accepted_foundation(root)
    package_root = root / target
    package_root.mkdir(parents=True)
    client_ids = {
        "codex": "codex-cli",
        "claude": "claude-code",
        "opencode": "opencode",
    }
    verdict_ids = {
        "codex": "FULL_RELEASE_CODEX",
        "claude": "FULL_RELEASE_CLAUDE",
        "opencode": "FULL_RELEASE_OPENCODE",
    }
    asset = package_root / f"{target}-base-1.0.0.zip"
    install_roots = {
        "codex": ".codex",
        "claude": ".claude",
        "opencode": ".config/opencode",
    }
    hot_paths = {
        "codex": ".codex/AGENTS.md",
        "claude": ".claude/CLAUDE.md",
        "opencode": ".config/opencode/AGENTS.md",
    }
    install_root = install_roots[target]
    hot_path = hot_paths[target]
    entries = {
        hot_path: b"# accepted candidate\n",
        f"{install_root}/base/runtime/check.txt": b"runtime\n",
    }
    package_manifest = {
        "schema_version": 1,
        "target": target,
        "version": "1.0.0",
        "client": {
            "id": client_ids[target],
            "supported_version": "1.0.0-test",
        },
        "foundation_engine_version": (
            REPOSITORY_ROOT / "VERSION"
        ).read_text(encoding="utf-8").strip(),
        "managed_surface": {
            "exact_directories": [f"{install_root}/base/runtime"],
            "replace_files": [hot_path],
            "preserved_paths": [f"{install_root}/auth.json"],
        },
        "sync_policy": {
            "direction": "hub-to-consumer",
            "consumer_feedback_upload": False,
            "consumer_push": False,
            "consumer_session_upload": False,
            "credentials_included": False,
        },
        "environment": {
            "scope": "current-user",
            "set": [],
        },
        "files": [
            {
                "path": name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
            for name, payload in sorted(entries.items())
        ],
    }
    package_manifest_bytes = (
        json.dumps(package_manifest, sort_keys=True).encode() + b"\n"
    )
    with zipfile.ZipFile(asset, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
        archive.writestr(
            "package-manifest.json",
            package_manifest_bytes,
        )
    release = package_root / "release-manifest.json"
    asset_record = {
        "name": asset.name,
        "sha256": _sha256(asset),
        "bytes": asset.stat().st_size,
    }
    release_value = {
        "schema_version": 1,
        "target": target,
        "version": "1.0.0",
        "tag": f"{target}-v1.0.0",
        "channel": "stable",
        "client": {
            "id": client_ids[target],
            "supported_version": "1.0.0-test",
        },
        "foundation_engine_version": package_manifest[
            "foundation_engine_version"
        ],
        "foundation_engine_manifest_sha256": "f" * 64,
        "source": {
            "repository": f"https://github.com/example/{target}-base",
            "commit": "a" * 40,
            "tree": "b" * 40,
            "transformation": f"{target}-native-v1",
        },
        "asset": asset_record,
        "package_manifest_sha256": hashlib.sha256(
            package_manifest_bytes
        ).hexdigest(),
        "components_lock_sha256": "c" * 64,
        "requires": {
            "immutable_release": True,
            "release_attestation": True,
        },
    }
    release_value["foundation_engine_manifest_sha256"] = json.loads(
        (foundation / "package-acceptance.json").read_text(encoding="utf-8")
    )["engine_files"]["engine-manifest.json"]["sha256"]
    _write_json(release, release_value)
    evidence = package_root / "acceptance-evidence.json"
    binding_keys = (
        "target",
        "version",
        "tag",
        "client",
        "asset",
        "package_manifest_sha256",
        "components_lock_sha256",
        "source",
        "foundation_engine_version",
        "foundation_engine_manifest_sha256",
    )
    binding = {
        key: release_value[key] for key in binding_keys
    }
    if target == "codex" and codex_flat_evidence:
        evidence_value = {
            "schema_version": 1,
            "target": target,
            "version": "1.0.0",
            "release_binding": binding,
            verdict_ids[target]: "PASS",
            "RELEASE_INTEGRITY": "PENDING_PUBLICATION",
        }
    else:
        evidence_value = {
            "schema_version": 1,
            "target": target,
            "version": "1.0.0",
            "release_binding": binding,
            "asset_sha256": _sha256(asset),
            "verdicts": {
                verdict_ids[target]: "PASS",
                "RELEASE_INTEGRITY": "PENDING_PUBLICATION",
            },
        }
    evidence_value["evidence_body_sha256"] = _evidence_body_sha256(
        evidence_value
    )
    _write_json(evidence, evidence_value)
    release_value["acceptance_evidence_sha256"] = _sha256(evidence)
    _write_json(release, release_value)
    release_verification = package_root / "release-verification.json"
    release_verification_value = {
        "schema_version": 1,
        "repository": f"example/{target}-base",
        "tag": release_value["tag"],
        "release_state": {
            "draft": False,
            "prerelease": False,
            "immutable": True,
        },
        "release_attestation": "PASS",
        "assets": [
            {
                **asset_record,
                "attestation": "PASS",
            }
        ],
        "RELEASE_INTEGRITY": "PASS",
    }
    release_verification_value["evidence_body_sha256"] = (
        _evidence_body_sha256(release_verification_value)
    )
    _write_json(release_verification, release_verification_value)
    _write_json(
        package_root / "package-acceptance.json",
        {
            "schema_version": 1,
            "target": target,
            "package_acceptance": "PASS",
            "client": {
                "id": client_ids[target],
                "supported_version": "1.0.0-test",
            },
            "asset": {
                "name": asset.name,
                "sha256": _sha256(asset),
                "bytes": asset.stat().st_size,
            },
            "release_manifest": {
                "name": release.name,
                "sha256": _sha256(release),
                "bytes": release.stat().st_size,
            },
            "acceptance_evidence": {
                "name": evidence.name,
                "sha256": _sha256(evidence),
                "bytes": evidence.stat().st_size,
            },
            "release_verification": {
                "name": release_verification.name,
                "sha256": _sha256(release_verification),
                "bytes": release_verification.stat().st_size,
            },
            "immutable_release": True,
            "release_attestation": True,
        },
    )
    return package_root


def _owner_claude_candidate(root: Path) -> Path:
    package_root = _accepted_package(root, "claude")
    foundation = root / "foundation"
    asset = package_root / "claude-base-1.0.0.zip"
    release = package_root / "release-manifest.json"
    release_value = json.loads(release.read_text(encoding="utf-8"))
    release_value["channel"] = "candidate"
    release_value["tag"] = "claude-v1.0.0"

    components = package_root / "components.lock.json"
    _write_json(
        components,
        {
            "schema_version": 1,
            "target": "claude",
            "source": release_value["source"],
        },
    )
    release_value["components_lock_sha256"] = _sha256(components)

    candidate = package_root / "candidate-acceptance.json"
    binding_keys = (
        "target",
        "version",
        "tag",
        "client",
        "asset",
        "package_manifest_sha256",
        "components_lock_sha256",
        "source",
        "foundation_engine_version",
        "foundation_engine_manifest_sha256",
    )
    candidate_value = {
        "schema_version": 1,
        "target": "claude",
        "CANDIDATE_OFFLINE": "PASS",
        "CLIENT_BINARY_ACCEPTANCE": "PASS",
        "FULL_RELEASE_CLAUDE": "NOT_PASS",
        "NON_RELEASABLE": True,
        "PROGRAM_RELEASE": "2/3",
        "client": release_value["client"],
        "asset": {
            "name": asset.name,
            "sha256": _sha256(asset),
            "bytes": asset.stat().st_size,
        },
        "release_binding": {
            key: release_value[key] for key in binding_keys
        },
        "foundation": {
            "version": FOUNDATION_VERSION,
            "evidence_sha256": _sha256(
                foundation / "acceptance-evidence.json"
            ),
        },
    }
    candidate_value["evidence_body_sha256"] = _evidence_body_sha256(
        candidate_value
    )
    _write_json(candidate, candidate_value)
    release_value["acceptance_evidence_sha256"] = _sha256(candidate)
    _write_json(release, release_value)

    canary = package_root / "claude-live-canary.json"
    canary_value = {
        "schema_version": 1,
        "target": "claude",
        "version": "1.0.0",
        "CLAUDE_CANARY": "PASS",
        "model_requests": 0,
        "credentials_included": False,
        "personal_data_included": False,
        "release_binding": candidate_value["release_binding"],
    }
    canary_value["evidence_body_sha256"] = _evidence_body_sha256(
        canary_value
    )
    _write_json(canary, canary_value)
    (package_root / "NON_RELEASABLE.txt").write_text(
        "OFFLINE-ACCEPTED CANDIDATE; NOT A STABLE RELEASE\n"
        "Client contract: 1.0.0-test\n"
        "Stable promotion and employee distribution are forbidden.\n",
        encoding="utf-8",
    )

    for obsolete in (
        "acceptance-evidence.json",
        "release-verification.json",
        "package-acceptance.json",
    ):
        (package_root / obsolete).unlink()
    return package_root


@pytest.fixture(scope="module")
def gui_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if POWERSHELL is None:
        pytest.skip("PowerShell is required to build the Windows GUI")
    output = tmp_path_factory.mktemp("foundation-gui") / "bundle"
    return _build_gui_bundle(output)


def test_gui_build_is_hash_bound_and_self_describing(gui_bundle: Path):
    executable = gui_bundle / "LLMFoundationInstaller.exe"
    engine = gui_bundle / "engine" / "foundation.ps1"
    manifest_path = gui_bundle / "bundle-manifest.json"

    assert executable.is_file()
    assert engine.is_file()
    assert manifest_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    app_version = (REPOSITORY_ROOT / "APP_VERSION").read_text(
        encoding="utf-8"
    ).strip()
    engine_version = (REPOSITORY_ROOT / "VERSION").read_text(
        encoding="utf-8"
    ).strip()
    assert app_version == "0.3.0"
    assert engine_version == FOUNDATION_VERSION
    source = (
        REPOSITORY_ROOT / "src" / "gui" / "InstallerApp.cs"
    ).read_text(encoding="utf-8")
    assert '[assembly: AssemblyVersion("0.3.0.0")]' in source
    assert '[assembly: AssemblyFileVersion("0.3.0.0")]' in source
    assert manifest["version"] == app_version
    assert (gui_bundle / "engine" / "VERSION").read_text(
        encoding="utf-8"
    ).strip() == engine_version
    assert manifest["schema_version"] == 1
    assert manifest["app_id"] == "llm-foundation-installer"
    assert manifest["network"] == "user-initiated-only"
    assert manifest["automatic_network"] is False
    assert manifest["telemetry"] is False
    assert manifest["reverse_flow"] is False
    assert manifest["distribution"] == "single-executable"
    assert manifest["distribution_mode"] == "preview"
    assert manifest["embedded_foundation"] is True
    assert manifest["signature"] == "unsigned-preview"
    assert manifest["employee_release"] is False
    assert manifest["employee_distribution_allowed"] is False
    assert manifest["public_distribution_allowed"] is False
    assert manifest["windows_warning_expected"] is False
    assert manifest["artifacts"]["LLMFoundationInstaller.exe"]["sha256"] == _sha256(
        executable
    )
    assert manifest["artifacts"]["engine/foundation.ps1"]["sha256"] == _sha256(
        engine
    )
    assert manifest["artifacts"]["engine/engine-manifest.json"][
        "sha256"
    ] == _sha256(gui_bundle / "engine" / "engine-manifest.json")
    assert manifest["artifacts"]["engine/VERSION"]["sha256"] == _sha256(
        gui_bundle / "engine" / "VERSION"
    )
    assert manifest["artifacts"]["VERSION"]["sha256"] == _sha256(
        gui_bundle / "VERSION"
    )

    result = subprocess.run(
        [str(executable), "--self-test-json"],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "app_id": "llm-foundation-installer",
        "engine_validated": True,
        "foundation_protocol": 1,
        "network": "user-initiated-only",
        "automatic_network": False,
        "reverse_flow": False,
        "targets": ["codex", "claude", "opencode"],
        "telemetry": False,
        "version": manifest["version"],
    }


def test_gui_embeds_and_validates_client_source_lock(gui_bundle: Path):
    source_lock = REPOSITORY_ROOT / "client-sources.lock.json"
    bundled_lock = gui_bundle / "client-sources.lock.json"
    manifest = json.loads(
        (gui_bundle / "bundle-manifest.json").read_text(encoding="utf-8")
    )

    assert bundled_lock.read_bytes() == source_lock.read_bytes()
    assert manifest["client_sources"] == {
        "schema_version": 1,
        "official_only": True,
        "test_only": False,
        "relative_path": "client-sources.lock.json",
        "resource_name": "ClientSources.lock.json",
        "sha256": _sha256(source_lock),
        "bytes": source_lock.stat().st_size,
    }
    assert manifest["artifacts"]["client-sources.lock.json"] == {
        "sha256": _sha256(source_lock),
        "bytes": source_lock.stat().st_size,
    }

    result = subprocess.run(
        [str(gui_bundle / "LLMFoundationInstaller.exe"), "--client-sources-json"],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "READY"
    assert payload["schema_version"] == 1
    assert payload["platform"] == {
        "os": "windows",
        "architecture": "x64",
        "minimum_build": 19041,
    }
    assert payload["official_only"] is True
    assert payload["test_only"] is False
    assert [
        (entry["id"], entry["version"], entry["source_kind"])
        for entry in payload["clients"]
    ] == [
        ("codex-cli", "0.146.0-alpha.3.1", "download"),
        ("codex-desktop", "store-current", "store"),
        ("claude-code", "2.1.218", "download"),
        ("opencode-cli", "1.18.7", "download"),
        ("opencode-desktop", "1.18.7", "download"),
    ]


def test_codex_desktop_source_uses_exact_store_product_and_identity(
    gui_bundle: Path,
):
    source_lock = json.loads(
        (gui_bundle / "client-sources.lock.json").read_text(encoding="utf-8")
    )
    desktop = next(
        entry
        for entry in source_lock["clients"]
        if entry["id"] == "codex-desktop"
    )
    assert desktop["url"].startswith(
        "https://apps.microsoft.com/detail/9plm9xgg6vks"
    )
    assert desktop["store_product_id"] == "9PLM9XGG6VKS"
    assert desktop["store_identity"] == "OpenAI.Codex"
    assert (
        desktop["store_publisher"]
        == "CN=50BDFD77-8903-4850-9FFE-6E8522F64D5B"
    )
    assert desktop["store_signature_kind"] == "Store"
    assert desktop["store_application_id"] == "App"
    assert desktop["store_executable"] == "app/ChatGPT.exe"
    assert desktop["store_entry_point"] == "Windows.FullTrustApplication"


def test_codex_cli_source_is_bound_to_exact_compatible_release_asset():
    source_lock = json.loads(
        (
            REPOSITORY_ROOT / "client-sources.lock.json"
        ).read_text(encoding="utf-8")
    )
    cli = next(
        entry
        for entry in source_lock["clients"]
        if entry["id"] == "codex-cli"
    )

    assert cli["version"] == "0.146.0-alpha.3.1"
    assert cli["url"] == (
        "https://github.com/openai/codex/releases/download/"
        "rust-v0.146.0-alpha.3.1/install.ps1"
    )
    assert cli["sha256"] == (
        "397cad1d3091728fc59531018c4b2cd99b49b51b36c6ad42f7ec304d8da8ba4f"
    )
    assert cli["artifact_kind"] == "powershell-installer-script"
    assert cli["install_mode"] == "official-script"


def test_store_record_validation_accepts_only_locked_codex_identity(
    gui_bundle: Path,
    tmp_path: Path,
):
    package_root = tmp_path / (
        "OpenAI.Codex_26.721.4979.0_x64__2p2nqsd0c76g0"
    )
    valid_record = tmp_path / "valid-store-record.json"
    _write_json(
        valid_record,
        {
            "present": True,
            "name": "OpenAI.Codex",
            "publisher": "CN=50BDFD77-8903-4850-9FFE-6E8522F64D5B",
            "signature_kind": "Store",
            "architecture": "X64",
            "version": "26.721.4979.0",
            "package_full_name": (
                "OpenAI.Codex_26.721.4979.0_x64__2p2nqsd0c76g0"
            ),
            "package_family_name": "OpenAI.Codex_2p2nqsd0c76g0",
            "install_location": str(package_root),
            "application_id": "App",
            "executable": "app/ChatGPT.exe",
            "entry_point": "Windows.FullTrustApplication",
        },
    )
    valid = subprocess.run(
        [
            str(gui_bundle / "LLMFoundationInstaller.exe"),
            "--validate-store-record-json",
            "codex-desktop",
            str(valid_record),
        ],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=20,
    )
    assert valid.returncode == 0, valid.stdout + valid.stderr
    payload = json.loads(valid.stdout)
    assert payload == {
        "status": "READY",
        "client_id": "codex-desktop",
        "version": "26.721.4979.0",
        "package_full_name": (
            "OpenAI.Codex_26.721.4979.0_x64__2p2nqsd0c76g0"
        ),
        "package_family_name": "OpenAI.Codex_2p2nqsd0c76g0",
        "install_location": str(package_root.resolve()),
        "application_id": "App",
        "executable": "app/ChatGPT.exe",
        "store_product_id": "9PLM9XGG6VKS",
        "source_uri": (
            "ms-windows-store://pdp/?ProductId=9PLM9XGG6VKS"
        ),
    }

    for field, wrong in (
        ("name", "Codex.QR"),
        ("publisher", "CN=Third Party"),
        ("signature_kind", "Developer"),
        ("architecture", "Arm64"),
        ("package_family_name", "ThirdParty.Codex_bad"),
        ("install_location", "relative"),
        ("application_id", "Other"),
        ("executable", "app/Other.exe"),
        ("entry_point", "Other.EntryPoint"),
    ):
        record = json.loads(valid_record.read_text(encoding="utf-8"))
        record[field] = wrong
        invalid_record = tmp_path / f"invalid-{field}.json"
        _write_json(invalid_record, record)
        invalid = subprocess.run(
            [
                str(gui_bundle / "LLMFoundationInstaller.exe"),
                "--validate-store-record-json",
                "codex-desktop",
                str(invalid_record),
            ],
            cwd=gui_bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=20,
        )
        assert invalid.returncode != 0
        assert "store package identity differs from source lock" in (
            invalid.stderr.lower()
        )


def test_preview_can_embed_explicit_local_test_source_lock(tmp_path: Path):
    source_lock = _local_client_source_lock(
        tmp_path / "client-sources.test.json"
    )
    bundle = _build_gui_bundle(
        tmp_path / "bundle",
        client_sources_lock=source_lock,
        allow_local_test_sources=True,
    )
    manifest = json.loads(
        (bundle / "bundle-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["distribution_mode"] == "preview"
    assert manifest["client_sources"]["official_only"] is False
    assert manifest["client_sources"]["test_only"] is True
    result = subprocess.run(
        [str(bundle / "LLMFoundationInstaller.exe"), "--client-sources-json"],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["test_only"] is True
    assert [entry["id"] for entry in payload["clients"]] == [
        "fixture-client"
    ]


def test_internal_unsigned_rejects_local_test_client_source_lock(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    for target in ("codex", "claude", "opencode"):
        _accepted_package(package_source, target)
    evidence = _provider_eligibility_evidence(
        tmp_path / "provider-eligibility-evidence.json"
    )
    source_lock = _local_client_source_lock(
        tmp_path / "client-sources.test.json"
    )
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(tmp_path / "bundle"),
            "-PackageRoot",
            str(package_source),
            "-ProviderEligibilityEvidence",
            str(evidence),
            "-DistributionMode",
            "InternalUnsigned",
            "-ClientSourcesLock",
            str(source_lock),
            "-AllowLocalTestSources",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode != 0
    assert "local test client sources are only allowed for preview" in (
        result.stdout + result.stderr
    ).lower()


def test_client_download_is_atomic_hash_verified_and_vpn_needs_no_proxy(
    tmp_path: Path,
):
    content = b"fixture-client-binary\n"

    class Handler(http.server.BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self):
            type(self).requests += 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=(
                f"http://127.0.0.1:{server.server_port}/client.bin"
            ),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        executable = bundle / "LLMFoundationInstaller.exe"
        home = tmp_path / "employee-home"
        home.mkdir()
        profile = tmp_path / "vpn.json"
        _write_json(
            profile,
            {"schema_version": 1, "mode": "VPN", "proxy": None},
        )
        saved = subprocess.run(
            [
                str(executable),
                "--save-connection-json",
                str(home),
                str(profile),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert saved.returncode == 0, saved.stdout + saved.stderr

        staging_component_length = max(
            len("client-staging"),
            174 - len(str(tmp_path)) - 1,
        )
        staging = tmp_path / "client-staging".ljust(
            staging_component_length,
            "x",
        )
        representative_partial = (
            staging
            / "fixture-client"
            / "1.0.0"
            / (
                ".client.part-"
                + ("0" * 32)
                + ".bin"
            )
        )
        assert len(str(representative_partial)) >= 245
        downloaded = subprocess.run(
            [
                str(executable),
                "--download-client-json",
                str(home),
                "fixture-client",
                str(staging),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )

        assert downloaded.returncode == 0, (
            downloaded.stdout + downloaded.stderr
        )
        payload = json.loads(downloaded.stdout)
        assert payload == {
            "status": "VERIFIED",
            "client_id": "fixture-client",
            "version": "1.0.0",
            "connection_mode": "VPN",
            "uses_proxy": False,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
            "relative_path": "fixture-client/1.0.0/client.bin",
        }
        final = staging / Path(payload["relative_path"])
        assert final.read_bytes() == content
        assert not list(staging.rglob("*part-*"))
        assert Handler.requests == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_download_removes_partial_file_when_hash_is_wrong(
    tmp_path: Path,
):
    content = b"tampered-client\n"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=(
                f"http://127.0.0.1:{server.server_port}/client.bin"
            ),
            sha256="0" * 64,
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        staging = tmp_path / "client-staging"
        downloaded = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--download-client-json",
                str(home),
                "fixture-client",
                str(staging),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert downloaded.returncode != 0
        assert "hash" in downloaded.stderr.lower()
        assert not list(staging.rglob("client.bin"))
        assert not list(staging.rglob("*part-*"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_download_rejects_reparse_ancestor_before_network(
    tmp_path: Path,
):
    content = b"must-not-be-requested"

    class Handler(http.server.BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self):
            type(self).requests += 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            return

    target = tmp_path / "junction-target"
    target.mkdir()
    junction = tmp_path / "junction"
    created = subprocess.run(
        [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            "mklink",
            "/J",
            str(junction),
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("Windows junction fixture is unavailable")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=f"http://127.0.0.1:{server.server_port}/client.bin",
            sha256=hashlib.sha256(content).hexdigest(),
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        result = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--download-client-json",
                str(home),
                "fixture-client",
                str(junction / "nested"),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert result.returncode != 0
        assert "reparse point" in result.stderr.lower()
        assert Handler.requests == 0
        assert not (target / "nested").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_download_interruption_leaves_no_partial_or_final_file(
    tmp_path: Path,
):
    partial = b"partial-download"
    expected = partial + b"-missing-tail"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(expected)))
            self.end_headers()
            self.wfile.write(partial)
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=f"http://127.0.0.1:{server.server_port}/client.bin",
            sha256=hashlib.sha256(expected).hexdigest(),
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        staging = tmp_path / "client-staging"
        result = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--download-client-json",
                str(home),
                "fixture-client",
                str(staging),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert result.returncode != 0
        assert "client download failed with curl exit" in result.stderr.lower()
        assert not list(staging.rglob("*.part-*"))
        assert not list(staging.rglob("client.bin"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_download_rejects_unsigned_executable_when_signature_required(
    tmp_path: Path,
):
    content = b"MZ-not-a-signed-executable\n"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=(
                f"http://127.0.0.1:{server.server_port}/client.exe"
            ),
            sha256=hashlib.sha256(content).hexdigest(),
            signature_required=True,
            publisher="Fixture Publisher",
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        staging = tmp_path / "client-staging"
        downloaded = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--download-client-json",
                str(home),
                "fixture-client",
                str(staging),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert downloaded.returncode != 0
        assert "authenticode" in downloaded.stderr.lower()
        assert not list(staging.rglob("client.exe"))
        assert not list(staging.rglob("*part-*"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_download_rejects_valid_signature_from_wrong_publisher(
    tmp_path: Path,
):
    signed_fixture = Path(shutil.which("pwsh") or "")
    if not signed_fixture.is_file():
        pytest.skip("PowerShell 7 signed fixture is unavailable")
    content = signed_fixture.read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=(
                f"http://127.0.0.1:{server.server_port}/client.exe"
            ),
            sha256=hashlib.sha256(content).hexdigest(),
            signature_required=True,
            publisher="Fixture Publisher",
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        staging = tmp_path / "client-staging"
        downloaded = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--download-client-json",
                str(home),
                "fixture-client",
                str(staging),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert downloaded.returncode != 0
        assert "publisher" in downloaded.stderr.lower()
        assert not list(staging.rglob("client.exe"))
        assert not list(staging.rglob("*part-*"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_download_rejects_publisher_substring_instead_of_exact_name(
    tmp_path: Path,
):
    signed_fixture = Path(shutil.which("pwsh") or "")
    if not signed_fixture.is_file():
        pytest.skip("PowerShell 7 signed fixture is unavailable")
    content = signed_fixture.read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=(
                f"http://127.0.0.1:{server.server_port}/client.exe"
            ),
            sha256=hashlib.sha256(content).hexdigest(),
            signature_required=True,
            publisher="Microsoft",
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        staging = tmp_path / "client-staging"

        downloaded = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--download-client-json",
                str(home),
                "fixture-client",
                str(staging),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )

        assert downloaded.returncode != 0
        assert "publisher" in downloaded.stderr.lower()
        assert not list(staging.rglob("client.exe"))
        assert not list(staging.rglob("*part-*"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_download_accepts_exact_authenticode_simple_name(
    tmp_path: Path,
):
    signed_fixture = Path(shutil.which("pwsh") or "")
    if not signed_fixture.is_file():
        pytest.skip("PowerShell 7 signed fixture is unavailable")
    content = signed_fixture.read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=(
                f"http://127.0.0.1:{server.server_port}/client.exe"
            ),
            sha256=hashlib.sha256(content).hexdigest(),
            signature_required=True,
            publisher="Microsoft Corporation",
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        staging = tmp_path / "client-staging"

        downloaded = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--download-client-json",
                str(home),
                "fixture-client",
                str(staging),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )

        assert downloaded.returncode == 0, downloaded.stderr
        result = json.loads(downloaded.stdout)
        assert result["status"] == "VERIFIED"
        assert result["sha256"] == hashlib.sha256(content).hexdigest()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_plan_and_install_managed_bin_without_mutating_real_user_path(
    tmp_path: Path,
):
    content = (
        b"@echo off\r\n"
        b"echo fixture-client 1.0.0\r\n"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self):
            type(self).requests += 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=(
                f"http://127.0.0.1:{server.server_port}/fixture-client.cmd"
            ),
            sha256=hashlib.sha256(content).hexdigest(),
            artifact_kind="portable-command",
            install_mode="managed-bin",
            detect_commands=["fixture-client.cmd"],
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        executable = bundle / "LLMFoundationInstaller.exe"
        home = tmp_path / "employee-home"
        home.mkdir()
        staging = tmp_path / "client-staging"

        missing = subprocess.run(
            [
                str(executable),
                "--client-plan-json",
                str(home),
                "fixture-client",
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert missing.returncode == 0, missing.stdout + missing.stderr
        assert json.loads(missing.stdout) == {
            "status": "INSTALL_AVAILABLE",
            "client_id": "fixture-client",
            "supported_version": "1.0.0",
            "detected_version": None,
            "detected_state": "missing",
            "action": "install",
        }

        installed = subprocess.run(
            [
                str(executable),
                "--install-client-json",
                str(home),
                "fixture-client",
                str(staging),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
        payload = json.loads(installed.stdout)
        assert payload == {
            "status": "INSTALLED",
            "client_id": "fixture-client",
            "version": "1.0.0",
            "relative_install_path": (
                ".llm-foundation/bin/fixture-client.cmd"
            ),
            "path_persisted": False,
            "authentication_touched": False,
        }
        managed = home / ".llm-foundation" / "bin" / "fixture-client.cmd"
        assert managed.read_bytes() == content
        assert Handler.requests == 1

        ready = subprocess.run(
            [
                str(executable),
                "--client-plan-json",
                str(home),
                "fixture-client",
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert ready.returncode == 0, ready.stdout + ready.stderr
        assert json.loads(ready.stdout) == {
            "status": "READY",
            "client_id": "fixture-client",
            "supported_version": "1.0.0",
            "detected_version": "1.0.0",
            "detected_state": "exact",
            "action": "none",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_install_blocks_newer_managed_version_before_download(
    tmp_path: Path,
):
    source_content = (
        b"@echo off\r\n"
        b"echo fixture-client 1.0.0\r\n"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self):
            type(self).requests += 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(source_content)))
            self.end_headers()
            self.wfile.write(source_content)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=(
                f"http://127.0.0.1:{server.server_port}/fixture-client.cmd"
            ),
            sha256=hashlib.sha256(source_content).hexdigest(),
            artifact_kind="portable-command",
            install_mode="managed-bin",
            detect_commands=["fixture-client.cmd"],
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        executable = bundle / "LLMFoundationInstaller.exe"
        home = tmp_path / "employee-home"
        managed = home / ".llm-foundation" / "bin"
        managed.mkdir(parents=True)
        newer = managed / "fixture-client.cmd"
        newer.write_bytes(
            b"@echo off\r\necho fixture-client 2.0.0\r\n"
        )
        before = newer.read_bytes()

        plan = subprocess.run(
            [
                str(executable),
                "--client-plan-json",
                str(home),
                "fixture-client",
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert plan.returncode == 20
        assert json.loads(plan.stdout)["status"] == "BLOCKED_NO_DOWNGRADE"
        install = subprocess.run(
            [
                str(executable),
                "--install-client-json",
                str(home),
                "fixture-client",
                str(tmp_path / "client-staging"),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert install.returncode == 20
        assert json.loads(install.stdout)["status"] == "BLOCKED_NO_DOWNGRADE"
        assert newer.read_bytes() == before
        assert Handler.requests == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_install_extracts_only_locked_zip_payload(tmp_path: Path):
    command = b"@echo off\r\necho fixture-client 1.0.0\r\n"
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("fixture-client.cmd", command)
    archive_bytes = archive_buffer.getvalue()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(archive_bytes)))
            self.end_headers()
            self.wfile.write(archive_bytes)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=f"http://127.0.0.1:{server.server_port}/client.zip",
            sha256=hashlib.sha256(archive_bytes).hexdigest(),
            artifact_kind="zip",
            archive_entry="fixture-client.cmd",
            install_mode="managed-bin",
            detect_commands=["fixture-client.cmd"],
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        installed = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--install-client-json",
                str(home),
                "fixture-client",
                str(tmp_path / "client-staging"),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
        assert json.loads(installed.stdout)["status"] == "INSTALLED"
        assert (
            home / ".llm-foundation" / "bin" / "fixture-client.cmd"
        ).read_bytes() == command
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_install_rejects_zip_with_path_traversal_entry(
    tmp_path: Path,
):
    command = b"@echo off\r\necho fixture-client 1.0.0\r\n"
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("fixture-client.cmd", command)
        archive.writestr("../escape.txt", b"escape")
    archive_bytes = archive_buffer.getvalue()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(archive_bytes)))
            self.end_headers()
            self.wfile.write(archive_bytes)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=f"http://127.0.0.1:{server.server_port}/client.zip",
            sha256=hashlib.sha256(archive_bytes).hexdigest(),
            artifact_kind="zip",
            archive_entry="fixture-client.cmd",
            install_mode="managed-bin",
            detect_commands=["fixture-client.cmd"],
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        installed = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--install-client-json",
                str(home),
                "fixture-client",
                str(tmp_path / "client-staging"),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert installed.returncode != 0
        assert "unsafe zip entry" in installed.stderr.lower()
        assert not (tmp_path / "escape.txt").exists()
        assert not (
            home / ".llm-foundation" / "bin" / "fixture-client.cmd"
        ).exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_official_script_is_ast_checked_and_runs_pinned_release_from_staging(
    tmp_path: Path,
):
    script = b"""[CmdletBinding()]
param([string]$Release)
$ErrorActionPreference = 'Stop'
if ($Release -cne '1.0.0') { throw 'release was not pinned' }
New-Item -ItemType Directory -Force -Path $env:CODEX_INSTALL_DIR | Out-Null
$command = Join-Path $env:CODEX_INSTALL_DIR 'fixture-client.cmd'
'@echo off`r`necho fixture-client 1.0.0' |
    Set-Content -LiteralPath $command -Encoding Ascii
"""

    class Handler(http.server.BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self):
            type(self).requests += 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(script)))
            self.end_headers()
            self.wfile.write(script)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=f"http://127.0.0.1:{server.server_port}/install.ps1",
            sha256=hashlib.sha256(script).hexdigest(),
            artifact_kind="powershell-installer-script",
            install_mode="official-script",
            detect_commands=["fixture-client.cmd"],
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        installed = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--install-client-json",
                str(home),
                "fixture-client",
                str(tmp_path / "client-staging"),
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
        payload = json.loads(installed.stdout)
        assert payload == {
            "status": "INSTALLED",
            "client_id": "fixture-client",
            "version": "1.0.0",
            "relative_install_path": (
                ".llm-foundation/clients/fixture-client/bin/"
                "fixture-client.cmd"
            ),
            "path_persisted": False,
            "authentication_touched": False,
        }
        command = (
            home
            / ".llm-foundation"
            / "clients"
            / "fixture-client"
            / "bin"
            / "fixture-client.cmd"
        )
        assert command.is_file()
        assert Handler.requests == 1
        assert not list((tmp_path / "client-staging").rglob("*.part-*"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_official_script_network_cmdlets_are_routed_through_safe_curl(
    tmp_path: Path,
):
    script = b"""[CmdletBinding()]
param([string]$Release)
$ErrorActionPreference = 'Stop'
if ((Get-Command Invoke-WebRequest).CommandType -cne 'Function' -or
    (Get-Command Invoke-RestMethod).CommandType -cne 'Function') {
    throw 'safe curl wrappers are not active'
}
$metadata = Invoke-RestMethod -Uri ($env:LLM_FIXTURE_BASE + '/metadata')
$response = Invoke-WebRequest -UseBasicParsing -Uri (
    $env:LLM_FIXTURE_BASE + '/payload'
)
if ($Release -cne '1.0.0' -or $metadata.version -cne '1.0.0') {
    throw 'release metadata differs'
}
New-Item -ItemType Directory -Force -Path $env:CODEX_INSTALL_DIR | Out-Null
$command = Join-Path $env:CODEX_INSTALL_DIR 'fixture-client.cmd'
('@echo off`r`necho fixture-client ' + $response.Content.Trim()) |
    Set-Content -LiteralPath $command -Encoding Ascii
"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/install.ps1":
                payload = script
                content_type = "text/plain"
            elif self.path == "/metadata":
                payload = b'{"version":"1.0.0"}'
                content_type = "application/json"
            elif self.path == "/payload":
                payload = b"1.0.0"
                content_type = "text/plain"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=base_url + "/install.ps1",
            sha256=hashlib.sha256(script).hexdigest(),
            artifact_kind="powershell-installer-script",
            install_mode="official-script",
            detect_commands=["fixture-client.cmd"],
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        environment = os.environ.copy()
        environment["LLM_FIXTURE_BASE"] = base_url
        installed = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--install-client-json",
                str(home),
                "fixture-client",
                str(tmp_path / "client-staging"),
            ],
            cwd=bundle,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )

        assert installed.returncode == 0, installed.stdout + installed.stderr
        assert json.loads(installed.stdout)["status"] == "INSTALLED"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_official_script_rejects_parse_error_before_execution(tmp_path: Path):
    script = b"""[CmdletBinding()]
param([string]$Release)
Set-Content -LiteralPath $env:LLM_SIDE_EFFECT -Value 'ran'
if (
"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(script)))
            self.end_headers()
            self.wfile.write(script)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=f"http://127.0.0.1:{server.server_port}/install.ps1",
            sha256=hashlib.sha256(script).hexdigest(),
            artifact_kind="powershell-installer-script",
            install_mode="official-script",
            detect_commands=["fixture-client.cmd"],
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        side_effect = tmp_path / "must-not-exist.txt"
        environment = os.environ.copy()
        environment["LLM_SIDE_EFFECT"] = str(side_effect)
        installed = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--install-client-json",
                str(home),
                "fixture-client",
                str(tmp_path / "client-staging"),
            ],
            cwd=bundle,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert installed.returncode != 0
        assert "powershell installer script failed ast validation" in (
            installed.stderr.lower()
        )
        assert not side_effect.exists()
        assert not (
            home
            / ".llm-foundation"
            / "clients"
            / "fixture-client"
            / "bin"
            / "fixture-client.cmd"
        ).exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_managed_desktop_install_is_atomic_registered_and_idempotent(
    tmp_path: Path,
):
    desktop = b"MZ-fixture-desktop-1.0.0"

    class Handler(http.server.BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self):
            type(self).requests += 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(desktop)))
            self.end_headers()
            self.wfile.write(desktop)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_lock = _local_client_source_lock(
            tmp_path / "client-sources.test.json",
            url=f"http://127.0.0.1:{server.server_port}/fixture-desktop.exe",
            sha256=hashlib.sha256(desktop).hexdigest(),
            artifact_kind="portable-exe",
            install_mode="managed-desktop",
            detect_commands=[],
        )
        bundle = _build_gui_bundle(
            tmp_path / "bundle",
            client_sources_lock=source_lock,
            allow_local_test_sources=True,
        )
        home_component_length = max(
            len("employee-home"),
            174 - len(str(tmp_path)) - 1,
        )
        home = tmp_path / "employee-home".ljust(
            home_component_length,
            "x",
        )
        home.mkdir()
        representative_temporary = (
            home
            / ".llm-foundation"
            / "apps"
            / "fixture-client"
            / "1.0.0"
            / (
                "fixture-desktop.exe.install-"
                + ("0" * 32)
            )
        )
        assert len(str(representative_temporary)) >= 260
        command = [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--install-client-json",
            str(home),
            "fixture-client",
            str(tmp_path / "client-staging"),
        ]
        first = subprocess.run(
            command,
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert first.returncode == 0, first.stdout + first.stderr
        payload = json.loads(first.stdout)
        assert payload == {
            "status": "INSTALLED",
            "client_id": "fixture-client",
            "version": "1.0.0",
            "relative_install_path": (
                ".llm-foundation/apps/fixture-client/1.0.0/"
                "fixture-desktop.exe"
            ),
            "path_persisted": False,
            "authentication_touched": False,
        }
        installed = (
            home
            / ".llm-foundation"
            / "apps"
            / "fixture-client"
            / "1.0.0"
            / "fixture-desktop.exe"
        )
        record = (
            home
            / ".llm-foundation"
            / "apps"
            / "fixture-client"
            / "current.json"
        )
        shortcut = (
            home
            / "AppData"
            / "Roaming"
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "LLM Foundation"
            / "fixture-client.lnk"
        )
        assert installed.read_bytes() == desktop
        assert json.loads(record.read_text(encoding="utf-8")) == {
            "schema_version": 1,
            "client_id": "fixture-client",
            "version": "1.0.0",
            "relative_path": "1.0.0/fixture-desktop.exe",
            "sha256": hashlib.sha256(desktop).hexdigest(),
        }
        assert shortcut.is_file()

        second = subprocess.run(
            command,
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert second.returncode == 0, second.stdout + second.stderr
        assert json.loads(second.stdout)["status"] == "ALREADY_READY"
        assert Handler.requests == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_managed_desktop_newer_version_blocks_downgrade_before_download(
    tmp_path: Path,
):
    desktop = b"MZ-fixture-desktop"

    class Handler(http.server.BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self):
            type(self).requests += 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(desktop)))
            self.end_headers()
            self.wfile.write(desktop)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        newest_lock = _local_client_source_lock(
            tmp_path / "client-sources-2.test.json",
            url=f"http://127.0.0.1:{server.server_port}/fixture-desktop.exe",
            sha256=hashlib.sha256(desktop).hexdigest(),
            artifact_kind="portable-exe",
            install_mode="managed-desktop",
            detect_commands=[],
            version="2.0.0",
        )
        newest_bundle = _build_gui_bundle(
            tmp_path / "bundle-2",
            client_sources_lock=newest_lock,
            allow_local_test_sources=True,
        )
        home = tmp_path / "employee-home"
        home.mkdir()
        installed = subprocess.run(
            [
                str(newest_bundle / "LLMFoundationInstaller.exe"),
                "--install-client-json",
                str(home),
                "fixture-client",
                str(tmp_path / "client-staging"),
            ],
            cwd=newest_bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
        assert Handler.requests == 1

        older_lock = _local_client_source_lock(
            tmp_path / "client-sources-1.test.json",
            url=f"http://127.0.0.1:{server.server_port}/fixture-desktop.exe",
            sha256=hashlib.sha256(desktop).hexdigest(),
            artifact_kind="portable-exe",
            install_mode="managed-desktop",
            detect_commands=[],
            version="1.0.0",
        )
        older_bundle = _build_gui_bundle(
            tmp_path / "bundle-1",
            client_sources_lock=older_lock,
            allow_local_test_sources=True,
        )
        blocked = subprocess.run(
            [
                str(older_bundle / "LLMFoundationInstaller.exe"),
                "--install-client-json",
                str(home),
                "fixture-client",
                str(tmp_path / "other-staging"),
            ],
            cwd=older_bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert blocked.returncode == 20, blocked.stdout + blocked.stderr
        payload = json.loads(blocked.stdout)
        assert payload["status"] == "BLOCKED_NO_DOWNGRADE"
        assert payload["detected_version"] == "2.0.0"
        assert Handler.requests == 1
        assert not (tmp_path / "other-staging").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_codex_newer_version_is_ready_without_download(
    tmp_path: Path,
):
    source_lock = _codex_cli_source_lock(
        tmp_path / "codex-client-sources.test.json"
    )
    bundle = _build_gui_bundle(
        tmp_path / "bundle",
        client_sources_lock=source_lock,
        allow_local_test_sources=True,
    )
    home = tmp_path / "employee-home"
    home.mkdir()
    staging = tmp_path / "client-staging"
    safe_path = tmp_path / "safe-path"
    safe_path.mkdir()
    _compile_versioned_codex(safe_path / "codex.exe", "2.0.0")
    environment = os.environ.copy()
    environment["PATH"] = str(safe_path)

    plan = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--client-plan-json",
            str(home),
            "codex-cli",
        ],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert plan.returncode == 0, plan.stdout + plan.stderr
    payload = json.loads(plan.stdout)
    assert payload["status"] == "READY"
    assert payload["status"] != "BLOCKED_NO_DOWNGRADE"
    assert payload["detected_version"] == "2.0.0"
    assert payload["action"] == "none"

    installed = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--install-client-json",
            str(home),
            "codex-cli",
            str(staging),
        ],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert json.loads(installed.stdout)["status"] == "ALREADY_READY"
    assert not staging.exists()


def test_target_plan_passes_detected_codex_version_to_foundation(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source)
    source_lock = _codex_cli_source_lock(
        tmp_path / "codex-client-sources.test.json",
        version="1.0.0-test",
    )
    bundle = _build_gui_bundle(
        tmp_path / "bundle",
        package_source,
        client_sources_lock=source_lock,
        allow_local_test_sources=True,
    )
    executable = bundle / "LLMFoundationInstaller.exe"
    home = tmp_path / "employee-home"
    home.mkdir()
    safe_path = tmp_path / "safe-path"
    safe_path.mkdir()
    _compile_versioned_codex(safe_path / "codex.exe", "2.0.0")
    environment = os.environ.copy()
    environment["PATH"] = str(safe_path)

    target_plan = subprocess.run(
        [
            str(executable),
            "--target-client-plan-json",
            str(home),
            "codex",
        ],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert target_plan.returncode == 0, (
        target_plan.stdout + target_plan.stderr
    )
    cli = json.loads(target_plan.stdout)["clients"][0]
    assert cli["client_id"] == "codex-cli"
    assert cli["detected_version"] == "2.0.0"

    foundation = subprocess.run(
        [
            str(executable),
            "--workflow-json",
            "plan",
            "codex",
            str(home),
            cli["detected_version"],
        ],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )

    assert foundation.returncode == 0, foundation.stdout + foundation.stderr
    assert json.loads(foundation.stdout)["status"] == "READY"

    source = (
        REPOSITORY_ROOT / "src" / "gui" / "InstallerApp.cs"
    ).read_text(encoding="utf-8")
    assert "row.detected_version = verified.clients" in source
    assert "cli.version;" not in source


def test_codex_missing_client_keeps_install_available(
    tmp_path: Path,
):
    source_lock = _codex_cli_source_lock(
        tmp_path / "codex-client-sources.test.json"
    )
    bundle = _build_gui_bundle(
        tmp_path / "bundle",
        client_sources_lock=source_lock,
        allow_local_test_sources=True,
    )
    home = tmp_path / "employee-home"
    home.mkdir()
    safe_path = tmp_path / "safe-path"
    safe_path.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = str(safe_path)

    result = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--client-plan-json",
            str(home),
            "codex-cli",
        ],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "status": "INSTALL_AVAILABLE",
        "client_id": "codex-cli",
        "supported_version": "1.0.0",
        "detected_version": None,
        "detected_state": "missing",
        "action": "install",
    }


def test_codex_store_missing_client_guides_store_without_cli_fallback(
    tmp_path: Path,
):
    bundle = _build_gui_bundle(tmp_path / "bundle")
    home = tmp_path / "employee-home"
    home.mkdir()
    safe_path = tmp_path / "safe-path"
    safe_path.mkdir()
    record = tmp_path / "missing-store-record.json"
    _write_json(record, {"present": False})
    environment = os.environ.copy()
    environment["PATH"] = str(safe_path)

    result = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--client-plan-store-record-json",
            str(home),
            "codex-desktop",
            str(record),
        ],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "status": "GUIDED_STORE",
        "client_id": "codex-desktop",
        "supported_version": "store-current",
        "detected_version": None,
        "detected_state": "not_checked",
        "action": "open_store",
    }
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_gui_builder_supports_powershell_7_and_5_1(
    tmp_path: Path,
    powershell: str,
):
    output = tmp_path / Path(powershell).stem / "bundle"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(output),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    executable = output / "LLMFoundationInstaller.exe"
    assert executable.is_file()
    assert ctypes.windll.shell32.ExtractIconExW(
        str(executable),
        -1,
        None,
        None,
        0,
    ) >= 1


def test_gui_executable_contains_a_branded_icon_resource(gui_bundle: Path):
    executable = gui_bundle / "LLMFoundationInstaller.exe"

    icon_count = ctypes.windll.shell32.ExtractIconExW(
        str(executable),
        -1,
        None,
        None,
        0,
    )

    assert icon_count >= 1


def test_unsigned_gui_build_is_byte_deterministic(tmp_path: Path):
    first = _build_gui_bundle(tmp_path / "first")
    second = _build_gui_bundle(tmp_path / "second")

    for relative in (
        "LLMFoundationInstaller.exe",
        "bundle-manifest.json",
        "client-sources.lock.json",
        "engine/foundation.ps1",
        "engine/engine-manifest.json",
        "engine/VERSION",
    ):
        assert (first / relative).read_bytes() == (second / relative).read_bytes()


def test_gui_can_render_employee_facing_preview(gui_bundle: Path, tmp_path: Path):
    executable = gui_bundle / "LLMFoundationInstaller.exe"
    preview = tmp_path / "installer-preview.png"
    result = subprocess.run(
        [str(executable), "--render-preview", str(preview)],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert preview.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", preview.read_bytes()[16:24])
    assert (width, height) == (1440, 900)


@pytest.mark.parametrize(
    ("edition", "resource"),
    [
        ("employee", "InstallerEmployeeView.xaml"),
        ("owner", "InstallerOwnerView.xaml"),
    ],
)
def test_singbox_connection_ui_contract_is_consistent_in_both_editions(
    edition: str,
    resource: str,
):
    """SingBox must reveal safely serializable proxy settings in every edition."""
    xaml = (REPOSITORY_ROOT / "src" / "gui" / resource).read_text(
        encoding="utf-8"
    )

    for technical_name in (
        "ProxyMode",
        "ProxySettings",
        "ProxyType",
        "ProxyHost",
        "ProxyPort",
        "ProxyAuth",
        "ProxyUsername",
        "ProxyPassword",
        "TestConnection",
    ):
        assert f'x:Name="{technical_name}"' in xaml, edition
    assert 'Visibility="Collapsed"' in xaml, edition
    assert 'Content="HTTP" Tag="HTTP"' in xaml, edition
    assert 'Content="HTTPS" Tag="HTTPS"' in xaml, edition
    assert 'Tag="None"' in xaml, edition
    assert 'Tag="UsernamePassword"' in xaml, edition
    assert "Сохранить и проверить" in xaml, edition
    assert "Launch Center сам запускает и останавливает sing-box" in xaml, edition


def test_singbox_connection_ui_reveals_settings_only_for_proxy_mode():
    source = (REPOSITORY_ROOT / "src" / "gui" / "InstallerApp.cs").read_text(
        encoding="utf-8"
    )

    update_mode = source.split("Action updateMode = delegate", 1)[1].split(
        "RoutedEventHandler checkedHandler", 1
    )[0]

    assert "contract.ProxySettings.IsEnabled = isProxy;" in update_mode
    assert "contract.ProxySettings.Visibility = isProxy" in update_mode
    assert update_mode.index("contract.ProxySettings.Visibility") < update_mode.index(
        "if (isProxy)"
    )
    assert "Заполните сервер, порт, логин и пароль" in update_mode


def test_connection_ui_contract_diagnostics_are_localized_for_users():
    source = (REPOSITORY_ROOT / "src" / "gui" / "InstallerApp.cs").read_text(
        encoding="utf-8"
    )

    assert "Не найдены элементы маршрута прокси" in source
    assert "Не найден элемент подключения: " in source
    assert "Proxy route controls are missing" not in source
    assert "Connection control is missing: " not in source


CONNECTION_UI_VARIANTS = [
    ("Employee", "Installer", "InstallerEmployeeView.xaml"),
    ("Owner", "Installer", "InstallerOwnerView.xaml"),
    ("Employee", "LaunchCenter", "LaunchCenterEmployeeView.xaml"),
    ("Owner", "LaunchCenter", "LaunchCenterOwnerView.xaml"),
]


@pytest.fixture(scope="module")
def connection_ui_bundles(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[tuple[str, str], Path]:
    """Build each real WPF view once for its connection-state contract."""
    root = tmp_path_factory.mktemp("connection-ui")
    return {
        (edition, product_role): _build_gui_bundle(
            root / f"{edition.lower()}-{product_role.lower()}",
            edition=edition,
            product_role=product_role,
        )
        for edition, product_role, _ in CONNECTION_UI_VARIANTS
    }


@pytest.mark.parametrize(
    ("edition", "product_role", "resource"),
    CONNECTION_UI_VARIANTS,
)
def test_four_view_connection_contract(
    connection_ui_bundles: dict[tuple[str, str], Path],
    edition: str,
    product_role: str,
    resource: str,
):
    """Changing a view route must expose the same usable HTTPS proxy form."""
    bundle = connection_ui_bundles[(edition, product_role)]
    executable = bundle / "LLMFoundationInstaller.exe"

    result = subprocess.run(
        [str(executable), "--ui-connection-state-json", "SingBoxHttps"],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, resource + ": " + result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["mode"] == "Proxy"
    assert value["proxy_type"] == "HTTPS"
    assert value["proxy_settings"] == "Visible"
    assert value["fields"] == ["server", "port", "login", "password"]
    assert value["save_enabled"] is True
    assert value["test_enabled"] is True
    assert value["stop_enabled"] is False
    assert value["status_text"].startswith(
        "Заполните сервер, порт, логин и пароль"
    )
    if edition == "Owner" and product_role == "LaunchCenter":
        assert value["route_detail"] == (
            "Launch Center управляет sing-box и временным прокси"
        )


@pytest.mark.parametrize("product_role", ["Installer", "LaunchCenter"])
def test_employee_connection_error_area_wraps_and_offers_managed_reset(
    connection_ui_bundles: dict[tuple[str, str], Path],
    product_role: str,
) -> None:
    """A long actionable failure must stay visible and resettable."""
    bundle = connection_ui_bundles[("Employee", product_role)]
    executable = bundle / "LLMFoundationInstaller.exe"

    result = subprocess.run(
        [str(executable), "--ui-connection-state-json", "SingBoxHttps"],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["status_wrapping"] == "Wrap"
    assert value["reset_enabled"] is True


def test_employee_launch_center_has_one_singbox_route_with_type_selector(
    connection_ui_bundles: dict[tuple[str, str], Path],
) -> None:
    """HTTP and HTTPS share one route without silently changing protocol."""
    bundle = connection_ui_bundles[("Employee", "LaunchCenter")]
    executable = bundle / "LLMFoundationInstaller.exe"

    for route, proxy_type in (
        ("SingBoxHttp", "HTTP"),
        ("SingBoxHttps", "HTTPS"),
    ):
        result = subprocess.run(
            [str(executable), "--ui-connection-state-json", route],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        value = json.loads(result.stdout)
        assert value["mode"] == "Proxy"
        assert value["proxy_type"] == proxy_type
        assert value["singbox_route_count"] == 1
        assert value["proxy_type_selector"] is True


@pytest.mark.parametrize(
    ("edition", "product_role", "resource"),
    [
        ("Employee", "LaunchCenter", "LaunchCenterEmployeeView.xaml"),
        ("Owner", "LaunchCenter", "LaunchCenterOwnerView.xaml"),
    ],
)
@pytest.mark.parametrize(
    ("route", "mode", "proxy_type", "proxy_settings"),
    [
        ("Direct", "Direct", None, "Collapsed"),
        ("VPN", "VPN", None, "Collapsed"),
        ("SingBoxHttp", "Proxy", "HTTP", "Visible"),
    ],
)
def test_launch_center_connection_state(
    connection_ui_bundles: dict[tuple[str, str], Path],
    edition: str,
    product_role: str,
    resource: str,
    route: str,
    mode: str,
    proxy_type: str | None,
    proxy_settings: str,
):
    """Launch Center route IDs must preserve the connection-profile mapping."""
    bundle = connection_ui_bundles[(edition, product_role)]
    executable = bundle / "LLMFoundationInstaller.exe"

    result = subprocess.run(
        [str(executable), "--ui-connection-state-json", route],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, resource + ": " + result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["mode"] == mode
    assert value["proxy_type"] == proxy_type
    assert value["proxy_settings"] == proxy_settings
    assert value["fields"] == ["server", "port", "login", "password"]


def test_gui_catalog_has_three_native_targets_and_no_fake_readiness(gui_bundle: Path):
    executable = gui_bundle / "LLMFoundationInstaller.exe"
    result = subprocess.run(
        [str(executable), "--catalog-json"],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)

    assert [row["id"] for row in payload["targets"]] == [
        "codex",
        "claude",
        "opencode",
    ]
    assert [row["client_id"] for row in payload["targets"]] == [
        "codex-cli",
        "claude-code",
        "opencode",
    ]
    assert all(row["package_state"] == "missing" for row in payload["targets"])
    assert payload["install_enabled"] is False
    assert payload["reason"] == (
        "Required edition packages are missing or changed"
    )


def test_gui_preflight_checks_clients_without_claiming_missing_packages(
    gui_bundle: Path,
):
    executable = gui_bundle / "LLMFoundationInstaller.exe"
    result = subprocess.run(
        [str(executable), "--preflight-json"],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["install_enabled"] is False
    assert all(
        row["package_state"] == "missing"
        for row in payload["targets"]
    )
    assert all(
        row["client_state"] in {"missing", "present_unbound"}
        for row in payload["targets"]
    )
    assert all(
        (row["detected_version"] is not None)
        == (row["client_state"] == "present_unbound")
        for row in payload["targets"]
    )


def test_store_only_codex_preflight_accepts_validated_store_record(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source)
    bundle = _build_gui_bundle(tmp_path / "bundle", package_source)
    safe_path = tmp_path / "safe-path"
    safe_path.mkdir()
    store_location = tmp_path / "WindowsApps" / (
        "OpenAI.Codex_26.721.4979.0_x64__2p2nqsd0c76g0"
    )
    store_location.mkdir(parents=True)
    record = _codex_store_record(
        tmp_path / "store-record.json",
        store_location,
    )
    environment = os.environ.copy()
    environment["PATH"] = str(safe_path)

    result = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--preflight-store-record-json",
            str(record),
        ],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    codex = next(row for row in payload["targets"] if row["id"] == "codex")
    assert codex["detected_version"] == "26.721.4979.0"
    assert codex["client_state"] == "ready"
    assert codex["client_state"] != "unsupported"


def test_store_only_codex_preflight_rejects_tampered_publisher_record(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source)
    bundle = _build_gui_bundle(tmp_path / "bundle", package_source)
    safe_path = tmp_path / "safe-path"
    safe_path.mkdir()
    store_location = tmp_path / "WindowsApps" / "OpenAI.Codex-fixture"
    store_location.mkdir(parents=True)
    record = _codex_store_record(
        tmp_path / "tampered-store-record.json",
        store_location,
    )
    tampered = json.loads(record.read_text(encoding="utf-8"))
    tampered["publisher"] = "CN=untrusted-fixture"
    _write_json(record, tampered)
    environment = os.environ.copy()
    environment["PATH"] = str(safe_path)

    result = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--preflight-store-record-json",
            str(record),
        ],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert "Store package identity differs from source lock" in result.stderr


def test_cli_fallback_codex_preflight_accepts_detected_version(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source)
    bundle = _build_gui_bundle(tmp_path / "bundle", package_source)
    safe_path = tmp_path / "safe-path"
    safe_path.mkdir()
    _compile_versioned_codex(safe_path / "codex.exe", "2.0.0")
    record = tmp_path / "missing-store-record.json"
    _write_json(record, {"present": False})
    environment = os.environ.copy()
    environment["PATH"] = str(safe_path)

    result = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--preflight-store-record-json",
            str(record),
        ],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    codex = next(row for row in payload["targets"] if row["id"] == "codex")
    assert codex["detected_version"] == "2.0.0"
    assert codex["client_state"] == "ready"
    assert codex["client_state"] != "unsupported"


def test_validated_store_codex_preflight_has_precedence_over_cli(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source)
    bundle = _build_gui_bundle(tmp_path / "bundle", package_source)
    safe_path = tmp_path / "safe-path"
    safe_path.mkdir()
    _compile_versioned_codex(safe_path / "codex.exe", "2.0.0")
    store_location = tmp_path / "WindowsApps" / (
        "OpenAI.Codex_26.721.4979.0_x64__2p2nqsd0c76g0"
    )
    store_location.mkdir(parents=True)
    record = _codex_store_record(
        tmp_path / "store-record.json",
        store_location,
    )
    environment = os.environ.copy()
    environment["PATH"] = str(safe_path)

    result = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--preflight-store-record-json",
            str(record),
        ],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    codex = next(row for row in payload["targets"] if row["id"] == "codex")
    assert codex["detected_version"] == "26.721.4979.0"
    assert codex["detected_version"] != "2.0.0"
    assert codex["client_state"] == "ready"

    home = tmp_path / "employee-home"
    home.mkdir()
    foundation = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--workflow-json",
            "plan",
            "codex",
            str(home),
            codex["detected_version"],
        ],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert foundation.returncode == 0, foundation.stdout + foundation.stderr
    assert json.loads(foundation.stdout)["status"] == "READY"


def test_platform_preflight_accepts_only_windows_x64_build_19041_or_newer(
    gui_bundle: Path,
):
    executable = gui_bundle / "LLMFoundationInstaller.exe"

    accepted = subprocess.run(
        [
            str(executable),
            "--evaluate-platform-json",
            "windows",
            "x64",
            "19041",
        ],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert json.loads(accepted.stdout) == {
        "status": "READY",
        "os": "windows",
        "architecture": "x64",
        "windows_build": 19041,
        "minimum_build": 19041,
        "admin_required": False,
        "reason": None,
    }

    for os_name, architecture, build, reason in (
        ("windows", "x86", "19041", "x64"),
        ("windows", "x64", "18363", "19041"),
        ("linux", "x64", "19041", "windows"),
    ):
        blocked = subprocess.run(
            [
                str(executable),
                "--evaluate-platform-json",
                os_name,
                architecture,
                build,
            ],
            cwd=gui_bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert blocked.returncode == 20
        payload = json.loads(blocked.stdout)
        assert payload["status"] == "BLOCKED"
        assert reason in payload["reason"].lower()


def test_gui_report_write_failure_is_non_fatal(
    gui_bundle: Path,
    tmp_path: Path,
):
    executable = gui_bundle / "LLMFoundationInstaller.exe"
    invalid_home = tmp_path / "home-is-a-file"
    invalid_home.write_text("not a directory", encoding="utf-8")

    result = subprocess.run(
        [str(executable), "--write-install-report-json", str(invalid_home)],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["written"] is False
    assert payload["path"] is None
    assert payload["error"]


def test_gui_accepts_only_build_verified_hash_bound_package(tmp_path: Path):
    package_source = tmp_path / "package-source"
    accepted = _accepted_package(package_source)
    bundle = _build_gui_bundle(tmp_path / "bundle", package_source)
    executable = bundle / "LLMFoundationInstaller.exe"

    result = subprocess.run(
        [str(executable), "--catalog-json"],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    states = {row["id"]: row["package_state"] for row in payload["targets"]}
    assert states == {
        "codex": "accepted",
        "claude": "missing",
        "opencode": "missing",
    }
    assert payload["install_enabled"] is False
    assert payload["reason"] == (
        "Required edition packages are missing or changed"
    )

    copied_asset = bundle / "packages" / "codex" / next(
        path.name for path in accepted.glob("*.zip")
    )
    copied_asset.write_bytes(b"tampered after build\n")
    tampered = subprocess.run(
        [str(executable), "--catalog-json"],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert tampered.returncode == 0, tampered.stdout + tampered.stderr
    tampered_payload = json.loads(tampered.stdout)
    tampered_states = {
        row["id"]: row["package_state"]
        for row in tampered_payload["targets"]
    }
    assert tampered_states["codex"] == "tampered"
    assert tampered_payload["install_enabled"] is False
    assert tampered_payload["reason"] == (
        "Required edition packages are missing or changed"
    )


def test_gui_preflight_keeps_accepted_base_installable_when_client_is_missing(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source)
    bundle = _build_gui_bundle(tmp_path / "bundle", package_source)
    safe_path = tmp_path / "safe-path"
    safe_path.mkdir()
    record = tmp_path / "missing-store-record.json"
    _write_json(record, {"present": False})
    environment = os.environ.copy()
    environment["PATH"] = str(safe_path)
    result = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--preflight-store-record-json",
            str(record),
        ],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    codex = next(row for row in payload["targets"] if row["id"] == "codex")
    assert codex["package_state"] == "accepted"
    assert codex["client_state"] == "missing"
    assert payload["install_enabled"] is False
    assert payload["reason"] == (
        "Required edition packages are missing or changed"
    )


def test_gui_accepts_native_codex_flat_release_evidence(tmp_path: Path):
    package_source = tmp_path / "package-source"
    _accepted_package(
        package_source,
        codex_flat_evidence=True,
    )

    bundle = _build_gui_bundle(tmp_path / "bundle", package_source)
    payload = json.loads(
        subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--catalog-json",
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=30,
        ).stdout
    )

    codex = next(row for row in payload["targets"] if row["id"] == "codex")
    assert codex["package_state"] == "accepted"


def test_gui_build_rejects_unaccepted_or_inconsistent_package(tmp_path: Path):
    package_source = tmp_path / "package-source"
    accepted = _accepted_package(package_source)
    acceptance_path = accepted / "package-acceptance.json"
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    acceptance["package_acceptance"] = "NOT_PASS"
    _write_json(acceptance_path, acceptance)

    output = tmp_path / "bundle"
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(output),
            "-PackageRoot",
            str(package_source),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode != 0
    assert "package acceptance" in (result.stdout + result.stderr).lower()


def test_gui_build_rejects_codex_evidence_bound_to_another_release(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    accepted = _accepted_package(package_source)
    evidence_path = accepted / "acceptance-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["release_binding"]["version"] = "9.9.9"
    _write_json(evidence_path, evidence)
    acceptance_path = accepted / "package-acceptance.json"
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    acceptance["acceptance_evidence"]["sha256"] = _sha256(evidence_path)
    acceptance["acceptance_evidence"]["bytes"] = evidence_path.stat().st_size
    _write_json(acceptance_path, acceptance)

    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(tmp_path / "bundle"),
            "-PackageRoot",
            str(package_source),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode != 0
    assert "release binding differs" in (
        result.stdout + result.stderr
    ).lower()


def test_owner_preview_marks_claude_candidate_without_provider_evidence(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source, "claude")
    bundle = _build_gui_bundle(
        tmp_path / "bundle",
        package_root=package_source,
    )
    result = subprocess.run(
        [str(bundle / "LLMFoundationInstaller.exe"), "--catalog-json"],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    states = {row["id"]: row["package_state"] for row in value["targets"]}
    assert states["claude"] == "owner_candidate"
    assert value["provider_eligibility"] == "NOT_PROVIDED"


def test_runtime_blocks_claude_when_provider_evidence_is_tampered(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source, "claude")
    evidence = _provider_eligibility_evidence(
        tmp_path / "provider-eligibility-evidence.json"
    )
    bundle = _build_gui_bundle(
        tmp_path / "bundle",
        package_source,
        evidence,
    )
    executable = bundle / "LLMFoundationInstaller.exe"

    accepted = subprocess.run(
        [str(executable), "--catalog-json"],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    accepted_payload = json.loads(accepted.stdout)
    accepted_states = {
        row["id"]: row["package_state"]
        for row in accepted_payload["targets"]
    }
    assert accepted_states["claude"] == "accepted"
    assert accepted_payload["provider_eligibility"] == "PASS"

    bundled_evidence = bundle / "provider-eligibility-evidence.json"
    bundled_evidence.write_text("tampered\n", encoding="utf-8")
    blocked = subprocess.run(
        [str(executable), "--catalog-json"],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert blocked.returncode == 0, blocked.stdout + blocked.stderr
    blocked_payload = json.loads(blocked.stdout)
    blocked_states = {
        row["id"]: row["package_state"]
        for row in blocked_payload["targets"]
    }
    assert blocked_states["claude"] == "owner_candidate"
    assert blocked_payload["provider_eligibility"] == "INVALID_OR_EXPIRED"
    assert blocked_payload["install_enabled"] is False
    assert blocked_payload["reason"] == (
        "Required edition packages are missing or changed"
    )


def test_owner_distribution_requires_all_targets(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source, "codex")
    output = tmp_path / "bundle"
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(output),
            "-PackageRoot",
            str(package_source),
            "-DistributionMode",
            "InternalUnsigned",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode != 0
    assert "owner target set differs from the edition contract" in (
        result.stdout + result.stderr
    ).lower()


def test_employee_edition_has_exact_two_target_contract(
    tmp_path: Path,
) -> None:
    package_source = tmp_path / "package-source"
    for target in ("codex", "opencode"):
        _accepted_package(package_source, target)

    bundle = _build_gui_bundle(
        tmp_path / "employee",
        package_root=package_source,
        distribution_mode="InternalUnsigned",
        edition="Employee",
    )
    manifest = json.loads(
        (bundle / "bundle-manifest.json").read_text(encoding="utf-8")
    )
    source_lock = json.loads(
        (bundle / "client-sources.lock.json").read_text(encoding="utf-8")
    )
    assert manifest["edition_id"] == "Employee"
    assert manifest["targets"] == ["codex", "opencode"]
    assert manifest["employee_distribution_allowed"] is True
    assert manifest["owner_controlled"] is False
    assert manifest["verdicts"]["PROGRAM_RELEASE"] == "2/2"
    assert manifest["verdicts"]["EMPLOYEE_INSTALLER_INTERNAL"] == "PASS"
    assert "claude" not in json.dumps(manifest, sort_keys=True).lower()
    assert "claude" not in json.dumps(source_lock, sort_keys=True).lower()

    catalog = subprocess.run(
        [str(bundle / "LLMFoundationInstaller.exe"), "--catalog-json"],
        cwd=bundle,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert catalog.returncode == 0, catalog.stdout + catalog.stderr
    catalog_value = json.loads(catalog.stdout)
    assert [row["id"] for row in catalog_value["targets"]] == [
        "codex",
        "opencode",
    ]
    assert catalog_value["install_enabled"] is True


def test_employee_edition_rejects_extra_claude_target(
    tmp_path: Path,
) -> None:
    package_source = tmp_path / "package-source"
    for target in ("codex", "claude", "opencode"):
        _accepted_package(package_source, target)
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            "-Edition",
            "Employee",
            "-ProductRole",
            "Installer",
            "-OutputRoot",
            str(tmp_path / "employee-extra"),
            "-PackageRoot",
            str(package_source),
            "-DistributionMode",
            "InternalUnsigned",
        ],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "employee target set differs" in (
        result.stdout + result.stderr
    ).lower()


def test_owner_edition_keeps_claude_provider_gate_visible(
    tmp_path: Path,
) -> None:
    package_source = tmp_path / "package-source"
    for target in ("codex", "claude", "opencode"):
        _accepted_package(package_source, target)

    bundle = _build_gui_bundle(
        tmp_path / "owner",
        package_root=package_source,
        distribution_mode="InternalUnsigned",
        edition="Owner",
    )
    manifest = json.loads(
        (bundle / "bundle-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["edition_id"] == "Owner"
    assert manifest["targets"] == ["claude", "codex", "opencode"]
    assert manifest["employee_distribution_allowed"] is False
    assert manifest["owner_controlled"] is True
    assert manifest["owner_claude_state"] == "OWNER_CANDIDATE"
    assert manifest["verdicts"]["FULL_RELEASE_CLAUDE"] == "NOT_PASS"
    assert manifest["verdicts"]["PROGRAM_RELEASE"] == "2/3"

    catalog = subprocess.run(
        [str(bundle / "LLMFoundationInstaller.exe"), "--catalog-json"],
        cwd=bundle,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert catalog.returncode == 0, catalog.stdout + catalog.stderr
    catalog_value = json.loads(catalog.stdout)
    states = {
        row["id"]: row["package_state"]
        for row in catalog_value["targets"]
    }
    assert states == {
        "codex": "accepted",
        "claude": "owner_candidate",
        "opencode": "accepted",
    }
    assert catalog_value["install_enabled"] is True
    assert catalog_value["provider_eligibility"] == "NOT_PROVIDED"


def test_owner_provider_evidence_promotes_claude_without_distribution(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    for target in ("codex", "claude", "opencode"):
        _accepted_package(package_source, target)

    candidate = _build_gui_bundle(
        tmp_path / "missing-evidence",
        package_root=package_source,
        distribution_mode="InternalUnsigned",
    )
    candidate_manifest = json.loads(
        (candidate / "bundle-manifest.json").read_text(encoding="utf-8")
    )
    assert candidate_manifest["owner_claude_state"] == "OWNER_CANDIDATE"
    assert candidate_manifest["employee_distribution_allowed"] is False

    evidence = _provider_eligibility_evidence(
        tmp_path / "provider-eligibility-evidence.json"
    )
    internal = _build_gui_bundle(
        tmp_path / "internal-unsigned",
        package_source,
        evidence,
        "InternalUnsigned",
    )
    manifest = json.loads(
        (internal / "bundle-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["distribution_mode"] == "internal_unsigned"
    assert manifest["signature"] == "unsigned-internal"
    assert manifest["employee_release"] is False
    assert manifest["employee_distribution_allowed"] is False
    assert manifest["public_distribution_allowed"] is False
    assert manifest["distribution_allowed"] is False
    assert manifest["owner_claude_state"] == "PROVIDER_READY"
    assert manifest["windows_warning_expected"] is True
    assert manifest["foundation_release"]["package_acceptance"] == "PASS"
    assert manifest["foundation_release"]["engine_version"] == (
        FOUNDATION_VERSION
    )
    foundation_acceptance = json.loads(
        (
            package_source
            / "foundation"
            / "package-acceptance.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["artifacts"][
        "engine/engine-manifest.json"
    ] == foundation_acceptance["engine_files"]["engine-manifest.json"]
    assert manifest["verdicts"] == {
        "FULL_RELEASE_CODEX": "PASS",
        "FULL_RELEASE_CLAUDE": "PASS",
        "FULL_RELEASE_OPENCODE": "PASS",
        "PROGRAM_RELEASE": "3/3",
        "OWNER_INSTALLER_INTERNAL": "OWNER_CANDIDATE",
        "PUBLIC_SIGNED_RELEASE": "NOT_APPLICABLE",
    }

    public_signed = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(tmp_path / "public-signed"),
            "-PackageRoot",
            str(package_source),
            "-ProviderEligibilityEvidence",
            str(evidence),
            "-DistributionMode",
            "PublicSigned",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert public_signed.returncode != 0
    assert "requires a code-signing certificate" in (
        public_signed.stdout + public_signed.stderr
    ).lower()


def test_employee_distribution_requires_immutable_foundation_package(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    for target in ("codex", "claude", "opencode"):
        _accepted_package(package_source, target)
    foundation = package_source / "foundation"
    detached = tmp_path / "detached-foundation"
    foundation.rename(detached)
    evidence = _provider_eligibility_evidence(
        tmp_path / "provider.json"
    )

    missing = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(tmp_path / "missing-foundation"),
            "-PackageRoot",
            str(package_source),
            "-ProviderEligibilityEvidence",
            str(evidence),
            "-DistributionMode",
            "InternalUnsigned",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert missing.returncode != 0
    assert "foundation package" in (
        missing.stdout + missing.stderr
    ).lower()

    verification = detached / "release-verification.json"
    payload = json.loads(verification.read_text(encoding="utf-8"))
    payload["release_state"]["immutable"] = False
    payload["evidence_body_sha256"] = _evidence_body_sha256(payload)
    _write_json(verification, payload)
    acceptance = detached / "package-acceptance.json"
    acceptance_payload = json.loads(acceptance.read_text(encoding="utf-8"))
    acceptance_payload["release_verification"]["sha256"] = _sha256(
        verification
    )
    acceptance_payload["release_verification"]["bytes"] = (
        verification.stat().st_size
    )
    _write_json(acceptance, acceptance_payload)
    mutable = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(tmp_path / "mutable-foundation"),
            "-PackageRoot",
            str(package_source),
            "-FoundationPackageRoot",
            str(detached),
            "-ProviderEligibilityEvidence",
            str(evidence),
            "-DistributionMode",
            "InternalUnsigned",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert mutable.returncode != 0
    assert "foundation release verification" in (
        mutable.stdout + mutable.stderr
    ).lower()


def test_non_public_distribution_rejects_signing_certificate(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    for target in ("codex", "claude", "opencode"):
        _accepted_package(package_source, target)
    evidence = _provider_eligibility_evidence(
        tmp_path / "provider-eligibility-evidence.json"
    )
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(tmp_path / "ambiguous-signing-mode"),
            "-PackageRoot",
            str(package_source),
            "-ProviderEligibilityEvidence",
            str(evidence),
            "-DistributionMode",
            "InternalUnsigned",
            "-SigningCertificateThumbprint",
            "0000000000000000000000000000000000000000",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode != 0
    assert "only valid for publicsigned" in (
        result.stdout + result.stderr
    ).lower()


def test_employee_distribution_rejects_unverified_release_record(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    for target in ("codex", "claude", "opencode"):
        _accepted_package(package_source, target)
    verification = (
        package_source
        / "opencode"
        / "release-verification.json"
    )
    payload = json.loads(verification.read_text(encoding="utf-8"))
    payload["release_state"]["immutable"] = False
    payload["evidence_body_sha256"] = _evidence_body_sha256(payload)
    _write_json(verification, payload)
    acceptance = (
        package_source
        / "opencode"
        / "package-acceptance.json"
    )
    acceptance_payload = json.loads(
        acceptance.read_text(encoding="utf-8")
    )
    acceptance_payload["release_verification"]["sha256"] = _sha256(
        verification
    )
    acceptance_payload["release_verification"]["bytes"] = (
        verification.stat().st_size
    )
    _write_json(acceptance, acceptance_payload)

    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(tmp_path / "bundle"),
            "-PackageRoot",
            str(package_source),
            "-ProviderEligibilityEvidence",
            str(
                _provider_eligibility_evidence(
                    tmp_path / "provider.json"
                )
            ),
            "-DistributionMode",
            "InternalUnsigned",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode != 0
    assert "release verification" in (
        result.stdout + result.stderr
    ).lower()


def test_provider_eligibility_rejects_expired_or_pii_bearing_evidence(
    tmp_path: Path,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expired = _provider_eligibility_evidence(
        tmp_path / "expired.json",
        reviewed_at=now - timedelta(days=8),
        expires_at=now - timedelta(days=1),
    )
    expired_result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(tmp_path / "expired-output"),
            "-ProviderEligibilityEvidence",
            str(expired),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert expired_result.returncode != 0
    assert "provider eligibility evidence is expired" in (
        expired_result.stdout + expired_result.stderr
    ).lower()

    pii = _provider_eligibility_evidence(
        tmp_path / "pii.json",
        extra_top_level={"employee_names": ["Employee One"]},
    )
    pii_result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(tmp_path / "pii-output"),
            "-ProviderEligibilityEvidence",
            str(pii),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert pii_result.returncode != 0
    assert "unexpected or personal-data fields" in (
        pii_result.stdout + pii_result.stderr
    ).lower()

    noncanonical = _provider_eligibility_evidence(
        tmp_path / "noncanonical-timestamp.json"
    )
    noncanonical_value = json.loads(
        noncanonical.read_text(encoding="utf-8")
    )
    noncanonical_value["reviewed_at_utc"] = (
        noncanonical_value["reviewed_at_utc"][:-1] + "+00:00"
    )
    _write_json(noncanonical, noncanonical_value)
    noncanonical_result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(tmp_path / "noncanonical-output"),
            "-ProviderEligibilityEvidence",
            str(noncanonical),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert noncanonical_result.returncode != 0
    assert "timestamps are not canonical utc" in (
        noncanonical_result.stdout + noncanonical_result.stderr
    ).lower()

    schema_string = _provider_eligibility_evidence(
        tmp_path / "schema-string.json"
    )
    schema_value = json.loads(schema_string.read_text(encoding="utf-8"))
    schema_value["schema_version"] = "1"
    _write_json(schema_string, schema_value)
    schema_result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(tmp_path / "schema-output"),
            "-ProviderEligibilityEvidence",
            str(schema_string),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert schema_result.returncode != 0
    assert "schema is unsupported" in (
        schema_result.stdout + schema_result.stderr
    ).lower()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_provider_eligibility_generator_is_fail_closed_and_pii_free(
    tmp_path: Path,
    powershell: str,
):
    output = tmp_path / f"eligibility-{Path(powershell).stem.lower()}.json"
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(
            REPOSITORY_ROOT
            / "tools"
            / "new-provider-eligibility-evidence.ps1"
        ),
        "-OutputPath",
        str(output),
    ]
    missing = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert missing.returncode != 0
    assert "all provider eligibility confirmations are required" in (
        missing.stdout + missing.stderr
    ).lower()
    assert not output.exists()

    generated = subprocess.run(
        command
        + [
            "-ConfirmEmployeeLocationEligibility",
            "-ConfirmOrganizationEligibility",
            "-ConfirmIndividualAccounts",
            "-ConfirmNoRegionOrBanBypass",
            "-ConfirmNoUnattendedConsumerAutomation",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    value = json.loads(output.read_text(encoding="utf-8"))
    assert set(value) == {
        "schema_version",
        "reviewed_at_utc",
        "expires_at_utc",
        "sources",
        "claude",
    }
    assert value["claude"] == {
        "employee_location_eligibility_verified": True,
        "organization_eligibility_verified": True,
        "individual_accounts_only": True,
        "transport_not_used_for_region_or_ban_bypass": True,
        "unattended_consumer_automation": False,
    }
    assert "employee" not in value
    assert "country" not in value
    reviewed = datetime.strptime(
        value["reviewed_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    expires = datetime.strptime(
        value["expires_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    assert timedelta(days=6, hours=23) < expires - reviewed <= timedelta(
        days=7
    )

    overwrite = subprocess.run(
        command
        + [
            "-ConfirmEmployeeLocationEligibility",
            "-ConfirmOrganizationEligibility",
            "-ConfirmIndividualAccounts",
            "-ConfirmNoRegionOrBanBypass",
            "-ConfirmNoUnattendedConsumerAutomation",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert overwrite.returncode != 0
    assert "output already exists" in (
        overwrite.stdout + overwrite.stderr
    ).lower()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_provider_eligibility_is_hash_bound_into_bundle_manifest(
    tmp_path: Path,
    powershell: str,
):
    evidence = _provider_eligibility_evidence(
        tmp_path / "provider-eligibility-evidence.json"
    )
    bundle = tmp_path / f"bundle-{Path(powershell).stem.lower()}"
    built = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            *DEFAULT_GUI_CONTRACT_ARGUMENTS,
            "-OutputRoot",
            str(bundle),
            "-ProviderEligibilityEvidence",
            str(evidence),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    manifest = json.loads(
        (bundle / "bundle-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["provider_eligibility"] == {
        "status": "PASS",
        "sha256": _sha256(evidence),
        "reviewed_at_utc": json.loads(
            evidence.read_text(encoding="utf-8")
        )["reviewed_at_utc"],
        "expires_at_utc": json.loads(
            evidence.read_text(encoding="utf-8")
        )["expires_at_utc"],
        "contains_personal_data": False,
    }
    bundled = bundle / "provider-eligibility-evidence.json"
    assert bundled.read_bytes() == evidence.read_bytes()
    assert manifest["artifacts"][bundled.name] == {
        "sha256": _sha256(bundled),
        "bytes": bundled.stat().st_size,
    }


def test_gui_runs_real_foundation_workflow_and_preserves_auth(tmp_path: Path):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source)
    bundle = _build_gui_bundle(tmp_path / "bundle", package_source)
    executable = bundle / "LLMFoundationInstaller.exe"
    home = tmp_path / "employee-home"
    auth = home / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text('{"token":"preserve"}\n', encoding="utf-8")

    def run(command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(executable),
                "--workflow-json",
                command,
                "codex",
                str(home),
                "1.0.0-test",
            ],
            cwd=bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=60,
        )

    plan = run("plan")
    assert plan.returncode == 0, plan.stdout + plan.stderr
    assert json.loads(plan.stdout)["status"] == "READY"
    assert not (home / ".codex" / "AGENTS.md").exists()

    install = run("install")
    assert install.returncode == 0, install.stdout + install.stderr
    assert json.loads(install.stdout)["status"] == "INSTALLED"
    assert (home / ".codex" / "AGENTS.md").read_text(
        encoding="utf-8"
    ) == "# accepted candidate\n"
    assert auth.read_text(encoding="utf-8") == '{"token":"preserve"}\n'

    doctor = run("doctor")
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert json.loads(doctor.stdout)["status"] == "HEALTHY"

    rollback = run("rollback")
    assert rollback.returncode == 0, rollback.stdout + rollback.stderr
    assert json.loads(rollback.stdout)["status"] == "ROLLED_BACK"
    assert not (home / ".codex" / "AGENTS.md").exists()
    assert auth.read_text(encoding="utf-8") == '{"token":"preserve"}\n'


def test_gui_executable_is_a_standalone_installer_payload(tmp_path: Path):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source)
    bundle = _build_gui_bundle(tmp_path / "bundle", package_source)
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    executable = standalone / "LLMFoundationInstaller.exe"
    shutil.copy2(bundle / executable.name, executable)

    self_test = subprocess.run(
        [str(executable), "--self-test-json"],
        cwd=standalone,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert self_test.returncode == 0, self_test.stdout + self_test.stderr
    assert json.loads(self_test.stdout)["engine_validated"] is True

    catalog = subprocess.run(
        [str(executable), "--catalog-json"],
        cwd=standalone,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert catalog.returncode == 0, catalog.stdout + catalog.stderr
    states = {
        row["id"]: row["package_state"]
        for row in json.loads(catalog.stdout)["targets"]
    }
    assert states["codex"] == "accepted"

    home = tmp_path / "standalone-home"
    home.mkdir()
    install = subprocess.run(
        [
            str(executable),
            "--workflow-json",
            "install",
            "codex",
            str(home),
            "1.0.0-test",
        ],
        cwd=standalone,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    assert json.loads(install.stdout)["status"] == "INSTALLED"
    assert (home / ".codex" / "AGENTS.md").is_file()


def test_gui_workflow_fails_closed_on_malformed_client_version(tmp_path: Path):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source)
    bundle = _build_gui_bundle(tmp_path / "bundle", package_source)
    executable = bundle / "LLMFoundationInstaller.exe"
    home = tmp_path / "employee-home"
    home.mkdir()
    result = subprocess.run(
        [
            str(executable),
            "--workflow-json",
            "plan",
            "codex",
            str(home),
            "0.0",
        ],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert result.returncode == 10
    assert json.loads(result.stdout)["code"] == "UNSUPPORTED_CLIENT"


@pytest.mark.parametrize("mode", ["Direct", "VPN"])
def test_connection_profile_treats_direct_and_vpn_as_ready_without_proxy(
    gui_bundle: Path,
    tmp_path: Path,
    mode: str,
):
    executable = gui_bundle / "LLMFoundationInstaller.exe"
    home = tmp_path / f"home-{mode}"
    home.mkdir()
    profile = tmp_path / f"{mode}.json"
    _write_json(
        profile,
        {
            "schema_version": 1,
            "mode": mode,
            "proxy": None,
        },
    )
    result = subprocess.run(
        [
            str(executable),
            "--save-connection-json",
            str(home),
            str(profile),
        ],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "READY"
    assert payload["profile"] == {
        "schema_version": 1,
        "mode": mode,
        "proxy": None,
    }
    assert not (
        home / ".llm-foundation" / "connection.cred"
    ).exists()


@pytest.mark.parametrize("proxy_type", ["HTTP", "HTTPS", "SOCKS5"])
def test_authenticated_connection_profiles_use_dpapi_and_never_store_password(
    gui_bundle: Path,
    tmp_path: Path,
    proxy_type: str,
):
    executable = gui_bundle / "LLMFoundationInstaller.exe"
    home = tmp_path / f"home-{proxy_type}"
    home.mkdir()
    profile = tmp_path / f"{proxy_type}.json"
    _write_json(
        profile,
        {
            "schema_version": 1,
            "mode": "Proxy",
            "proxy": {
                "type": proxy_type,
                "host": "proxy.example.com",
                "port": 8443,
                "auth": {
                    "mode": "UsernamePassword",
                    "username": "employee",
                },
            },
        },
    )
    password = "never-write-this-password"
    result = subprocess.run(
        [
            str(executable),
            "--save-connection-json",
            str(home),
            str(profile),
        ],
        cwd=gui_bundle,
        input=password + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    state_root = home / ".llm-foundation"
    profile_bytes = (state_root / "connection.json").read_bytes()
    credential_bytes = (state_root / "connection.cred").read_bytes()
    assert password.encode() not in profile_bytes
    assert password.encode() not in credential_bytes
    assert password not in result.stdout
    assert json.loads(result.stdout)["profile"]["proxy"]["type"] == proxy_type

    loaded = subprocess.run(
        [str(executable), "--connection-json", str(home)],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert loaded.returncode == 0, loaded.stdout + loaded.stderr
    assert json.loads(loaded.stdout)["credential_state"] == "protected"
    assert password not in loaded.stdout


@pytest.mark.parametrize(
    ("proxy_type", "expected_scheme"),
    [
        ("HTTP", "http"),
        ("HTTPS", "https"),
        ("SOCKS5", "socks5h"),
    ],
)
def test_connection_process_environment_is_type_aware_and_redacted(
    gui_bundle: Path,
    tmp_path: Path,
    proxy_type: str,
    expected_scheme: str,
):
    executable = gui_bundle / "LLMFoundationInstaller.exe"
    home = tmp_path / f"env-home-{proxy_type}"
    home.mkdir()
    profile = tmp_path / f"env-{proxy_type}.json"
    _write_json(
        profile,
        {
            "schema_version": 1,
            "mode": "Proxy",
            "proxy": {
                "type": proxy_type,
                "host": "proxy.example.com",
                "port": 8443,
                "auth": {
                    "mode": "UsernamePassword",
                    "username": "employee",
                },
            },
        },
    )
    password = "redact-this-password"
    saved = subprocess.run(
        [
            str(executable),
            "--save-connection-json",
            str(home),
            str(profile),
        ],
        cwd=gui_bundle,
        input=password + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert saved.returncode == 0, saved.stdout + saved.stderr

    described = subprocess.run(
        [
            str(executable),
            "--connection-environment-json",
            str(home),
        ],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )

    assert described.returncode == 0, described.stdout + described.stderr
    assert json.loads(described.stdout) == {
        "mode": "Proxy",
        "uses_proxy": True,
        "proxy_type": proxy_type,
        "proxy_scheme": expected_scheme,
        "auth_mode": "UsernamePassword",
        "credential_applied": True,
    }
    assert password not in described.stdout
    assert "employee" not in described.stdout
    assert "proxy.example.com" not in described.stdout


def test_connection_probe_uses_saved_direct_profile_in_a_real_child_process(
    gui_bundle: Path,
    tmp_path: Path,
):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        executable = gui_bundle / "LLMFoundationInstaller.exe"
        home = tmp_path / "probe-home"
        home.mkdir()
        profile = tmp_path / "probe-direct.json"
        _write_json(
            profile,
            {
                "schema_version": 1,
                "mode": "Direct",
                "proxy": None,
            },
        )
        saved = subprocess.run(
            [
                str(executable),
                "--save-connection-json",
                str(home),
                str(profile),
            ],
            cwd=gui_bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert saved.returncode == 0, saved.stdout + saved.stderr

        environment = os.environ.copy()
        environment["HTTP_PROXY"] = "http://stale.invalid:8080"
        endpoint = f"http://127.0.0.1:{server.server_port}/"
        probed = subprocess.run(
            [
                str(executable),
                "--probe-connection-json",
                str(home),
                endpoint,
            ],
            cwd=gui_bundle,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )

        assert probed.returncode == 0, probed.stdout + probed.stderr
        payload = json.loads(probed.stdout)
        assert payload["status"] == "READY"
        assert payload["mode"] == "Direct"
        assert payload["uses_proxy"] is False
        assert payload["endpoint_host"] == "127.0.0.1"
        assert "stale.invalid" not in probed.stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_connection_route_probe_dispatches_proxy_to_singbox(
    gui_bundle: Path,
    tmp_path: Path,
) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        executable = gui_bundle / "LLMFoundationInstaller.exe"
        home = tmp_path / "route-probe-home"
        home.mkdir()
        profile = tmp_path / "route-probe-proxy.json"
        _write_json(
            profile,
            {
                "schema_version": 1,
                "mode": "Proxy",
                "proxy": {
                    "type": "HTTP",
                    "host": "proxy.example.test",
                    "port": 8080,
                    "auth": {"mode": "None", "username": None},
                },
            },
        )
        saved = subprocess.run(
            [
                str(executable),
                "--save-connection-json",
                str(home),
                str(profile),
            ],
            cwd=gui_bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert saved.returncode == 0, saved.stdout + saved.stderr
        endpoint = f"http://127.0.0.1:{server.server_port}/route-check"

        proxy_probe = subprocess.run(
            [
                str(executable),
                "--test-connection-route-json",
                str(home),
                "SingBoxHttp",
                endpoint,
            ],
            cwd=gui_bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert proxy_probe.stdout.strip(), proxy_probe.stderr
        proxy_value = json.loads(proxy_probe.stdout)
        assert proxy_probe.returncode == 20
        assert proxy_value["status"] == "FAILED"
        assert proxy_value["uses_proxy"] is True
        assert (
            proxy_value["reason"]
            == "RUNTIME_BUNDLE_ARCHIVE_MISSING"
        )

        direct_profile = tmp_path / "route-probe-direct.json"
        _write_json(
            direct_profile,
            {"schema_version": 1, "mode": "Direct", "proxy": None},
        )
        saved = subprocess.run(
            [
                str(executable),
                "--save-connection-json",
                str(home),
                str(direct_profile),
            ],
            cwd=gui_bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert saved.returncode == 0, saved.stdout + saved.stderr
        direct_probe = subprocess.run(
            [
                str(executable),
                "--test-connection-route-json",
                str(home),
                "Direct",
                endpoint,
            ],
            cwd=gui_bundle,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert direct_probe.returncode == 0, (
            direct_probe.stdout + direct_probe.stderr
        )
        direct_value = json.loads(direct_probe.stdout)
        assert direct_value["status"] == "READY"
        assert direct_value["uses_proxy"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_connection_route_failure_messages_are_russian_and_actionable() -> None:
    source = (
        REPOSITORY_ROOT / "src" / "gui" / "InstallerApp.cs"
    ).read_text(encoding="utf-8")
    compact = " ".join(source.split())

    assert "DescribeTestFailure(reason)" in compact
    expected_actions = {
        "RUNTIME_BUNDLE_ARCHIVE_MISSING": (
            "Распакуйте весь ZIP: архив runtime должен лежать рядом"
        ),
        "RUNTIME_ARCHIVE_INTEGRITY_FAILED": (
            "Архив runtime повреждён"
        ),
        "RUNTIME_INSTALL_FAILED": (
            "Runtime SingBox не удалось установить"
        ),
        "CONFIG_CHECK_FAILED": (
            "Проверьте сервер, порт, логин и пароль"
        ),
        "LOCAL_PROXY_NOT_READY": (
            "SingBox не запустил локальный прокси"
        ),
        "ROUTE_PROBE_FAILED": (
            "запрос через него не прошёл"
        ),
        "SESSION_CLEANUP_FAILED": (
            "Не удалось безопасно очистить временную сессию SingBox"
        ),
    }
    for reason, action in expected_actions.items():
        assert reason in source
        assert action in source
    assert "server, port, login" not in source


def test_invalid_proxy_does_not_overwrite_last_known_good_profile(
    gui_bundle: Path,
    tmp_path: Path,
):
    executable = gui_bundle / "LLMFoundationInstaller.exe"
    home = tmp_path / "home"
    home.mkdir()
    good = tmp_path / "good.json"
    _write_json(
        good,
        {"schema_version": 1, "mode": "VPN", "proxy": None},
    )
    saved = subprocess.run(
        [
            str(executable),
            "--save-connection-json",
            str(home),
            str(good),
        ],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert saved.returncode == 0
    saved_bytes = (
        home / ".llm-foundation" / "connection.json"
    ).read_bytes()

    invalid = tmp_path / "invalid.json"
    _write_json(
        invalid,
        {
            "schema_version": 1,
            "mode": "Proxy",
            "proxy": {
                "type": "HTTPS",
                "host": "https://bad host/",
                "port": 0,
                "auth": {"mode": "None", "username": None},
            },
        },
    )
    rejected = subprocess.run(
        [
            str(executable),
            "--save-connection-json",
            str(home),
            str(invalid),
        ],
        cwd=gui_bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert rejected.returncode != 0
    assert (
        home / ".llm-foundation" / "connection.json"
    ).read_bytes() == saved_bytes


def test_gui_source_contains_no_reverse_flow_or_secret_collection():
    source_root = REPOSITORY_ROOT / "src" / "gui"
    source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in sorted(source_root.glob("*"))
        if path.is_file()
    )
    forbidden = (
        "httpclient",
        "webclient",
        "feedback-pending",
        "auth.json",
        "api_key",
        "proxy-authorization",
        "session-report",
        "--verbose",
    )
    assert not [token for token in forbidden if token in source]


def test_gui_install_workflow_is_non_blocking_and_locally_reported():
    source = (
        REPOSITORY_ROOT / "src" / "gui" / "InstallerApp.cs"
    ).read_text(encoding="utf-8")
    xaml = (
        REPOSITORY_ROOT / "src" / "gui" / "InstallerView.xaml"
    ).read_text(encoding="utf-8")
    for required in (
        "RunPlanAndInstallAsync",
        "await RunFoundationAsync",
        "return Task.Run",
        'Path.Combine(\n                Path.GetFullPath(home),\n'
        '                ".llm-foundation",\n'
        '                "reports"',
        '"official-client-downloads-only"',
        '"reverse_flow", false',
    ):
        assert required in source
    for required in (
        'x:Name="InstallProgress"',
        'x:Name="Step1Badge"',
        'x:Name="Step4Badge"',
        'x:Name="CodexStatusBadge"',
    ):
        assert required in xaml


def test_gui_workflow_bootstraps_clients_and_exposes_seven_real_stages():
    source = (
        REPOSITORY_ROOT / "src" / "gui" / "InstallerApp.cs"
    ).read_text(encoding="utf-8")
    xaml = (
        REPOSITORY_ROOT / "src" / "gui" / "InstallerView.xaml"
    ).read_text(encoding="utf-8")
    for required in (
        "ClientBootstrap.PlanTarget",
        "verified.clients.First",
        "ClientBootstrap.Install",
        "ClientBootstrap.OpenStoreSource",
        "RunClientBootstrapAsync",
        "OpenAuthorizationActions",
        '"official-client-downloads-only"',
    ):
        assert required in source
    for required in (
        'x:Name="Step5Badge"',
        'x:Name="Step6Badge"',
        'x:Name="Step7Badge"',
        'Text="Клиенты"',
        'Text="Авторизация"',
        'Text="Готово"',
    ):
        assert required in xaml
    assert "winget search" not in source.lower()
    assert "winget install codex" not in source.lower()


def test_owner_candidate_is_installable_only_in_owner_edition():
    source = (
        REPOSITORY_ROOT / "src" / "gui" / "InstallerApp.cs"
    ).read_text(encoding="utf-8")

    assert "IsInstallableTarget" in source
    assert "edition.owner_controlled" in source
    assert 'row.package_state == "owner_candidate"' in source


def test_owner_internal_build_carries_nonreleasable_claude_candidate(
    tmp_path: Path,
) -> None:
    package_source = tmp_path / "package-source"
    _accepted_package(package_source, "codex")
    _accepted_package(package_source, "opencode")
    candidate = _owner_claude_candidate(
        tmp_path / "owner-candidate-source"
    )

    bundle = _build_gui_bundle(
        tmp_path / "owner-bundle",
        package_root=package_source,
        owner_candidate_root=candidate,
        distribution_mode="InternalUnsigned",
        edition="Owner",
    )
    manifest = json.loads(
        (bundle / "bundle-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["embedded_target_count"] == 3
    assert manifest["owner_claude_state"] == "OWNER_CANDIDATE"
    assert manifest["distribution_allowed"] is False
    assert manifest["employee_distribution_allowed"] is False
    assert manifest["verdicts"]["FULL_RELEASE_CLAUDE"] == "NOT_PASS"
    assert manifest["verdicts"]["PROGRAM_RELEASE"] == "2/3"
    assert (
        manifest["owner_candidate"]["target"] == "claude"
    )
    assert (
        manifest["owner_candidate"]["FULL_RELEASE_CLAUDE"]
        == "NOT_PASS"
    )
    assert (
        bundle / "packages" / "claude" / "NON_RELEASABLE.txt"
    ).is_file()

    catalog = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--catalog-json",
        ],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert catalog.returncode == 0, catalog.stdout + catalog.stderr
    states = {
        row["id"]: row["package_state"]
        for row in json.loads(catalog.stdout)["targets"]
    }
    assert states == {
        "codex": "accepted",
        "claude": "owner_candidate",
        "opencode": "accepted",
    }


def test_owner_candidate_input_is_owner_only_and_hash_bound(
    tmp_path: Path,
) -> None:
    candidate = _owner_claude_candidate(tmp_path / "candidate-source")
    employee_output = tmp_path / "employee"
    employee = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            "-Edition",
            "Employee",
            "-ProductRole",
            "Installer",
            "-OutputRoot",
            str(employee_output),
            "-OwnerCandidateRoot",
            str(candidate),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert employee.returncode != 0
    assert "only valid for Owner edition" in (
        employee.stdout + employee.stderr
    )

    (candidate / "claude-base-1.0.0.zip").write_bytes(b"tampered")
    owner_output = tmp_path / "owner"
    owner = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            "-Edition",
            "Owner",
            "-ProductRole",
            "Installer",
            "-OutputRoot",
            str(owner_output),
            "-OwnerCandidateRoot",
            str(candidate),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert owner.returncode != 0
    assert "Owner candidate" in owner.stdout + owner.stderr


def test_employee_guide_does_not_present_connection_modes_as_policy_bypass():
    employee = (
        REPOSITORY_ROOT / "docs" / "ИНСТРУКЦИЯ-СОТРУДНИКУ.md"
    ).read_text(encoding="utf-8").lower()
    owner = (
        REPOSITORY_ROOT / "docs" / "ИНСТРУКЦИЯ-ВЛАДЕЛЬЦУ.md"
    ).read_text(encoding="utf-8").lower()
    employee = " ".join(employee.split())
    owner = " ".join(owner.split())
    assert "не подтверждает право использования" in employee
    assert "не должен применяться для обхода" in employee
    assert "отдельную допустимую учётную запись" in employee
    assert "https://www.anthropic.com/supported-countries" in owner
    assert "https://www.anthropic.com/legal/consumer-terms" in owner
    assert "обход региона" in owner
    assert "без участия человека" in owner
    assert "new-provider-eligibility-evidence.ps1" in owner
    assert "providereligibilityevidence" in owner
    assert "7 суток" in owner


def test_installer_ui_makes_provider_policy_boundary_visible():
    xaml = (
        REPOSITORY_ROOT / "src" / "gui" / "InstallerView.xaml"
    ).read_text(encoding="utf-8").lower()
    source = (
        REPOSITORY_ROOT / "src" / "gui" / "InstallerApp.cs"
    ).read_text(encoding="utf-8")
    assert "vpn/proxy — это только транспорт" in xaml
    assert "не подтверждает доступность сервиса в регионе" in xaml
    assert "Допуск провайдера истёк или недействителен" in source
    assert "Установка Claude заблокирована" in source
