from __future__ import annotations

import argparse
import json
from pathlib import Path

from foundation_release import prepare_foundation_release


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare exact foundation-engine-v0.2.1 release assets from "
            "synthetic-accepted engine bytes."
        )
    )
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--acceptance-evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    result = prepare_foundation_release(
        engine_root=arguments.engine_root,
        acceptance_evidence_path=arguments.acceptance_evidence,
        output=arguments.output,
    )
    print(
        json.dumps(
            {
                "status": "FOUNDATION_RELEASE_ASSETS_PREPARED",
                "tag": "foundation-engine-v0.2.1",
                "asset": str(result.asset_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
