"""Build and validate the atomic K-7 release set."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import os
from pathlib import Path

REQUIRED = (
    "codex-base",
    "claude-base",
    "opencode-base",
    "llm-foundation-installer",
    "officecli",
    "officecli-exporter",
    "k7-revit-suite",
)
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
PUBLIC_CHANNELS = {"Stable", "Public"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(payload: dict, *, verify_files: bool = True, base_dir: Path | None = None) -> None:
    if payload.get("schema_version") != 1:
        raise ValueError("release-set schema differs")
    if payload.get("release_set_id") != "K-7":
        raise ValueError("release_set_id must be K-7")
    channel = payload.get("channel")
    if channel not in {"InternalUnsigned", *PUBLIC_CHANNELS}:
        raise ValueError("unknown release channel")
    components = payload.get("components")
    if not isinstance(components, list):
        raise ValueError("components must be a list")
    ids = [row.get("id") for row in components if isinstance(row, dict)]
    if sorted(ids) != sorted(REQUIRED) or len(ids) != len(set(ids)):
        raise ValueError("partial or duplicate release set")
    for row in components:
        if not VERSION.fullmatch(str(row.get("version", ""))):
            raise ValueError("invalid component version: {}".format(row.get("id")))
        if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))):
            raise ValueError("invalid component hash: {}".format(row.get("id")))
        if not isinstance(row.get("bytes"), int) or row["bytes"] <= 0:
            raise ValueError("invalid component size: {}".format(row.get("id")))
        path = Path(str(row.get("path", "")))
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
        if verify_files and (
            not path.is_file()
            or path.stat().st_size != row["bytes"]
            or sha256(path) != row["sha256"]
        ):
            raise ValueError("component bytes differ: {}".format(row.get("id")))
    gates = payload.get("gates")
    if not isinstance(gates, dict) or "TECHNICAL_READY" not in gates:
        raise ValueError("release gates are incomplete")
    if channel in PUBLIC_CHANNELS:
        if any(value != "PASS" for value in gates.values()):
            raise ValueError("partial stable/public releases are forbidden")
        if payload.get("signed") is not True or payload.get("publication_allowed") is not True:
            raise ValueError("stable/public release must be signed and publishable")
        if payload.get("public_distribution_allowed") is not True:
            raise ValueError("stable/public release must allow public distribution")
    elif (
        payload.get("signed") is not False
        or payload.get("publication_allowed") is not True
        or payload.get("internal_distribution_allowed") is not True
        or payload.get("public_distribution_allowed") is not False
    ):
        raise ValueError(
            "InternalUnsigned must be publishable only as an unsigned "
            "internal distribution"
        )


def build(channel: str, components: list[list[str]], gates: list[str]) -> dict:
    rows = []
    for component_id, version, raw_path in components:
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise ValueError("component is missing: {}".format(path))
        rows.append(
            {
                "id": component_id,
                "version": version,
                "path": str(path),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    gate_map = {}
    for item in gates:
        name, separator, value = item.partition("=")
        if not separator or not name or not value:
            raise ValueError("gate must be NAME=VALUE")
        gate_map[name] = value
    payload = {
        "schema_version": 1,
        "release_set_id": "K-7",
        "channel": channel,
        "signed": False,
        "publication_allowed": channel == "InternalUnsigned",
        "internal_distribution_allowed": channel == "InternalUnsigned",
        "public_distribution_allowed": False,
        "components": sorted(rows, key=lambda row: row["id"]),
        "gates": dict(sorted(gate_map.items())),
    }
    validate(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    builder = sub.add_parser("build")
    builder.add_argument("--channel", required=True, choices=["InternalUnsigned", "Stable", "Public"])
    builder.add_argument("--component", nargs=3, action="append", required=True, metavar=("ID", "VERSION", "PATH"))
    builder.add_argument("--gate", action="append", default=[])
    builder.add_argument("--output", type=Path, required=True)
    checker = sub.add_parser("validate")
    checker.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        payload = build(args.channel, args.component, args.gate)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output_root = args.output.resolve().parent
        for row in payload["components"]:
            row["path"] = Path(os.path.relpath(row["path"], output_root)).as_posix()
        validate(payload, base_dir=output_root)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        validate(
            json.loads(args.manifest.read_text(encoding="utf-8")),
            base_dir=args.manifest.resolve().parent,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
