"""Общие session-scoped бандлы для тестов (кандидат №25 этапа 2а).

Полный прогон собирал ~124 бандла; часть конфигураций строилась заново в
каждом модуле. Здесь живёт матрица {Employee, Owner} × {Installer,
LaunchCenter} на репозиторных локах — она собирается один раз за сессию и
переиспользуется всеми модулями.

Использовать ТОЛЬКО read-only: EXE в bundleRoot не пишет (все записи идут в
переданный home, %TEMP% или HKCU), поэтому общий бандл безопасен. Тесты,
которые кладут файлы ВНУТРЬ бандла (runtime-архив sing-box) или встраивают
свой client/runtime-лок, обязаны строить собственный бандл, как и раньше.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPOSITORY_ROOT / "tools" / "build-gui.ps1"
POWERSHELL = (
    os.environ.get("K7_TEST_POWERSHELL")
    or shutil.which("pwsh")
    or shutil.which("powershell.exe")
)


def _build_shared_bundle(
    output: Path,
    edition: str,
    product_role: str,
    *,
    test_hooks: bool = True,
) -> Path:
    # Тесты собирают тестовый хост (-TestHooks): в нём есть test-only
    # CLI-точки. Релизная поверхность — test_hooks=False.
    command = [
        POWERSHELL,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(BUILD_SCRIPT),
        "-OutputRoot",
        str(output),
        "-Edition",
        edition,
        "-ProductRole",
        product_role,
    ]
    if test_hooks:
        command.append("-TestHooks")
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


@pytest.fixture(scope="session")
def shared_bundle(tmp_path_factory: pytest.TempPathFactory):
    """Фабрика общих бандлов: (edition, product_role) → путь, сборка один раз."""
    if POWERSHELL is None:
        pytest.skip("PowerShell is required to build the Windows GUI")
    built: dict[tuple[str, str], Path] = {}

    def factory(edition: str = "Owner", product_role: str = "Installer") -> Path:
        key = (edition, product_role)
        if key not in built:
            root = tmp_path_factory.mktemp(
                f"shared-{edition}-{product_role}".lower()
            )
            built[key] = _build_shared_bundle(
                root / "bundle", edition, product_role
            )
        return built[key]

    return factory


@pytest.fixture(scope="session")
def owner_installer_bundle(shared_bundle) -> Path:
    return shared_bundle("Owner", "Installer")


@pytest.fixture(scope="session")
def employee_installer_bundle(shared_bundle) -> Path:
    return shared_bundle("Employee", "Installer")


@pytest.fixture(scope="session")
def owner_launch_center_bundle(shared_bundle) -> Path:
    return shared_bundle("Owner", "LaunchCenter")


@pytest.fixture(scope="session")
def employee_launch_center_bundle(shared_bundle) -> Path:
    return shared_bundle("Employee", "LaunchCenter")


@pytest.fixture(scope="session")
def release_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Employee/Installer без тестового хоста — поверхность релизного EXE."""
    if POWERSHELL is None:
        pytest.skip("PowerShell is required to build the Windows GUI")
    root = tmp_path_factory.mktemp("release-employee-installer")
    return _build_shared_bundle(
        root / "bundle", "Employee", "Installer", test_hooks=False
    )
