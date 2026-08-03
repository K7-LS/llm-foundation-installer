from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.edition_release_fixtures import (
    FALLBACK_FILE,
    PRODUCT_FILES,
    RUNTIME_FILE,
    employee_bundle,
    json_bytes,
    record,
    write,
    write_canary,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "installer_release",
    ROOT / "tools" / "installer_release.py",
)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_draft_release_is_deterministic_and_binds_full_employee_edition(
    tmp_path: Path,
):
    bundle = employee_bundle(tmp_path / "bundle")
    canary = write_canary(
        bundle,
        tmp_path / "canary.json",
        release.evidence_body_sha256,
    )

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
    for filename in (*PRODUCT_FILES.values(), FALLBACK_FILE, RUNTIME_FILE):
        assert (first.root / filename).read_bytes() == (
            bundle / filename
        ).read_bytes()
    manifest = json.loads(
        first.release_manifest_path.read_text(encoding="utf-8")
    )
    assert manifest["tag"] == "employee-v0.3.0"
    assert manifest["channel"] == "draft"
    assert manifest["products"] == {
        key: record(bundle / filename)
        for key, filename in PRODUCT_FILES.items()
    }
    assert manifest["runtime"] == record(bundle / RUNTIME_FILE)
    assert manifest["launch_center_fallback"] == record(
        bundle / FALLBACK_FILE
    )
    assert manifest["verdicts"]["PROGRAM_RELEASE"] == "2/2"
    assert "FULL_RELEASE_CLAUDE" not in manifest["verdicts"]
    assert manifest["verdicts"]["CLEAN_PC_PILOT"] == "PENDING"


def test_draft_release_rejects_tampered_product(tmp_path: Path):
    bundle = employee_bundle(tmp_path / "bundle")
    canary = write_canary(
        bundle,
        tmp_path / "canary.json",
        release.evidence_body_sha256,
    )
    (bundle / PRODUCT_FILES["launch_center"]).write_bytes(b"changed")

    with pytest.raises(ValueError, match="binding"):
        release.prepare_draft_release(
            bundle=bundle,
            hub_canary_path=canary,
            output=tmp_path / "draft",
        )


def test_draft_release_rejects_tampered_launch_center_fallback(
    tmp_path: Path,
) -> None:
    bundle = employee_bundle(tmp_path / "bundle")
    canary = write_canary(
        bundle,
        tmp_path / "canary.json",
        release.evidence_body_sha256,
    )
    (bundle / FALLBACK_FILE).write_bytes(b"@echo off\r\nmalicious\r\n")

    with pytest.raises(ValueError, match="fallback binding"):
        release.prepare_draft_release(
            bundle=bundle,
            hub_canary_path=canary,
            output=tmp_path / "draft",
        )


def test_draft_release_rejects_owner_or_claude_contract(tmp_path: Path):
    bundle = employee_bundle(tmp_path / "bundle")
    manifest_path = bundle / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["targets"].append("claude")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="Employee"):
        release.validate_bundle(bundle)


def test_draft_release_rejects_canary_with_model_request(tmp_path: Path):
    bundle = employee_bundle(tmp_path / "bundle")
    canary = write_canary(
        bundle,
        tmp_path / "canary.json",
        release.evidence_body_sha256,
    )
    value = json.loads(canary.read_text(encoding="utf-8"))
    value["model_requests"] = 1
    value["evidence_body_sha256"] = release.evidence_body_sha256(value)
    write(canary, json_bytes(value))

    with pytest.raises(ValueError, match="canary"):
        release.prepare_draft_release(
            bundle=bundle,
            hub_canary_path=canary,
            output=tmp_path / "draft",
        )


def test_draft_release_rejects_missing_runtime(tmp_path: Path):
    bundle = employee_bundle(tmp_path / "bundle")
    (bundle / RUNTIME_FILE).unlink()

    with pytest.raises(ValueError, match="inventory"):
        release.validate_bundle(bundle)
