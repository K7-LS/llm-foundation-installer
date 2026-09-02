"""Диагностика рабочей станции: fail-closed, привязка к комплекту, обе оболочки.

Ревью Codex 2026-09-02: прежняя версия подавляла ошибки и всегда заканчивалась
успехом, версии Codex/Claude были зашиты, плана OpenCode не было, EXE
искался по одному имени, отчёт не был привязан к хешам комплекта.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "tools" / "worksite-diagnostics.ps1"
WINDOWS_POWERSHELL = shutil.which("powershell.exe")
PWSH = shutil.which("pwsh")


def _shells() -> list[str]:
    return [shell for shell in (WINDOWS_POWERSHELL, PWSH) if shell]


@pytest.mark.parametrize("shell", _shells())
def test_script_parses_in_every_shell(shell: str) -> None:
    # Скрипт запускают там, где pwsh может не быть: разбор обязан проходить
    # и в Windows PowerShell 5.1, и в PowerShell 7.
    command = (
        "$ErrorActionPreference='Stop';"
        "[scriptblock]::Create((Get-Content -Raw -LiteralPath $env:K7_DIAG_SCRIPT)) | Out-Null;"
        "'PARSED'"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={"K7_DIAG_SCRIPT": str(SCRIPT), "SystemRoot": "C:\\Windows", "PATH": ""},
        timeout=60,
    )
    assert "PARSED" in result.stdout, result.stdout + result.stderr


def test_report_is_fail_closed_on_bundle_without_packages(
    employee_launch_center_bundle: Path, tmp_path: Path
) -> None:
    # Бандл без принятых пакетов: план не строится. Прежняя диагностика
    # молчала об этом и печатала «успех»; новая обязана дать ERROR в отчёте,
    # назвать причину и вернуть ненулевой код.
    shell = WINDOWS_POWERSHELL or PWSH
    out = tmp_path / "reports"
    result = subprocess.run(
        [
            shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPT),
            "-BundleRoot", str(employee_launch_center_bundle),
            "-OutputDirectory", str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    reports = sorted(out.glob("*.json"))
    assert len(reports) == 1, result.stdout + result.stderr
    report = json.loads(reports[0].read_text(encoding="utf-8-sig"))
    assert report["schema_version"] == 2
    assert report["status"] == "ERROR"
    assert report["errors"], "ошибки должны быть перечислены, а не проглочены"
    # привязка к комплекту: EXE найден по маске, хеш посчитан
    assert report["bundle"]["exe"]
    assert len(report["bundle"]["exe_sha256"]) == 64
    # старые поля сохранены — прежние отчёты и их разбор не ломаются
    for key in ("machine", "user_is_admin", "packages", "launch_targets", "install_plans", "skill_junctions"):
        assert key in report, key
    # план — по целям каталога, версии из каталога, а не зашитые в скрипт
    for plan in report["install_plans"]:
        assert plan["version"], plan


def test_script_has_no_hardcoded_client_versions() -> None:
    text = SCRIPT.read_text(encoding="utf-8-sig")
    assert "0.146.0" not in text
    assert "2.1.218" not in text
    assert "supported_version" in text
    assert ".config\\opencode\\skills" in text
