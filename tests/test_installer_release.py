from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "installer_release",
    ROOT / "tools" / "installer_release.py",
)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _bundle(root: Path) -> Path:
    files = {
        "LLMFoundationInstaller.exe": b"MZfixture-installer",
        "VERSION": b"0.3.0\n",
        "engine/foundation.ps1": b"exit 0\n",
        "engine/engine-manifest.json": b'{"engine_version":"0.2.1"}\n',
        "engine/VERSION": b"0.2.1\n",
        "client-sources.lock.json": _json_bytes(
            {
                "schema_version": 1,
                "official_only": True,
                "test_only": False,
                "clients": [
                    {
                        "id": "codex-cli",
                        "version": "0.146.0-alpha.3.1",
                    },
                    {
                        "id": "codex-desktop",
                        "version": "store-current",
                    },
                    {
                        "id": "claude-code",
                        "version": "2.1.218",
                    },
                    {
                        "id": "opencode-cli",
                        "version": "1.18.7",
                    },
                    {
                        "id": "opencode-desktop",
                        "version": "1.18.7",
                    },
                ],
            }
        ),
        "provider-eligibility-evidence.json": b'{"status":"PASS"}\n',
        "foundation/foundation-engine-0.2.1.zip": b"foundation-zip",
        "foundation/release-manifest.json": b'{"channel":"stable"}\n',
        "foundation/acceptance-evidence.json": (
            b'{"FOUNDATION_SYNTHETIC":"PASS"}\n'
        ),
        "foundation/release-verification.json": (
            b'{"RELEASE_INTEGRITY":"PASS"}\n'
        ),
    }
    for target in ("codex", "claude", "opencode"):
        prefix = f"packages/{target}"
        files.update(
            {
                f"{prefix}/{target}-base-0.1.0.zip": (
                    target.encode("ascii") + b"-zip"
                ),
                f"{prefix}/release-manifest.json": b'{"channel":"stable"}\n',
                f"{prefix}/acceptance-evidence.json": b'{"full":"PASS"}\n',
                f"{prefix}/release-verification.json": (
                    b'{"RELEASE_INTEGRITY":"PASS"}\n'
                ),
                f"{prefix}/package-acceptance.json": (
                    b'{"package_acceptance":"PASS"}\n'
                ),
            }
        )
    foundation_engine_files = {
        name.removeprefix("engine/"): {
            "sha256": hashlib.sha256(files[name]).hexdigest(),
            "bytes": len(files[name]),
        }
        for name in (
            "engine/VERSION",
            "engine/engine-manifest.json",
            "engine/foundation.ps1",
        )
    }
    files["foundation/package-acceptance.json"] = _json_bytes(
        {
            "schema_version": 1,
            "target": "foundation",
            "engine_version": "0.2.1",
            "package_acceptance": "PASS",
            "engine_files": foundation_engine_files,
            "immutable_release": True,
            "release_attestation": True,
        }
    )
    for name, payload in files.items():
        _write(root / name, payload)
    artifacts = {
        name: {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
        for name, payload in sorted(files.items())
    }
    manifest = {
        "schema_version": 1,
        "app_id": "llm-foundation-installer",
        "version": "0.3.0",
        "network": "user-initiated-only",
        "automatic_network": False,
        "telemetry": False,
        "reverse_flow": False,
        "distribution_mode": "internal_unsigned",
        "embedded_target_count": 3,
        "signature": "unsigned-internal",
        "employee_release": True,
        "employee_distribution_allowed": True,
        "public_distribution_allowed": False,
        "windows_warning_expected": True,
        "targets": ["codex", "claude", "opencode"],
        "foundation_release": {
            "package_acceptance": "PASS",
            "engine_version": "0.2.1",
            "asset": {
                "relative_path": (
                    "foundation/foundation-engine-0.2.1.zip"
                ),
                **artifacts[
                    "foundation/foundation-engine-0.2.1.zip"
                ],
            },
            "release_manifest": {
                "relative_path": "foundation/release-manifest.json",
                **artifacts["foundation/release-manifest.json"],
            },
            "acceptance_evidence": {
                "relative_path": "foundation/acceptance-evidence.json",
                **artifacts["foundation/acceptance-evidence.json"],
            },
            "release_verification": {
                "relative_path": "foundation/release-verification.json",
                **artifacts["foundation/release-verification.json"],
            },
            "package_acceptance_record": {
                "relative_path": "foundation/package-acceptance.json",
                **artifacts["foundation/package-acceptance.json"],
            },
        },
        "verdicts": {
            "FULL_RELEASE_CODEX": "PASS",
            "FULL_RELEASE_CLAUDE": "PASS",
            "FULL_RELEASE_OPENCODE": "PASS",
            "PROGRAM_RELEASE": "3/3",
            "EMPLOYEE_INSTALLER_INTERNAL": "PASS",
            "PUBLIC_SIGNED_RELEASE": "DEFERRED_BY_OWNER",
        },
        "artifacts": artifacts,
    }
    _write(root / "bundle-manifest.json", _json_bytes(manifest))
    return root


def _canary(bundle: Path, path: Path) -> Path:
    value = {
        "schema_version": 1,
        "target": "installer",
        "version": "0.3.0",
        "bundle": {
            "manifest_sha256": _sha256(
                bundle / "bundle-manifest.json"
            ),
            "installer_sha256": _sha256(
                bundle / "LLMFoundationInstaller.exe"
            ),
        },
        "clients": {
            "codex-cli": "0.146.0-alpha.3.1",
            "codex-desktop": "store-identity-verified",
            "claude-code": "2.1.218",
            "opencode-cli": "1.18.7",
            "opencode-desktop": "1.18.7",
        },
        "targets": {
            target: {
                "status": "PASS",
                "plan": "READY",
                "install": "INSTALLED",
                "doctor": "HEALTHY",
                "inventory": "INSTALLED",
                "rollback": "ROLLED_BACK",
                "preserved_data": "PASS",
            }
            for target in ("codex", "claude", "opencode")
        },
        "model_requests": 0,
        "unexpected_network": 0,
        "INSTALLER_HUB_CANARY": "PASS",
    }
    value["evidence_body_sha256"] = release.evidence_body_sha256(value)
    _write(path, _json_bytes(value))
    return path


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_draft_release_is_deterministic_and_exact_exe_bound(
    tmp_path: Path,
):
    bundle = _bundle(tmp_path / "bundle")
    canary = _canary(bundle, tmp_path / "canary.json")

    first = release.prepare_draft_release(
        bundle=bundle,
        hub_canary_path=canary,
        output=tmp_path / "first",
    )
    second = release.prepare_draft_release(
        bundle=bundle,
        hub_canary_path=canary,
        output=tmp_path / "second",
    )

    assert _tree(first.root) == _tree(second.root)
    assert first.installer_path.read_bytes() == (
        bundle / "LLMFoundationInstaller.exe"
    ).read_bytes()
    manifest = json.loads(
        first.release_manifest_path.read_text(encoding="utf-8")
    )
    assert manifest["tag"] == "installer-v0.3.0"
    assert manifest["channel"] == "draft"
    assert manifest["installer"]["sha256"] == _sha256(
        bundle / "LLMFoundationInstaller.exe"
    )
    assert manifest["verdicts"]["PROGRAM_RELEASE"] == "3/3"
    assert manifest["verdicts"]["CLEAN_PC_PILOT"] == "PENDING"
    components = json.loads(
        first.components_lock_path.read_text(encoding="utf-8")
    )
    assert components["foundation"]["version"] == "0.2.1"
    assert components["foundation"]["package_acceptance"] == "PASS"


def test_draft_release_rejects_tampered_bundle_artifact(tmp_path: Path):
    bundle = _bundle(tmp_path / "bundle")
    canary = _canary(bundle, tmp_path / "canary.json")
    (bundle / "packages" / "claude" / "claude-base-0.1.0.zip").write_bytes(
        b"tampered"
    )

    with pytest.raises(ValueError, match="artifact"):
        release.prepare_draft_release(
            bundle=bundle,
            hub_canary_path=canary,
            output=tmp_path / "draft",
        )


def test_draft_release_rejects_canary_with_model_request(tmp_path: Path):
    bundle = _bundle(tmp_path / "bundle")
    canary = _canary(bundle, tmp_path / "canary.json")
    value = json.loads(canary.read_text(encoding="utf-8"))
    value["model_requests"] = 1
    value["evidence_body_sha256"] = release.evidence_body_sha256(value)
    _write(canary, _json_bytes(value))

    with pytest.raises(ValueError, match="canary"):
        release.prepare_draft_release(
            bundle=bundle,
            hub_canary_path=canary,
            output=tmp_path / "draft",
        )


def test_draft_release_rejects_missing_foundation_provenance(tmp_path: Path):
    bundle = _bundle(tmp_path / "bundle")
    canary = _canary(bundle, tmp_path / "canary.json")
    (bundle / "foundation" / "release-verification.json").unlink()

    with pytest.raises(ValueError, match="artifact"):
        release.prepare_draft_release(
            bundle=bundle,
            hub_canary_path=canary,
            output=tmp_path / "draft",
        )
