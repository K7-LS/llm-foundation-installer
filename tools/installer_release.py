from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


TARGETS = ("codex", "claude", "opencode")
EXPECTED_CLIENTS = {
    "codex-cli": "0.146.0-alpha.3.1",
    "codex-desktop": "store-current",
    "claude-code": "2.1.218",
    "opencode-cli": "1.18.7",
    "opencode-desktop": "1.18.7",
}
EXPECTED_CANARY_CLIENTS = {
    **EXPECTED_CLIENTS,
    "codex-desktop": "store-identity-verified",
}
EXPECTED_TARGET_LIFECYCLE = {
    "status": "PASS",
    "plan": "READY",
    "install": "INSTALLED",
    "doctor": "HEALTHY",
    "inventory": "INSTALLED",
    "rollback": "ROLLED_BACK",
    "preserved_data": "PASS",
}
EXPECTED_BUNDLE_VERDICTS = {
    "FULL_RELEASE_CODEX": "PASS",
    "FULL_RELEASE_CLAUDE": "PASS",
    "FULL_RELEASE_OPENCODE": "PASS",
    "PROGRAM_RELEASE": "3/3",
    "EMPLOYEE_INSTALLER_INTERNAL": "PASS",
    "PUBLIC_SIGNED_RELEASE": "DEFERRED_BY_OWNER",
}
INSTALL_GUIDE = """# LLM Foundation Installer v0.3.0

This is the approved internal unsigned employee installer.

1. Verify the downloaded executable:

   `Get-FileHash .\\LLMFoundationInstaller.exe -Algorithm SHA256`

2. Compare the result with `SHA256SUMS`.
3. Windows can show `Unknown Publisher` or SmartScreen. This is expected for
   `InternalUnsigned`; verify the SHA-256 before choosing to continue.
4. Run without administrator rights and follow the seven installer stages.
5. Sign in interactively inside Codex, Claude, and OpenCode. The installer
   never requests or transfers LLM credentials.

This release is for controlled employee distribution only. Public
distribution is not authorized.
"""


@dataclass(frozen=True)
class DraftRelease:
    root: Path
    installer_path: Path
    release_manifest_path: Path
    components_lock_path: Path
    sha256sums_path: Path


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_record(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "sha256": _sha256(payload),
        "bytes": len(payload),
    }


def evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return _sha256(_json_bytes(body))


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _safe_relative(value: str) -> bool:
    if "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        bool(value)
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _validate_artifact(
    bundle: Path,
    relative: str,
    record: object,
) -> Path:
    if not _safe_relative(relative) or not isinstance(record, dict):
        raise ValueError("bundle artifact record is unsafe")
    expected_hash = record.get("sha256")
    expected_bytes = record.get("bytes")
    path = bundle.joinpath(*PurePosixPath(relative).parts)
    if (
        not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or expected_bytes < 0
        or not path.is_file()
    ):
        raise ValueError(f"bundle artifact is invalid: {relative}")
    actual = _file_record(path)
    if actual != {
        "sha256": expected_hash,
        "bytes": expected_bytes,
    }:
        raise ValueError(f"bundle artifact binding differs: {relative}")
    return path


def _validate_client_lock(path: Path) -> None:
    value = _load_json(path)
    clients = value.get("clients")
    found = (
        {
            str(row.get("id")): str(row.get("version"))
            for row in clients
            if isinstance(row, dict)
        }
        if isinstance(clients, list)
        else {}
    )
    if (
        value.get("schema_version") != 1
        or value.get("official_only") is not True
        or value.get("test_only") is not False
        or found != EXPECTED_CLIENTS
    ):
        raise ValueError("client source lock is not release-accepted")


def validate_bundle(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve()
    manifest_path = bundle / "bundle-manifest.json"
    manifest = _load_json(manifest_path)
    verdicts = manifest.get("verdicts")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("app_id") != "llm-foundation-installer"
        or manifest.get("version") != "0.3.0"
        or manifest.get("network") != "user-initiated-only"
        or manifest.get("automatic_network") is not False
        or manifest.get("telemetry") is not False
        or manifest.get("reverse_flow") is not False
        or manifest.get("distribution_mode") != "internal_unsigned"
        or manifest.get("embedded_target_count") != 3
        or manifest.get("signature") != "unsigned-internal"
        or manifest.get("employee_release") is not True
        or manifest.get("employee_distribution_allowed") is not True
        or manifest.get("public_distribution_allowed") is not False
        or manifest.get("windows_warning_expected") is not True
        or manifest.get("targets") != list(TARGETS)
        or verdicts != EXPECTED_BUNDLE_VERDICTS
    ):
        raise ValueError("bundle manifest is not an accepted internal release")
    artifacts = manifest.get("artifacts")
    foundation = manifest.get("foundation_release")
    if not isinstance(artifacts, dict):
        raise ValueError("bundle artifact inventory is missing")
    foundation_paths = {
        "asset": "foundation/foundation-engine-0.2.1.zip",
        "release_manifest": "foundation/release-manifest.json",
        "acceptance_evidence": "foundation/acceptance-evidence.json",
        "release_verification": "foundation/release-verification.json",
        "package_acceptance_record": (
            "foundation/package-acceptance.json"
        ),
    }
    if (
        not isinstance(foundation, dict)
        or foundation.get("package_acceptance") != "PASS"
        or foundation.get("engine_version") != "0.2.1"
    ):
        raise ValueError("bundle Foundation release is not accepted")
    for label, relative in foundation_paths.items():
        record = foundation.get(label)
        if (
            not isinstance(record, dict)
            or record.get("relative_path") != relative
            or {
                "sha256": record.get("sha256"),
                "bytes": record.get("bytes"),
            }
            != artifacts.get(relative)
        ):
            raise ValueError("bundle Foundation release binding differs")
    required = {
        "LLMFoundationInstaller.exe",
        "VERSION",
        "engine/foundation.ps1",
        "engine/engine-manifest.json",
        "engine/VERSION",
        "client-sources.lock.json",
        "provider-eligibility-evidence.json",
        *foundation_paths.values(),
    }
    for target in TARGETS:
        prefix = f"packages/{target}/"
        required.update(
            {
                prefix + "release-manifest.json",
                prefix + "acceptance-evidence.json",
                prefix + "release-verification.json",
                prefix + "package-acceptance.json",
            }
        )
        zip_names = [
            name
            for name in artifacts
            if name.startswith(prefix)
            and name.endswith(".zip")
            and "/" not in name[len(prefix) :]
        ]
        if len(zip_names) != 1:
            raise ValueError(f"bundle artifact inventory for {target} differs")
        required.add(zip_names[0])
    missing = sorted(required.difference(artifacts))
    if missing:
        raise ValueError(
            "bundle artifact inventory is incomplete: " + ", ".join(missing)
        )
    for relative, record in artifacts.items():
        _validate_artifact(bundle, str(relative), record)
    foundation_acceptance = _load_json(
        bundle / "foundation" / "package-acceptance.json"
    )
    expected_engine_files = {
        name: artifacts[f"engine/{name}"]
        for name in ("VERSION", "engine-manifest.json", "foundation.ps1")
    }
    if (
        foundation_acceptance.get("schema_version") != 1
        or foundation_acceptance.get("target") != "foundation"
        or foundation_acceptance.get("engine_version") != "0.2.1"
        or foundation_acceptance.get("package_acceptance") != "PASS"
        or foundation_acceptance.get("engine_files")
        != expected_engine_files
        or foundation_acceptance.get("immutable_release") is not True
        or foundation_acceptance.get("release_attestation") is not True
    ):
        raise ValueError(
            "bundle Foundation package acceptance is invalid or unbound"
        )
    _validate_client_lock(bundle / "client-sources.lock.json")
    return manifest


def _validate_hub_canary(
    path: Path,
    bundle: Path,
) -> dict[str, Any]:
    evidence = _load_json(path)
    binding = evidence.get("bundle")
    targets = evidence.get("targets")
    if (
        evidence.get("schema_version") != 1
        or evidence.get("target") != "installer"
        or evidence.get("version") != "0.3.0"
        or evidence.get("INSTALLER_HUB_CANARY") != "PASS"
        or evidence.get("model_requests") != 0
        or evidence.get("unexpected_network") != 0
        or evidence.get("clients") != EXPECTED_CANARY_CLIENTS
        or not isinstance(binding, dict)
        or binding.get("manifest_sha256")
        != _file_record(bundle / "bundle-manifest.json")["sha256"]
        or binding.get("installer_sha256")
        != _file_record(bundle / "LLMFoundationInstaller.exe")["sha256"]
        or not isinstance(targets, dict)
        or set(targets) != set(TARGETS)
        or any(
            targets.get(target) != EXPECTED_TARGET_LIFECYCLE
            for target in TARGETS
        )
        or evidence.get("evidence_body_sha256")
        != evidence_body_sha256(evidence)
    ):
        raise ValueError("installer hub canary evidence is invalid or unbound")
    return evidence


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite release file: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _copy_exact(source: Path, destination: Path) -> None:
    _write_new(destination, source.read_bytes())
    if destination.read_bytes() != source.read_bytes():
        raise AssertionError(f"exact-byte copy failed: {source.name}")


def _release_components(
    bundle: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    target_records: dict[str, object] = {}
    artifacts = manifest["artifacts"]
    for target in TARGETS:
        prefix = f"packages/{target}/"
        target_records[target] = {
            name[len(prefix) :]: record
            for name, record in artifacts.items()
            if name.startswith(prefix)
        }
    return {
        "schema_version": 1,
        "target": "installer",
        "version": "0.3.0",
        "foundation_engine": {
            "version": (
                bundle / "engine" / "VERSION"
            ).read_text(encoding="utf-8").strip(),
            "manifest": artifacts["engine/engine-manifest.json"],
            "script": artifacts["engine/foundation.ps1"],
        },
        "foundation": {
            "version": manifest["foundation_release"]["engine_version"],
            "package_acceptance": manifest["foundation_release"][
                "package_acceptance"
            ],
            "release_artifacts": {
                label: artifacts[relative]
                for label, relative in {
                    "asset": "foundation/foundation-engine-0.2.1.zip",
                    "release_manifest": (
                        "foundation/release-manifest.json"
                    ),
                    "acceptance_evidence": (
                        "foundation/acceptance-evidence.json"
                    ),
                    "release_verification": (
                        "foundation/release-verification.json"
                    ),
                    "package_acceptance": (
                        "foundation/package-acceptance.json"
                    ),
                }.items()
            },
        },
        "client_sources": artifacts["client-sources.lock.json"],
        "targets": target_records,
        "sync_direction": "hub-to-consumer",
        "consumer_upload": False,
    }


def _sha256sums(root: Path) -> bytes:
    lines = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.name != "SHA256SUMS":
            lines.append(f"{_file_record(path)['sha256']}  {path.name}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def prepare_draft_release(
    *,
    bundle: Path,
    hub_canary_path: Path,
    output: Path,
) -> DraftRelease:
    """Prepare deterministic draft assets without rebuilding the EXE."""

    bundle = bundle.resolve()
    output = output.resolve()
    manifest = validate_bundle(bundle)
    canary = _validate_hub_canary(
        hub_canary_path.resolve(),
        bundle,
    )
    if output.exists():
        raise ValueError("draft release output must not exist")
    output.mkdir(parents=True)

    installer = output / "LLMFoundationInstaller.exe"
    _copy_exact(bundle / installer.name, installer)
    _copy_exact(
        bundle / "bundle-manifest.json",
        output / "bundle-manifest.json",
    )
    _copy_exact(
        bundle / "client-sources.lock.json",
        output / "client-sources.lock.json",
    )
    _copy_exact(
        bundle / "provider-eligibility-evidence.json",
        output / "provider-eligibility-evidence.json",
    )
    _copy_exact(
        hub_canary_path.resolve(),
        output / "hub-canary-evidence.json",
    )
    components = output / "components.lock.json"
    _write_new(components, _json_bytes(_release_components(bundle, manifest)))
    _write_new(
        output / "EMPLOYEE-INSTALL.md",
        INSTALL_GUIDE.encode("utf-8"),
    )

    artifacts = {
        path.name: _file_record(path)
        for path in sorted(output.iterdir(), key=lambda item: item.name)
        if path.is_file()
    }
    release_manifest: dict[str, Any] = {
        "schema_version": 1,
        "app_id": "llm-foundation-installer",
        "version": "0.3.0",
        "tag": "installer-v0.3.0",
        "channel": "draft",
        "distribution_mode": "internal_unsigned",
        "installer": artifacts["LLMFoundationInstaller.exe"],
        "bundle_manifest_sha256": artifacts["bundle-manifest.json"]["sha256"],
        "hub_canary_sha256": artifacts["hub-canary-evidence.json"]["sha256"],
        "artifacts": artifacts,
        "verdicts": {
            **EXPECTED_BUNDLE_VERDICTS,
            "INSTALLER_HUB_CANARY": "PASS",
            "CLEAN_PC_PILOT": "PENDING",
            "EMPLOYEE_INSTALLER_INTERNAL": "PENDING_PILOT",
        },
        "requires": {
            "clean_pc_pilot": True,
            "same_installer_bytes": True,
            "immutable_release": True,
            "release_attestation": True,
        },
    }
    release_manifest["evidence_body_sha256"] = evidence_body_sha256(
        release_manifest
    )
    release_manifest_path = output / "release-manifest.json"
    _write_new(release_manifest_path, _json_bytes(release_manifest))
    sums = output / "SHA256SUMS"
    _write_new(sums, _sha256sums(output))
    return DraftRelease(
        root=output,
        installer_path=installer,
        release_manifest_path=release_manifest_path,
        components_lock_path=components,
        sha256sums_path=sums,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare deterministic installer-v0.3.0 draft assets from an "
            "accepted InternalUnsigned bundle and hub canary."
        )
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--hub-canary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    result = prepare_draft_release(
        bundle=arguments.bundle,
        hub_canary_path=arguments.hub_canary,
        output=arguments.output,
    )
    print(
        json.dumps(
            {
                "status": "DRAFT_ASSETS_PREPARED",
                "tag": "installer-v0.3.0",
                "installer_sha256": _file_record(
                    result.installer_path
                )["sha256"],
                "pilot": "PENDING",
                "output": str(result.root),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
