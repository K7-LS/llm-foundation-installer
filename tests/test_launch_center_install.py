"""Установка центра запуска в профиль и ярлыки (решение владельца 2026-09-03).

После успешной установки копия комплекта лежит в `~/.llm-foundation/launcher`,
ярлык «K7 Launch Center» на рабочем столе и в «Пуск → LLM Foundation» открывает
её с `--launch-center-ui`. Проверяется через test-only точку тестового хоста
`--install-launch-center-json <home>`; рабочий стол для тестового home —
`<home>/Desktop`, меню «Пуск» — `<home>/AppData/Roaming/...` (как у клиентов).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
POWERSHELL = (
    os.environ.get("K7_TEST_POWERSHELL")
    or shutil.which("pwsh")
    or shutil.which("powershell.exe")
)
EXE_NAME = "LLMFoundationInstaller.exe"
SHORTCUT = "K7 Launch Center.lnk"


def _install(bundle: Path, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(bundle / EXE_NAME), "--install-launch-center-json", str(home)],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def _shortcut(path: Path) -> dict[str, str]:
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('"
        + str(path).replace("'", "''")
        + "'); [Console]::Out.Write((@{target=$s.TargetPath; arguments=$s.Arguments;"
        " cwd=$s.WorkingDirectory} | ConvertTo-Json -Compress))"
    )
    result = subprocess.run(
        [str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_install_copies_manifest_files_and_creates_both_shortcuts(
    employee_installer_bundle: Path, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = _install(employee_installer_bundle, home)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    launcher = home / ".llm-foundation" / "launcher"
    assert payload["status"] == "INSTALLED"
    assert Path(payload["launcher_root"]) == launcher
    # Одиночная сборка build-gui описывает файлы через artifacts (комплект
    # издания — через products.installer); модуль понимает обе формы.
    manifest = json.loads(
        (employee_installer_bundle / "bundle-manifest.json").read_text(encoding="utf-8")
    )
    artifacts = manifest["artifacts"]
    assert Path(payload["executable_path"]) == launcher / EXE_NAME
    copied = set(payload["copied"])
    assert copied >= {EXE_NAME, "bundle-manifest.json", "engine\\foundation.ps1"}
    for relative, record in artifacts.items():
        assert relative.replace("/", "\\") in copied, relative
        assert _sha256(launcher / relative) == record["sha256"], relative
    assert (launcher / "bundle-manifest.json").read_bytes() == (
        employee_installer_bundle / "bundle-manifest.json"
    ).read_bytes()
    exe_name = EXE_NAME

    desktop = home / "Desktop" / SHORTCUT
    start_menu = (
        home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu"
        / "Programs" / "LLM Foundation" / SHORTCUT
    )
    assert Path(payload["desktop_shortcut"]) == desktop
    assert Path(payload["start_menu_shortcut"]) == start_menu
    for link in (desktop, start_menu):
        assert link.is_file(), link
        info = _shortcut(link)
        assert Path(info["target"]) == launcher / exe_name
        assert info["arguments"] == "--launch-center-ui"
        assert Path(info["cwd"]) == launcher


def test_repeated_install_replaces_copy_without_leftovers(
    employee_installer_bundle: Path, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    assert _install(employee_installer_bundle, home).returncode == 0
    launcher = home / ".llm-foundation" / "launcher"
    (launcher / "stale-file.txt").write_text("старая копия", encoding="utf-8")
    second = _install(employee_installer_bundle, home)
    assert second.returncode == 0, second.stdout + second.stderr
    assert json.loads(second.stdout)["status"] == "INSTALLED"
    assert not (launcher / "stale-file.txt").exists()          # копия заменена целиком
    siblings = [p.name for p in launcher.parent.iterdir()]
    assert siblings.count("launcher") == 1
    assert not [n for n in siblings if n.startswith("launcher.")]  # без .install-/.previous-
    assert (home / "Desktop" / SHORTCUT).is_file()


def test_install_from_own_launcher_root_only_refreshes_shortcuts(
    employee_installer_bundle: Path, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    launcher = home / ".llm-foundation" / "launcher"
    shutil.copytree(employee_installer_bundle, launcher)
    result = _install(launcher, home)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "SKIPPED_SAME_ROOT"
    assert payload["copied"] == []
    assert (home / "Desktop" / SHORTCUT).is_file()
    assert Path(_shortcut(home / "Desktop" / SHORTCUT)["target"]) == launcher / EXE_NAME


def test_installer_success_path_installs_launch_center() -> None:
    # Статический гейт: продуктовый поток установки зовёт тот же модуль, что и
    # test-only точка; поведение модуля покрыто тестами выше.
    source = (REPOSITORY / "src" / "gui" / "InstallerActions.cs").read_text(encoding="utf-8")
    assert "LaunchCenterInstall.Install(" in source
    assert "K7 Launch Center" in source
