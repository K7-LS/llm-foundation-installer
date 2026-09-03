# Таблица CLI-команд и тестовый хост (`-TestHooks`) — план реализации

> **Для агентов:** выполнять задачами по порядку (superpowers:executing-plans
> или subagent-driven-development). Шаги — чекбоксы `- [ ]`.

**Goal:** релизный EXE отдаёт только точки, которые используют код, tools и
ДИАГНОСТИКА; 42 test-only точки живут в тестовом хосте — том же EXE,
собранном с флагом `-TestHooks` (define `K7_TEST_HOOKS`).

**Architecture:** `InstallerApp.Main` перестаёт быть if-цепочкой на 1 000
строк: команды регистрируются в таблице `InstallerCommands` (имя → вид →
арность → обработчик). Продуктовые и инструментальные обработчики живут в
`InstallerCommands.cs`; test-only — в `InstallerTestHost.cs`, целиком под
`#if K7_TEST_HOOKS`, и регистрируются через partial-метод (в релизной
сборке вызов исчезает вместе с реализацией). Новая инструментальная точка
`--commands-json` печатает таблицу — она же машинно-читаемый контракт для
гейта и для таблицы команд в README (следующий PR).

**Tech stack:** C# 7.3 / .NET Framework 4.8 (SDK-style csproj, `dotnet
build`), PowerShell (`tools/build-gui.ps1`), pytest.

## Global Constraints

- `src/foundation.ps1` не трогать (версия движка пинится манифестами).
- `InstallerApp.cs`: только класс `Program` и assembly-метаданные, меньше
  1200 строк (`tests/test_gui_build_project.py::test_installer_app_stays_thin`).
- Каждый `*.cs` в `src/gui` перечислен в csproj `<Compile Include>`
  (`test_every_gui_source_is_compiled_by_the_sdk_project`).
- `.ps1` с кириллицей — с UTF-8 BOM (`test_powershell_scripts_with_cyrillic_carry_utf8_bom`).
- Релизный путь `tools/build-edition.ps1` флага `-TestHooks` не знает и не
  передаёт — тестовый хост нельзя собрать релизной командой.
- Поведение всех 51 существующих точек не меняется: те же аргументы, тот же
  вывод, те же коды возврата (0 / 2 / 20 / 30); неизвестная команда или
  неверная арность — по-прежнему `Неподдерживаемая команда`, код 2.
- Классификация точек — из `отчёты/2026-09-03-installer-cli-точки-классификация.md`
  проекта: продукт 1, инструменты 8, test-only 42 (34 только-тесты + 8
  только-дизайн-доки).
- Тесты не мутируют отслеживаемые файлы (CI: `git status --porcelain` пуст).
- Ревью Codex: не переносить тесты на точки, идущие под удаление; каждому
  снятому ассерту — behavior-тест или статический гейт.

---

## Карта файлов

| Файл | Ответственность |
|---|---|
| `src/gui/InstallerCommands.cs` (новый) | таблица команд, диспетчер `TryRun`, 9 продуктовых/инструментальных обработчиков, `--commands-json`, `RunSelfTest` |
| `src/gui/InstallerTestHost.cs` (новый) | `#if K7_TEST_HOOKS`: 42 test-only обработчиков + `RegisterTestHost()` |
| `src/gui/InstallerApp.cs` | `Main`: переключатели AppContext → `TryRun` → окно; `WriteOutput/WriteError` становятся `internal` |
| `src/gui/LlmFoundationInstaller.csproj` | два новых `Compile`; `K7_TEST_HOOKS` при `K7TestHooks=true` |
| `tools/build-gui.ps1` | `[switch]$TestHooks` → `-p:K7TestHooks=true` |
| `tests/conftest.py` | `_build_shared_bundle(..., test_hooks=True)`; фикстура `release_bundle` (Employee/Installer без хуков) |
| `tests/test_cli_surface.py` (новый) | гейт поверхности: релиз = 10 точек, тестовый хост = 52; поведенческие пробы |
| тесты со сборкой через `build-gui.ps1` | `-TestHooks` в каждом вызове: `test_editions.py`, `test_gui.py` (21 место), `test_launch_center.py`, `test_launcher_runtime.py`, `test_product_config.py`, `test_system_proxy_lease.py` (2) |
| `README.md` «Режимы сборки» | абзац про `-TestHooks` и `--commands-json` |

## Классификация (контракт гейта)

Релиз (10): `--catalog-json`, `--commands-json`, `--ensure-runtime-json`,
`--launch-center-product-json`, `--launch-center-ui`, `--product-json`,
`--resolve-launch-target-json`, `--self-test-json`, `--system-proxy-watchdog`
(вид `product`), `--workflow-json`.

Test-only (42): `--chrome-proxy-json`, `--client-plan-json`,
`--client-plan-store-record-json`, `--client-sources-json`,
`--connection-environment-json`, `--connection-json`,
`--connection-status-texts-json`, `--describe-edition`,
`--download-client-json`, `--evaluate-platform-json`, `--install-client-json`,
`--install-runtime-json`, `--latest-base-json`, `--launch-routes-json`,
`--launch-target-json`, `--preflight-json`, `--preflight-store-record-json`,
`--probe-connection-json`, `--render-guide-preview`, `--render-preview`,
`--reset-managed-route-json`, `--resolve-store-launch-target-record-json`,
`--resolve-vscode-mutating-record-json`, `--resolve-vscode-record-json`,
`--save-connection-json`, `--save-launch-route-json`,
`--system-proxy-test-json`, `--target-client-plan-json`,
`--test-appx-singbox-json`, `--test-connection-route-json`,
`--test-singbox-route-json`, `--test-singbox-session-json`,
`--ui-connection-state-json`, `--ui-guide-selection-json`,
`--ui-launch-selection-json`, `--ui-selection-json`,
`--ui-stored-launch-route-json`, `--ui-vscode-resolution-json`,
`--validate-store-record-json`, `--verify-runtime-json`,
`--write-install-report-json`, `--write-singbox-config-test-json`.

Арность (аргументы без имени команды) — по текущим `args.Length` в `Main`:
0: launch-center-ui, launch-center-product-json, describe-edition,
connection-status-texts-json, product-json, chrome-proxy-json, self-test-json,
catalog-json, preflight-json, client-sources-json, commands-json;
1: ensure-runtime-json, verify-runtime-json, reset-managed-route-json,
preflight-store-record-json, write-install-report-json, launch-routes-json,
connection-json, connection-environment-json, render-preview,
render-guide-preview, ui-connection-state-json, ui-selection-json,
ui-launch-selection-json, ui-guide-selection-json;
2: resolve-launch-target-json, resolve-store-launch-target-record-json,
resolve-vscode-record-json, ui-vscode-resolution-json, install-runtime-json,
latest-base-json, validate-store-record-json, client-plan-json,
target-client-plan-json, save-connection-json, probe-connection-json,
ui-stored-launch-route-json;
3: resolve-vscode-mutating-record-json, launch-target-json,
test-singbox-route-json, test-connection-route-json,
test-singbox-session-json, evaluate-platform-json, download-client-json,
client-plan-store-record-json, install-client-json, save-launch-route-json;
4: workflow-json; 5: write-singbox-config-test-json;
2..3: system-proxy-watchdog; 4..5: system-proxy-test-json;
5..6: test-appx-singbox-json.

---

### Task 1: таблица команд и `--commands-json` (поведение не меняется)

**Files:**
- Create: `src/gui/InstallerCommands.cs`, `src/gui/InstallerTestHost.cs`
- Modify: `src/gui/InstallerApp.cs` (весь `Main` 34–1053, `RunSelfTest`, `WriteOutput/WriteError`), `src/gui/LlmFoundationInstaller.csproj` (`<Compile>`)
- Test: `tests/test_cli_surface.py` (новый), `tests/conftest.py`

**Interfaces:**
- Produces: `InstallerCommands.TryRun(EditionProfile edition, string bundleRoot, string[] args, out int exitCode) : bool`;
  `InstallerCommands.ContinueToUi = -1`; `Program.WriteOutput/WriteError` — `internal`;
  `--commands-json` → `{"test_hooks": bool, "commands": [{"name","kind","min_args","max_args"}]}` (сортировка по `name`).

- [ ] **Шаг 1. RED-тест на таблицу** — `tests/test_cli_surface.py`:

```python
"""Этап 4 плана переработки: поверхность CLI релизного EXE и тестового хоста.

Контракт — классификация 51 точки (отчёт проекта от 2026-09-03): продукт 1,
инструменты 8, test-only 42. `--commands-json` печатает таблицу команд EXE;
релизная сборка (без -TestHooks) отдаёт только продукт и инструменты.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
TEST_ONLY_COMMANDS = { ... 42 имени из раздела «Классификация» ... }


def _run(bundle: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(bundle / "LLMFoundationInstaller.exe"), *arguments],
        cwd=bundle, capture_output=True, text=True, encoding="utf-8",
        check=False, timeout=60,
    )


def _table(bundle: Path) -> dict:
    result = _run(bundle, "--commands-json")
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_test_host_lists_every_command(employee_installer_bundle: Path) -> None:
    table = _table(employee_installer_bundle)
    assert table["test_hooks"] is True
    commands = {row["name"]: row for row in table["commands"]}
    assert set(commands) == set(RELEASE_COMMANDS) | TEST_ONLY_COMMANDS
    assert {n: commands[n]["kind"] for n in RELEASE_COMMANDS} == RELEASE_COMMANDS
    assert {commands[n]["kind"] for n in TEST_ONLY_COMMANDS} == {"test"}
    assert [row["name"] for row in table["commands"]] == sorted(commands)
    assert commands["--system-proxy-watchdog"]["min_args"] == 2
    assert commands["--system-proxy-watchdog"]["max_args"] == 3
```

- [ ] **Шаг 2. Убедиться, что тест красный:** `python -m pytest tests/test_cli_surface.py -q` → FAIL (`--commands-json` → «Неподдерживаемая команда», код 2).

- [ ] **Шаг 3. `InstallerCommands.cs`** — ядро:

```csharp
namespace LlmFoundationInstaller
{
    internal delegate int CommandHandler(
        EditionProfile edition, string bundleRoot, string[] args);

    internal sealed class CliCommandRecord
    {
        public string name;
        public string kind;      // product | tool | test
        public int min_args;     // без имени команды
        public int max_args;
    }

    internal sealed class CliCommandTable
    {
        public bool test_hooks;
        public List<CliCommandRecord> commands;
    }

    internal static partial class InstallerCommands
    {
        // Обработчик просит Main открыть окно (--launch-center-ui).
        internal const int ContinueToUi = -1;

        private sealed class Entry
        {
            public CliCommandRecord record;
            public CommandHandler handler;
        }

        private static readonly List<Entry> Commands = new List<Entry>();

        static InstallerCommands()
        {
            Register("--launch-center-ui", "tool", 0, 0, LaunchCenterUi);
            // ... остальные 9 продуктовых/инструментальных
            RegisterTestHost();   // partial: реализован только под K7_TEST_HOOKS
            Commands.Sort((a, b) => String.CompareOrdinal(a.record.name, b.record.name));
        }

        static partial void RegisterTestHost();

        private static void Register(string name, string kind, int minArgs, int maxArgs, CommandHandler handler) { ... }

        internal static bool TryRun(EditionProfile edition, string bundleRoot, string[] args, out int exitCode)
        {
            exitCode = 2;
            if (args.Length == 0) return false;
            Entry entry = Commands.Find(e => e.record.name == args[0]);
            int count = args.Length - 1;
            if (entry == null || count < entry.record.min_args || count > entry.record.max_args) return false;
            exitCode = entry.handler(edition, bundleRoot, args);
            return true;
        }

        private static int CommandsJson(EditionProfile edition, string bundleRoot, string[] args)
        {
            CliCommandTable table = new CliCommandTable { commands = new List<CliCommandRecord>() };
            foreach (Entry entry in Commands) table.commands.Add(entry.record);
            table.test_hooks = Commands.Exists(e => e.record.kind == "test");
            WriteOutput(new JavaScriptSerializer().Serialize(table));
            return 0;
        }
    }
}
```

  Тела 9 обработчиков переносятся из `Main` дословно (`RunSelfTest` — в
  `SelfTestJson`); `LaunchCenterUi` ставит `edition.product_role = "LaunchCenter"`
  и возвращает `ContinueToUi`; `LaunchCenterProductJson` ставит роль и печатает
  `LaunchTargetCatalog.Describe`. Вывод — через `using static LlmFoundationInstaller.Program;`.

- [ ] **Шаг 4. `InstallerTestHost.cs`** — весь файл между `#if K7_TEST_HOOKS` и `#endif`; `static partial void RegisterTestHost()` регистрирует 42 команды с видом `test`; 42 методов с телами веток из `Main` дословно (`edition`/`bundleRoot`/`args` — параметры).

- [ ] **Шаг 5. `InstallerApp.cs`** — `Main`:

```csharp
try
{
    string bundleRoot = AppDomain.CurrentDomain.BaseDirectory;
    EditionProfile edition = EditionProfile.LoadEmbedded();
    if (args.Length != 0)
    {
        int exitCode;
        if (!InstallerCommands.TryRun(edition, bundleRoot, args, out exitCode))
        {
            WriteError("Неподдерживаемая команда");
            return 2;
        }
        if (exitCode != InstallerCommands.ContinueToUi)
        {
            return exitCode;
        }
    }
    // проверка платформы и окно — без изменений
}
```

  `WriteOutput`/`WriteError`/`WriteStream` → `internal static`; `RunSelfTest` удалить из `Program`; лишние `using` убрать.

- [ ] **Шаг 6. csproj:** `<Compile Include="InstallerCommands.cs" />`, `<Compile Include="InstallerTestHost.cs" />` (по алфавиту); пока без define — Task 2.

  На этом шаге `InstallerTestHost.cs` временно компилируется всегда (без `#if`)? Нет: чтобы Task 1 был зелёным до появления флага, в Task 1 файл `InstallerTestHost.cs` пишется уже с `#if K7_TEST_HOOKS`, а csproj получает `<DefineConstants Condition="'$(K7TestHooks)' != 'false'">$(DefineConstants);K7_TEST_HOOKS</DefineConstants>`
  — то есть хуки включены по умолчанию, пока Task 2 не перевернёт условие на `== 'true'`. Так поведение всех 51 точки и все существующие тесты остаются зелёными между задачами.

- [ ] **Шаг 7. GREEN:** `python -m pytest tests/test_cli_surface.py tests/test_gui_build_project.py -q` → PASS. Сборка: `pwsh -File tools/build-gui.ps1 -OutputRoot <tmp> -Edition Employee -ProductRole Installer` → EXE; `LLMFoundationInstaller.exe --commands-json` печатает 52 команды.

- [ ] **Шаг 8. Регрессия по точкам:** `python -m pytest tests/test_editions.py tests/test_launch_center.py tests/test_latest_base_updater.py -q` (модули, покрывающие много точек и коды 2/20) → PASS.

- [ ] **Шаг 9. Commit:** `refactor(cli): таблица команд InstallerCommands вместо if-цепочки в Main; точка --commands-json`.

### Task 2: флаг `-TestHooks`, релизная поверхность без test-only точек

**Files:**
- Modify: `src/gui/LlmFoundationInstaller.csproj` (условие define → `== 'true'`), `tools/build-gui.ps1` (param + `-p:K7TestHooks=true`), `tests/conftest.py`, 27 вызовов сборки в тестах, `README.md`
- Test: `tests/test_cli_surface.py`

**Interfaces:**
- Consumes: `--commands-json` из Task 1.
- Produces: `build-gui.ps1 -TestHooks`; фикстура `release_bundle` (session, Employee/Installer, без хуков).

- [ ] **Шаг 1. RED-тесты** (добавить в `tests/test_cli_surface.py`):

```python
def test_release_build_lists_only_product_and_tool_commands(release_bundle: Path) -> None:
    table = _table(release_bundle)
    assert table["test_hooks"] is False
    assert {row["name"]: row["kind"] for row in table["commands"]} == RELEASE_COMMANDS


def test_release_build_rejects_test_only_command_that_test_host_serves(
    release_bundle: Path, employee_installer_bundle: Path
) -> None:
    # Чистая точка без аргументов и побочных эффектов: в тестовом хосте
    # отвечает JSON, в релизе — «Неподдерживаемая команда», код 2.
    served = _run(employee_installer_bundle, "--connection-status-texts-json")
    assert served.returncode == 0, served.stdout + served.stderr
    json.loads(served.stdout)
    rejected = _run(release_bundle, "--connection-status-texts-json")
    assert rejected.returncode == 2
    assert "Неподдерживаемая команда" in rejected.stderr
    assert rejected.stdout.strip() == ""


def test_release_build_still_serves_tool_commands(release_bundle: Path) -> None:
    result = _run(release_bundle, "--self-test-json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["app_id"] == "llm-foundation-installer"


def test_test_host_source_is_fenced_by_the_define() -> None:
    source = (REPOSITORY / "src" / "gui" / "InstallerTestHost.cs").read_text(encoding="utf-8")
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    assert lines[0] == "#if K7_TEST_HOOKS"
    assert lines[-1] == "#endif"
    edition = (REPOSITORY / "tools" / "build-edition.ps1").read_text(encoding="utf-8")
    assert "TestHooks" not in edition
```

  `release_bundle` в `tests/conftest.py`:

```python
def _build_shared_bundle(output, edition, product_role, *, test_hooks=True):
    command = [..., "-ProductRole", product_role]
    if test_hooks:
        command.append("-TestHooks")
    ...

@pytest.fixture(scope="session")
def release_bundle(tmp_path_factory) -> Path:
    """Employee/Installer без -TestHooks — та же поверхность, что у релизного EXE."""
    if POWERSHELL is None:
        pytest.skip("PowerShell is required to build the Windows GUI")
    root = tmp_path_factory.mktemp("release-employee-installer")
    return _build_shared_bundle(root / "bundle", "Employee", "Installer", test_hooks=False)
```

- [ ] **Шаг 2. RED:** `python -m pytest tests/test_cli_surface.py -q` → падают три релизных теста (в релизной сборке пока есть хуки) и статический (`build-gui.ps1` не знает `-TestHooks` → сборка фикстуры падает).

- [ ] **Шаг 3. `build-gui.ps1`:** после `[switch]$AllowLocalTestSources` — `[switch]$TestHooks`; после блока `K7ExtraDefines`:

```powershell
if ($TestHooks) {
    # Тестовый хост: 42 test-only CLI-точки компилируются только с этим флагом.
    $DotnetArguments += '-p:K7TestHooks=true'
}
```

- [ ] **Шаг 4. csproj:** `<DefineConstants Condition="'$(K7TestHooks)' == 'true'">$(DefineConstants);K7_TEST_HOOKS</DefineConstants>`.

- [ ] **Шаг 5. Тесты собирают тестовый хост:** во все 27 вызовов `build-gui.ps1` добавить `"-TestHooks",` сразу после строки со скриптом (скриптом, детерминированно): `tests/conftest.py` (через параметр), `tests/test_editions.py:41`, `tests/test_gui.py` (280, 1412, 3386, 4289, 4328, 4442, 4524, 4690, 4765, 4808, 4849, 4912, 4956, 4985, 5020, 5051, 5184, 6032, 6061), `tests/test_launch_center.py:41`, `tests/test_launcher_runtime.py:69`, `tests/test_product_config.py:96`, `tests/test_system_proxy_lease.py:94,140`.

- [ ] **Шаг 6. GREEN:** `python -m pytest tests/test_cli_surface.py tests/test_gui_build_project.py tests/test_product_config.py -q` → PASS.

- [ ] **Шаг 7. README «Режимы сборки»:** абзац: релизный EXE отдаёт только команды из `--commands-json` (продукт + инструменты диагностики/сборки); тестовый хост = `build-gui.ps1 -TestHooks`; `build-edition.ps1` флага не имеет.

- [ ] **Шаг 8. Commit:** `build: флаг -TestHooks (K7_TEST_HOOKS) — test-only CLI-точки только в тестовом хосте; гейт релизной поверхности`.

### Task 3: широкая проверка, CI, черновой PR

- [ ] **Шаг 1.** Локально (фон, вывод в файл, итог — по строке pytest): `python -m pytest tests/test_gui.py tests/test_editions.py tests/test_launch_center.py tests/test_launcher_runtime.py tests/test_latest_base_updater.py tests/test_cli_surface.py -q` → `N passed`.
- [ ] **Шаг 2.** `git push -u origin refactor/cli-command-table-test-hooks`; `gh workflow run windows-ci.yml --ref refactor/cli-command-table-test-hooks`; дождаться зелёных job PS7 и PS5.1.
- [ ] **Шаг 3.** Draft PR c пометкой «не сливать до upgrade-canary 0.4.1» (гейт из классификации, п. 3).

## Done when

- `--commands-json` релизной сборки = 10 команд, тестового хоста = 52.
- Релизная сборка отвечает на `--connection-status-texts-json` кодом 2, тестовый хост — JSON.
- `InstallerApp.cs` < 200 строк, только `Program`; все существующие тесты зелёные локально и в CI (обе оболочки).
- `build-edition.ps1` не содержит `TestHooks`.
