import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_internal", ROOT / "tools" / "prepare_internal_packages.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def candidate(root: Path, target: str, client: str):
    root.mkdir()
    asset = root / f"{target}-base-1.0.0.zip"
    asset.write_bytes(target.encode())
    manifest = {
        "schema_version": 1,
        "target": target,
        "version": "1.0.0",
        "channel": "candidate",
        "client": {"id": client, "supported_version": "1.0.0"},
        "asset": MODULE.record(asset),
    }
    (root / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_internal_package_set_is_atomic_and_hash_bound(tmp_path):
    sources = [
        candidate(tmp_path / target, target, client)
        for target, client in MODULE.TARGETS.items()
    ]
    output = tmp_path / "out"
    MODULE.prepare(output, sources)
    assert sorted(path.name for path in output.iterdir()) == ["claude", "codex", "opencode"]
    for target in MODULE.TARGETS:
        acceptance = json.loads((output / target / "internal-acceptance.json").read_text())
        assert acceptance["channel"] == "InternalUnsigned"
        assert acceptance["TECHNICAL_READY"] == "PASS"


def test_partial_internal_package_set_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="partial"):
        MODULE.prepare(
            tmp_path / "out",
            [candidate(tmp_path / "codex", "codex", "codex-cli")],
        )
