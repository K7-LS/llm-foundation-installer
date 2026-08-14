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
    "installer_launch",
    "launch_center_launch",
    "installer_to_launch_center_handoff",
    "codex_desktop",
    "codex_cli",
    "opencode_cli",
    "opencode_oauth",
    "claude_code",
    "claude_live_login",
    "provider_eligibility",
    "direct_mode",
    "vpn_mode",
    "singbox_http_mode",
    "singbox_https_mode",
    "interactive_guide",
    "install_doctor_inventory_rollback",
    "preserved_user_data",
    "no_reverse_flow",
)
NETWORK_MODES = ("Direct", "VPN", "SingBoxHttp", "SingBoxHttps")
EXPECTED_DRAFT_VERDICTS = installer_release.EXPECTED_DRAFT_VERDICTS
EXPECTED_STABLE_VERDICTS = {
    **installer_release.EXPECTED_BUNDLE_VERDICTS,
    "INSTALLER_HUB_CANARY": "PASS",
    "HOME_PC_CANARY": "PASS",
    "EMPLOYEE_INSTALLER_INTERNAL": "PASS",
    "RELEASE_INTEGRITY": "PENDING_PUBLICATION",
}


@dataclass(frozen=True)
class EmployeeRelease:
    root: Path
    product_paths: dict[str, Path]
    runtime_path: Path
    release_manifest_path: Path
    acceptance_evidence_path: Path
    pilot_evidence_path: Path
    sha256sums_path: Path

    @property
    def installer_path(self) -> Path:
        return self.product_paths["installer"]

    @property
    def launch_center_path(self) -> Path:
        return self.product_paths["launch_center"]


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
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{path.name} is missing or unsafe")
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
    if not draft.is_dir() or draft.is_symlink():
        raise ValueError("Employee draft root is missing or unsafe")
    manifest = _load_json(draft / "release-manifest.json")
    artifacts = manifest.get("artifacts")
    products = manifest.get("products")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("app_id") != "k7-ai-employee-edition"
        or manifest.get("edition_id") != "Employee"
        or manifest.get("version") != installer_release.VERSION
        or manifest.get("tag") != installer_release.TAG
        or manifest.get("channel") != "draft"
        or manifest.get("distribution_mode") != "InternalUnsigned"
        or manifest.get("verdicts") != EXPECTED_DRAFT_VERDICTS
        or manifest.get("evidence_body_sha256")
        != installer_release.evidence_body_sha256(manifest)
        or not isinstance(artifacts, dict)
        or not isinstance(products, dict)
    ):
        raise ValueError("Employee draft release manifest is invalid")
    expected_artifact_names = {
        path.name
        for path in draft.iterdir()
        if path.is_file()
    }.difference({"release-manifest.json", "SHA256SUMS"})
    if set(artifacts) != expected_artifact_names:
        raise ValueError("Employee draft artifact inventory differs")
    for name, record in artifacts.items():
        path = draft / str(name)
        if (
            not path.is_file()
            or path.is_symlink()
            or not isinstance(record, dict)
            or _record(path) != record
        ):
            raise ValueError(f"Employee draft artifact differs: {name}")
    expected_products = {
        product: _record(draft / filename)
        for product, filename in installer_release.PRODUCT_FILES.items()
    }
    if products != expected_products:
        raise ValueError("Employee draft product binding differs")
    if manifest.get("runtime") != _record(
        draft / installer_release.RUNTIME_FILE
    ):
        raise ValueError("Employee draft runtime binding differs")
    sums = draft / "SHA256SUMS"
    if (
        not sums.is_file()
        or sums.is_symlink()
        or sums.read_text(encoding="utf-8") != _expected_sums(draft)
    ):
        raise ValueError("Employee draft SHA256SUMS differs")
    return manifest


def _validate_pilot(evidence: dict[str, Any], draft: Path) -> None:
    machine = evidence.get("machine")
    checks = evidence.get("checks")
    privacy = evidence.get("privacy")
    expected_products = {
        product: _record(draft / filename)
        for product, filename in installer_release.PRODUCT_FILES.items()
    }
    valid = (
        evidence.get("schema_version") == 1
        and evidence.get("target") == "employee_edition"
        and evidence.get("version") == installer_release.VERSION
        and isinstance(evidence.get("recorded_at_utc"), str)
        and evidence.get("products") == expected_products
        and evidence.get("runtime")
        == _record(draft / installer_release.RUNTIME_FILE)
        and evidence.get("draft_release_manifest_sha256")
        == _record(draft / "release-manifest.json")["sha256"]
        and isinstance(machine, dict)
        and machine.get("environment_kind") == "owner-attested-home-pc"
        and machine.get("owner_authorized") is True
        and machine.get("windows_x64") is True
        and isinstance(machine.get("windows_build"), int)
        and not isinstance(machine.get("windows_build"), bool)
        and machine["windows_build"] >= 19041
        and machine.get("admin_used") is False
        and evidence.get("network_modes") == list(NETWORK_MODES)
        and isinstance(checks, dict)
        and set(checks) == set(REQUIRED_PILOT_CHECKS)
        and all(checks[name] == "PASS" for name in REQUIRED_PILOT_CHECKS)
        and privacy
        == {
            "credentials_included": False,
            "personal_data_included": False,
            "machine_identifier_included": False,
        }
        and evidence.get("HOME_PC_CANARY") == "PASS"
        and evidence.get("evidence_body_sha256")
        == installer_release.evidence_body_sha256(evidence)
    )
    if not valid:
        raise ValueError(
            "home-PC Employee pilot/canary evidence is invalid or unbound"
        )


def finalize_employee_release(
    *,
    draft: Path,
    pilot_evidence_path: Path,
    output: Path,
) -> EmployeeRelease:
    """Finalize stable metadata while preserving every accepted input byte."""

    draft = draft.resolve()
    output = output.resolve()
    draft_manifest = _validate_draft(draft)
    pilot_source = pilot_evidence_path.resolve()
    pilot_value = _load_json(pilot_source)
    _validate_pilot(pilot_value, draft)
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

    product_paths = {
        product: output / filename
        for product, filename in installer_release.PRODUCT_FILES.items()
    }
    runtime_path = output / installer_release.RUNTIME_FILE
    acceptance: dict[str, Any] = {
        "schema_version": 1,
        "target": "employee_edition",
        "version": installer_release.VERSION,
        "tag": installer_release.TAG,
        "distribution_mode": "InternalUnsigned",
        "products": {
            product: _record(path)
            for product, path in product_paths.items()
        },
        "runtime": _record(runtime_path),
        "draft_release_manifest_sha256": _record(
            draft / "release-manifest.json"
        )["sha256"],
        "hub_canary_evidence_sha256": _record(
            output / "hub-canary-evidence.json"
        )["sha256"],
        "pilot_evidence_sha256": _record(pilot_path)["sha256"],
        "verdicts": EXPECTED_STABLE_VERDICTS,
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
            "products": {
                product: _record(path)
                for product, path in product_paths.items()
            },
            "runtime": _record(runtime_path),
            "artifacts": artifacts,
            "promoted_from_draft_manifest_sha256": _record(
                draft / "release-manifest.json"
            )["sha256"],
            "pilot_acceptance_sha256": _record(pilot_path)["sha256"],
            "acceptance_evidence_sha256": _record(
                acceptance_path
            )["sha256"],
            "verdicts": EXPECTED_STABLE_VERDICTS,
        }
    )
    stable_manifest["evidence_body_sha256"] = (
        installer_release.evidence_body_sha256(stable_manifest)
    )
    manifest_path = output / "release-manifest.json"
    _write_new(manifest_path, _json_bytes(stable_manifest))
    sums = output / "SHA256SUMS"
    _write_new(sums, _expected_sums(output).encode("utf-8"))
    for product, filename in installer_release.PRODUCT_FILES.items():
        if product_paths[product].read_bytes() != (
            draft / filename
        ).read_bytes():
            raise AssertionError(
                f"pilot finalization changed {product} bytes"
            )
    if runtime_path.read_bytes() != (
        draft / installer_release.RUNTIME_FILE
    ).read_bytes():
        raise AssertionError("pilot finalization changed runtime bytes")
    return EmployeeRelease(
        root=output,
        product_paths=product_paths,
        runtime_path=runtime_path,
        release_manifest_path=manifest_path,
        acceptance_evidence_path=acceptance_path,
        pilot_evidence_path=pilot_path,
        sha256sums_path=sums,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Finalize employee-v0.4.0 after an owner-accepted home-PC canary "
            "without rebuilding either executable or the runtime."
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
                "PUBLIC_SIGNED_RELEASE": "DEFERRED_UNSIGNED",
                "products": {
                    product: _record(path)["sha256"]
                    for product, path in result.product_paths.items()
                },
                "runtime_sha256": _record(
                    result.runtime_path
                )["sha256"],
                "output": str(result.root),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
