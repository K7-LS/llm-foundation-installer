from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
REFERENCE = REPOSITORY / "docs" / "reference" / "ai-singbox-launchers"
MANIFEST = REFERENCE / "SOURCE-MANIFEST.json"
EXPECTED = {
    "Start-AI-SingBox-HTTPS.ps1": {
        "bytes": 36565,
        "sha256": (
            "5b5a10ae706e479f08c79377abc204e682f73494ee83e98117af7a7ba91f661d"
        ),
    },
    "Start-AI-SingBox-HTTP.ps1": {
        "bytes": 36413,
        "sha256": (
            "4687c15cafb749e8c9a25c93ee2fa7ed3fd9d27a6cc260d79f05ce040281e0d9"
        ),
    },
    "Test-AI-SingBox-Launchers.ps1": {
        "bytes": 5787,
        "sha256": (
            "ceba3cb018c937125573eae8448a7639c6aff5b90e42e19ccc4a6d599e116bf2"
        ),
    },
    "AI-SINGBOX-LAUNCHERS-HANDOFF.md": {
        "bytes": 9647,
        "sha256": (
            "ee102e8fa61ad3840674bf8c440204550b917f0119b5ff408c33e5ff09b6d654"
        ),
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_launcher_reference_manifest_is_exact_and_private() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["source_kind"] == "owner_supplied_behavior_reference"
    assert manifest["executed_during_intake"] is False
    assert set(manifest["files"]) == set(EXPECTED)
    for name, expected in EXPECTED.items():
        path = REFERENCE / name
        assert path.stat().st_size == expected["bytes"]
        assert _sha256(path) == expected["sha256"]
        assert manifest["files"][name] == {
            **expected,
            "contains_credentials": False,
        }
    serialized = json.dumps(manifest).lower()
    for forbidden in (
        "c:\\\\users",
        "password",
        "username",
        "proxy_host",
        "downloads",
    ):
        assert forbidden not in serialized
