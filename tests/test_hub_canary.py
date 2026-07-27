from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "installer_hub_canary",
    ROOT / "tools" / "hub_canary.py",
)
assert SPEC and SPEC.loader
canary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = canary
SPEC.loader.exec_module(canary)


def _binding() -> dict[str, str]:
    return {
        "manifest_sha256": "a" * 64,
        "installer_sha256": "b" * 64,
    }


def _clients() -> dict[str, str]:
    return {
        "codex-cli": "0.146.0-alpha.3.1",
        "codex-desktop": "store-identity-verified",
        "claude-code": "2.1.218",
        "opencode-cli": "1.18.7",
        "opencode-desktop": "1.18.7",
    }


def _target() -> dict[str, str]:
    return {
        "status": "PASS",
        "plan": "READY",
        "install": "INSTALLED",
        "doctor": "HEALTHY",
        "inventory": "INSTALLED",
        "rollback": "ROLLED_BACK",
        "preserved_data": "PASS",
    }


def test_canary_evidence_is_fail_closed_and_privacy_safe():
    evidence = canary.build_hub_canary_evidence(
        bundle_binding=_binding(),
        clients=_clients(),
        target_results={
            target: _target()
            for target in ("codex", "claude", "opencode")
        },
    )

    assert evidence["INSTALLER_HUB_CANARY"] == "PASS"
    assert evidence["model_requests"] == 0
    assert evidence["unexpected_network"] == 0
    assert evidence["credentials_included"] is False
    assert canary.evidence_body_sha256(evidence) == (
        evidence["evidence_body_sha256"]
    )


def test_canary_evidence_rejects_unready_desktop_client():
    clients = _clients()
    clients["opencode-desktop"] = "missing"
    with pytest.raises(ValueError, match="canary"):
        canary.build_hub_canary_evidence(
            bundle_binding=_binding(),
            clients=clients,
            target_results={
                target: _target()
                for target in ("codex", "claude", "opencode")
            },
        )


def test_canary_evidence_rejects_non_identical_rollback():
    targets = {
        target: _target()
        for target in ("codex", "claude", "opencode")
    }
    targets["claude"]["preserved_data"] = "NOT_PASS"
    with pytest.raises(ValueError, match="canary"):
        canary.build_hub_canary_evidence(
            bundle_binding=_binding(),
            clients=_clients(),
            target_results=targets,
        )
