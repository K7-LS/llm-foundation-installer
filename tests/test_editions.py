import hashlib
import json
import os
import shutil
import struct
import subprocess
import re
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPOSITORY / "tools" / "build-gui.ps1"
EDITION_BUILD_SCRIPT = REPOSITORY / "tools" / "build-edition.ps1"
APP_VERSION = (REPOSITORY / "APP_VERSION").read_text(
    encoding="utf-8"
).strip()
POWERSHELL = (
    os.environ.get("K7_TEST_POWERSHELL")
    or shutil.which("pwsh")
    or shutil.which("powershell.exe")
)


def _run_build(
    tmp_path: Path,
    *,
    edition: str | None,
    product_role: str | None,
) -> subprocess.CompletedProcess[str]:
    output = tmp_path / (
        f"{edition or 'missing'}-{product_role or 'missing'}"
    )
    command = [
        str(POWERSHELL),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(BUILD_SCRIPT),
        "-TestHooks",
        "-OutputRoot",
        str(output),
    ]
    if edition is not None:
        command.extend(["-Edition", edition])
    if product_role is not None:
        command.extend(["-ProductRole", product_role])
    return subprocess.run(
        command,
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        timeout=90,
    )


def _describe_edition(
    tmp_path: Path,
    edition: str,
    product_role: str,
) -> dict[str, object]:
    built = _run_build(
        tmp_path,
        edition=edition,
        product_role=product_role,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    output = tmp_path / f"{edition}-{product_role}"
    executables = sorted(output.glob("*.exe"))
    assert len(executables) == 1
    result = subprocess.run(
        [str(executables[0]), "--describe-edition"],
        cwd=output,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _render_preview(
    tmp_path: Path,
    edition: str,
    product_role: str,
) -> Path:
    built = _run_build(
        tmp_path,
        edition=edition,
        product_role=product_role,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    output = tmp_path / f"{edition}-{product_role}"
    executable = output / "LLMFoundationInstaller.exe"
    preview = output / "preview.png"
    result = subprocess.run(
        [str(executable), "--render-preview", str(preview)],
        cwd=output,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return preview


def _render_guide_preview(
    tmp_path: Path,
    edition: str,
    product_role: str,
) -> Path:
    built = _run_build(
        tmp_path,
        edition=edition,
        product_role=product_role,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    output = tmp_path / f"{edition}-{product_role}"
    executable = output / "LLMFoundationInstaller.exe"
    preview = output / "guide-preview.png"
    result = subprocess.run(
        [str(executable), "--render-guide-preview", str(preview)],
        cwd=output,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return preview


@pytest.mark.parametrize(
    (
        "edition",
        "product_role",
        "included",
        "required",
        "distribution_allowed",
        "theme_id",
        "owner_controlled",
    ),
    [
        (
            "Employee",
            "Installer",
            ["claude", "codex", "opencode"],
            ["claude", "codex", "opencode"],
            True,
            "K7Signal",
            False,
        ),
        (
            "Owner",
            "LaunchCenter",
            ["claude", "codex", "opencode"],
            ["claude", "codex", "opencode"],
            False,
            "SignalConsole",
            True,
        ),
    ],
)
def test_embedded_edition_contract(
    tmp_path: Path,
    edition: str,
    product_role: str,
    included: list[str],
    required: list[str],
    distribution_allowed: bool,
    theme_id: str,
    owner_controlled: bool,
) -> None:
    value = _describe_edition(tmp_path, edition, product_role)
    assert value == {
        "edition_id": edition,
        "display_name": f"K-7 AI Foundation {edition}",
        "distribution_allowed": distribution_allowed,
        "included_target_ids": included,
        "required_target_ids": required,
        "theme_id": theme_id,
        "owner_controlled": owner_controlled,
        "product_role": product_role,
    }


@pytest.mark.parametrize(
    ("edition", "product_role"),
    [
        (None, "Installer"),
        ("Employee", None),
        ("Unknown", "Installer"),
        ("Employee", "Unknown"),
    ],
)
def test_build_rejects_missing_or_unknown_contract(
    tmp_path: Path,
    edition: str | None,
    product_role: str | None,
) -> None:
    result = _run_build(
        tmp_path,
        edition=edition,
        product_role=product_role,
    )
    assert result.returncode != 0
    output = tmp_path / (
        f"{edition or 'missing'}-{product_role or 'missing'}"
    )
    assert not list(output.glob("*.exe"))


@pytest.mark.parametrize(
    "view_name",
    [
        "InstallerEmployeeView.xaml",
        "LaunchCenterEmployeeView.xaml",
    ],
)
def test_employee_views_use_exact_k7_visual_contract(view_name: str) -> None:
    value = (REPOSITORY / "src" / "gui" / view_name).read_text(
        encoding="utf-8"
    )
    for token in (
        "#071E22",
        "#FC4912",
        "#77CBB9",
        "#30BCED",
        "Bahnschrift SemiCondensed",
        "Segoe UI",
        "Cascadia Mono",
        "M144.91,200h-50l-32.38-48.57",
        "7.62-7.06",
    ):
        assert token in value
    assert "Claude" in value


@pytest.mark.parametrize(
    "view_name",
    [
        "InstallerOwnerView.xaml",
        "LaunchCenterOwnerView.xaml",
    ],
)
def test_owner_views_use_signal_console_contract(view_name: str) -> None:
    value = (REPOSITORY / "src" / "gui" / view_name).read_text(
        encoding="utf-8"
    )
    for token in (
        "#071E22",
        "#FC4912",
        "#77CBB9",
        "#30BCED",
        "ПОД КОНТРОЛЕМ ВЛАДЕЛЬЦА",
        "Выбранный клиент",
        "Локальный шлюз",
        "Провайдер",
        "Claude",
    ):
        assert token in value
    assert "Neon" not in value
    assert "Cyberpunk" not in value


def test_owner_launch_center_separates_claude_technical_and_provider_state() -> None:
    value = (
        REPOSITORY / "src" / "gui" / "LaunchCenterOwnerView.xaml"
    ).read_text(encoding="utf-8")

    assert "CLAUDE CODE" in value
    assert 'Text="TECHNICAL READY"' in value
    assert "PROVIDER_LIVE не подменяется" in value
    assert 'Text="BLOCKED"' not in value


@pytest.mark.parametrize(
    ("edition", "product_role"),
    [
        ("Employee", "Installer"),
        ("Employee", "LaunchCenter"),
        ("Owner", "Installer"),
        ("Owner", "LaunchCenter"),
    ],
)
def test_each_edition_product_renders_its_own_preview(
    tmp_path: Path,
    edition: str,
    product_role: str,
) -> None:
    preview = _render_preview(tmp_path, edition, product_role)
    payload = preview.read_bytes()
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", payload[16:24]) == (1440, 900)


def test_all_products_expose_embedded_interactive_operator_dashboard() -> None:
    dashboard = (
        REPOSITORY / "src" / "gui" / "OperatorGuideDashboard.cs"
    )
    assert dashboard.is_file()
    source = dashboard.read_text(encoding="utf-8")
    for marker in (
        "01 / СТАРТ",
        "02 / МАРШРУТЫ",
        "03 / БЕЗОПАСНОСТЬ",
        "04 / ВОССТАНОВЛЕНИЕ",
        "distribution_allowed=false",
        "Codex + Claude + OpenCode",
    ):
        assert marker in source

    build_source = (
        REPOSITORY / "src" / "gui" / "LlmFoundationInstaller.csproj"
    ).read_text(encoding="utf-8")
    assert '<Compile Include="OperatorGuideDashboard.cs" />' in build_source
    for view in (
        "InstallerEmployeeView.xaml",
        "InstallerOwnerView.xaml",
        "LaunchCenterEmployeeView.xaml",
        "LaunchCenterOwnerView.xaml",
    ):
        xaml = (REPOSITORY / "src" / "gui" / view).read_text(
            encoding="utf-8"
        )
        assert 'x:Name="OpenGuideDashboard"' in xaml


def test_launch_center_selection_does_not_paint_default_listbox_chrome() -> None:
    for view in (
        "LaunchCenterEmployeeView.xaml",
        "LaunchCenterOwnerView.xaml",
    ):
        xaml = (REPOSITORY / "src" / "gui" / view).read_text(
            encoding="utf-8"
        )
        assert 'x:Key="ClientListItem"' in xaml
        assert '<ControlTemplate TargetType="ListBoxItem">' in xaml
        assert 'Style="{StaticResource ClientListItem}"' in xaml


@pytest.mark.parametrize(
    (
        "edition",
        "command",
        "target_id",
        "button_label",
        "client_display",
        "provider_display",
    ),
    [
        (
            "Employee",
            "--ui-selection-json",
            "opencode-cli",
            "Запустить OpenCode →",
            "OPENCODE CLI",
            None,
        ),
        (
            "Owner",
            "--ui-guide-selection-json",
            "claude-code",
            "Запустить Claude →",
            "CLAUDE",
            "ANTHROPIC",
        ),
    ],
)
def test_launch_center_selection_is_real_visible_and_target_specific(
    tmp_path: Path,
    edition: str,
    command: str,
    target_id: str,
    button_label: str,
    client_display: str,
    provider_display: str | None,
) -> None:
    built = _run_build(
        tmp_path,
        edition=edition,
        product_role="LaunchCenter",
    )
    assert built.returncode == 0, built.stdout + built.stderr
    output = tmp_path / f"{edition}-LaunchCenter"
    executable = output / "LLMFoundationInstaller.exe"
    result = subprocess.run(
        [str(executable), command, target_id],
        cwd=output,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(result.stdout)
    assert state == {
        "selected_target": target_id,
        "button_content": button_label,
        "button_enabled": True,
        "selection_visual": "VISIBLE",
        "client_display": client_display,
        "provider_display": provider_display,
        "route_display": "НАПРЯМУЮ",
        "evidence_status": "Пакет проверен",
    }


def test_user_facing_views_are_russian_and_expose_selected_state() -> None:
    forbidden_phrases = (
        "OPERATING GUIDE",
        "OWNER CONTROLLED",
        "DISTRIBUTION DISALLOWED",
        "CLIENT MATRIX",
        "LAUNCH SELECTED",
        "SELECT CLIENT",
        "ROUTE STATE",
        "EVIDENCE STATE",
        "ROLLBACK STATE",
        "Awaiting evidence",
        "Provider gate blocked",
        "REVIEW PLAN / INSTALL",
        "Required packages and gates are being evaluated.",
    )
    for view in (
        "InstallerEmployeeView.xaml",
        "InstallerOwnerView.xaml",
        "LaunchCenterEmployeeView.xaml",
        "LaunchCenterOwnerView.xaml",
    ):
        xaml = (REPOSITORY / "src" / "gui" / view).read_text(
            encoding="utf-8"
        )
        for phrase in forbidden_phrases:
            assert phrase not in xaml, f"{view}: {phrase}"

    for view in (
        "LaunchCenterEmployeeView.xaml",
        "LaunchCenterOwnerView.xaml",
    ):
        xaml = (REPOSITORY / "src" / "gui" / view).read_text(
            encoding="utf-8"
        )
        assert 'x:Name="SelectionFrame"' in xaml
        assert '<Trigger Property="IsSelected" Value="True">' in xaml
        assert 'Tag="opencode-cli"' in xaml


def test_operator_dashboard_can_return_a_real_llm_selection() -> None:
    source = (
        REPOSITORY / "src" / "gui" / "OperatorGuideDashboard.cs"
    ).read_text(encoding="utf-8")
    for marker in (
        "ApplyHostSelection",
        "Выбрать Codex",
        "Выбрать Claude",
        "Выбрать OpenCode",
        "Вернуться к выбору",
    ):
        assert marker in source


@pytest.mark.parametrize(
    ("edition", "product_role"),
    [
        ("Employee", "Installer"),
        ("Employee", "LaunchCenter"),
        ("Owner", "Installer"),
        ("Owner", "LaunchCenter"),
    ],
)
def test_each_product_renders_its_embedded_guide_dashboard(
    tmp_path: Path,
    edition: str,
    product_role: str,
) -> None:
    preview = _render_guide_preview(tmp_path, edition, product_role)
    payload = preview.read_bytes()
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", payload[16:24]) == (1440, 900)


def test_role_specific_operator_guides_match_edition_boundaries() -> None:
    employee = (
        REPOSITORY / "docs" / "ИНСТРУКЦИЯ-СОТРУДНИКУ.md"
    ).read_text(encoding="utf-8")
    owner = (
        REPOSITORY / "docs" / "ИНСТРУКЦИЯ-ВЛАДЕЛЬЦУ.md"
    ).read_text(encoding="utf-8")
    employee_normalized = " ".join(employee.split())

    for marker in (
        "Codex",
        "OpenCode",
        "Напрямую",
        "SingBox HTTP",
        "SingBox HTTPS",
        "InternalUnsigned",
        "SmartScreen",
        "build-edition.ps1",
        "RuntimeArchive",
        "Инструкция",
    ):
        assert marker in employee
    assert "Claude" in employee
    assert (
        "транспорт не подтверждает право использования сервиса"
        in employee_normalized
    )

    for marker in (
        "Codex",
        "Claude",
        "OpenCode",
        "distribution_allowed=false",
        "TECHNICAL_READY",
        "PROVIDER_LIVE",
        "перераспространение запрещено",
        "build-edition.ps1",
        "RuntimeArchive",
        "Интерактивная инструкция",
    ):
        assert marker in owner


def _build_edition(
    output: Path,
    edition: str,
    *,
    runtime_lock: Path | None = None,
    runtime_archive: Path | None = None,
) -> Path:
    command = [
            str(POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(EDITION_BUILD_SCRIPT),
            "-OutputRoot",
            str(output),
            "-Edition",
            edition,
            "-DistributionMode",
            "Preview",
    ]
    if runtime_lock is not None:
        command.extend(
            [
                "-RuntimeSourcesLock",
                str(runtime_lock),
                "-AllowLocalTestSources",
            ]
        )
    if runtime_archive is not None:
        command.extend(["-RuntimeArchive", str(runtime_archive)])
    result = subprocess.run(
        command,
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def test_edition_builder_declares_exact_internal_artifact_names() -> None:
    source = EDITION_BUILD_SCRIPT.read_text(encoding="utf-8")
    for name in (
        "K7-AI-Foundation-Employee-InternalUnsigned.exe",
        "K7-AI-Foundation-Owner-InternalUnsigned.exe",
    ):
        assert name in source
    assert "Simple" not in source
    assert "K7-AI-Launch-Center-Employee-InternalUnsigned.exe" not in source


def test_deterministic_edition_bundle_ships_single_installer_exe(
    tmp_path: Path,
) -> None:
    first = _build_edition(tmp_path / "first", "Employee")
    second = _build_edition(tmp_path / "second", "Employee")
    installer_name = "K7-AI-Foundation-Employee-Preview.exe"
    manifest = json.loads(
        (first / "bundle-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert manifest["edition_id"] == "Employee"
    assert manifest["theme_id"] == "K7Signal"
    assert manifest["distribution_mode"] == "Preview"
    assert manifest["version"] == APP_VERSION
    assert manifest["targets"] == ["claude", "codex", "opencode"]
    assert {
        role: value["file"]
        for role, value in manifest["products"].items()
    } == {"installer": installer_name}
    assert not list(first.glob("K7-AI-Launch-Center-*.exe"))
    fallback_name = "K7-AI-Launch-Center-Employee-Preview.cmd"
    fallback = first / fallback_name
    fallback_record = manifest["launch_center_fallback"]
    assert fallback_record["file"] == fallback_name
    assert fallback_record["product_role"] == "LaunchCenter"
    assert fallback_record["arguments"] == "--launch-center-ui"
    assert fallback_record["sha256"] == hashlib.sha256(
        fallback.read_bytes()
    ).hexdigest()
    assert fallback_record["bytes"] == len(fallback.read_bytes())
    assert fallback.read_bytes() == (second / fallback_name).read_bytes()
    assert fallback.read_text(encoding="utf-8") == (
        "@echo off\n"
        'start "" "%~dp0K7-AI-Foundation-Employee-Preview.exe" '
        "--launch-center-ui\n"
    )
    first_executable = first / installer_name
    second_executable = second / installer_name
    assert first_executable.read_bytes() == second_executable.read_bytes()
    assert manifest["products"]["installer"]["sha256"] == hashlib.sha256(
        first_executable.read_bytes()
    ).hexdigest()
    self_test = subprocess.run(
        [str(first_executable), "--self-test-json"],
        cwd=first,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert self_test.returncode == 0, self_test.stdout + self_test.stderr
    assert json.loads(self_test.stdout)["version"] == APP_VERSION
    assert (first / "bundle-manifest.json").read_bytes() == (
        second / "bundle-manifest.json"
    ).read_bytes()
    launch_center_product = subprocess.run(
        [str(first_executable), "--launch-center-product-json"],
        cwd=first,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert launch_center_product.returncode == 0, (
        launch_center_product.stdout + launch_center_product.stderr
    )
    embedded = json.loads(launch_center_product.stdout)
    assert embedded["product_role"] == "LaunchCenter"
    assert embedded["edition_id"] == "Employee"


def test_edition_bundle_carries_hash_bound_runtime_sidecar(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "sing-box-fixture.zip"
    archive.write_bytes(b"PK\x03\x04runtime-fixture")
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    lock = tmp_path / "runtime.lock.json"
    lock.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "test_only": True,
                "runtime": {
                    "id": "sing-box",
                    "version": "1.13.14",
                    "url": "http://127.0.0.1:43118/" + archive.name,
                    "sha256": archive_hash,
                    "archive_kind": "zip",
                    "archive_entry": (
                        "sing-box-1.13.14-windows-amd64/sing-box.exe"
                    ),
                    "executable_name": "sing-box.exe",
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    bundle = _build_edition(
        tmp_path / "bundle",
        "Employee",
        runtime_lock=lock,
        runtime_archive=archive,
    )
    copied = bundle / archive.name
    manifest = json.loads(
        (bundle / "bundle-manifest.json").read_text(encoding="utf-8")
    )

    assert copied.read_bytes() == archive.read_bytes()
    assert manifest["runtime"] == {
        "id": "sing-box",
        "version": "1.13.14",
        "file": archive.name,
        "sha256": archive_hash,
        "bytes": archive.stat().st_size,
    }


def test_powershell_scripts_with_cyrillic_carry_utf8_bom() -> None:
    # Windows PowerShell 5.1 читает .ps1 без BOM как ANSI: кириллица
    # рассыпается, кавычки внутри строк разъезжаются и скрипт падает с
    # ParserError ещё до первой команды. Наблюдалось на машине сотрудника,
    # где нет pwsh 7 и запуск ушёл в 5.1. pwsh 7 читает UTF-8 сам, поэтому
    # на машине разработчика дефект не виден — только BOM закрывает оба.
    offenders = []
    for path in sorted((REPOSITORY / "tools").rglob("*.ps1")):
        payload = path.read_bytes()
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            offenders.append(path.name + " (не UTF-8)")
            continue
        has_cyrillic = any("Ѐ" <= ch <= "ӿ" for ch in text)
        if has_cyrillic and not payload.startswith(b"\xef\xbb\xbf"):
            offenders.append(path.name)
    assert offenders == [], (
        "эти .ps1 содержат кириллицу без UTF-8 BOM и сломаются "
        "в Windows PowerShell 5.1: " + ", ".join(offenders)
    )


def test_vpn_mode_is_gone_from_product_and_tooling() -> None:
    # VPN убран полностью (решение владельца 2026-09-02). Единственное
    # допустимое упоминание — миграция старых профилей в ConnectionProfile.Load,
    # чтобы сохранённый mode=VPN читался как Direct, а не падал.
    # Допустимые упоминания: миграции старых файлов (только внутри Load),
    # тесты этих миграций, архив планов/спецификаций.
    migrations = {
        REPOSITORY / "src" / "gui" / "ConnectionProfile.cs":
            ("public static ConnectionStateResult Load", "private static string StateRoot"),
        REPOSITORY / "src" / "gui" / "LaunchRoutePreferences.cs":
            ("public static LaunchRoutePreferences Load", "public static LaunchRouteSelection Save"),
    }
    offenders = []
    for folder in ("src", "tools", "tests", "docs"):
        for path in sorted((REPOSITORY / folder).rglob("*")):
            if not path.is_file() or path.suffix not in {
                ".cs", ".xaml", ".py", ".ps1", ".json", ".md", ".cmd",
            }:
                continue
            if "__pycache__" in path.parts or path == Path(__file__).resolve():
                continue
            if "superpowers" in path.parts:
                continue  # архив планов и спецификаций — история решений
            text = path.read_text(encoding="utf-8", errors="replace")
            if "vpn" not in text.lower():
                continue
            if path in migrations:
                begin, end = migrations[path]
                load = text.split(begin, 1)[1].split(end, 1)[0]
                rest = text.replace(load, "")
                if "vpn" in rest.lower():
                    offenders.append(f"{path.relative_to(REPOSITORY)} (вне Load)")
                continue
            if path.name == "test_gui.py":
                # тест миграции старых профилей обязан упоминать VPN
                # все тесты миграции носят префикс test_legacy_vpn_
                rest = re.sub(
                    r"def test_legacy_vpn_[^\n]*\n(?:(?!\ndef ).*\n)*", "", text
                )
                if "vpn" in rest.lower():
                    offenders.append(f"{path.relative_to(REPOSITORY)} (вне теста миграции)")
                continue
            offenders.append(str(path.relative_to(REPOSITORY)))
    assert offenders == [], "VPN всё ещё упоминается: " + ", ".join(offenders)
