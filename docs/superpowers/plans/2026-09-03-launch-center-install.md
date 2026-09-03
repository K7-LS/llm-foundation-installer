# Установка центра запуска в профиль и ярлыки — план реализации

> **Для агентов:** выполнять задачами по порядку (superpowers:executing-plans). Шаги — чекбоксы `- [ ]`.

**Goal:** после успешной установки центр запуска доступен с рабочего стола и из меню «Пуск» без папки комплекта: копия комплекта лежит в `~/.llm-foundation/launcher`, ярлык «K7 Launch Center» открывает её с `--launch-center-ui`; повторная установка обновляет копию и ярлыки.

**Architecture:** новый модуль `LaunchCenterInstall` читает `bundle-manifest.json` (EXE с SHA-256, `.cmd` центра запуска, архив runtime), копирует эти файлы во временный каталог рядом с целью, сверяет SHA EXE, атомарно подменяет `~/.llm-foundation/launcher`, затем пишет два ярлыка общим механизмом ярлыков (`ClientBootstrap.WriteShortcut`, PowerShell WScript.Shell через `BoundedProcess`). Установка вызывает его в успешной ветке `RunPlanAndInstallAsync`; отказ — предупреждение в статусе, не провал установки. Проверка — test-only точка `--install-launch-center-json <home>` в тестовом хосте.

**Tech stack:** C# / .NET Framework 4.8 (WPF), PowerShell (ярлыки), pytest.

## Global Constraints

- Решение владельца 2026-09-03: место — `~/.llm-foundation/launcher`; ярлыки — рабочий стол и `Пуск → LLM Foundation` (где уже лежат ярлыки клиентов); при каждой установке копия и ярлыки обновляются, старая копия не хранится; шаг «Готово» сообщает, где ярлык.
- Копируются только файлы, объявленные манифестом (`products.installer.file`, `launch_center_fallback.file`, `runtime.file`, сам `bundle-manifest.json`) — не всё содержимое папки синка.
- SHA-256 скопированного EXE сверяется с `products.installer.sha256`; расхождение — `FAILED`, копия не подменяется.
- Комплект запущен из самой папки `launcher` — копирование пропускается (`SKIPPED_SAME_ROOT`), ярлыки обновляются.
- Ошибки копирования или ярлыков не роняют установку: статус `FAILED` с причиной, установщик пишет предупреждение и подсказку «запускайте из папки комплекта».
- Релизная поверхность CLI не меняется: новая точка только в тестовом хосте (`K7_TEST_HOOKS`); `--commands-json` релиза — прежние 10 команд.
- `InstallerApp.cs` не трогать; `InstallerActions.cs` — только вызов и тексты.
- Пути через `ToExtendedLengthPath`, каталоги через `EnsureSafeDirectory` (без reparse-предков, не корень диска) — как у клиентов.

## Карта файлов

| Файл | Ответственность |
|---|---|
| `src/gui/LaunchCenterInstall.cs` (новый) | `LaunchCenterInstallResult`, `LaunchCenterInstall.Install(bundleRoot, home)`: манифест → копия → swap → ярлыки |
| `src/gui/ClientBootstrap.cs` | `WriteShortcut(path, target, arguments, workingDirectory, description)` — общий механизм (выделен из `CreateManagedDesktopShortcut`), `RoamingApplicationDataForHome`, `EnsureSafeDirectory`, `ToExtendedLengthPath` → `internal` |
| `src/gui/InstallerTestHost.cs` | регистрация `--install-launch-center-json` (test, 1 аргумент) |
| `src/gui/InstallerActions.cs` | вызов `LaunchCenterInstall.Install(bundleRoot, home)` в успешной ветке; текст статуса и окна |
| `src/gui/LlmFoundationInstaller.csproj` | `<Compile Include="LaunchCenterInstall.cs" />` |
| `tests/test_launch_center_install.py` (новый) | поведение точки: копия, SHA, ярлыки, повтор, запуск из собственной копии; статический гейт вызова из `InstallerActions` |
| `tests/test_cli_surface.py` | `TEST_ONLY_COMMANDS` + `--install-launch-center-json` (43) |
| `README.md` «Что делает установщик» | строка про копию центра запуска и ярлыки |

## Контракт `--install-launch-center-json <home>` (тестовый хост)

```json
{"status": "INSTALLED" | "SKIPPED_SAME_ROOT" | "FAILED",
 "reason": "…",                       // при FAILED, иначе ""
 "launcher_root": "<home>\\.llm-foundation\\launcher",
 "executable_path": "…\\K7-AI-Foundation-Employee-Preview.exe",
 "copied": ["K7-AI-Foundation-Employee-Preview.exe", "bundle-manifest.json", "K7-AI-Launch-Center-Employee-Preview.cmd"],
 "desktop_shortcut": "<Desktop>\\K7 Launch Center.lnk",
 "start_menu_shortcut": "…\\Start Menu\\Programs\\LLM Foundation\\K7 Launch Center.lnk"}
```
Код возврата: 0 при `INSTALLED` / `SKIPPED_SAME_ROOT`, 20 при `FAILED`. Рабочий стол: `home/Desktop`, если `home` не равен реальному профилю (тесты), иначе `Environment.SpecialFolder.DesktopDirectory`.

---

### Task 1: RED-тесты

- [ ] `tests/test_launch_center_install.py`: копия и ярлыки; повтор без остатков; `SKIPPED_SAME_ROOT`; статический гейт `LaunchCenterInstall.Install(bundleRoot, home)` в `InstallerActions.cs`.
- [ ] `tests/test_cli_surface.py`: `--install-launch-center-json` в `TEST_ONLY_COMMANDS`, счётчик 43.
- [ ] Запуск: `python -m pytest tests/test_launch_center_install.py tests/test_cli_surface.py -q` → FAIL («Неподдерживаемая команда», нет точки в таблице).

### Task 2: реализация

- [ ] `ClientBootstrap.WriteShortcut` (internal) с аргументами и рабочим каталогом; `CreateManagedDesktopShortcut` делегирует ему (поведение прежнее: без аргументов, cwd = каталог цели).
- [ ] `LaunchCenterInstall.cs`: чтение манифеста (`JavaScriptSerializer`, вложенные словари), копия во `launcher.install-<guid>`, сверка SHA EXE, swap (`launcher` → `launcher.previous-<guid>` → удалить best-effort), ярлыки, результат.
- [ ] `InstallerTestHost.cs`: `Register("--install-launch-center-json", "test", 1, 1, InstallLaunchCenterJson)`.
- [ ] `InstallerActions.cs`: после `OpenAuthorizationActions` — установка центра запуска; текст «Центр запуска: ярлык «K7 Launch Center» на рабочем столе и в меню Пуск.» либо предупреждение с причиной.
- [ ] csproj, README.
- [ ] GREEN: новые тесты + `tests/test_cli_surface.py` + тест ярлыка клиента в `test_gui.py` + `test_gui_build_project.py`.

### Task 3: широкая проверка, PR

- [ ] Фон: test_gui, test_editions, test_launch_center, test_launcher_runtime, test_cli_surface, test_launch_center_install → строка pytest.
- [ ] Push, PR; CI обеих оболочек; после слияния — версия 0.4.3, комплект в синк, узкий canary (проверить ярлык на станции).

## Done when

- На тестовом хосте `--install-launch-center-json <home>` копирует EXE (SHA совпал), манифест, `.cmd` в `<home>/.llm-foundation/launcher`, создаёт два ярлыка с `--launch-center-ui`; повтор идемпотентен; запуск из собственной копии — `SKIPPED_SAME_ROOT`.
- Релизная сборка по-прежнему отдаёт 10 команд.
- Существующие тесты зелёные локально и в CI.
