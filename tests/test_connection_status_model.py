"""Ф5: статусы соединения формирует чистый view-model без WPF.

ConnectionStatusModel строит тексты и тон (info/ok/warn); ConnectionUi
только применяет их к контролам. Канонический словарь доступен через
--connection-status-texts-json и служит smoke-тестом привязок.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
GUI = REPOSITORY / "src" / "gui"
MODEL = GUI / "ConnectionStatusModel.cs"


def test_connection_status_model_is_wpf_free():
    source = MODEL.read_text(encoding="utf-8")
    assert "using System.Windows" not in source
    assert "SolidColorBrush" not in source
    assert "Color.FromRgb" not in source
    assert "DescribeTestFailure" in source


def test_connection_ui_delegates_status_texts_to_the_model():
    source = (GUI / "ConnectionUi.cs").read_text(encoding="utf-8")
    assert "ConnectionStatusModel" in source
    assert 'Status.Text = "' not in source
    assert source.count("Color.FromRgb") <= 3


def test_model_is_compiled_and_exposed_by_the_project():
    project = (
        GUI / "LlmFoundationInstaller.csproj"
    ).read_text(encoding="utf-8")
    assert '<Compile Include="ConnectionStatusModel.cs" />' in project


@pytest.fixture(scope="module")
def status_catalog(tmp_path_factory: pytest.TempPathFactory) -> dict:
    output = tmp_path_factory.mktemp("status-model") / "center"
    powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
    built = subprocess.run(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(REPOSITORY / "tools" / "build-gui.ps1"),
            "-OutputRoot",
            str(output),
            "-Edition",
            "Employee",
            "-ProductRole",
            "LaunchCenter",
        ],
        cwd=REPOSITORY,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=600,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run(
        [
            str(output / "LLMFoundationInstaller.exe"),
            "--connection-status-texts-json",
        ],
        cwd=output,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_status_catalog_smoke_covers_bound_texts(status_catalog: dict):
    entries = status_catalog["statuses"]
    by_key = {entry["key"]: entry for entry in entries}
    assert by_key["mode_idle_direct"]["text"] == (
        "Напрямую: прокси не используется."
    )
    assert by_key["mode_idle_vpn"]["text"] == "VPN: прокси не требуется."
    assert by_key["proxy_guidance"]["text"].startswith(
        "Заполните сервер, порт, логин и пароль"
    )
    assert by_key["singbox_route_pass"]["text"] == (
        "Маршрут SingBox проверен сквозным запросом."
    )
    for entry in entries:
        assert entry["tone"] in {"info", "ok", "warn"}, entry["key"]
        assert entry["text"].strip(), entry["key"]


def test_status_catalog_describes_failure_reasons(status_catalog: dict):
    failures = status_catalog["test_failures"]
    by_reason = {entry["reason"]: entry["text"] for entry in failures}
    assert by_reason["CONFIG_CHECK_FAILED"].startswith(
        "Проверка не пройдена (CONFIG_CHECK_FAILED)."
    )
    assert "Проверьте сервер, порт, логин и пароль" in (
        by_reason["CONFIG_CHECK_FAILED"]
    )
    assert "Распакуйте весь ZIP" in by_reason["RUNTIME_BUNDLE_ARCHIVE_MISSING"]
