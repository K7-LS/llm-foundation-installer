from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "installer_release_verifier",
    ROOT / "tools" / "installer_release_verifier.py",
)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


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


def _stable_release(root: Path) -> tuple[Path, dict[str, object]]:
    root.mkdir()
    payloads = {
        "LLMFoundationInstaller.exe": b"MZaccepted-installer",
        "bundle-manifest.json": b'{"version":"0.3.0"}\n',
        "components.lock.json": b'{"targets":{}}\n',
        "client-sources.lock.json": b'{"official_only":true}\n',
        "acceptance-evidence.json": b'{"release":"pending"}\n',
        "pilot-acceptance.json": b'{"pilot":"PASS"}\n',
        "hub-canary-evidence.json": b'{"canary":"PASS"}\n',
        "provider-eligibility-evidence.json": b'{"eligibility":"PASS"}\n',
        "EMPLOYEE-INSTALL.md": b"# Internal install\n",
    }
    for name, payload in payloads.items():
        (root / name).write_bytes(payload)
    artifacts = {
        name: _record(root / name)
        for name in sorted(payloads)
    }
    manifest = {
        "schema_version": 1,
        "app_id": "llm-foundation-installer",
        "version": "0.3.0",
        "tag": "installer-v0.3.0",
        "channel": "stable",
        "distribution_mode": "internal_unsigned",
        "installer": artifacts["LLMFoundationInstaller.exe"],
        "artifacts": artifacts,
        "verdicts": {
            "FULL_RELEASE_CODEX": "PASS",
            "FULL_RELEASE_CLAUDE": "PASS",
            "FULL_RELEASE_OPENCODE": "PASS",
            "PROGRAM_RELEASE": "3/3",
            "INSTALLER_HUB_CANARY": "PASS",
            "CLEAN_PC_PILOT": "PASS",
            "EMPLOYEE_INSTALLER_INTERNAL": "PASS",
            "PUBLIC_SIGNED_RELEASE": "DEFERRED_BY_OWNER",
            "RELEASE_INTEGRITY": "PENDING_PUBLICATION",
        },
    }
    manifest["evidence_body_sha256"] = verifier.evidence_body_sha256(manifest)
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
    return root, manifest


def _release_api(root: Path) -> dict[str, object]:
    return {
        "tag_name": "installer-v0.3.0",
        "draft": False,
        "prerelease": False,
        "immutable": True,
        "assets": [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "digest": f"sha256:{_record(path)['sha256']}",
            }
            for path in sorted(root.iterdir(), key=lambda item: item.name)
            if path.is_file()
        ],
    }


def _attestations(root: Path) -> dict[str, bytes]:
    return {
        path.name: b'{"verificationResult":"success"}\n'
        for path in root.iterdir()
        if path.is_file()
    }


def test_verifier_binds_immutable_release_and_every_asset(tmp_path: Path):
    root, _ = _stable_release(tmp_path / "stable")
    release_api = _release_api(root)

    evidence = verifier.build_release_verification(
        stable_root=root,
        release_api=release_api,
        release_attestation_output=b'{"verificationResult":"success"}\n',
        asset_attestation_outputs=_attestations(root),
        gh_version="gh version 2.96.0",
    )

    assert evidence["RELEASE_INTEGRITY"] == "PASS"
    assert evidence["repository"] == (
        "daniileliseev1337/llm-foundation-installer"
    )
    assert evidence["tag"] == "installer-v0.3.0"
    assert [row["name"] for row in evidence["assets"]] == sorted(
        path.name for path in root.iterdir() if path.is_file()
    )
    assert all(row["attestation"] == "PASS" for row in evidence["assets"])
    assert evidence["privacy"] == {
        "raw_attestation_output_included": False,
        "credentials_included": False,
        "personal_data_included": False,
    }
    assert evidence["evidence_body_sha256"] == (
        verifier.evidence_body_sha256(evidence)
    )
    assert "verificationResult" not in str(evidence)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("draft", True),
        ("prerelease", True),
        ("immutable", False),
        ("tag_name", "installer-v9.9.9"),
    ],
)
def test_verifier_rejects_nonstable_or_mutable_release(
    tmp_path: Path,
    field: str,
    value: object,
):
    root, _ = _stable_release(tmp_path / "stable")
    release_api = _release_api(root)
    release_api[field] = value

    with pytest.raises(ValueError, match="immutable stable"):
        verifier.build_release_verification(
            stable_root=root,
            release_api=release_api,
            release_attestation_output=b"{}",
            asset_attestation_outputs=_attestations(root),
            gh_version="gh version 2.96.0",
        )


def test_verifier_rejects_missing_remote_asset(tmp_path: Path):
    root, _ = _stable_release(tmp_path / "stable")
    release_api = _release_api(root)
    release_api["assets"] = release_api["assets"][:-1]

    with pytest.raises(ValueError, match="asset inventory"):
        verifier.build_release_verification(
            stable_root=root,
            release_api=release_api,
            release_attestation_output=b"{}",
            asset_attestation_outputs=_attestations(root),
            gh_version="gh version 2.96.0",
        )


def test_verifier_rejects_changed_local_asset(tmp_path: Path):
    root, _ = _stable_release(tmp_path / "stable")
    release_api = _release_api(root)
    (root / "LLMFoundationInstaller.exe").write_bytes(b"changed")

    with pytest.raises(ValueError, match="artifact"):
        verifier.build_release_verification(
            stable_root=root,
            release_api=release_api,
            release_attestation_output=b"{}",
            asset_attestation_outputs=_attestations(root),
            gh_version="gh version 2.96.0",
        )


def test_verifier_requires_attestation_for_every_asset(tmp_path: Path):
    root, _ = _stable_release(tmp_path / "stable")
    outputs = _attestations(root)
    outputs.pop("SHA256SUMS")

    with pytest.raises(ValueError, match="attestation inventory"):
        verifier.build_release_verification(
            stable_root=root,
            release_api=_release_api(root),
            release_attestation_output=b"{}",
            asset_attestation_outputs=outputs,
            gh_version="gh version 2.96.0",
        )
