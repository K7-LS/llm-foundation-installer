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


EXPECTED_CLIENTS = installer_release.EXPECTED_CANARY_CLIENTS
EXPECTED_TARGET = installer_release.EXPECTED_TARGET_LIFECYCLE
TARGET_CLIENT = {
    "codex": ("codex-cli", "0.146.0-alpha.3.1"),
    "claude": ("claude-code", "2.1.218"),
    "opencode": ("opencode", "1.18.7"),
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
    clients: dict[str, str],
    target_results: dict[str, dict[str, str]],
) -> dict[str, Any]:
    valid = (
        set(bundle_binding) == {
            "manifest_sha256",
            "installer_sha256",
        }
        and all(_valid_sha256(value) for value in bundle_binding.values())
        and clients == EXPECTED_CLIENTS
        and set(target_results) == set(installer_release.TARGETS)
        and all(
            target_results.get(target) == EXPECTED_TARGET
            for target in installer_release.TARGETS
        )
    )
    if not valid:
        raise ValueError("installer hub canary inputs are not accepted")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "target": "installer",
        "version": "0.3.0",
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "bundle": bundle_binding,
        "clients": clients,
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
            f"installer command failed ({result.returncode}): "
            + (result.stderr or result.stdout)[-2000:]
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("installer command returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise RuntimeError("installer command returned a non-object")
    return payload


def _verify_self_test(executable: Path, bundle: Path) -> None:
    result = _run_json(
        executable,
        ["--self-test-json"],
        cwd=bundle,
        timeout=30,
    )
    expected = {
        "app_id": "llm-foundation-installer",
        "engine_validated": True,
        "foundation_protocol": 1,
        "network": "user-initiated-only",
        "automatic_network": False,
        "reverse_flow": False,
        "targets": ["codex", "claude", "opencode"],
        "telemetry": False,
        "version": "0.3.0",
    }
    if result != expected:
        raise ValueError("installer self-test differs from release contract")


def _verify_catalog(executable: Path, bundle: Path) -> None:
    result = _run_json(
        executable,
        ["--catalog-json"],
        cwd=bundle,
        timeout=30,
    )
    rows = result.get("targets")
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
        states != {target: "accepted" for target in installer_release.TARGETS}
        or result.get("install_enabled") is not True
        or result.get("provider_eligibility") != "PASS"
    ):
        raise ValueError("installer catalog is not fully accepted")


def _verify_clients(
    executable: Path,
    bundle: Path,
    user_home: Path,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for client_id, expected in EXPECTED_CLIENTS.items():
        plan = _run_json(
            executable,
            [
                "--client-plan-json",
                str(user_home),
                client_id,
            ],
            cwd=bundle,
            timeout=30,
        )
        if plan.get("status") != "READY":
            raise ValueError(f"hub client is not ready: {client_id}")
        if client_id == "codex-desktop":
            if plan.get("detected_state") != "exact_identity":
                raise ValueError("Codex Store identity was not verified")
        elif plan.get("detected_version") != expected:
            raise ValueError(f"hub client version differs: {client_id}")
        result[client_id] = expected
    return result


def _sentinel_paths(home: Path, target: str) -> dict[Path, bytes]:
    if target == "codex":
        return {
            home / ".codex" / "auth.json": b'{"auth":"preserve"}\n',
            home / ".codex" / "sessions" / "s.json": b"session\n",
        }
    if target == "claude":
        return {
            home / ".claude.json": b'{"auth":"preserve"}\n',
            home / ".claude" / "projects" / "s.json": b"session\n",
        }
    return {
        home
        / ".config"
        / "opencode"
        / "auth.json": b'{"auth":"preserve"}\n',
        home
        / ".local"
        / "share"
        / "opencode"
        / "session.json": b"session\n",
    }


def _run_target(
    executable: Path,
    bundle: Path,
    target: str,
    root: Path,
) -> dict[str, str]:
    home = root / target
    home.mkdir(parents=True)
    sentinels = _sentinel_paths(home, target)
    for path, payload in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    _, version = TARGET_CLIENT[target]
    statuses: dict[str, str] = {}
    for command in ("plan", "install", "doctor", "inventory", "rollback"):
        result = _run_json(
            executable,
            [
                "--workflow-json",
                command,
                target,
                str(home),
                version,
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
                "install": "INSTALLED",
                "doctor": "HEALTHY",
                "inventory": "INSTALLED",
                "rollback": "ROLLED_BACK",
            }
            and preserved
            else "NOT_PASS"
        ),
        **statuses,
        "preserved_data": "PASS" if preserved else "NOT_PASS",
    }
    if result != EXPECTED_TARGET:
        raise ValueError(f"hub target workflow differs: {target}")
    return result


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
            "Run the approved no-model hub canary for the exact employee "
            "installer bundle. Default is a zero-action dry-run."
        )
    )
    parser.add_argument("--execute-approved-hub-canary", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/installer-hub-canary.json"),
    )
    arguments = parser.parse_args()
    plan = {
        "schema_version": 1,
        "would_execute": bool(arguments.execute_approved_hub_canary),
        "version": "0.3.0",
        "clients": EXPECTED_CLIENTS,
        "targets": list(installer_release.TARGETS),
        "workflow": [
            "self-test",
            "catalog",
            "client-detection",
            "plan",
            "install",
            "doctor",
            "inventory",
            "rollback",
        ],
        "model_requests": 0,
    }
    if not arguments.execute_approved_hub_canary:
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if arguments.bundle is None:
        raise SystemExit("--bundle is required for execution")
    bundle = arguments.bundle.resolve()
    installer_release.validate_bundle(bundle)
    executable = bundle / "LLMFoundationInstaller.exe"
    _verify_self_test(executable, bundle)
    _verify_catalog(executable, bundle)
    clients = _verify_clients(
        executable,
        bundle,
        arguments.home.resolve(),
    )
    with tempfile.TemporaryDirectory(prefix="installer-hub-canary-") as raw:
        root = Path(raw)
        targets = {
            target: _run_target(executable, bundle, target, root)
            for target in installer_release.TARGETS
        }
    evidence = build_hub_canary_evidence(
        bundle_binding={
            "manifest_sha256": _sha256(
                bundle / "bundle-manifest.json"
            ),
            "installer_sha256": _sha256(executable),
        },
        clients=clients,
        target_results=targets,
    )
    _write_new(arguments.output.resolve(), evidence)
    print(
        json.dumps(
            {
                "INSTALLER_HUB_CANARY": "PASS",
                "output": str(arguments.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
