from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


installer_release = _load(
    "pilot_installer_release",
    ROOT / "tools" / "installer_release.py",
)
pilot = _load(
    "pilot_release",
    ROOT / "tools" / "pilot_release.py",
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _record(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _draft(root: Path) -> Path:
    root.mkdir()
    files = {
        "LLMFoundationInstaller.exe": b"MZsame-installer-bytes",
        "bundle-manifest.json": b'{"version":"0.3.0"}\n',
        "client-sources.lock.json": b'{"official_only":true}\n',
        "provider-eligibility-evidence.json": b'{"status":"PASS"}\n',
        "hub-canary-evidence.json": b'{"INSTALLER_HUB_CANARY":"PASS"}\n',
        "components.lock.json": b'{"targets":{}}\n',
        "EMPLOYEE-INSTALL.md": b"# Install\n",
    }
    for name, payload in files.items():
        (root / name).write_bytes(payload)
    manifest = {
        "schema_version": 1,
        "app_id": "llm-foundation-installer",
        "version": "0.3.0",
        "tag": "installer-v0.3.0",
        "channel": "draft",
        "distribution_mode": "internal_unsigned",
        "installer": _record(root / "LLMFoundationInstaller.exe"),
        "bundle_manifest_sha256": _record(
            root / "bundle-manifest.json"
        )["sha256"],
        "hub_canary_sha256": _record(
            root / "hub-canary-evidence.json"
        )["sha256"],
        "artifacts": {
            name: _record(root / name)
            for name in sorted(files)
        },
        "verdicts": {
            "FULL_RELEASE_CODEX": "PASS",
            "FULL_RELEASE_CLAUDE": "PASS",
            "FULL_RELEASE_OPENCODE": "PASS",
            "PROGRAM_RELEASE": "3/3",
            "INSTALLER_HUB_CANARY": "PASS",
            "CLEAN_PC_PILOT": "PENDING",
            "EMPLOYEE_INSTALLER_INTERNAL": "PENDING_PILOT",
            "PUBLIC_SIGNED_RELEASE": "DEFERRED_BY_OWNER",
        },
        "requires": {
            "clean_pc_pilot": True,
            "same_installer_bytes": True,
            "immutable_release": True,
            "release_attestation": True,
        },
    }
    manifest["evidence_body_sha256"] = (
        installer_release.evidence_body_sha256(manifest)
    )
    (root / "release-manifest.json").write_bytes(_json_bytes(manifest))
    sums = [
        f"{_record(path)['sha256']}  {path.name}"
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    (root / "SHA256SUMS").write_text(
        "\n".join(sums) + "\n",
        encoding="utf-8",
    )
    return root


def _pilot_evidence(
    draft: Path,
    path: Path,
    *,
    admin_used: bool = False,
) -> Path:
    value = {
        "schema_version": 1,
        "target": "installer",
        "version": "0.3.0",
        "recorded_at_utc": "2026-07-27T12:00:00Z",
        "installer_sha256": _record(
            draft / "LLMFoundationInstaller.exe"
        )["sha256"],
        "draft_release_manifest_sha256": _record(
            draft / "release-manifest.json"
        )["sha256"],
        "machine": {
            "clean_windows_x64": True,
            "windows_build": 19045,
            "admin_used": admin_used,
        },
        "network_mode": "VPN",
        "checks": {
            name: "PASS"
            for name in pilot.REQUIRED_PILOT_CHECKS
        },
        "privacy": {
            "credentials_included": False,
            "personal_data_included": False,
            "machine_identifier_included": False,
        },
        "CLEAN_PC_PILOT": "PASS",
    }
    value["evidence_body_sha256"] = (
        installer_release.evidence_body_sha256(value)
    )
    path.write_bytes(_json_bytes(value))
    return path


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_pilot_finalization_preserves_exact_installer_bytes(
    tmp_path: Path,
):
    draft = _draft(tmp_path / "draft")
    evidence = _pilot_evidence(draft, tmp_path / "pilot.json")

    first = pilot.finalize_employee_release(
        draft=draft,
        pilot_evidence_path=evidence,
        output=tmp_path / "stable-one",
    )
    second = pilot.finalize_employee_release(
        draft=draft,
        pilot_evidence_path=evidence,
        output=tmp_path / "stable-two",
    )

    assert _tree(first.root) == _tree(second.root)
    assert first.installer_path.read_bytes() == (
        draft / "LLMFoundationInstaller.exe"
    ).read_bytes()
    manifest = json.loads(
        first.release_manifest_path.read_text(encoding="utf-8")
    )
    assert manifest["channel"] == "stable"
    assert manifest["verdicts"]["EMPLOYEE_INSTALLER_INTERNAL"] == "PASS"
    assert manifest["verdicts"]["CLEAN_PC_PILOT"] == "PASS"
    assert manifest["verdicts"]["RELEASE_INTEGRITY"] == (
        "PENDING_PUBLICATION"
    )


def test_pilot_finalization_rejects_admin_run(tmp_path: Path):
    draft = _draft(tmp_path / "draft")
    evidence = _pilot_evidence(
        draft,
        tmp_path / "pilot.json",
        admin_used=True,
    )

    with pytest.raises(ValueError, match="pilot"):
        pilot.finalize_employee_release(
            draft=draft,
            pilot_evidence_path=evidence,
            output=tmp_path / "stable",
        )
