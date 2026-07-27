from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from foundation_release import (
    REPOSITORY,
    build_release_verification,
)


def _run(command: list[str]) -> bytes:
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace")[-2000:]
        )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--asset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--gh", default="gh")
    arguments = parser.parse_args()
    if arguments.output.exists():
        raise SystemExit("release verification exists; refusing overwrite")
    manifest = json.loads(
        arguments.manifest.read_text(encoding="utf-8")
    )
    tag = str(manifest["tag"])
    gh = arguments.gh
    gh_version = _run([gh, "--version"]).decode(
        "utf-8", errors="replace"
    ).strip()
    verification = build_release_verification(
        manifest_path=arguments.manifest.resolve(),
        asset_path=arguments.asset.resolve(),
        release_api=json.loads(
            _run(
                [
                    gh,
                    "api",
                    f"repos/{REPOSITORY}/releases/tags/{tag}",
                ]
            )
        ),
        release_attestation_output=_run(
            [
                gh,
                "release",
                "verify",
                tag,
                "-R",
                REPOSITORY,
                "--format",
                "json",
            ]
        ),
        asset_attestation_output=_run(
            [
                gh,
                "release",
                "verify-asset",
                tag,
                str(arguments.asset.resolve()),
                "-R",
                REPOSITORY,
                "--format",
                "json",
            ]
        ),
        gh_version=gh_version,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = arguments.output.with_name(arguments.output.name + ".tmp")
    temporary.write_text(
        json.dumps(
            verification,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, arguments.output)
    print(
        json.dumps(
            {
                "RELEASE_INTEGRITY": "PASS",
                "output": str(arguments.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
