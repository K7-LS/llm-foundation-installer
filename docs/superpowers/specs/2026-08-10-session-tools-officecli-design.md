# Session Tools Auto-Pull and OfficeCLI Design

## Goal

Доставить `ru-writing-style` и OfficeCLI во все управляемые установки Claude,
Codex и OpenCode. Восстановить двухуровневую модель обновления:

- подтягивать лёгкие инструменты автоматически в начале новой сессии;
- устанавливать бинарники и компоненты со сложной настройкой через
  `$sync-base` или общий установщик.

## Context

- В legacy-контуре Claude уже работает `SessionStart -> auto-pull.ps1`.
  Скрипт обновляет файлы из `claude-base`, не блокирует сессию при ошибке сети и
  выставляет pending-флаг для сложных установок.
- В `codex-base`, `claude-base-v2` и `opencode-base` SessionStart сейчас только
  проверяет наличие нового stable release не чаще одного раза в сутки.
- `ru-writing-style` уже присутствует в legacy Claude, но отсутствует в
  опубликованных нативных базах и активной установке Codex.
- OfficeCLI отсутствует в управляемой установке. Использовать официальный
  Windows x64 asset версии `1.0.143`:
  `officecli-win-x64.exe`, SHA-256
  `d4d4c10fced307e209744cf98a56b003a6e613424fd651b08469274704afd2c6`.
- Текущая Foundation запрещает target-пакету напрямую писать в
  `.llm-foundation/**`. Общий бинарник должен устанавливать Foundation, а не
  отдельная база.

## Constraints

- Сохранять поток только `hub -> consumer`.
- Не отправлять feedback, telemetry, локальные файлы и изменения пользователя.
- Не изменять пользовательские skills, не зарегистрированные как управляемые.
- Не выполнять бинарники и установочные скрипты из auto-pull-канала.
- Не блокировать запуск сессии при отсутствии сети или ошибке обновления.
- Не обходить release verification target-пакетов и Foundation. Для OfficeCLI
  проверять exact upstream tag/asset URL и закреплённый SHA-256; не заявлять
  отсутствующую upstream-подпись.
- Не объявлять release готовым без целевых acceptance и live-canary.
- Сохранить работающий legacy auto-pull Claude до успешной замены.

## Decision

Использовать два независимых канала доставки.

### 1. Session tools channel

Применять канал для декларативных инструментов, которым достаточно файлов в
каталоге skills. В первой версии разрешить только текстовые форматы:

- `.md`;
- `.json`;
- `.yaml` и `.yml`;
- `.toml`;
- `.txt`.

Не включать `.ps1`, `.py`, `.exe`, `.dll`, архивы, MCP-конфигурацию и изменения
PATH. Инструмент с такими файлами маркировать как требующий `$sync-base`.

Для каждой нативной базы добавить manifest `catalog/session-tools.json`. Он
остаётся маленьким HTTP-fetchable контрактом и содержит:

- `schema_version`;
- target базы;
- точный upstream raw-content base URL и branch;
- список управляемых tool id;
- относительный source path;
- относительный install path;
- список файлов и SHA-256 каждого файла.

Первым инструментом manifest объявляет `ru-writing-style`. Источник остаётся
`skills/ru-writing-style/SKILL.md` внутри соответствующей базы.

### 2. Managed setup channel

Применять канал для OfficeCLI и будущих инструментов, которые:

- добавляют бинарник;
- меняют PATH;
- требуют системную зависимость;
- регистрируют MCP или выполняют установочный код.

Канонический source record OfficeCLI хранить в
`llm-foundation-installer/client-sources.lock.json` как target `shared` и
install mode `managed-bin`. Закрепить URL, версию и SHA-256.

Новый пользователь получает OfficeCLI во время обычной установки. Действующий
пользователь получает его одной командой `$sync-base` любой установленной базы.
Один общий state предотвращает повторную установку одним target поверх другого.

## SessionStart Flow

Для `codex-base`, `claude-base-v2` и `opencode-base` добавить общий по контракту,
но target-specific по путям updater.

1. Запустить updater первым SessionStart-hook новой сессии.
2. Взять межпроцессный lock с коротким TTL.
3. Получить `catalog/session-tools.json` через существующий connection runtime.
4. Использовать conditional request с сохранённым ETag; при `not modified`
   завершить no-op.
5. Проверить target, raw-content base URL, относительные пути и расширения.
6. Скачать только файлы изменившихся tool id во временный sidecar snapshot:
   `%USERPROFILE%/.llm-foundation/session-tools/<target>/staging`.
7. Проверить SHA-256 каждого файла до изменения target-каталога.
8. Собрать каждый skill во временном каталоге рядом с назначением.
9. Атомарно заменить только каталог управляемого tool id.
10. Записать ETag, manifest hash и file hashes в target state.
11. Удалить временные файлы и завершить hook с кодом `0`.

Сделать проверку при каждом новом startup без суточного TTL. Ограничить весь
hook 30 секундами. При сетевой ошибке оставить последнюю проверенную копию и
записать короткую диагностическую строку в локальный лог.

Не удалять локальные skills, которых нет в manifest. Удаление или миграцию
управляемого skill выполнять только через `$sync-base`.

### Same-session discovery

Acceptance должен доказать, что skill, установленный SessionStart-hook, виден
в той же новой сессии. Если конкретный клиент индексирует skills до hook,
перенести тот же updater в его управляемый pre-launch этап. Не принимать
вариант, при котором новый skill становится доступен только после второй новой
сессии.

## Legacy Claude

Оставить текущий `~/.claude/scripts/auto-pull.ps1` рабочим на переходный период.
Он уже обновляет legacy Claude на `startup` и `resume`.

Не переносить raw `git pull --rebase --autostash` напрямую в пользовательские
каталоги новых баз. Использовать manifest-driven sidecar download. Не делать
Git скрытой обязательной зависимостью нового updater.

После успешного canary нового канала решить отдельной задачей, нужен ли legacy
Claude переход на общий updater. Эта миграция не входит в текущий scope.

## OfficeCLI Flow

### Source contract

Добавить shared source:

- id: `officecli`;
- version: `1.0.143`;
- URL:
  `https://github.com/iOfficeAI/OfficeCLI/releases/download/v1.0.143/officecli-win-x64.exe`;
- SHA-256:
  `d4d4c10fced307e209744cf98a56b003a6e613424fd651b08469274704afd2c6`;
- artifact kind: `portable-exe`;
- install mode: `managed-bin`;
- command: `officecli.exe`;
- version arguments: `--version`;
- signature required: `false`;
- license: `Apache-2.0`.

### New installation

Изменить target planning общего установщика так, чтобы источник target `shared`
включался в план Claude, Codex и OpenCode. Устанавливать бинарник один раз в:

`%USERPROFILE%/.llm-foundation/bin/officecli.exe`.

Добавить каталог в current-user PATH идемпотентно. Проверить exact version после
установки.

### Existing installation

Расширить Foundation shared-tool contract. Target package объявляет
`required_shared_tools: ["officecli"]`, а `$sync-base` передаёт это объявление
проверенному Foundation engine.

Foundation выполняет:

1. `plan` — определить missing, exact, older или newer;
2. `install` — скачать exact asset, проверить SHA-256 и атомарно заменить файл;
3. `doctor` — проверить путь, hash и `officecli --version`;
4. `rollback` — восстановить прежний файл и состояние PATH при неуспешном doctor.

Не выполнять downgrade при установленной более новой версии без отдельного
явного решения. Вернуть `BLOCKED_NO_DOWNGRADE`.

## State and Rollback

Хранить общий state в `%USERPROFILE%/.llm-foundation/state/shared-tools/`.

Записывать:

- installed version;
- expected and observed SHA-256;
- install path;
- PATH state до изменения;
- previous file backup для незавершённой транзакции;
- timestamp и source contract hash.

Снимать rollback journal только после успешного doctor. При прерванной операции
следующий `plan` возвращает требование rollback, как текущая Foundation для баз.

Session tools используют отдельный target state и максимум одну предыдущую
проверенную копию каждого управляемого skill.

## Repository Responsibilities

### `codex-base`

- перенести `ru-writing-style` из существующей feature-ветки на свежий main;
- добавить skill и cold reference OfficeCLI в catalogs;
- добавить `catalog/session-tools.json`;
- добавить генератор manifest и тест отсутствия drift относительно source files;
- заменить version-only SessionStart на session-tools update плюс release check;
- объявить OfficeCLI в target package shared-tool contract;
- обновить counts, docs, generated reports и tests.

### `claude-base-v2`

- добавить тот же skill, reference и session manifest;
- добавить генератор manifest и drift-test;
- добавить target-specific SessionStart updater;
- объявить OfficeCLI в package shared-tool contract;
- обновить counts, docs, reports и tests;
- не объявлять provider-blocked live canary успешным.

### `opencode-base`

- добавить тот же skill, reference и session manifest;
- добавить генератор manifest и drift-test;
- добавить target-specific SessionStart updater;
- объявить OfficeCLI в package shared-tool contract;
- обновить counts, docs, reports и tests;
- сохранить существующие provider и immutable-integrity gates.

### `llm-foundation-installer`

- добавить canonical OfficeCLI shared source;
- включать shared sources в каждый target plan;
- расширить Foundation для shared-tool plan/install/doctor/rollback;
- использовать один managed-bin path и общий state;
- обновить GUI, build contract, canary и focused tests;
- не изменять пользовательские незакоммиченные файлы в основном checkout.

## Bootstrap and Delivery

- Legacy Claude уже имеет auto-pull и получает `ru-writing-style` без bootstrap.
- Новый Foundation Installer сразу ставит session updater и OfficeCLI.
- Существующий пользователь нативной базы один раз запускает `$sync-base`, чтобы
  получить SessionStart updater и OfficeCLI.
- После bootstrap новые декларативные tools приезжают автоматически при каждой
  новой сессии.
- Auto-pull consumer не выполняет push. Legacy owner auto-push остаётся вне
  текущей миграции.

## Error Handling

- Network unavailable: log, keep last verified state, start session.
- Manifest invalid: reject snapshot, keep current skill, show one compact warning.
- Hash mismatch: reject affected tool and keep previous version.
- Local unmanaged tool collision: do not overwrite; return a named conflict.
- OfficeCLI download/hash/version failure: restore previous binary and PATH.
- Foundation rollback failure: return hard failure and preserve journal for
  manual recovery.

## Acceptance

### Session tools

- Первичная установка `ru-writing-style` из пустого managed target.
- No-op при неизменившихся ETag и manifest hash.
- Обновление changed skill в той же новой сессии.
- Сохранение unmanaged local skill.
- Отклонение path traversal, executable extension и hash mismatch.
- Fail-open при offline, timeout и занятом lock.
- Rollback при прерывании atomic replace.
- UTF-8 test с кириллицей без `PYTHONIOENCODING`.
- Live same-session discovery отдельно для Claude, Codex и OpenCode.

### OfficeCLI

- План missing/exact/older/newer.
- Установка exact binary по закреплённому SHA-256.
- Один shared install для каждого target plan.
- Идемпотентное добавление PATH.
- No-downgrade для newer version.
- Wrong hash и wrong version не оставляют частичную установку.
- Doctor проверяет command, version, hash и state.
- Rollback восстанавливает существующий binary и PATH.
- Новый installer и `$sync-base` дают эквивалентный final state.

### Release boundaries

- Прогнать focused и full repository tests каждого изменённого репозитория.
- Прогнать target acceptance и live canary там, где provider доступен.
- Зафиксировать `NOT_PASS`, если внешний provider или immutable gate остаётся
  заблокированным.
- Не публиковать release автоматически в рамках реализации.

## Non-goals

- Не включать unattended установку EXE на SessionStart.
- Не переносить MCP, plugins, hooks пользователя и credentials через auto-pull.
- Не объединять три target-репозитория в один.
- Не удалять legacy Claude auto-push.
- Не исправлять несвязанные dirty-файлы или release blockers.

## Done When

- `ru-writing-style` находится во всех трёх target-базах и активной локальной
  установке Codex.
- После bootstrap новый декларативный skill становится доступен в той же новой
  сессии каждого клиента без `$sync-base`.
- OfficeCLI устанавливается новым installer и действующим `$sync-base`, проходит
  doctor и имеет проверенный rollback.
- Локальные unmanaged skills и пользовательские настройки не изменены.
- Все доступные проверки перечислены с фактическим результатом; непройденные
  release gates не представлены как PASS.
