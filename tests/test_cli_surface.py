"""Этап 4 плана переработки: поверхность CLI релизного EXE и тестового хоста.

Контракт — классификация 51 точки (отчёт проекта от 2026-09-03): продукт 1,
инструменты 8, test-only 42 (34 только-тесты + 8 только-дизайн-доки).
`--commands-json` печатает таблицу команд EXE; релизная сборка (без
`-TestHooks`) отдаёт только продукт и инструменты, тестовый хост — всё.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]

# Что остаётся в релизном EXE: продукт (сам EXE зовёт точку) и инструменты
# (hub_canary.py, worksite-diagnostics.ps1, build-edition.ps1,
# installer_release.py, ДИАГНОСТИКА).
RELEASE_COMMANDS = {
    "--catalog-json": "tool",
    "--commands-json": "tool",
    "--ensure-runtime-json": "tool",
    "--launch-center-product-json": "tool",
    "--launch-center-ui": "tool",
    "--product-json": "tool",
    "--resolve-launch-target-json": "tool",
    "--self-test-json": "tool",
    "--system-proxy-watchdog": "product",
    "--workflow-json": "tool",
}

# Точки, которые зовут только тесты и дизайн-заметки июля.
TEST_ONLY_COMMANDS = {
    "--chrome-proxy-json",
    "--client-plan-json",
    "--client-plan-store-record-json",
    "--client-sources-json",
    "--connection-environment-json",
    "--connection-json",
    "--connection-status-texts-json",
    "--describe-edition",
    "--download-client-json",
    "--evaluate-platform-json",
    "--install-client-json",
    "--install-launch-center-json",
    "--install-runtime-json",
    "--latest-base-json",
    "--launch-routes-json",
    "--launch-target-json",
    "--preflight-json",
    "--preflight-store-record-json",
    "--probe-connection-json",
    "--render-guide-preview",
    "--render-preview",
    "--reset-managed-route-json",
    "--resolve-store-launch-target-record-json",
    "--resolve-vscode-mutating-record-json",
    "--resolve-vscode-record-json",
    "--save-connection-json",
    "--save-launch-route-json",
    "--system-proxy-test-json",
    "--target-client-plan-json",
    "--test-appx-singbox-json",
    "--test-connection-route-json",
    "--test-singbox-route-json",
    "--test-singbox-session-json",
    "--ui-connection-state-json",
    "--ui-guide-selection-json",
    "--ui-launch-selection-json",
    "--ui-selection-json",
    "--ui-stored-launch-route-json",
    "--ui-vscode-resolution-json",
    "--validate-store-record-json",
    "--verify-runtime-json",
    "--write-install-report-json",
    "--write-singbox-config-test-json",
}


def _run(bundle: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(bundle / "LLMFoundationInstaller.exe"), *arguments],
        cwd=bundle,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def _table(bundle: Path) -> dict:
    result = _run(bundle, "--commands-json")
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_classification_counts_match_the_report() -> None:
    # 42 точки по классификации от 2026-09-03 + `--install-launch-center-json`
    # (тестовый хост фичи «установка центра запуска», решение владельца).
    assert len(TEST_ONLY_COMMANDS) == 43
    assert len(RELEASE_COMMANDS) == 10
    assert not TEST_ONLY_COMMANDS & set(RELEASE_COMMANDS)


def test_test_host_lists_every_command(employee_installer_bundle: Path) -> None:
    table = _table(employee_installer_bundle)
    assert table["test_hooks"] is True
    commands = {row["name"]: row for row in table["commands"]}
    assert set(commands) == set(RELEASE_COMMANDS) | TEST_ONLY_COMMANDS
    assert {
        name: commands[name]["kind"] for name in RELEASE_COMMANDS
    } == RELEASE_COMMANDS
    assert {commands[name]["kind"] for name in TEST_ONLY_COMMANDS} == {"test"}
    assert [row["name"] for row in table["commands"]] == sorted(commands)
    watchdog = commands["--system-proxy-watchdog"]
    assert (watchdog["min_args"], watchdog["max_args"]) == (2, 3)
    assert (commands["--workflow-json"]["min_args"],
            commands["--workflow-json"]["max_args"]) == (4, 4)


def test_release_build_lists_only_product_and_tool_commands(
    release_bundle: Path,
) -> None:
    table = _table(release_bundle)
    assert table["test_hooks"] is False
    assert {
        row["name"]: row["kind"] for row in table["commands"]
    } == RELEASE_COMMANDS


def test_release_build_rejects_test_only_command_that_test_host_serves(
    release_bundle: Path,
    employee_installer_bundle: Path,
) -> None:
    # Чистая точка без аргументов и побочных эффектов: тестовый хост
    # отвечает JSON, релиз — «Неподдерживаемая команда» с кодом 2.
    served = _run(employee_installer_bundle, "--connection-status-texts-json")
    assert served.returncode == 0, served.stdout + served.stderr
    json.loads(served.stdout)
    rejected = _run(release_bundle, "--connection-status-texts-json")
    assert rejected.returncode == 2, rejected.stdout + rejected.stderr
    assert "Неподдерживаемая команда" in rejected.stderr
    assert rejected.stdout.strip() == ""


def test_release_build_still_serves_tool_commands(release_bundle: Path) -> None:
    result = _run(release_bundle, "--self-test-json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["app_id"] == "llm-foundation-installer"


def _readme_command_table() -> dict[str, tuple[int, int]]:
    """Строки таблицы «## Команды EXE» в README: имя → (min_args, max_args)."""
    text = (REPOSITORY / "README.md").read_text(encoding="utf-8")
    assert "## Команды EXE" in text, "в README нет раздела «Команды EXE»"
    section = text.split("## Команды EXE", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, tuple[int, int]] = {}
    for line in section.splitlines():
        match = re.match(
            r"^\| `(--[a-z-]+)[^`]*` \| [^|]+ \| (\d+)(?:–(\d+))? \|", line
        )
        if match:
            low = int(match.group(2))
            rows[match.group(1)] = (low, int(match.group(3) or low))
    return rows


def test_readme_command_table_matches_release_surface(
    release_bundle: Path,
) -> None:
    # Гейт полноты: таблица команд в README — ровно поверхность релизного
    # EXE (имена и число аргументов), ни больше ни меньше.
    exe = {
        row["name"]: (row["min_args"], row["max_args"])
        for row in _table(release_bundle)["commands"]
    }
    assert _readme_command_table() == exe


def test_test_host_source_is_fenced_and_release_path_has_no_flag() -> None:
    source = (REPOSITORY / "src" / "gui" / "InstallerTestHost.cs").read_text(
        encoding="utf-8"
    )
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    assert lines[0] == "#if K7_TEST_HOOKS"
    assert lines[-1] == "#endif"
    edition_build = (REPOSITORY / "tools" / "build-edition.ps1").read_text(
        encoding="utf-8"
    )
    assert "TestHooks" not in edition_build
