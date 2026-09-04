# K-7 — установщик и центр запуска ИИ

Локальный установщик Windows 10/11 и ежедневный центр запуска в двух строго
разделённых версиях:

- **для сотрудников** — Claude Code, Codex Desktop, Codex CLI, OpenCode Desktop,
  OpenCode CLI и VS Code + Codex; стабильная выдача разрешается только после
  пилота на чистом ПК и неизменяемой публикации;
- **для владельца** — Claude Code, Codex и OpenCode с расширенной диагностикой.

Техническая готовность Claude и допустимый live-доступ провайдера разделены:
`TECHNICAL_READY` отвечает за официальный бинарник и пакет, а
`PROVIDER_LIVE` — за интерактивный допустимый вход. Подтверждение владельца
может закрыть live-гейт без повторного автоматического блокирования; оно не
подменяет подпись Authenticode, если выбирается публичный канал.

Каждая версия — единый комплект с двумя EXE, `bundle-manifest.json`,
закреплённой средой SingBox и файлами официального установщика Codex
(`codex-package-*.tar.gz`, `codex-package_SHA256SUMS`,
`codex-release-*.json`): установка Codex CLI идёт из комплекта, без
скачивания ~130 МБ на рабочем месте. Программы работают от текущего пользователя,
не требуют прав администратора и не собирают учётные данные LLM.

## Что делает установщик

1. Проверяет Windows, принятые пакеты и установленные официальные клиенты.
2. Позволяет выбрать нужные клиенты и отдельный маршрут для каждого из них.
3. Сравнивает версии выбранных клиентов, скачивает отсутствующие и обновляет
   устаревшие из закреплённых официальных источников.
4. Для каждой выбранной LLM получает последний immutable stable-релиз её
   нативной базы и проверяет repository, tag, manifest, размер и SHA-256.
5. Показывает детерминированный план изменений.
6. Создаёт резервную копию, устанавливает, запускает диагностику и при ошибке
   автоматически восстанавливает предыдущее состояние.
7. Открывает официальный клиент для интерактивного входа пользователя.
8. Копирует центр запуска в `~/.llm-foundation/launcher` (файлы из
   `bundle-manifest.json`, SHA-256 EXE сверяется) и создаёт ярлык
   «K7 Launch Center» на рабочем столе и в меню «Пуск → LLM Foundation»;
   при каждой установке копия и ярлыки обновляются.
9. Записывает локальный отчёт.

Уже найденная официальная версия внешнего клиента принимается и показывается.
Если клиента нет или версия устарела, установщик предлагает закреплённую
официальную версию. Автоматическое понижение не выполняется. При недоступном
или несовместимом latest-релизе базы Installer явно использует встроенный
проверенный пакет.

## Маршруты

- **Напрямую** — у дочернего процесса очищаются унаследованные
  прокси-переменные.
- **VPN** — используется уже активная системная маршрутизация.
- **SingBox HTTP/HTTPS** — сохраняемый отдельно для каждого клиента локальный
  маршрут. Process-only клиенты получают proxy только в своём процессе. Store
  Codex получает маршрут через временный системный proxy текущего пользователя;
  интерфейс явно предупреждает, что на время такого сеанса он может затронуть
  другие приложения, использующие системные настройки proxy.

Пароль прокси защищается Windows DPAPI для текущего пользователя. Он не
попадает в аргументы командной строки, манифесты, отчёты или логи.

В Installer и Launch Center выбор SingBox сразу открывает подписанные поля
**Сервер / Порт / Логин / Пароль** и шаг **«Сохранить и проверить»**. Launch
Center сам запускает и останавливает sing-box; отдельный пользовательский
скрипт не нужен. Кнопка **«Остановить маршрут»** не завершает клиент.

Маршрут не подтверждает право использования сервиса и не предназначен для
обхода региона, блокировки аккаунта или защитных ограничений провайдера.

## Границы данных

Авторизация остаётся внутри официальных клиентов. Установщик не управляет
проектами, историей, сессиями, cookies, памятью или внешними рабочими папками.
Потребительские устройства не отправляют изменения, отчёты или телеметрию
обратно в репозиторий.

## Режимы сборки

```text
-DistributionMode Preview | InternalUnsigned | PublicSigned
```

- `Preview` — разработка и синтетические проверки.
- `InternalUnsigned` — контролируемая внутренняя выдача после всех гейтов.
- `PublicSigned` — дополнительно требует действующую подпись Authenticode.

Публично подписанный вариант сейчас отложен владельцем. Это не блокирует
внутренний неподписанный релиз после фактического пилота.

Тестовый хост. Релизный EXE отвечает только на команды продукта и
инструментов — их печатает `LLMFoundationInstaller.exe --commands-json`
(`--catalog-json`, `--workflow-json`, `--resolve-launch-target-json`,
`--self-test-json`, `--product-json`, `--launch-center-product-json`,
`--ensure-runtime-json`, `--launch-center-ui`, `--system-proxy-watchdog`).
42 test-only точки живут в `src/gui/InstallerTestHost.cs` и компилируются
только с флагом `tools/build-gui.ps1 -TestHooks` (define `K7_TEST_HOOKS`) —
так собирают бандлы тесты. `build-edition.ps1` флага не имеет: релизный
комплект тестовый хост не содержит. Гейт — `tests/test_cli_surface.py`.

## Команды EXE

Релизный EXE отвечает только на команды из этой таблицы; тот же список
печатает `--commands-json`, а тест `tests/test_cli_surface.py` сверяет
таблицу с фактической поверхностью EXE. Число аргументов — после имени
команды.

| Команда | Кто использует | Аргументов | Назначение |
|---|---|---|---|
| `--launch-center-ui` | `.cmd` центра запуска в комплекте, ярлык | 0 | открыть окно центра запуска |
| `--launch-center-product-json` | `hub_canary.py` | 0 | описание целей запуска в роли центра запуска (JSON) |
| `--product-json` | `hub_canary.py` | 0 | описание продукта и целей запуска (JSON) |
| `--catalog-json` | `hub_canary.py`, `worksite-diagnostics.ps1` | 0 | каталог целей, состояние пакетов, право установки |
| `--self-test-json` | `hub_canary.py`, CI релиза | 0 | самопроверка: движок, платформа, версия комплекта |
| `--commands-json` | тесты, диагностика | 0 | таблица команд этого EXE (`test_hooks`, `commands`) |
| `--ensure-runtime-json <home>` | `hub_canary.py` | 1 | установить или проверить runtime sing-box в профиле |
| `--resolve-launch-target-json <home> <target>` | `worksite-diagnostics.ps1` | 2 | разрешить цель запуска: путь клиента и режим |
| `--workflow-json <команда> <target> <home> <версия>` | `hub_canary.py`, `worksite-diagnostics.ps1` | 4 | команда движка foundation: plan, install, doctor и другие |
| `--system-proxy-watchdog <pid> <state> [<subkey>]` | сам EXE (`SystemProxyLease`) | 2–3 | сторож системного прокси при аварии владельца маршрута |

Служебные команды автотестов (42) собираются только в тестовом хосте
(`build-gui.ps1 -TestHooks`, см. «Режимы сборки»).

## Сборка и тесты

Нужны Python 3.12, PowerShell 7, Windows PowerShell 5.1 и Roslyn из Visual
Studio Build Tools.

```powershell
py -3.12 -m pytest -q
py -3.12 .\tools\run-acceptance.py
```

Предварительная сборка интерфейса:

```powershell
pwsh -NoProfile -File .\tools\build-gui.ps1 `
  -OutputRoot .\dist\предпросмотр `
  -Edition Employee `
  -ProductRole Installer `
  -DistributionMode Preview
```

`run-acceptance.py` требует чистое Git-дерево и связывает evidence с коммитом,
деревом исходников, обеими версиями PowerShell и точными файлами движка.

## Внутренний кандидат для сотрудников

```powershell
pwsh -NoProfile -File .\tools\build-edition.ps1 `
  -OutputRoot .\dist\для-сотрудников `
  -Edition Employee `
  -DistributionMode InternalUnsigned `
  -PackageRoot <принятые-пакеты-codex-opencode> `
  -FoundationPackageRoot <принятый-пакет-foundation> `
  -ClientSourcesLock .\client-sources.lock.json `
  -RuntimeSourcesLock .\runtime-sources.lock.json `
  -RuntimeArchive .\.work\runtime-cache\sing-box-1.13.14-windows-amd64.zip `
  -ClientAssetRoot .\.work\client-assets
```

`-ClientAssetRoot` — кеш файлов официальных установщиков из
`bundled_assets` в `client-sources.lock.json`, раскладка
`<ClientAssetRoot>\<client id>\<version>\<file>`; сборка сверяет SHA-256 и
размер с lock и копирует файлы рядом с EXE. Вне Preview файлы обязательны;
в Preview без кеша комплект качает их сетью на рабочем месте. Заполнить кеш:

```powershell
$root = '.\.work\client-assets\codex-cli\0.153.0'
New-Item -ItemType Directory -Force $root | Out-Null
curl.exe --fail --location --proto =https -C - --speed-limit 1024 --speed-time 60 `
  -o "$root\codex-release-0.153.0.json" https://releases.openai.com/codex/releases/0.153.0/release.json
curl.exe --fail --location --proto =https -C - --speed-limit 1024 --speed-time 60 `
  -o "$root\codex-package_SHA256SUMS" https://releases.openai.com/codex/releases/0.153.0/codex-package_SHA256SUMS
curl.exe --fail --location --proto =https -C - --speed-limit 1024 --speed-time 60 `
  -o "$root\codex-package-x86_64-pc-windows-msvc.tar.gz" https://releases.openai.com/codex/releases/0.153.0/codex-package-x86_64-pc-windows-msvc.tar.gz
```

Версия для сотрудников содержит официальный закреплённый Claude Code, но не
синтезирует и не переносит свидетельство допуска провайдера Claude.

## Релизный конвейер Employee 0.4.0

Изолированный контрольный прогон без вызовов модели:

```powershell
py -3.12 .\tools\hub_canary.py `
  --execute-approved-hub-canary `
  --bundle .\dist\для-сотрудников `
  --output .\dist\контрольный-прогон.json
```

Подготовка точного draft-набора:

```powershell
py -3.12 .\tools\installer_release.py `
  --bundle .\dist\для-сотрудников `
  --hub-canary .\dist\контрольный-прогон.json `
  --output .\dist\черновик-employee-0.4.0
```

Пилот выполняется на чистой Windows x64 без прав администратора теми же
байтами. Он обязан проверить оба приложения, все пять Employee-режимов, OAuth,
прямой маршрут, VPN, SingBox HTTP/HTTPS, инструкцию, диагностику,
инвентаризацию, восстановление и сохранность пользовательских данных.

После подтверждённого пилота:

```powershell
py -3.12 .\tools\pilot_release.py `
  --draft .\dist\черновик-employee-0.4.0 `
  --pilot-evidence <свидетельство-пилота.json> `
  --output .\dist\стабильный-employee-0.4.0
```

Публикация выполняется под тегом `employee-v0.4.0`. После включения
неизменяемости каждый удалённый файл и attestation проверяются:

```powershell
py -3.12 .\tools\installer_release_verifier.py `
  --stable-root .\dist\стабильный-employee-0.4.0 `
  --output .\dist\проверка-релиза-employee-0.4.0.json
```

Синтетический PASS или hosted CI не заменяют интерактивный пилот на чистом ПК.
Для 0.4.0 подтверждённый владельцем домашний canary и provider-live фиксируются
в evidence выпуска; отсутствие повторной автоматической проверки само по себе
не возвращает уже принятый гейт в вечный `PENDING`.

## Инструкции

- [Инструкция сотруднику](docs/ИНСТРУКЦИЯ-СОТРУДНИКУ.md)
- [Инструкция владельцу](docs/ИНСТРУКЦИЯ-ВЛАДЕЛЬЦУ.md)

В обоих приложениях есть встроенная интерактивная кнопка **«Инструкция»**.
