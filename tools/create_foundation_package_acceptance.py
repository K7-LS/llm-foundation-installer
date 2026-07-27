from __future__ import annotations

import argparse
import json
from pathlib import Path

from foundation_release import create_package_acceptance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--release-verification", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.output.exists():
        raise SystemExit("package acceptance exists; refusing overwrite")
    result = create_package_acceptance(
        arguments.manifest.resolve(),
        arguments.evidence.resolve(),
        arguments.release_verification.resolve(),
        arguments.output.resolve(),
    )
    print(
        json.dumps(
            {
                "target": "foundation",
                "package_acceptance": result["package_acceptance"],
                "output": str(arguments.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
