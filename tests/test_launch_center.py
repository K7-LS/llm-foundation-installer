from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import textwrap
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


def _find_csharp_compiler() -> Path | None:
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")),
        Path(os.environ.get("ProgramFiles", "C:/Program Files")),
    ]
    matches: list[Path] = []
    for root in candidates:
        matches.extend(
            root.glob(
                "Microsoft Visual Studio/*/*/MSBuild/Current/Bin/Roslyn/csc.exe"
            )
        )
    framework = Path(
        "C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    )
    if framework.is_file():
        matches.append(framework)
    return sorted(matches)[0] if matches else None


def _compile_environment_probe(path: Path) -> None:
    compiler = _find_csharp_compiler()
    if compiler is None:
        pytest.skip("C# compiler is unavailable")
    source = path.with_suffix(".cs")
    source.write_text(
        textwrap.dedent(
            """
            using System;
            using System.IO;
            public static class Probe
            {
                public static int Main()
                {
                    string output = Environment.GetEnvironmentVariable(
                        "K7_TEST_OUTPUT"
                    );
                    File.WriteAllText(
                        output,
                        "HTTP_PROXY=" +
                            (Environment.GetEnvironmentVariable(
                                "HTTP_PROXY"
                            ) ?? "<null>") + "\\n" +
                        "HTTPS_PROXY=" +
                            (Environment.GetEnvironmentVariable(
                                "HTTPS_PROXY"
                            ) ?? "<null>") + "\\n" +
                        "ALL_PROXY=" +
                            (Environment.GetEnvironmentVariable(
                                "ALL_PROXY"
                            ) ?? "<null>") + "\\n"
                    );
                    return 0;
                }
            }
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:exe",
            f"/out:{path}",
            str(source),
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


@pytest.mark.parametrize("route", ["Direct", "VPN"])
def test_direct_vpn_launch_exact_process_without_proxy_environment(
    tmp_path: Path,
    route: str,
) -> None:
    fixture = tmp_path / "environment-probe.exe"
    _compile_environment_probe(fixture)
    payload = fixture.read_bytes()
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
                        "url": "http://127.0.0.1:43117/environment-probe.exe",
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
        / "environment-probe.exe"
    )
    executable.parent.mkdir(parents=True)
    executable.write_bytes(payload)
    (executable.parents[1] / "current.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "client_id": "opencode-desktop",
                "version": "1.0.0",
                "relative_path": "1.0.0/environment-probe.exe",
                "sha256": payload_hash,
            }
        ),
        encoding="utf-8",
    )
    probe_output = tmp_path / f"{route}.txt"
    environment = dict(os.environ)
    environment.update(
        {
            "K7_TEST_OUTPUT": str(probe_output),
            "HTTP_PROXY": "http://sentinel.invalid:8080",
            "HTTPS_PROXY": "http://sentinel.invalid:8080",
            "ALL_PROXY": "socks5://sentinel.invalid:1080",
        }
    )
    result = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--launch-target-json",
            str(home),
            "opencode-desktop",
            route,
        ],
        cwd=bundle,
        text=True,
        capture_output=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )
    assert result.stdout.strip(), result.stderr
    value = json.loads(result.stdout)

    assert result.returncode == 0
    assert value["status"] == "PASS"
    assert value["transport"] == route
    assert value["uses_proxy"] is False
    assert value["cleanup_verified"] is True
    assert value["process_exit_code"] == 0
    assert value["executable_path"] == str(executable.resolve())
    assert probe_output.read_text(encoding="utf-8").splitlines() == [
        "HTTP_PROXY=<null>",
        "HTTPS_PROXY=<null>",
        "ALL_PROXY=<null>",
    ]


def test_installer_handoff_requires_matching_edition_and_manifest_hash(
    tmp_path: Path,
) -> None:
    installer = _build(
        tmp_path / "employee-installer",
        edition="Employee",
        product_role="Installer",
    )
    employee_center = _build(
        tmp_path / "employee-center",
        edition="Employee",
        product_role="LaunchCenter",
    )
    owner_center = _build(
        tmp_path / "owner-center",
        edition="Owner",
        product_role="LaunchCenter",
    )

    returncode, value = _run_json(
        installer,
        "--resolve-sibling-json",
        str(employee_center),
    )
    assert returncode == 0
    assert value["status"] == "RESOLVED"
    assert value["edition_id"] == "Employee"
    assert value["product_role"] == "LaunchCenter"
    assert value["executable_path"] == str(
        (employee_center / "LLMFoundationInstaller.exe").resolve()
    )

    returncode, value = _run_json(
        installer,
        "--resolve-sibling-json",
        str(owner_center),
    )
    assert returncode == 20
    assert value["reason"] == "SIBLING_EDITION_MISMATCH"

    with (employee_center / "LLMFoundationInstaller.exe").open("ab") as stream:
        stream.write(b"tampered")
    returncode, value = _run_json(
        installer,
        "--resolve-sibling-json",
        str(employee_center),
    )
    assert returncode == 20
    assert value["reason"] == "SIBLING_INTEGRITY_FAILED"
