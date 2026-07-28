from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.edition_release_fixtures import (
    PRODUCT_FILES,
    RUNTIME_FILE,
    employee_bundle,
    write_canary,
)


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


installer_release = _load(
    "verifier_installer_release",
    ROOT / "tools" / "installer_release.py",
)
pilot = _load(
    "verifier_pilot_release",
    ROOT / "tools" / "pilot_release.py",
)
pilot_evidence = _load(
    "verifier_pilot_evidence",
    ROOT / "tools" / "pilot_evidence.py",
)
verifier = _load(
    "installer_release_verifier",
    ROOT / "tools" / "installer_release_verifier.py",
)


def _record(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _stable_release(tmp_path: Path) -> Path:
    bundle = employee_bundle(tmp_path / "bundle")
    canary = write_canary(
        bundle,
        tmp_path / "canary.json",
        installer_release.evidence_body_sha256,
    )
    draft = installer_release.prepare_draft_release(
        bundle=bundle,
        hub_canary_path=canary,
        output=tmp_path / "draft",
    ).root
    evidence_path = tmp_path / "pilot.json"
    pilot_evidence.create_pilot_evidence(
        draft=draft,
        output=evidence_path,
        windows_build=19045,
        confirmations={
            name: True for name in pilot.REQUIRED_PILOT_CHECKS
        },
    )
    return pilot.finalize_employee_release(
        draft=draft,
        pilot_evidence_path=evidence_path,
        output=tmp_path / "stable",
    ).root


def _release_api(root: Path) -> dict[str, object]:
    return {
        "tag_name": "employee-v0.3.0",
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


def test_verifier_binds_immutable_employee_release_and_every_asset(
    tmp_path: Path,
):
    root = _stable_release(tmp_path)
    evidence = verifier.build_release_verification(
        stable_root=root,
        release_api=_release_api(root),
        release_attestation_output=b'{"verificationResult":"success"}\n',
        asset_attestation_outputs=_attestations(root),
        gh_version="gh version 2.96.0",
    )

    assert evidence["RELEASE_INTEGRITY"] == "PASS"
    assert evidence["tag"] == "employee-v0.3.0"
    assert [row["name"] for row in evidence["assets"]] == sorted(
        path.name for path in root.iterdir() if path.is_file()
    )
    assert {row["name"] for row in evidence["assets"]}.issuperset(
        {*PRODUCT_FILES.values(), RUNTIME_FILE}
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("draft", True),
        ("prerelease", True),
        ("immutable", False),
        ("tag_name", "installer-v0.3.0"),
    ],
)
def test_verifier_rejects_nonstable_or_wrong_release(
    tmp_path: Path,
    field: str,
    value: object,
):
    root = _stable_release(tmp_path)
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


def test_verifier_rejects_changed_launch_center(tmp_path: Path):
    root = _stable_release(tmp_path)
    release_api = _release_api(root)
    (root / PRODUCT_FILES["launch_center"]).write_bytes(b"changed")

    with pytest.raises(ValueError, match="artifact"):
        verifier.build_release_verification(
            stable_root=root,
            release_api=release_api,
            release_attestation_output=b"{}",
            asset_attestation_outputs=_attestations(root),
            gh_version="gh version 2.96.0",
        )


def test_verifier_rejects_missing_remote_asset(tmp_path: Path):
    root = _stable_release(tmp_path)
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


def test_verifier_requires_attestation_for_every_asset(tmp_path: Path):
    root = _stable_release(tmp_path)
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
