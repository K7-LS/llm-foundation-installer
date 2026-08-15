import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_set", ROOT / "tools" / "release_set.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def components(tmp_path: Path):
    rows = []
    for component_id in MODULE.REQUIRED:
        path = tmp_path / (component_id + ".bin")
        path.write_bytes((component_id + "\n").encode())
        rows.append([component_id, "1.0.0", str(path)])
    return rows


def test_internal_unsigned_binds_every_component_and_allows_blocked_live_gates(tmp_path):
    payload = MODULE.build(
        "InternalUnsigned",
        components(tmp_path),
        ["TECHNICAL_READY=PASS", "PROVIDER_LIVE=BLOCKED_PROVIDER_ELIGIBILITY", "REVIT_LIVE=BLOCKED_RESTART_REQUIRED"],
    )
    assert payload["signed"] is False
    assert payload["publication_allowed"] is True
    assert payload["internal_distribution_allowed"] is True
    assert payload["public_distribution_allowed"] is False
    MODULE.validate(payload)


def test_partial_release_set_is_rejected(tmp_path):
    payload = MODULE.build("InternalUnsigned", components(tmp_path), ["TECHNICAL_READY=PASS"])
    payload["components"].pop()
    with pytest.raises(ValueError, match="partial"):
        MODULE.validate(payload)


def test_public_unsigned_requires_all_pass_and_owner_warning(tmp_path):
    payload = MODULE.build(
        "PublicUnsigned",
        components(tmp_path),
        ["TECHNICAL_READY=PASS", "PROVIDER_LIVE=PASS"],
    )
    assert payload["signed"] is False
    assert payload["publication_allowed"] is True
    assert payload["public_distribution_allowed"] is True
    assert payload["owner_authorized_public_unsigned"] is True
    assert payload["windows_warning_expected"] is True
    MODULE.validate(payload)

    payload["gates"]["PROVIDER_LIVE"] = "BLOCKED"
    with pytest.raises(ValueError, match="partial public unsigned"):
        MODULE.validate(payload)


def test_stable_is_fail_closed_on_any_nonpass_gate(tmp_path):
    payload = MODULE.build("InternalUnsigned", components(tmp_path), ["TECHNICAL_READY=PASS"])
    payload["channel"] = "Stable"
    payload["signed"] = True
    payload["publication_allowed"] = True
    payload["public_distribution_allowed"] = True
    payload["gates"]["PROVIDER_LIVE"] = "BLOCKED_PROVIDER_ELIGIBILITY"
    with pytest.raises(ValueError, match="forbidden"):
        MODULE.validate(payload)
