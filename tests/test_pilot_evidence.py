from __future__ import annotations

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
    (root / "LLMFoundationInstaller.exe").write_bytes(b"MZpilot")
    (root / "release-manifest.json").write_text(
        '{"channel":"draft"}\n',
        encoding="utf-8",
    )
    return root


def _confirmations() -> dict[str, bool]:
    return {
        name: True
        for name in pilot_release.REQUIRED_PILOT_CHECKS
    }


def test_create_pilot_evidence_is_pii_free_and_exactly_bound(
    tmp_path: Path,
):
    draft = _draft(tmp_path / "draft")
    output = tmp_path / "pilot.json"

    evidence = evidence_tool.create_pilot_evidence(
        draft=draft,
        output=output,
        windows_build=19045,
        network_mode="VPN",
        confirmations=_confirmations(),
    )

    assert evidence["CLEAN_PC_PILOT"] == "PASS"
    assert evidence["machine"] == {
        "clean_windows_x64": True,
        "windows_build": 19045,
        "admin_used": False,
    }
    assert evidence["checks"] == {
        name: "PASS"
        for name in pilot_release.REQUIRED_PILOT_CHECKS
    }
    assert evidence["privacy"] == {
        "credentials_included": False,
        "personal_data_included": False,
        "machine_identifier_included": False,
    }
    assert evidence["evidence_body_sha256"] == (
        evidence_tool.evidence_body_sha256(evidence)
    )
    assert json.loads(output.read_text(encoding="utf-8")) == evidence


def test_create_pilot_evidence_requires_every_explicit_confirmation(
    tmp_path: Path,
):
    confirmations = _confirmations()
    confirmations["rollback"] = False

    with pytest.raises(ValueError, match="rollback"):
        evidence_tool.create_pilot_evidence(
            draft=_draft(tmp_path / "draft"),
            output=tmp_path / "pilot.json",
            windows_build=19045,
            network_mode="Direct",
            confirmations=confirmations,
        )


@pytest.mark.parametrize(
    ("windows_build", "network_mode"),
    [(18363, "Direct"), (19045, "Tor")],
)
def test_create_pilot_evidence_rejects_unsupported_machine_or_network(
    tmp_path: Path,
    windows_build: int,
    network_mode: str,
):
    with pytest.raises(ValueError):
        evidence_tool.create_pilot_evidence(
            draft=_draft(tmp_path / "draft"),
            output=tmp_path / "pilot.json",
            windows_build=windows_build,
            network_mode=network_mode,
            confirmations=_confirmations(),
        )
