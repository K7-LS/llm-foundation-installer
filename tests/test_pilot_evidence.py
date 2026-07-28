from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.edition_release_fixtures import PRODUCT_FILES, RUNTIME_FILE, record


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pilot_release = _load(
    "pilot_evidence_release",
    ROOT / "tools" / "pilot_release.py",
)
evidence_tool = _load(
    "pilot_evidence",
    ROOT / "tools" / "pilot_evidence.py",
)


def _draft(root: Path) -> Path:
    root.mkdir()
    for filename in PRODUCT_FILES.values():
        (root / filename).write_bytes(("MZ" + filename).encode())
    (root / RUNTIME_FILE).write_bytes(b"PK\x03\x04runtime")
    (root / "release-manifest.json").write_bytes(b'{"channel":"draft"}\n')
    return root


def _confirmations() -> dict[str, bool]:
    return {name: True for name in pilot_release.REQUIRED_PILOT_CHECKS}


def test_pilot_evidence_is_pii_free_and_binds_every_executable_and_runtime(
    tmp_path: Path,
):
    draft = _draft(tmp_path / "draft")
    evidence = evidence_tool.create_pilot_evidence(
        draft=draft,
        output=tmp_path / "pilot.json",
        windows_build=19045,
        confirmations=_confirmations(),
    )

    assert evidence["CLEAN_PC_PILOT"] == "PASS"
    assert evidence["products"] == {
        key: record(draft / filename)
        for key, filename in PRODUCT_FILES.items()
    }
    assert evidence["runtime"] == record(draft / RUNTIME_FILE)
    assert evidence["network_modes"] == [
        "Direct",
        "VPN",
        "SingBoxHttp",
        "SingBoxHttps",
    ]
    assert evidence["privacy"]["personal_data_included"] is False


def test_pilot_evidence_requires_every_confirmation(tmp_path: Path):
    confirmations = _confirmations()
    confirmations["installer_to_launch_center_handoff"] = False

    with pytest.raises(ValueError, match="handoff"):
        evidence_tool.create_pilot_evidence(
            draft=_draft(tmp_path / "draft"),
            output=tmp_path / "pilot.json",
            windows_build=19045,
            confirmations=confirmations,
        )


def test_pilot_evidence_rejects_unsupported_windows(tmp_path: Path):
    with pytest.raises(ValueError, match="Windows build"):
        evidence_tool.create_pilot_evidence(
            draft=_draft(tmp_path / "draft"),
            output=tmp_path / "pilot.json",
            windows_build=18363,
            confirmations=_confirmations(),
        )
