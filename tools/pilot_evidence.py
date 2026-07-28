from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import installer_release  # noqa: E402
import pilot_release  # noqa: E402


NETWORK_MODES = ("Direct", "VPN", "SingBoxHttp", "SingBoxHttps")


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def _record(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def create_pilot_evidence(
    *,
    draft: Path,
    output: Path,
    windows_build: int,
    confirmations: dict[str, bool],
) -> dict[str, object]:
    """Record explicit, PII-free clean-PC Employee pilot confirmations."""

    draft = draft.resolve()
    output = output.resolve()
    required_paths = {
        "manifest": draft / "release-manifest.json",
        "runtime": draft / installer_release.RUNTIME_FILE,
        **{
            product: draft / filename
            for product, filename in installer_release.PRODUCT_FILES.items()
        },
    }
    if (
        not draft.is_dir()
        or draft.is_symlink()
        or any(
            not path.is_file() or path.is_symlink()
            for path in required_paths.values()
        )
    ):
        raise ValueError("Employee draft release binding is missing or unsafe")
    if output.exists():
        raise ValueError("pilot evidence output already exists")
    try:
        output.relative_to(draft)
    except ValueError:
        pass
    else:
        raise ValueError("pilot evidence output must be outside draft")
    if (
        not isinstance(windows_build, int)
        or isinstance(windows_build, bool)
        or windows_build < 19041
    ):
        raise ValueError("pilot Windows build is unsupported")
    expected = set(pilot_release.REQUIRED_PILOT_CHECKS)
    if set(confirmations) != expected:
        raise ValueError("pilot confirmation inventory differs")
    failed = sorted(
        name for name in expected if confirmations.get(name) is not True
    )
    if failed:
        raise ValueError(
            "pilot check is not explicitly confirmed: " + ", ".join(failed)
        )
    evidence: dict[str, object] = {
        "schema_version": 1,
        "target": "employee_edition",
        "version": installer_release.VERSION,
        "recorded_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "products": {
            product: _record(required_paths[product])
            for product in installer_release.PRODUCT_FILES
        },
        "runtime": _record(required_paths["runtime"]),
        "draft_release_manifest_sha256": _record(
            required_paths["manifest"]
        )["sha256"],
        "machine": {
            "clean_windows_x64": True,
            "windows_build": windows_build,
            "admin_used": False,
        },
        "network_modes": list(NETWORK_MODES),
        "checks": {
            name: "PASS"
            for name in pilot_release.REQUIRED_PILOT_CHECKS
        },
        "privacy": {
            "credentials_included": False,
            "personal_data_included": False,
            "machine_identifier_included": False,
        },
        "CLEAN_PC_PILOT": "PASS",
    }
    evidence["evidence_body_sha256"] = evidence_body_sha256(evidence)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_bytes(_json_bytes(evidence))
    os.replace(temporary, output)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create PII-free Employee clean-PC pilot evidence only after "
            "every required check has been explicitly confirmed."
        )
    )
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--windows-build", required=True, type=int)
    parser.add_argument(
        "--confirm-clean-windows-x64",
        action="store_true",
    )
    parser.add_argument("--confirm-no-admin", action="store_true")
    for name in pilot_release.REQUIRED_PILOT_CHECKS:
        parser.add_argument(
            "--confirm-" + name.replace("_", "-"),
            action="store_true",
            dest="confirm_" + name,
        )
    arguments = parser.parse_args()
    if (
        not arguments.confirm_clean_windows_x64
        or not arguments.confirm_no_admin
    ):
        raise SystemExit(
            "clean Windows x64 and no-admin use must be explicitly confirmed"
        )
    confirmations = {
        name: bool(getattr(arguments, "confirm_" + name))
        for name in pilot_release.REQUIRED_PILOT_CHECKS
    }
    evidence = create_pilot_evidence(
        draft=arguments.draft,
        output=arguments.output,
        windows_build=arguments.windows_build,
        confirmations=confirmations,
    )
    print(
        json.dumps(
            {
                "CLEAN_PC_PILOT": evidence["CLEAN_PC_PILOT"],
                "output": str(arguments.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
