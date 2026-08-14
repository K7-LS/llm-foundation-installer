from __future__ import annotations

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
    "pilot_installer_release",
    ROOT / "tools" / "installer_release.py",
)
pilot = _load(
    "pilot_release",
    ROOT / "tools" / "pilot_release.py",
)
pilot_evidence = _load(
    "pilot_release_evidence",
    ROOT / "tools" / "pilot_evidence.py",
)


def _draft(tmp_path: Path) -> Path:
    bundle = employee_bundle(tmp_path / "bundle")
    canary = write_canary(
        bundle,
        tmp_path / "canary.json",
        installer_release.evidence_body_sha256,
    )
    return installer_release.prepare_draft_release(
        bundle=bundle,
        hub_canary_path=canary,
        output=tmp_path / "draft",
    ).root


def _pilot_evidence(draft: Path, path: Path) -> Path:
    pilot_evidence.create_pilot_evidence(
        draft=draft,
        output=path,
        windows_build=19045,
        confirmations={
            name: True for name in pilot.REQUIRED_PILOT_CHECKS
        },
    )
    return path


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_pilot_finalization_preserves_both_products_and_runtime(
    tmp_path: Path,
):
    draft = _draft(tmp_path)
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
    for filename in (*PRODUCT_FILES.values(), RUNTIME_FILE):
        assert (first.root / filename).read_bytes() == (
            draft / filename
        ).read_bytes()
    manifest = json.loads(
        first.release_manifest_path.read_text(encoding="utf-8")
    )
    assert manifest["tag"] == "employee-v0.4.0"
    assert manifest["channel"] == "stable"
    assert manifest["verdicts"]["PROGRAM_RELEASE"] == "3/3"
    assert manifest["verdicts"]["FULL_RELEASE_CLAUDE"] == "PASS"
    assert manifest["verdicts"]["HOME_PC_CANARY"] == "PASS"
    assert manifest["verdicts"]["RELEASE_INTEGRITY"] == (
        "PENDING_PUBLICATION"
    )


def test_pilot_finalization_rejects_admin_run(tmp_path: Path):
    draft = _draft(tmp_path)
    evidence_path = _pilot_evidence(draft, tmp_path / "pilot.json")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["machine"]["admin_used"] = True
    evidence["evidence_body_sha256"] = (
        installer_release.evidence_body_sha256(evidence)
    )
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="pilot"):
        pilot.finalize_employee_release(
            draft=draft,
            pilot_evidence_path=evidence_path,
            output=tmp_path / "stable",
        )


def test_pilot_finalization_rejects_changed_runtime(tmp_path: Path):
    draft = _draft(tmp_path)
    evidence_path = _pilot_evidence(draft, tmp_path / "pilot.json")
    (draft / RUNTIME_FILE).write_bytes(b"changed")

    with pytest.raises(ValueError, match="artifact"):
        pilot.finalize_employee_release(
            draft=draft,
            pilot_evidence_path=evidence_path,
            output=tmp_path / "stable",
        )
