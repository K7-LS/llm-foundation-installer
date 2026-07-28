from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.edition_release_fixtures import (
    canary_value,
    employee_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "hub_canary",
    ROOT / "tools" / "hub_canary.py",
)
assert SPEC and SPEC.loader
canary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = canary
SPEC.loader.exec_module(canary)


def test_hub_canary_accepts_two_products_runtime_and_two_targets(
    tmp_path: Path,
):
    bundle = employee_bundle(tmp_path / "bundle")
    expected = canary_value(
        bundle,
        canary.evidence_body_sha256,
    )

    value = canary.build_hub_canary_evidence(
        bundle_binding=expected["bundle"],
        product_results=expected["products"],
        runtime_result=expected["runtime"],
        target_results=expected["targets"],
    )

    assert value["target"] == "employee_edition"
    assert set(value["products"]) == {"installer", "launch_center"}
    assert value["products"]["installer"]["sibling_handoff"] == "PASS"
    assert (
        value["products"]["launch_center"]["sibling_handoff"]
        == "NOT_APPLICABLE"
    )
    assert set(value["targets"]) == {"codex", "opencode"}
    assert value["runtime"]["status"] == "VERIFIED"
    assert value["model_requests"] == 0
    assert value["evidence_body_sha256"] == canary.evidence_body_sha256(
        value
    )


def test_hub_canary_rejects_old_single_exe_contract(tmp_path: Path):
    bundle = employee_bundle(tmp_path / "bundle")
    expected = canary_value(
        bundle,
        canary.evidence_body_sha256,
    )
    binding = dict(expected["bundle"])
    binding.pop("launch_center_sha256")

    with pytest.raises(ValueError, match="not accepted"):
        canary.build_hub_canary_evidence(
            bundle_binding=binding,
            product_results=expected["products"],
            runtime_result=expected["runtime"],
            target_results=expected["targets"],
        )


def test_hub_canary_rejects_unverified_runtime(tmp_path: Path):
    bundle = employee_bundle(tmp_path / "bundle")
    expected = canary_value(
        bundle,
        canary.evidence_body_sha256,
    )
    runtime = dict(expected["runtime"])
    runtime["status"] = "MISSING"

    with pytest.raises(ValueError, match="not accepted"):
        canary.build_hub_canary_evidence(
            bundle_binding=expected["bundle"],
            product_results=expected["products"],
            runtime_result=runtime,
            target_results=expected["targets"],
        )
