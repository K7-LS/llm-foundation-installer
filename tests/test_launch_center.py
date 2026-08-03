from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import textwrap
import uuid
import winreg
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPOSITORY / "tools" / "build-gui.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe")
TEST_REGISTRY_PREFIX = r"Software\K7AITests"


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


@pytest.fixture
def process_only_registry_key() -> str:
    subkey = (
        TEST_REGISTRY_PREFIX
        + "\\process-only-"
        + uuid.uuid4().hex
    )
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        subkey,
        0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    ) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(
            key,
            "ProxyServer",
            0,
            winreg.REG_SZ,
            "process-only-sentinel.invalid:8899",
        )
    try:
        yield subkey
    finally:
        assert subkey.startswith(TEST_REGISTRY_PREFIX + "\\")
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            subkey,
            0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            while True:
                try:
                    value_name = winreg.EnumValue(key, 0)[0]
                except OSError:
                    break
                winreg.DeleteValue(key, value_name)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)


def _proxy_registry_snapshot(
    subkey: str,
) -> dict[str, tuple[object, int]]:
    assert subkey.startswith(TEST_REGISTRY_PREFIX + "\\")
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        subkey,
        0,
        winreg.KEY_READ,
    ) as key:
        return {
            name: winreg.QueryValueEx(key, name)
            for name in ("ProxyEnable", "ProxyServer")
        }


def _launch_target_tags(path: Path) -> set[str]:
    root = ET.parse(path).getroot()
    presentation = "http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xaml = "http://schemas.microsoft.com/winfx/2006/xaml"
    launch_list = root.find(
        f".//{{{presentation}}}ListBox"
        f"[@{{{xaml}}}Name='LaunchTargetList']"
    )
    assert launch_list is not None
    return {
        item.attrib["Tag"]
        for item in launch_list.findall(f"{{{presentation}}}ListBoxItem")
    }


def _write_test_only_client_lock(path: Path) -> None:
    path.write_text(
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
                        "url": (
                            "http://127.0.0.1:43117/"
                            "opencode-desktop.exe"
                        ),
                        "sha256": "0" * 64,
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


def _write_vscode_record(
    path: Path,
    **overrides: object,
) -> None:
    record: dict[str, object] = {
        "executable_path": r"C:\fixture\Code.exe",
        "signature_status": "Valid",
        "signer_subject": (
            "CN=Microsoft Corporation, O=Microsoft Corporation, "
            "L=Redmond, S=Washington, C=US"
        ),
        "extension_publisher": "OpenAI",
        "extension_name": "chatgpt",
        "extension_path": (
            r"C:\fixture\.vscode\extensions\openai.chatgpt-1.0.0"
        ),
        "code_running": False,
    }
    record.update(overrides)
    path.write_text(json.dumps(record), encoding="utf-8")


def _find_vscode_candidate() -> Path | None:
    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data)
            / "Programs"
            / "Microsoft VS Code"
            / "Code.exe"
        )
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "Microsoft VS Code" / "Code.exe")
    for raw_entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = Path(raw_entry.strip().strip('"'))
        if not str(entry):
            continue
        candidates.append(entry / "Code.exe")
        if (entry / "code.cmd").is_file():
            candidates.append(entry.parent / "Code.exe")
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


@pytest.fixture(scope="module")
def vscode_test_bundle(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    root = tmp_path_factory.mktemp("vscode-test-bundle")
    source_lock = root / "client-sources.lock.json"
    _write_test_only_client_lock(source_lock)
    return _build(
        root / "center",
        edition="Employee",
        product_role="LaunchCenter",
        client_lock=source_lock,
    )


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
                "vscode-codex",
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
                "vscode-codex",
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


def test_complete_target_catalog_matches_real_launch_center_cards(
    tmp_path: Path,
) -> None:
    employee = _build(
        tmp_path / "employee-center",
        edition="Employee",
        product_role="LaunchCenter",
    )
    owner = _build(
        tmp_path / "owner-center",
        edition="Owner",
        product_role="LaunchCenter",
    )
    _, employee_product = _run_json(employee, "--product-json")
    _, owner_product = _run_json(owner, "--product-json")
    employee_targets = [
        "codex-cli",
        "codex-desktop",
        "opencode-cli",
        "opencode-desktop",
        "vscode-codex",
    ]

    assert employee_product["targets"] == employee_targets
    assert _launch_target_tags(
        REPOSITORY / "src" / "gui" / "LaunchCenterEmployeeView.xaml"
    ) == set(employee_targets)
    assert _launch_target_tags(
        REPOSITORY / "src" / "gui" / "LaunchCenterOwnerView.xaml"
    ) == set(owner_product["targets"])


def test_installer_binary_exposes_launch_center_fallback(
    tmp_path: Path,
) -> None:
    installer = _build(
        tmp_path / "employee-installer",
        edition="Employee",
        product_role="Installer",
    )

    returncode, value = _run_json(
        installer,
        "--launch-center-product-json",
    )

    assert returncode == 0
    assert value["app_id"] == "k7-ai-launch-center"
    assert value["product_role"] == "LaunchCenter"
    assert value["edition_id"] == "Employee"


def test_ui_launch_selection_json_shows_vscode_correlation(
    tmp_path: Path,
) -> None:
    center = _build(
        tmp_path / "employee-center",
        edition="Employee",
        product_role="LaunchCenter",
    )

    returncode, value = _run_json(
        center,
        "--ui-launch-selection-json",
        "vscode-codex",
    )

    assert returncode == 0
    assert value["selected_target"] == "vscode-codex"
    assert value["selection_visual"] == "VISIBLE"
    assert value["button_content"] == "Запустить VS Code →"
    assert value["client_display"] == "VS CODE — CODEX"
    assert value["evidence_status"] == (
        "Локальный ID OpenAI.chatgpt будет обнаружен при запуске"
    )


def test_vscode_trusted_record_resolves_only_from_test_only_bundle(
    tmp_path: Path,
    vscode_test_bundle: Path,
) -> None:
    record = tmp_path / "vscode-record.json"
    _write_vscode_record(record)

    returncode, value = _run_json(
        vscode_test_bundle,
        "--resolve-vscode-record-json",
        str(tmp_path / "home"),
        str(record),
    )

    assert returncode == 0
    assert value["status"] == "RESOLVED"
    assert value["target_id"] == "vscode-codex"
    assert value["client_id"] == "codex-desktop"
    assert value["role"] == "desktop"
    assert value["launch_mode"] == "executable"
    assert value["executable_path"] == r"C:\fixture\Code.exe"
    assert value["extension_path"] == (
        r"C:\fixture\.vscode\extensions\openai.chatgpt-1.0.0"
    )
    assert value["reason"] is None


def test_vscode_missing_extension_returns_official_install_action(
    tmp_path: Path,
    vscode_test_bundle: Path,
) -> None:
    record = tmp_path / "vscode-record.json"
    _write_vscode_record(
        record,
        extension_publisher=None,
        extension_name=None,
        extension_path=None,
    )

    returncode, value = _run_json(
        vscode_test_bundle,
        "--resolve-vscode-record-json",
        str(tmp_path / "home"),
        str(record),
    )

    assert returncode == 20
    assert value["reason"] == "CODEX_EXTENSION_ID_NOT_DETECTED"
    assert value["action"] == (
        "Идентификатор расширения OpenAI.chatgpt не обнаружен. "
        "Откройте страницу Marketplace и установите расширение вручную."
    )
    assert value["official_url"] == (
        "https://marketplace.visualstudio.com/"
        "items?itemName=OpenAI.chatgpt"
    )


def test_vscode_mutation_between_signature_and_second_hash_is_blocked(
    tmp_path: Path,
    vscode_test_bundle: Path,
) -> None:
    home = tmp_path / "home"
    executable = home / "fixture" / "Code.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"before-signature-check\n")
    mutation = tmp_path / "mutation.bin"
    mutation.write_bytes(b"after-signature-check\n")
    record = tmp_path / "vscode-record.json"
    _write_vscode_record(record, executable_path=str(executable))

    returncode, value = _run_json(
        vscode_test_bundle,
        "--resolve-vscode-mutating-record-json",
        str(home),
        str(record),
        str(mutation),
    )

    assert executable.read_bytes() == b"after-signature-check\n"
    assert returncode == 20
    assert value["status"] == "BLOCKED"
    assert value["reason"] == "VSCODE_INTEGRITY_CHANGED"


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        (
            {"signature_status": "NotSigned"},
            "VSCODE_SIGNATURE_INVALID",
        ),
        (
            {
                "signer_subject": (
                    "CN=Contoso Corporation, O=Contoso Corporation, "
                    "L=Redmond, S=Washington, C=US"
                )
            },
            "VSCODE_PUBLISHER_INVALID",
        ),
        (
            {
                "signer_subject": (
                    "CN=Microsoft Corporation, O=Contoso Corporation, "
                    "L=Redmond, S=Washington, C=US"
                )
            },
            "VSCODE_PUBLISHER_INVALID",
        ),
        (
            {"extension_publisher": "Contoso"},
            "CODEX_EXTENSION_ID_NOT_DETECTED",
        ),
        (
            {"extension_name": "codex"},
            "CODEX_EXTENSION_ID_NOT_DETECTED",
        ),
        (
            {"code_running": True},
            "VSCODE_ALREADY_RUNNING",
        ),
    ],
)
def test_vscode_trust_failures_have_stable_reasons(
    tmp_path: Path,
    vscode_test_bundle: Path,
    overrides: dict[str, object],
    expected_reason: str,
) -> None:
    record = tmp_path / "vscode-record.json"
    _write_vscode_record(record, **overrides)

    returncode, value = _run_json(
        vscode_test_bundle,
        "--resolve-vscode-record-json",
        str(tmp_path / "home"),
        str(record),
    )

    assert returncode == 20
    assert value["status"] == "BLOCKED"
    assert value["reason"] == expected_reason
    if expected_reason == "CODEX_EXTENSION_ID_NOT_DETECTED":
        assert value["action"] == (
            "Идентификатор расширения OpenAI.chatgpt не обнаружен. "
            "Откройте страницу Marketplace и установите расширение вручную."
        )
        assert value["official_url"] == (
            "https://marketplace.visualstudio.com/"
            "items?itemName=OpenAI.chatgpt"
        )
    if expected_reason == "VSCODE_ALREADY_RUNNING":
        assert value["action"] == (
            "Сохраните работу, закройте все окна VS Code "
            "и повторите запуск."
        )


def test_vscode_missing_id_is_shown_as_wpf_marketplace_guidance(
    tmp_path: Path,
    vscode_test_bundle: Path,
) -> None:
    home = tmp_path / "home"
    record = tmp_path / "vscode-record.json"
    _write_vscode_record(
        record,
        extension_publisher=None,
        extension_name=None,
        extension_path=None,
    )

    returncode, value = _run_json(
        vscode_test_bundle,
        "--ui-vscode-resolution-json",
        str(home),
        str(record),
    )

    assert returncode == 0
    assert value == {
        "resolution_reason": "CODEX_EXTENSION_ID_NOT_DETECTED",
        "action_text": (
            "Идентификатор расширения OpenAI.chatgpt не обнаружен. "
            "Откройте страницу Marketplace и установите расширение вручную."
        ),
        "action_visibility": "Visible",
        "official_url": (
            "https://marketplace.visualstudio.com/"
            "items?itemName=OpenAI.chatgpt"
        ),
        "official_link_visibility": "Visible",
        "official_link_content": "Открыть страницу OpenAI.chatgpt →",
    }
    assert not (home / ".vscode" / "extensions").exists()


def test_vscode_normal_resolver_checks_installed_signed_code(
    tmp_path: Path,
) -> None:
    candidate = _find_vscode_candidate()
    if candidate is None:
        pytest.skip(
            "VS Code Code.exe is unavailable in approved install paths or PATH"
        )
    center = _build(
        tmp_path / "employee-center",
        edition="Employee",
        product_role="LaunchCenter",
    )
    home = tmp_path / "home"
    extension = (
        home
        / ".vscode"
        / "extensions"
        / "openai.chatgpt-normal-resolver-test"
    )
    extension.mkdir(parents=True)
    (extension / "package.json").write_text(
        json.dumps({"publisher": "OpenAI", "name": "chatgpt"}),
        encoding="utf-8",
    )

    returncode, value = _run_json(
        center,
        "--resolve-launch-target-json",
        str(home),
        "vscode-codex",
    )

    if value["reason"] == "VSCODE_ALREADY_RUNNING":
        assert returncode == 20
        assert value["action"] == (
            "Сохраните работу, закройте все окна VS Code "
            "и повторите запуск."
        )
    else:
        assert returncode == 0
        assert value["status"] == "RESOLVED"
        assert Path(str(value["executable_path"])).resolve() == candidate
        assert value["sha256"] == hashlib.sha256(candidate.read_bytes()).hexdigest()
        assert Path(str(value["extension_path"])).resolve() == extension.resolve()


def test_vscode_test_record_command_rejects_production_source_lock(
    tmp_path: Path,
) -> None:
    center = _build(
        tmp_path / "employee-center",
        edition="Employee",
        product_role="LaunchCenter",
    )
    record = tmp_path / "vscode-record.json"
    _write_vscode_record(record)

    returncode, value = _run_json(
        center,
        "--resolve-vscode-record-json",
        str(tmp_path / "home"),
        str(record),
    )

    assert returncode == 20
    assert value["status"] == "BLOCKED"
    assert value["reason"] == "TEST_ONLY_SOURCE_REQUIRED"


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
        "launch_mode": "executable",
        "executable_path": str(executable.resolve()),
        "sha256": payload_hash,
        "activation_id": None,
        "package_full_name": None,
        "official_url": None,
        "action": None,
        "extension_path": None,
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


def test_exact_managed_cli_resolution_requires_install_record(
    tmp_path: Path,
) -> None:
    payload = b"managed-opencode-cli-fixture\n"
    executable_hash = hashlib.sha256(payload).hexdigest()
    source_hash = "a" * 64
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
                        "id": "opencode-cli",
                        "target": "opencode",
                        "display_name": "OpenCode CLI",
                        "role": "cli",
                        "required_for_base": True,
                        "required_for_employee": True,
                        "version": "1.0.0",
                        "source_kind": "download",
                        "url": "http://127.0.0.1:43117/opencode.exe",
                        "sha256": source_hash,
                        "artifact_kind": "portable-exe",
                        "archive_entry": None,
                        "publisher": None,
                        "signature_required": False,
                        "install_mode": "managed-bin",
                        "detect_commands": ["opencode.exe"],
                        "version_arguments": ["--version"],
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
    executable = home / ".llm-foundation" / "bin" / "opencode.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(payload)

    returncode, value = _run_json(
        bundle,
        "--resolve-launch-target-json",
        str(home),
        "opencode-cli",
    )
    assert returncode == 20
    assert value["reason"] == "MANAGED_COMMAND_NOT_FOUND"

    record = (
        home
        / ".llm-foundation"
        / "clients"
        / "opencode-cli"
        / "current.json"
    )
    record.parent.mkdir(parents=True)
    record.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "client_id": "opencode-cli",
                "version": "1.0.0",
                "relative_path": ".llm-foundation/bin/opencode.exe",
                "sha256": executable_hash,
                "source_sha256": source_hash,
            }
        ),
        encoding="utf-8",
    )

    returncode, value = _run_json(
        bundle,
        "--resolve-launch-target-json",
        str(home),
        "opencode-cli",
    )
    assert returncode == 0
    assert value == {
        "status": "RESOLVED",
        "target_id": "opencode-cli",
        "client_id": "opencode-cli",
        "role": "cli",
        "launch_mode": "executable",
        "executable_path": str(executable.resolve()),
        "sha256": executable_hash,
        "activation_id": None,
        "package_full_name": None,
        "official_url": None,
        "action": None,
        "extension_path": None,
        "reason": None,
    }

    executable.write_bytes(payload + b"tampered")
    returncode, value = _run_json(
        bundle,
        "--resolve-launch-target-json",
        str(home),
        "opencode-cli",
    )
    assert returncode == 20
    assert value["reason"] == "MANAGED_COMMAND_INTEGRITY_FAILED"


def test_store_launch_resolution_is_manifest_and_hash_bound(
    tmp_path: Path,
) -> None:
    bundle = _build(
        tmp_path / "center",
        edition="Employee",
        product_role="LaunchCenter",
    )
    home = tmp_path / "home"
    package_root = tmp_path / "WindowsApps" / (
        "OpenAI.Codex_26.721.4979.0_x64__2p2nqsd0c76g0"
    )
    executable = package_root / "app" / "ChatGPT.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"store-codex-fixture\n")
    executable_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    record = tmp_path / "store-record.json"
    record.write_text(
        json.dumps(
            {
                "present": True,
                "name": "OpenAI.Codex",
                "publisher": "CN=50BDFD77-8903-4850-9FFE-6E8522F64D5B",
                "signature_kind": "Store",
                "architecture": "X64",
                "version": "26.721.4979.0",
                "package_full_name": (
                    "OpenAI.Codex_26.721.4979.0_x64__2p2nqsd0c76g0"
                ),
                "package_family_name": "OpenAI.Codex_2p2nqsd0c76g0",
                "install_location": str(package_root),
                "application_id": "App",
                "executable": "app/ChatGPT.exe",
                "entry_point": "Windows.FullTrustApplication",
            }
        ),
        encoding="utf-8",
    )

    returncode, value = _run_json(
        bundle,
        "--resolve-store-launch-target-record-json",
        "codex-desktop",
        str(record),
    )

    assert returncode == 0
    assert value == {
        "status": "RESOLVED",
        "target_id": "codex-desktop",
        "client_id": "codex-desktop",
        "role": "desktop",
        "launch_mode": "appx",
        "executable_path": str(executable.resolve()),
        "sha256": executable_hash,
        "activation_id": "OpenAI.Codex_2p2nqsd0c76g0!App",
        "package_full_name": (
            "OpenAI.Codex_26.721.4979.0_x64__2p2nqsd0c76g0"
        ),
        "official_url": None,
        "action": None,
        "extension_path": None,
        "reason": None,
    }

    executable.write_bytes(b"tampered")
    returncode, value = _run_json(
        bundle,
        "--resolve-store-launch-target-record-json",
        "codex-desktop",
        str(record),
    )
    assert returncode == 0
    assert value["sha256"] != executable_hash


def test_store_launcher_uses_appx_activation_manager_and_exact_pid() -> None:
    source = (
        REPOSITORY / "src" / "gui" / "ClientLauncher.cs"
    ).read_text(encoding="utf-8")
    installer_source = (
        REPOSITORY / "src" / "gui" / "InstallerApp.cs"
    ).read_text(encoding="utf-8")
    proxy_source = (
        REPOSITORY / "src" / "gui" / "SystemProxyLease.cs"
    ).read_text(encoding="utf-8")

    assert "IApplicationActivationManager" in source
    assert "ActivateApplication" in source
    assert "Process.GetProcessById" in source
    assert "PROCESS_PROXY_NOT_SUPPORTED" not in source
    assert "SystemProxyLease.Acquire" in source
    assert "public static SingBoxSessionResult StopActiveRoute()" in source
    assert "contract.Stop.Click +=" in installer_source
    assert "ClientLauncher.StopActiveRoute()" in installer_source
    assert "ClientLauncher.HasActiveRoute()" in installer_source
    assert installer_source.index(
        "Task<LauncherSessionResult> launchTask"
    ) < installer_source.index("ClientLauncher.HasActiveRoute()")
    assert "InternetSetOption" in proxy_source


@pytest.mark.parametrize("route", ["Direct", "VPN"])
def test_direct_vpn_launch_exact_process_without_proxy_environment(
    tmp_path: Path,
    route: str,
    process_only_registry_key: str,
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
            "K7_SYSTEM_PROXY_TEST_SUBKEY": process_only_registry_key,
        }
    )
    registry_before = _proxy_registry_snapshot(
        process_only_registry_key
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
    assert "TEST_SYSTEM_PROXY_GUARD_ACTIVE" in value["lifecycle"]
    assert _proxy_registry_snapshot(
        process_only_registry_key
    ) == registry_before
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
