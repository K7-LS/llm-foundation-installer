from __future__ import annotations

import hashlib
import json
from pathlib import Path


PRODUCT_FILES = {
    "installer": "K7-AI-Foundation-Employee-PublicUnsigned.exe",
}
FALLBACK_FILE = "K7-AI-Launch-Center-Employee-PublicUnsigned.cmd"
RUNTIME_FILE = "sing-box-1.13.14-windows-amd64.zip"


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def record(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def employee_bundle(root: Path) -> Path:
    root.mkdir(parents=True)
    write(root / PRODUCT_FILES["installer"], b"MZemployee-installer")
    write(
        root / FALLBACK_FILE,
        (
            '@echo off\r\nstart "" '
            '"%~dp0K7-AI-Foundation-Employee-PublicUnsigned.exe" '
            "--launch-center-ui\r\n"
        ).encode("utf-8"),
    )
    write(root / RUNTIME_FILE, b"PK\x03\x04sing-box-runtime")
    manifest = {
        "schema_version": 1,
        "app_id": "k7-ai-edition-bundle",
        "edition_id": "Employee",
        "version": "0.4.0",
        "theme_id": "K7Signal",
        "owner_controlled": False,
        "distribution_allowed": True,
        "distribution_mode": "PublicUnsigned",
        "targets": ["claude", "codex", "opencode"],
        "verdicts": {
            "FULL_RELEASE_CLAUDE": "PASS",
            "FULL_RELEASE_CODEX": "PASS",
            "FULL_RELEASE_OPENCODE": "PASS",
            "TECHNICAL_READY": "PASS",
            "PROVIDER_LIVE": "PASS",
            "PROGRAM_RELEASE": "3/3",
            "INTERNAL_UNSIGNED_RELEASE": "NOT_PASS",
            "PUBLIC_UNSIGNED_RELEASE": "PASS",
            "PUBLIC_SIGNED_RELEASE": "DEFERRED_UNSIGNED",
        },
        "runtime": {
            "id": "sing-box",
            "version": "1.13.14",
            "file": RUNTIME_FILE,
            **record(root / RUNTIME_FILE),
        },
        "launch_center_fallback": {
            "product_role": "LaunchCenter",
            "file": FALLBACK_FILE,
            "arguments": "--launch-center-ui",
            **record(root / FALLBACK_FILE),
        },
        "products": {
            "installer": {
                "product_role": "Installer",
                "file": PRODUCT_FILES["installer"],
                **record(root / PRODUCT_FILES["installer"]),
            },
        },
    }
    write(root / "bundle-manifest.json", json_bytes(manifest))
    return root


def canary_value(bundle: Path, evidence_body_sha256) -> dict[str, object]:
    binding = {
        "manifest_sha256": record(bundle / "bundle-manifest.json")["sha256"],
        "installer_sha256": record(
            bundle / PRODUCT_FILES["installer"]
        )["sha256"],
        "runtime_sha256": record(bundle / RUNTIME_FILE)["sha256"],
    }
    installer_result = {
        "product": "PASS",
        "self_test": "PASS",
        "catalog": "PASS",
        "launch_center_fallback": "PASS",
    }
    target_result = {
        "status": "PASS",
        "plan": "READY",
        "install": "CANONICAL",
        "doctor": "CANONICAL",
        "inventory": "INSTALLED",
        "rollback": "ROLLED_BACK",
        "preserved_data": "PASS",
    }
    value = {
        "schema_version": 1,
        "target": "employee_edition",
        "version": "0.4.0",
        "generated_at_utc": "2026-07-28T12:00:00Z",
        "bundle": binding,
        "products": {
            "installer": installer_result,
        },
        "runtime": {
            "id": "sing-box",
            "version": "1.13.14",
            "status": "VERIFIED",
        },
        "targets": {
            "claude": dict(target_result),
            "codex": dict(target_result),
            "opencode": dict(target_result),
        },
        "model_requests": 0,
        "unexpected_network": 0,
        "credentials_included": False,
        "personal_data_included": False,
        "INSTALLER_HUB_CANARY": "PASS",
    }
    value["evidence_body_sha256"] = evidence_body_sha256(value)
    return value


def write_canary(bundle: Path, path: Path, evidence_body_sha256) -> Path:
    write(path, json_bytes(canary_value(bundle, evidence_body_sha256)))
    return path
