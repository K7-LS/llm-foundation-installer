from __future__ import annotations

import ctypes
from datetime import datetime, timedelta, timezone
import hashlib
import http.server
import json
import os
import shutil
import struct
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe")
POWERSHELLS = [
    value
    for value in (shutil.which("pwsh"), shutil.which("powershell.exe"))
    if value
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_gui_bundle(
    output: Path,
    package_root: Path | None = None,
    provider_eligibility_evidence: Path | None = None,
) -> Path:
    arguments = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
        "-OutputRoot",
        str(output),
    ]
    if package_root is not None:
        arguments.extend(["-PackageRoot", str(package_root)])
    if provider_eligibility_evidence is not None:
        arguments.extend(
            [
                "-ProviderEligibilityEvidence",
                str(provider_eligibility_evidence),
            ]
        )
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


def _accepted_package(
    root: Path,
    target: str = "codex",
    *,
    codex_flat_evidence: bool = True,
) -> Path:
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
        "foundation_engine_version": "0.2.0",
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
    _write_json(release, release_value)
    evidence = package_root / "acceptance-evidence.json"
    if target == "codex" and codex_flat_evidence:
        binding_keys = (
            "target",
            "version",
            "tag",
            "asset",
            "package_manifest_sha256",
            "components_lock_sha256",
            "source",
            "foundation_engine_version",
            "foundation_engine_manifest_sha256",
        )
        evidence_value = {
            "schema_version": 1,
            "target": target,
            "version": "1.0.0",
            "release_binding": {
                key: release_value[key] for key in binding_keys
            },
            verdict_ids[target]: "PASS",
            "RELEASE_INTEGRITY": "PASS",
        }
    else:
        evidence_value = {
            "schema_version": 1,
            "target": target,
            "asset_sha256": _sha256(asset),
            "release_manifest_sha256": _sha256(release),
            "verdicts": {
                verdict_ids[target]: "PASS",
                "RELEASE_INTEGRITY": "PASS",
            },
        }
    _write_json(evidence, evidence_value)
    if target == "codex" and codex_flat_evidence:
        release_value["acceptance_evidence_sha256"] = _sha256(evidence)
        _write_json(release, release_value)
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
            "immutable_release": True,
            "release_attestation": True,
        },
    )
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
    assert manifest["schema_version"] == 1
    assert manifest["app_id"] == "llm-foundation-installer"
    assert manifest["network"] == "user-initiated-only"
    assert manifest["automatic_network"] is False
    assert manifest["telemetry"] is False
    assert manifest["reverse_flow"] is False
    assert manifest["distribution"] == "single-executable"
    assert manifest["embedded_foundation"] is True
    assert manifest["signature"] == "unsigned-preview"
    assert manifest["employee_release"] is False
    assert manifest["employee_distribution_allowed"] is False
    assert manifest["artifacts"]["LLMFoundationInstaller.exe"]["sha256"] == _sha256(
        executable
    )
    assert manifest["artifacts"]["engine/foundation.ps1"]["sha256"] == _sha256(
        engine
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
    assert payload["reason"] == "No accepted target packages are bundled"


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
    assert payload["install_enabled"] is True
    assert payload["reason"] == "Accepted target package is available"

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
    assert tampered_payload["reason"] == "No accepted target packages are bundled"


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


def test_gui_rejects_accepted_claude_package_without_provider_evidence(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    _accepted_package(package_source, "claude")
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
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
    assert "accepted claude package requires current provider eligibility" in (
        result.stdout + result.stderr
    ).lower()


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
    assert blocked_states["claude"] == "policy_blocked"
    assert blocked_payload["provider_eligibility"] == "INVALID_OR_EXPIRED"
    assert blocked_payload["install_enabled"] is False
    assert blocked_payload["reason"] == (
        "Claude provider eligibility evidence is missing, invalid, or expired"
    )


def test_employee_release_requires_all_targets_and_code_signing(
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
            "-OutputRoot",
            str(output),
            "-PackageRoot",
            str(package_source),
            "-EmployeeRelease",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode != 0
    assert "requires accepted codex, claude, and opencode" in (
        result.stdout + result.stderr
    ).lower()


def test_employee_release_requires_provider_eligibility_before_signing(
    tmp_path: Path,
):
    package_source = tmp_path / "package-source"
    for target in ("codex", "claude", "opencode"):
        _accepted_package(package_source, target)

    missing = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            "-OutputRoot",
            str(tmp_path / "missing-evidence"),
            "-PackageRoot",
            str(package_source),
            "-EmployeeRelease",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert missing.returncode != 0
    assert "requires current provider eligibility evidence" in (
        missing.stdout + missing.stderr
    ).lower()

    evidence = _provider_eligibility_evidence(
        tmp_path / "provider-eligibility-evidence.json"
    )
    accepted_policy = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "tools" / "build-gui.ps1"),
            "-OutputRoot",
            str(tmp_path / "accepted-policy"),
            "-PackageRoot",
            str(package_source),
            "-ProviderEligibilityEvidence",
            str(evidence),
            "-EmployeeRelease",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert accepted_policy.returncode != 0
    assert "requires a code-signing certificate" in (
        accepted_policy.stdout + accepted_policy.stderr
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


def test_gui_workflow_fails_closed_on_wrong_client_version(tmp_path: Path):
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
            "0.0.0-wrong",
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


def test_gui_source_contains_no_network_or_secret_collection():
    source_root = REPOSITORY_ROOT / "src" / "gui"
    source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in sorted(source_root.glob("*"))
        if path.is_file()
    )
    forbidden = (
        "httpclient",
        "webrequest",
        "webclient",
        "feedback-pending",
        "auth.json",
        "api_key",
        "proxy-authorization",
        "session-report",
        "--verbose",
        "proxy-authorization",
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
        '"network_during_install", "offline"',
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


def test_employee_guide_does_not_present_connection_modes_as_policy_bypass():
    guide = (
        REPOSITORY_ROOT / "docs" / "EMPLOYEE-OPERATOR-GUIDE.md"
    ).read_text(encoding="utf-8").lower()
    guide = " ".join(guide.split())
    assert "https://www.anthropic.com/supported-countries" in guide
    assert "не подтверждает право использования" in guide
    assert "не должен использоваться для обхода" in guide
    assert "отдельная допустимая учётная запись" in guide
    assert "автоматизированный или без участия человека доступ" in guide
    assert "new-provider-eligibility-evidence.ps1" in guide
    assert "providereligibilityevidence" in guide
    assert "7 суток" in guide


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
