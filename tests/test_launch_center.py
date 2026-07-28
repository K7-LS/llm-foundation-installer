from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPOSITORY / "tools" / "build-gui.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe")


def _build(
    output: Path,
    *,
    edition: str,
    product_role: str,
    client_lock: Path | None = None,
) -> Path:
    command = [
        str(POWERSHELL),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(BUILD_SCRIPT),
        "-OutputRoot",
        str(output),
        "-Edition",
        edition,
        "-ProductRole",
        product_role,
    ]
    if client_lock is not None:
        command.extend(
            [
                "-ClientSourcesLock",
                str(client_lock),
                "-AllowLocalTestSources",
            ]
        )
    result = subprocess.run(
        command,
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def _run_json(bundle: Path, *arguments: str) -> tuple[int, dict[str, object]]:
    result = subprocess.run(
        [str(bundle / "LLMFoundationInstaller.exe"), *arguments],
        cwd=bundle,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.stdout.strip(), result.stderr
    return result.returncode, json.loads(result.stdout)


@pytest.mark.parametrize(
    ("edition", "expected_targets"),
    [
        (
            "Employee",
            [
                "codex-cli",
                "codex-desktop",
                "opencode-cli",
                "opencode-desktop",
            ],
        ),
        (
            "Owner",
            [
                "codex-cli",
                "codex-desktop",
                "claude-code",
                "opencode-cli",
                "opencode-desktop",
            ],
        ),
    ],
)
def test_product_role_exposes_edition_bound_launch_targets(
    tmp_path: Path,
    edition: str,
    expected_targets: list[str],
) -> None:
    installer = _build(
        tmp_path / f"{edition}-installer",
        edition=edition,
        product_role="Installer",
    )
    center = _build(
        tmp_path / f"{edition}-center",
        edition=edition,
        product_role="LaunchCenter",
    )

    _, installer_value = _run_json(installer, "--product-json")
    _, center_value = _run_json(center, "--product-json")

    assert installer_value["app_id"] == "k7-ai-foundation-installer"
    assert installer_value["product_role"] == "Installer"
    assert center_value["app_id"] == "k7-ai-launch-center"
    assert center_value["product_role"] == "LaunchCenter"
    assert center_value["edition_id"] == edition
    assert center_value["targets"] == expected_targets


def test_exact_managed_desktop_resolution_is_hash_bound(
    tmp_path: Path,
) -> None:
    payload = b"managed-opencode-desktop-fixture\n"
    payload_hash = hashlib.sha256(payload).hexdigest()
    source_lock = tmp_path / "client-sources.lock.json"
    source_lock.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "official_only": False,
                "test_only": True,
                "platform": {
                    "os": "windows",
                    "architecture": "x64",
                    "minimum_build": 19041,
                },
                "clients": [
                    {
                        "id": "opencode-desktop",
                        "target": "opencode",
                        "display_name": "OpenCode Desktop",
                        "role": "desktop",
                        "required_for_base": False,
                        "required_for_employee": True,
                        "version": "1.0.0",
                        "source_kind": "download",
                        "url": "http://127.0.0.1:43117/opencode-desktop.exe",
                        "sha256": payload_hash,
                        "artifact_kind": "portable-exe",
                        "archive_entry": None,
                        "publisher": None,
                        "signature_required": False,
                        "install_mode": "managed-desktop",
                        "detect_commands": [],
                        "version_arguments": [],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    bundle = _build(
        tmp_path / "center",
        edition="Employee",
        product_role="LaunchCenter",
        client_lock=source_lock,
    )
    home = tmp_path / "home"
    executable = (
        home
        / ".llm-foundation"
        / "apps"
        / "opencode-desktop"
        / "1.0.0"
        / "opencode-desktop.exe"
    )
    executable.parent.mkdir(parents=True)
    executable.write_bytes(payload)
    record = executable.parents[1] / "current.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "client_id": "opencode-desktop",
                "version": "1.0.0",
                "relative_path": "1.0.0/opencode-desktop.exe",
                "sha256": payload_hash,
            }
        ),
        encoding="utf-8",
    )

    returncode, value = _run_json(
        bundle,
        "--resolve-launch-target-json",
        str(home),
        "opencode-desktop",
    )

    assert returncode == 0
    assert value == {
        "status": "RESOLVED",
        "target_id": "opencode-desktop",
        "client_id": "opencode-desktop",
        "role": "desktop",
        "executable_path": str(executable.resolve()),
        "sha256": payload_hash,
        "reason": None,
    }

    executable.write_bytes(payload + b"tampered")
    returncode, value = _run_json(
        bundle,
        "--resolve-launch-target-json",
        str(home),
        "opencode-desktop",
    )
    assert returncode == 20
    assert value["status"] == "BLOCKED"
    assert value["reason"] == "MANAGED_DESKTOP_INTEGRITY_FAILED"
