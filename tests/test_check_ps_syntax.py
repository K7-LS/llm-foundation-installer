"""tools/check-ps-syntax.ps1 проверяет только те .ps1, которые видит git.

Гейт powershell_syntax приёмки (run-acceptance.py) спотыкался о намеренно
битые фикстуры тестов в игнорируемых каталогах .work/, .worktrees/, dist/
и во вложенных worktree — это артефакты вне git, а не исходники.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "tools" / "check-ps-syntax.ps1"
POWERSHELL = (
    os.environ.get("K7_TEST_POWERSHELL")
    or shutil.which("pwsh")
    or shutil.which("powershell.exe")
)
# Как фикстура test_official_script_rejects_p0: незакрытая скобка.
BROKEN_SCRIPT = "param([string]$Release)\nif (\n"
VALID_SCRIPT = "Write-Output 'ok'\n"

pytestmark = pytest.mark.skipif(
    POWERSHELL is None, reason="PowerShell is required to run the syntax check"
)


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _write(root / ".gitignore", ".work/\n.worktrees/\ndist/\n")
    _write(root / "tools" / "build.ps1", VALID_SCRIPT)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "syntax@example.invalid")
    _git(root, "config", "user.name", "Syntax Test")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


def _check(root: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    # cwd вне репозитория: скрипт обязан опираться на -Root, как в приёмке.
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-Root",
            str(root),
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )


def test_ignored_artifacts_do_not_fail_syntax_check(repository, tmp_path):
    for relative in (
        ".work/acceptance/pytest-home/test_official_script_rejects_p0/"
        "client-staging/fixture-client/1.0.0/install.ps1",
        ".worktrees/other/.work/acceptance/pytest-home/install.ps1",
        "dist/preview/engine/foundation.ps1",
    ):
        _write(repository / relative, BROKEN_SCRIPT)

    result = _check(repository, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["PowerShell syntax PASS: 1"]


def test_nested_repository_is_not_scanned(repository, tmp_path):
    nested = repository / ".claude" / "worktrees" / "other"
    _write(nested / "tools" / "install.ps1", BROKEN_SCRIPT)
    _git(nested, "init", "-q")

    result = _check(repository, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["PowerShell syntax PASS: 1"]


def test_tracked_script_with_syntax_error_still_fails(repository, tmp_path):
    broken = repository / "tools" / "broken.ps1"
    _write(broken, BROKEN_SCRIPT)
    _git(repository, "add", ".")

    result = _check(repository, tmp_path)

    assert result.returncode == 1
    assert f"{broken}:" in result.stderr
    assert "PowerShell syntax PASS" not in result.stdout


def test_untracked_script_outside_ignored_paths_is_checked(
    repository, tmp_path
):
    new = repository / "tools" / "new.ps1"
    _write(new, BROKEN_SCRIPT)

    result = _check(repository, tmp_path)

    assert result.returncode == 1
    assert f"{new}:" in result.stderr


def test_non_ascii_tracked_path_is_checked(repository, tmp_path):
    broken = repository / "скрипты" / "сломан.ps1"
    _write(broken, BROKEN_SCRIPT)
    _git(repository, "add", ".")

    result = _check(repository, tmp_path)

    assert result.returncode == 1
    assert f"{broken}:" in result.stderr
