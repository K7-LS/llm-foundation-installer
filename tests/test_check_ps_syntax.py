"""Гейт powershell_syntax проверяет только .ps1, которые видит git.

Локальные артефакты вне git (намеренно битые фикстуры в .work/, копии
соседних worktree в .worktrees/, сборки в dist/) не должны ронять приёмку,
а отслеживаемые и новые неигнорируемые скрипты должны проверяться.
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
# Незакрытое условие: парсер даёт ошибку без выполнения скрипта.
BROKEN_SCRIPT = "param([string]$Release)\nif (\n"
VALID_SCRIPT = "Write-Output 'ok'\n"

pytestmark = pytest.mark.skipif(
    POWERSHELL is None, reason="PowerShell недоступен"
)


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    # Скрипт читает индекс и рабочую копию, HEAD ему не нужен:
    # достаточно init + add.
    root = tmp_path / "repository"
    root.mkdir()
    _write(root / ".gitignore", ".work/\n.worktrees/\ndist/\n")
    _write(root / "tools" / "build.ps1", VALID_SCRIPT)
    _git(root, "init", "-q")
    _git(root, "add", ".")
    return root


def _check(root: Path) -> subprocess.CompletedProcess[str]:
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
        cwd=root.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_ignored_artifacts_do_not_fail_syntax_check(repository: Path) -> None:
    broken_home = (
        repository / ".work" / "acceptance" / "pytest-home"
        / "test_official_script_rejects_p0" / "install.ps1"
    )
    _write(broken_home, BROKEN_SCRIPT)
    _write(repository / ".worktrees" / "other" / "tools" / "x.ps1", BROKEN_SCRIPT)
    _write(repository / "dist" / "engine" / "foundation.ps1", BROKEN_SCRIPT)
    result = _check(repository)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PowerShell syntax PASS: 1" in result.stdout.splitlines()


def test_nested_repository_is_not_scanned(repository: Path) -> None:
    # Worktree другой сессии под .claude/worktrees/ — отдельный репозиторий,
    # git его не обходит даже без записи в .gitignore.
    nested = repository / ".claude" / "worktrees" / "other"
    _write(nested / "tools" / "broken.ps1", BROKEN_SCRIPT)
    _git(nested, "init", "-q")
    result = _check(repository)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PowerShell syntax PASS: 1" in result.stdout.splitlines()


@pytest.mark.parametrize(
    ("relative", "tracked"),
    [
        ("tools/broken.ps1", True),
        ("tools/new.ps1", False),
        ("скрипты/сломан.ps1", True),
        ("tools/Broken.PS1", True),
    ],
    ids=["tracked", "untracked", "non-ascii", "upper-case-extension"],
)
def test_broken_script_visible_to_git_fails(
    repository: Path, relative: str, tracked: bool
) -> None:
    # Регистр расширения не важен, как у -Filter '*.ps1' на Windows.
    broken = repository / relative
    _write(broken, BROKEN_SCRIPT)
    if tracked:
        _git(repository, "add", ".")
    result = _check(repository)
    assert result.returncode == 1
    assert f"{broken}:" in result.stderr


def test_root_without_git_visible_scripts_fails(repository: Path) -> None:
    # -Root внутри игнорируемого каталога: git не видит ни одного .ps1.
    # Это ошибка вызова, а не пустой PASS.
    engine = repository / ".work" / "engine"
    _write(engine / "foundation.ps1", BROKEN_SCRIPT)
    result = _check(engine)
    assert result.returncode == 1
    assert "PowerShell syntax PASS" not in result.stdout
    assert "No .ps1 files" in result.stderr
