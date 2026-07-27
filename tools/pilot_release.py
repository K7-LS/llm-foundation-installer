from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import installer_release  # noqa: E402


REQUIRED_PILOT_CHECKS = (
    "windows_preflight",
    "codex_desktop",
    "codex_cli",
    "claude_code",
    "opencode_desktop",
    "opencode_cli",
    "opencode_oauth",
    "simple_chat",
    "discovery_16_37",
    "sync_base",
    "rollback",
    "preserved_user_data",
    "no_reverse_flow",
)
EXPECTED_DRAFT_VERDICTS = {
    "FULL_RELEASE_CODEX": "PASS",
    "FULL_RELEASE_CLAUDE": "PASS",
    "FULL_RELEASE_OPENCODE": "PASS",
    "PROGRAM_RELEASE": "3/3",
    "INSTALLER_HUB_CANARY": "PASS",
    "CLEAN_PC_PILOT": "PENDING",
    "EMPLOYEE_INSTALLER_INTERNAL": "PENDING_PILOT",
    "PUBLIC_SIGNED_RELEASE": "DEFERRED_BY_OWNER",
}


@dataclass(frozen=True)
class EmployeeRelease:
    root: Path
    installer_path: Path
    release_manifest_path: Path
    acceptance_evidence_path: Path
    pilot_evidence_path: Path
    sha256sums_path: Path


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _record(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite release file: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _copy_exact(source: Path, destination: Path) -> None:
    payload = source.read_bytes()
    _write_new(destination, payload)
    if destination.read_bytes() != payload:
        raise AssertionError(f"exact-byte copy failed: {source.name}")


def _expected_sums(root: Path) -> str:
    lines = [
        f"{_record(path)['sha256']}  {path.name}"
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    return "\n".join(lines) + "\n"


def _validate_draft(draft: Path) -> dict[str, Any]:
    manifest_path = draft / "release-manifest.json"
    manifest = _load_json(manifest_path)
    installer = draft / "LLMFoundationInstaller.exe"
    artifacts = manifest.get("artifacts")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("app_id") != "llm-foundation-installer"
        or manifest.get("version") != "0.3.0"
        or manifest.get("tag") != "installer-v0.3.0"
        or manifest.get("channel") != "draft"
        or manifest.get("distribution_mode") != "internal_unsigned"
        or manifest.get("installer") != _record(installer)
        or manifest.get("verdicts") != EXPECTED_DRAFT_VERDICTS
        or manifest.get("evidence_body_sha256")
        != installer_release.evidence_body_sha256(manifest)
        or not isinstance(artifacts, dict)
    ):
        raise ValueError("installer draft release manifest is invalid")
    for name, record in artifacts.items():
        path = draft / str(name)
        if (
            not path.is_file()
            or not isinstance(record, dict)
            or _record(path) != record
        ):
            raise ValueError(f"installer draft artifact differs: {name}")
    sums = draft / "SHA256SUMS"
    if (
        not sums.is_file()
        or sums.read_text(encoding="utf-8") != _expected_sums(draft)
    ):
        raise ValueError("installer draft SHA256SUMS differs")
    return manifest


def _validate_pilot(
    evidence: dict[str, Any],
    draft: Path,
) -> None:
    machine = evidence.get("machine")
    checks = evidence.get("checks")
    privacy = evidence.get("privacy")
    valid = (
        evidence.get("schema_version") == 1
        and evidence.get("target") == "installer"
        and evidence.get("version") == "0.3.0"
        and isinstance(evidence.get("recorded_at_utc"), str)
        and evidence.get("installer_sha256")
        == _record(draft / "LLMFoundationInstaller.exe")["sha256"]
        and evidence.get("draft_release_manifest_sha256")
        == _record(draft / "release-manifest.json")["sha256"]
        and isinstance(machine, dict)
        and machine.get("clean_windows_x64") is True
        and isinstance(machine.get("windows_build"), int)
        and not isinstance(machine.get("windows_build"), bool)
        and machine["windows_build"] >= 19041
        and machine.get("admin_used") is False
        and evidence.get("network_mode") in {"Direct", "VPN", "Proxy"}
        and isinstance(checks, dict)
        and set(checks) == set(REQUIRED_PILOT_CHECKS)
        and all(checks[name] == "PASS" for name in REQUIRED_PILOT_CHECKS)
        and privacy
        == {
            "credentials_included": False,
            "personal_data_included": False,
            "machine_identifier_included": False,
        }
        and evidence.get("CLEAN_PC_PILOT") == "PASS"
        and evidence.get("evidence_body_sha256")
        == installer_release.evidence_body_sha256(evidence)
    )
    if not valid:
        raise ValueError("clean-PC pilot evidence is invalid or unbound")


def finalize_employee_release(
    *,
    draft: Path,
    pilot_evidence_path: Path,
    output: Path,
) -> EmployeeRelease:
    """Finalize stable metadata while preserving the exact draft EXE."""

    draft = draft.resolve()
    output = output.resolve()
    draft_manifest = _validate_draft(draft)
    pilot_source = pilot_evidence_path.resolve()
    pilot_evidence = _load_json(pilot_source)
    _validate_pilot(pilot_evidence, draft)
    if output.exists():
        raise ValueError("employee release output must not exist")
    output.mkdir(parents=True)

    for source in sorted(draft.iterdir(), key=lambda item: item.name):
        if not source.is_file() or source.name in {
            "release-manifest.json",
            "SHA256SUMS",
        }:
            continue
        _copy_exact(source, output / source.name)
    pilot_path = output / "pilot-acceptance.json"
    _copy_exact(pilot_source, pilot_path)

    installer = output / "LLMFoundationInstaller.exe"
    acceptance: dict[str, Any] = {
        "schema_version": 1,
        "target": "installer",
        "version": "0.3.0",
        "tag": "installer-v0.3.0",
        "distribution_mode": "internal_unsigned",
        "installer": _record(installer),
        "draft_release_manifest_sha256": _record(
            draft / "release-manifest.json"
        )["sha256"],
        "hub_canary_evidence_sha256": _record(
            output / "hub-canary-evidence.json"
        )["sha256"],
        "pilot_evidence_sha256": _record(pilot_path)["sha256"],
        "verdicts": {
            "FULL_RELEASE_CODEX": "PASS",
            "FULL_RELEASE_CLAUDE": "PASS",
            "FULL_RELEASE_OPENCODE": "PASS",
            "PROGRAM_RELEASE": "3/3",
            "INSTALLER_HUB_CANARY": "PASS",
            "CLEAN_PC_PILOT": "PASS",
            "EMPLOYEE_INSTALLER_INTERNAL": "PASS",
            "PUBLIC_SIGNED_RELEASE": "DEFERRED_BY_OWNER",
            "RELEASE_INTEGRITY": "PENDING_PUBLICATION",
        },
        "privacy": {
            "credentials_included": False,
            "personal_data_included": False,
            "telemetry_included": False,
        },
    }
    acceptance["evidence_body_sha256"] = (
        installer_release.evidence_body_sha256(acceptance)
    )
    acceptance_path = output / "acceptance-evidence.json"
    _write_new(acceptance_path, _json_bytes(acceptance))

    artifacts = {
        path.name: _record(path)
        for path in sorted(output.iterdir(), key=lambda item: item.name)
        if path.is_file()
    }
    stable_manifest = dict(draft_manifest)
    stable_manifest.pop("evidence_body_sha256", None)
    stable_manifest.update(
        {
            "channel": "stable",
            "installer": _record(installer),
            "artifacts": artifacts,
            "promoted_from_draft_manifest_sha256": _record(
                draft / "release-manifest.json"
            )["sha256"],
            "pilot_acceptance_sha256": _record(pilot_path)["sha256"],
            "acceptance_evidence_sha256": _record(
                acceptance_path
            )["sha256"],
            "verdicts": acceptance["verdicts"],
        }
    )
    stable_manifest["evidence_body_sha256"] = (
        installer_release.evidence_body_sha256(stable_manifest)
    )
    manifest_path = output / "release-manifest.json"
    _write_new(manifest_path, _json_bytes(stable_manifest))
    sums = output / "SHA256SUMS"
    _write_new(sums, _expected_sums(output).encode("utf-8"))
    if installer.read_bytes() != (
        draft / "LLMFoundationInstaller.exe"
    ).read_bytes():
        raise AssertionError("pilot finalization changed installer bytes")
    return EmployeeRelease(
        root=output,
        installer_path=installer,
        release_manifest_path=manifest_path,
        acceptance_evidence_path=acceptance_path,
        pilot_evidence_path=pilot_path,
        sha256sums_path=sums,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Finalize installer-v0.3.0 after an accepted clean-PC pilot "
            "without rebuilding the installer executable."
        )
    )
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument("--pilot-evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    result = finalize_employee_release(
        draft=arguments.draft,
        pilot_evidence_path=arguments.pilot_evidence,
        output=arguments.output,
    )
    print(
        json.dumps(
            {
                "EMPLOYEE_INSTALLER_INTERNAL": "PASS",
                "PUBLIC_SIGNED_RELEASE": "DEFERRED_BY_OWNER",
                "installer_sha256": _record(
                    result.installer_path
                )["sha256"],
                "output": str(result.root),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
