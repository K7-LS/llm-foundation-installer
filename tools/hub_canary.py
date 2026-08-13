from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import installer_release  # noqa: E402


TARGET_CLIENT = {
    "claude": "2.1.218",
    "codex": "0.146.0-alpha.3.1",
    "opencode": "1.18.13",
}


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def build_hub_canary_evidence(
    *,
    bundle_binding: dict[str, str],
    product_results: dict[str, dict[str, str]],
    runtime_result: dict[str, str],
    target_results: dict[str, dict[str, str]],
) -> dict[str, Any]:
    valid = (
        set(bundle_binding)
        == {
            "manifest_sha256",
            "installer_sha256",
            "launch_center_sha256",
            "runtime_sha256",
        }
        and all(_valid_sha256(value) for value in bundle_binding.values())
        and product_results
        == installer_release.EXPECTED_PRODUCT_CANARY
        and runtime_result == installer_release.EXPECTED_RUNTIME_CANARY
        and target_results
        == {
            target: installer_release.EXPECTED_TARGET_LIFECYCLE
            for target in installer_release.TARGETS
        }
    )
    if not valid:
        raise ValueError("Employee edition hub canary inputs are not accepted")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "target": "employee_edition",
        "version": installer_release.VERSION,
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "bundle": bundle_binding,
        "products": product_results,
        "runtime": runtime_result,
        "targets": target_results,
        "model_requests": 0,
        "unexpected_network": 0,
        "credentials_included": False,
        "personal_data_included": False,
        "INSTALLER_HUB_CANARY": "PASS",
    }
    evidence["evidence_body_sha256"] = evidence_body_sha256(evidence)
    return evidence


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_json(
    executable: Path,
    arguments: list[str],
    *,
    cwd: Path,
    timeout: int = 120,
) -> dict[str, Any]:
    result = subprocess.run(
        [str(executable), *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"edition command failed ({result.returncode}): "
            + (result.stderr or result.stdout)[-2000:]
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("edition command returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise RuntimeError("edition command returned a non-object")
    return payload


def _verify_product(
    executable: Path,
    bundle: Path,
    product: str,
) -> dict[str, str]:
    role = installer_release.PRODUCT_ROLES[product]
    product_value = _run_json(
        executable,
        ["--product-json"],
        cwd=bundle,
        timeout=30,
    )
    if (
        product_value.get("edition_id") != "Employee"
        or product_value.get("product_role") != role
        or product_value.get("targets")
        != [
            "chrome-browser",
            "codex-cli",
            "codex-desktop",
            "claude-code",
            "opencode-cli",
            "opencode-desktop",
            "vscode-codex",
        ]
    ):
        raise ValueError(f"Employee {product} identity differs")
    self_test = _run_json(
        executable,
        ["--self-test-json"],
        cwd=bundle,
        timeout=30,
    )
    if (
        self_test.get("version") != installer_release.VERSION
        or self_test.get("targets") != list(installer_release.TARGETS)
        or self_test.get("engine_validated") is not True
        or self_test.get("automatic_network") is not False
        or self_test.get("telemetry") is not False
        or self_test.get("reverse_flow") is not False
    ):
        raise ValueError(f"Employee {product} self-test differs")
    catalog = _run_json(
        executable,
        ["--catalog-json"],
        cwd=bundle,
        timeout=30,
    )
    rows = catalog.get("targets")
    states = (
        {
            row.get("id"): row.get("package_state")
            for row in rows
            if isinstance(row, dict)
        }
        if isinstance(rows, list)
        else {}
    )
    if (
        states
        != {
            target: "accepted"
            for target in installer_release.TARGETS
        }
        or catalog.get("install_enabled") is not True
        or catalog.get("provider_eligibility") != "PASS"
    ):
        raise ValueError(f"Employee {product} catalog differs")
    if product == "installer":
        sibling = _run_json(
            executable,
            ["--resolve-sibling-json", str(bundle)],
            cwd=bundle,
            timeout=30,
        )
        if (
            sibling.get("status") != "RESOLVED"
            or sibling.get("edition_id") != "Employee"
            or sibling.get("product_role") != "LaunchCenter"
            or Path(str(sibling.get("executable_path"))).resolve()
            != (
                bundle
                / installer_release.PRODUCT_FILES["launch_center"]
            ).resolve()
        ):
            raise ValueError("Employee Installer sibling handoff differs")
    return dict(installer_release.EXPECTED_PRODUCT_CANARY[product])


def _sentinel_paths(home: Path, target: str) -> dict[Path, bytes]:
    if target == "codex":
        return {
            home / ".codex" / "auth.json": b'{"auth":"preserve"}\n',
            home / ".codex" / "sessions" / "s.json": b"session\n",
        }
    if target == "claude":
        return {
            home / ".claude" / ".credentials.json": (
                b'{"auth":"preserve"}\n'
            ),
            home / ".claude" / "projects" / "session.json": b"session\n",
        }
    return {
        home / ".config" / "opencode" / "auth.json": (
            b'{"auth":"preserve"}\n'
        ),
        home / ".local" / "share" / "opencode" / "session.json": (
            b"session\n"
        ),
    }


def _run_target(
    executable: Path,
    bundle: Path,
    target: str,
    root: Path,
) -> dict[str, str]:
    isolated_home = root / target
    isolated_home.mkdir(parents=True)
    sentinels = _sentinel_paths(isolated_home, target)
    for path, payload in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    statuses: dict[str, str] = {}
    for command in ("plan", "install", "doctor", "inventory", "rollback"):
        result = _run_json(
            executable,
            [
                "--workflow-json",
                command,
                target,
                str(isolated_home),
                TARGET_CLIENT[target],
            ],
            cwd=bundle,
            timeout=180,
        )
        statuses[command] = str(result.get("status"))
    preserved = all(
        path.is_file() and path.read_bytes() == payload
        for path, payload in sentinels.items()
    )
    result = {
        "status": (
            "PASS"
            if statuses
            == {
                "plan": "READY",
                "install": "CANONICAL",
                "doctor": "CANONICAL",
                "inventory": "INSTALLED",
                "rollback": "ROLLED_BACK",
            }
            and preserved
            else "NOT_PASS"
        ),
        **statuses,
        "preserved_data": "PASS" if preserved else "NOT_PASS",
    }
    if result != installer_release.EXPECTED_TARGET_LIFECYCLE:
        raise ValueError(f"Employee target workflow differs: {target}")
    return result


def _verify_runtime(
    executable: Path,
    bundle: Path,
    isolated_home: Path,
) -> dict[str, str]:
    value = _run_json(
        executable,
        ["--ensure-runtime-json", str(isolated_home)],
        cwd=bundle,
        timeout=180,
    )
    if (
        value.get("status") != "VERIFIED"
        or value.get("runtime_id") != "sing-box"
        or value.get("version") != "1.13.14"
        or value.get("archive_sha256")
        != _sha256(bundle / installer_release.RUNTIME_FILE)
    ):
        raise ValueError("Employee runtime verification differs")
    return dict(installer_release.EXPECTED_RUNTIME_CANARY)


def _write_new(path: Path, value: object) -> None:
    if path.exists():
        raise RuntimeError("hub canary evidence exists; refusing overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the isolated zero-model hub canary for the exact Employee "
            "edition bundle. Default is a zero-action dry-run."
        )
    )
    parser.add_argument("--execute-approved-hub-canary", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/employee-hub-canary.json"),
    )
    arguments = parser.parse_args()
    plan = {
        "schema_version": 1,
        "would_execute": bool(arguments.execute_approved_hub_canary),
        "version": installer_release.VERSION,
        "products": list(installer_release.PRODUCT_FILES),
        "runtime": "sing-box-1.13.14",
        "targets": list(installer_release.TARGETS),
        "workflow": [
            "product-identity",
            "self-test",
            "catalog",
            "sibling-handoff",
            "runtime-bootstrap",
            "plan",
            "install",
            "doctor",
            "inventory",
            "rollback",
        ],
        "isolated_home": True,
        "model_requests": 0,
    }
    if not arguments.execute_approved_hub_canary:
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if arguments.bundle is None:
        raise SystemExit("--bundle is required for execution")
    bundle = arguments.bundle.resolve()
    installer_release.validate_bundle(bundle)
    executables = {
        product: bundle / filename
        for product, filename in installer_release.PRODUCT_FILES.items()
    }
    products = {
        product: _verify_product(executable, bundle, product)
        for product, executable in executables.items()
    }
    with tempfile.TemporaryDirectory(
        prefix="employee-hub-canary-"
    ) as raw:
        isolated_root = Path(raw)
        runtime = _verify_runtime(
            executables["launch_center"],
            bundle,
            isolated_root / "runtime-home",
        )
        targets = {
            target: _run_target(
                executables["installer"],
                bundle,
                target,
                isolated_root / "workflow-homes",
            )
            for target in installer_release.TARGETS
        }
    evidence = build_hub_canary_evidence(
        bundle_binding={
            "manifest_sha256": _sha256(
                bundle / "bundle-manifest.json"
            ),
            "installer_sha256": _sha256(executables["installer"]),
            "launch_center_sha256": _sha256(
                executables["launch_center"]
            ),
            "runtime_sha256": _sha256(
                bundle / installer_release.RUNTIME_FILE
            ),
        },
        product_results=products,
        runtime_result=runtime,
        target_results=targets,
    )
    _write_new(arguments.output.resolve(), evidence)
    print(
        json.dumps(
            {
                "INSTALLER_HUB_CANARY": "PASS",
                "model_requests": 0,
                "output": str(arguments.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
