"""Create hash-bound package input for an InternalUnsigned installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

TARGETS = {"codex": "codex-cli", "claude": "claude-code", "opencode": "opencode"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(path: Path) -> dict:
    return {"name": path.name, "sha256": digest(path), "bytes": path.stat().st_size}


def prepare(output: Path, sources: list[Path]) -> None:
    if output.exists():
        raise ValueError("output must not exist")
    by_target = {}
    for source in sources:
        manifest_path = source / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        target = manifest.get("target")
        if target not in TARGETS or target in by_target:
            raise ValueError("target set differs")
        asset = source / manifest["asset"]["name"]
        if (
            manifest.get("channel") != "candidate"
            or manifest.get("client", {}).get("id") != TARGETS[target]
            or not asset.is_file()
            or digest(asset) != manifest["asset"]["sha256"]
            or asset.stat().st_size != manifest["asset"]["bytes"]
        ):
            raise ValueError("candidate contract differs: {}".format(target))
        by_target[target] = (manifest, manifest_path, asset)
    if set(by_target) != set(TARGETS):
        raise ValueError("partial internal package set")
    for target in sorted(TARGETS):
        manifest, manifest_path, asset = by_target[target]
        destination = output / target
        destination.mkdir(parents=True)
        shutil.copy2(asset, destination / asset.name)
        shutil.copy2(manifest_path, destination / manifest_path.name)
        acceptance = {
            "schema_version": 1,
            "target": target,
            "channel": "InternalUnsigned",
            "TECHNICAL_READY": "PASS",
            "client": manifest["client"],
            "asset": record(destination / asset.name),
            "release_manifest": record(destination / manifest_path.name),
        }
        (destination / "internal-acceptance.json").write_text(
            json.dumps(acceptance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, action="append", required=True)
    args = parser.parse_args()
    prepare(args.output.resolve(), [path.resolve() for path in args.source])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
