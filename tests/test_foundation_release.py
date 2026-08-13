from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
SPEC = importlib.util.spec_from_file_location(
    "foundation_release",
    ROOT / "tools" / "foundation_release.py",
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


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "foundation.ps1").write_text(
        "Write-Output 'foundation'\n",
        encoding="utf-8",
    )
    (engine / "VERSION").write_text(
        FOUNDATION_VERSION + "\n",
        encoding="utf-8",
    )
    script_hash = _sha256(engine / "foundation.ps1")
    (engine / "engine-manifest.json").write_bytes(
        _json_bytes(
            {
                "schema_version": 1,
                "protocol_version": 1,
                "engine_version": FOUNDATION_VERSION,
                "foundation_ps1_sha256": script_hash,
                "network": "offline",
                "commands": [
                    "doctor",
                    "install",
                    "inventory",
                    "plan",
                    "rollback",
                ],
                "supported_powershell": ["5.1", "7"],
            }
        )
    )
    files = {
        path.name: _sha256(path)
        for path in sorted(engine.iterdir(), key=lambda item: item.name)
    }
    evidence = {
        "schema_version": 1,
        "generated_at_utc": "2026-07-27T12:00:00Z",
        "engine_version": FOUNDATION_VERSION,
        "installer_version": "0.4.0",
        "source": {
            "repository": (
                "https://github.com/daniileliseev1337/"
                "llm-foundation-installer"
            ),
            "commit": "a" * 40,
            "tree": "b" * 40,
            "hashes": {
                "VERSION": "c" * 64,
                "APP_VERSION": "d" * 64,
                "client-sources.lock.json": "e" * 64,
                "src": "f" * 64,
                "tests": "1" * 64,
                "tools": "2" * 64,
            },
        },
        "FOUNDATION_SYNTHETIC": "PASS",
        "powershell_syntax": {
            "ps7": {"status": "PASS"},
            "ps51": {"status": "PASS"},
        },
        "engine_builds": {
            "ps7": {"status": "PASS", "files": files},
            "ps51": {"status": "PASS", "files": files},
        },
        "deterministic_engine_bundle": "PASS",
        "pytest": {
            "status": "PASS",
            "counts": {
                "tests": 143,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
                "passed": 143,
                "ps7_cases": 20,
                "ps51_cases": 20,
                "shared_cases": 103,
            },
        },
    }
    evidence["evidence_body_sha256"] = release.evidence_body_sha256(evidence)
    evidence_path = tmp_path / "foundation-acceptance.json"
    evidence_path.write_bytes(_json_bytes(evidence))
    return engine, evidence_path


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_prepare_foundation_release_is_deterministic_and_bound(tmp_path: Path):
    engine, evidence = _fixture(tmp_path)

    first = release.prepare_foundation_release(
        engine_root=engine,
        acceptance_evidence_path=evidence,
        output=tmp_path / "first",
    )
    second = release.prepare_foundation_release(
        engine_root=engine,
        acceptance_evidence_path=evidence,
        output=tmp_path / "second",
    )

    assert _tree(first.root) == _tree(second.root)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["tag"] == f"foundation-engine-v{FOUNDATION_VERSION}"
    assert manifest["channel"] == "stable"
    assert manifest["asset"]["name"] == (
        f"foundation-engine-{FOUNDATION_VERSION}.zip"
    )
    assert manifest["asset"]["sha256"] == _sha256(first.asset_path)
    assert manifest["engine_files"]["foundation.ps1"]["sha256"] == (
        _sha256(engine / "foundation.ps1")
    )


def test_prepare_foundation_release_rejects_changed_engine(tmp_path: Path):
    engine, evidence = _fixture(tmp_path)
    (engine / "foundation.ps1").write_text("changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="engine bytes"):
        release.prepare_foundation_release(
            engine_root=engine,
            acceptance_evidence_path=evidence,
            output=tmp_path / "out",
        )


def test_release_verification_and_package_acceptance_bind_exact_asset(
    tmp_path: Path,
):
    engine, evidence = _fixture(tmp_path)
    stable = release.prepare_foundation_release(
        engine_root=engine,
        acceptance_evidence_path=evidence,
        output=tmp_path / "stable",
    )
    manifest = json.loads(stable.manifest_path.read_text(encoding="utf-8"))
    verification = release.build_release_verification(
        manifest_path=stable.manifest_path,
        asset_path=stable.asset_path,
        release_api={
            "tag_name": manifest["tag"],
            "draft": False,
            "prerelease": False,
            "immutable": True,
        },
        release_attestation_output=b"{}",
        asset_attestation_output=b"{}",
        gh_version="gh version 2.96.0",
    )
    verification_path = stable.root / "release-verification.json"
    verification_path.write_bytes(_json_bytes(verification))
    output = stable.root / "package-acceptance.json"

    result = release.create_package_acceptance(
        stable.manifest_path,
        stable.evidence_path,
        verification_path,
        output,
    )

    assert result["package_acceptance"] == "PASS"
    assert result["target"] == "foundation"
    assert result["engine_version"] == FOUNDATION_VERSION
    assert result["asset"]["sha256"] == manifest["asset"]["sha256"]


def test_package_acceptance_rejects_mutable_release(tmp_path: Path):
    engine, evidence = _fixture(tmp_path)
    stable = release.prepare_foundation_release(
        engine_root=engine,
        acceptance_evidence_path=evidence,
        output=tmp_path / "stable",
    )
    manifest = json.loads(stable.manifest_path.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="immutable"):
        release.build_release_verification(
            manifest_path=stable.manifest_path,
            asset_path=stable.asset_path,
            release_api={
                "tag_name": manifest["tag"],
                "draft": False,
                "prerelease": False,
                "immutable": False,
            },
            release_attestation_output=b"{}",
            asset_attestation_output=b"{}",
            gh_version="gh version 2.96.0",
        )
