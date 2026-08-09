# Session Tools Auto-Pull and OfficeCLI Design

## Goal

Доставить `ru-writing-style` и OfficeCLI во все управляемые установки Claude,
Codex и OpenCode. Восстановить двухуровневую модель обновления:

- подтягивать лёгкие декларативные инструменты в начале каждой новой сессии;
- устанавливать бинарники и компоненты со сложной настройкой через `$sync-base`
  или общий установщик.

## Context

- В legacy-контуре Claude работает `SessionStart -> auto-pull.ps1`. Фактический
  лог подтверждает успешный pull. Сохранять этот механизм до отдельной миграции.
- В `codex-base`, `claude-base-v2` и `opencode-base` SessionStart сейчас только
  проверяет наличие нового stable release не чаще одного раза в сутки.
- `ru-writing-style` присутствует в legacy Claude. В Codex он существует только
  в локальной неслитой ветке, а в опубликованных нативных базах его нет.
- OfficeCLI не установлен. Использовать официальный Windows x64 asset версии
  `1.0.143`: `officecli-win-x64.exe`, SHA-256
  `d4d4c10fced307e209744cf98a56b003a6e613424fd651b08469274704afd2c6`.
- Codex и Claude включают метаданные навыков в начальный контекст. Их
  SessionStart-hook не является доказанной pre-discovery точкой. OpenCode V2
  поддерживает `ctx.skill.reload`, но этот API остаётся beta.
- Текущая Foundation удаляет каждый `exact_directory` перед копированием.
  Широкие roots `.agents/skills`, `.claude/skills` и
  `.config/opencode/skills` поэтому несовместимы с обещанием сохранить локальные
  пользовательские навыки.
- Старый `$sync-base` извлекает из проверенного target ZIP новый
  `foundation.ps1` и передаёт ему тот же ZIP. Он допускает дополнительные поля
  package manifest, но требует Foundation protocol `1`, команды
  `doctor,install,inventory,plan,rollback` и offline engine.

## Constraints

- Сохранять поток только `hub -> consumer`.
- Не отправлять feedback, telemetry, локальные файлы и изменения пользователя.
- Не изменять пользовательские skills, не зарегистрированные как управляемые.
- Не выполнять бинарники и установочные скрипты из session-tools asset.
- Не блокировать запуск сессии при отсутствии сети или ошибке auto-pull.
- Не читать session manifest из mutable branch или raw-content URL.
- Проверять release, asset, attestation, manifest binding и SHA-256 до записи.
- Проверять закреплённый OfficeCLI asset. Не заявлять отсутствующую upstream
  подпись.
- Не объявлять release готовым без target acceptance и live-canary.
- Сохранить legacy Claude auto-pull до успешной отдельной миграции.

## Decision

Использовать два независимых канала доставки.

### 1. Session tools channel

Применять канал только для декларативных навыков. Разрешить в первой версии:

- `.md`;
- `.json`;
- `.yaml` и `.yml`;
- `.toml`;
- `.txt`.

Отклонять `.ps1`, `.py`, `.exe`, `.dll`, архивы, MCP-конфигурацию, plugins,
hooks и изменения PATH. Помечать такой инструмент как требующий `$sync-base`.

Публиковать с каждым stable target release отдельный asset:

`session-tools-<target>-<version>.zip`.

Добавить в `release-manifest.json` необязательное поле
`session_tools_asset` со строгими полями:

- `name`;
- `sha256`;
- `bytes`;
- `manifest_sha256`;
- `tool_count`;
- `file_count`.

Старый `$sync-base` игнорирует это необязательное поле. Новый release verifier
проверяет его как часть immutable release contract.

Внутри ZIP хранить только:

- `session-tools-manifest.json`;
- `tools/<tool-id>/<relative-file>`.

Manifest schema `1` содержит target, release tag, base version и отсортированный
список tools. Для каждого tool задавать id и отсортированные file records:
relative path, SHA-256 и bytes. Не передавать remote URL и install path.
Destination вычислять локально по target:

- Codex: `.agents/skills/<tool-id>`;
- Claude: `.claude/skills/<tool-id>`;
- OpenCode: `.config/opencode/skills/<tool-id>`.

Формировать manifest как UTF-8 без BOM, LF, с детерминированным порядком ключей
и записей. Runtime parser отклоняет duplicate JSON keys, duplicate tool id,
duplicate file path, регистронезависимые коллизии Windows и неизвестные поля.

Ограничить один asset:

- до 32 tools;
- до 256 файлов;
- до 1 MiB на файл;
- до 8 MiB распакованного содержимого;
- до 10 MiB ZIP.

Первый asset содержит только `ru-writing-style`.

### 2. Managed setup channel

Применять канал для OfficeCLI и будущих инструментов, которые:

- добавляют бинарник;
- меняют PATH или user environment;
- требуют системную зависимость;
- регистрируют MCP;
- выполняют установочный код.

Канонический source record OfficeCLI хранить в
`llm-foundation-installer/client-sources.lock.json` как target `shared`.
Foundation build переносит проверенную запись в `shared-tools.lock.json`.
Использовать эти records только на build/release этапе. Не поручать
`ClientBootstrap.cs` отдельную runtime-установку OfficeCLI.

Foundation release builder скачивает exact asset и проверяет SHA-256. Stable
Foundation release asset содержит engine files, `shared-tools.lock.json` и
`shared-tools/officecli/officecli.exe`; Foundation release manifest связывает
их hashes и sizes. Target builders принимают только проверенный Foundation
release плюс `package-acceptance.json` и включают те же OfficeCLI bytes в каждый
target ZIP, который требует tool. Foundation остаётся единственным runtime
transaction owner для нового installer и `$sync-base`.

## Trust Chain for Session Tools

При каждой новой сессии выполнить дешёвую проверку latest stable tag. Если tag
совпадает с установленным state, завершить no-op. Если tag новее:

1. Проверить semver monotonicity и target prefix.
2. Выполнить `gh release verify <tag>` для target repository.
3. Скачать только `release-manifest.json` и объявленный session-tools asset во
   временный каталог.
4. Выполнить `gh release verify-asset` и `gh attestation verify` для обоих
   файлов.
5. Проверить stable channel, target, tag, version, source commit/tree и binding
   session asset по name, SHA-256 и bytes.
6. Проверить SHA-256 внутренних manifest bytes по `manifest_sha256`.
7. Проверить строгую schema, limits, paths, extensions, file sizes и hashes.
8. Только после всех проверок применить snapshot.

Не использовать raw branch, ETag непроверенного manifest или хеши из того же
mutable источника как trust root.

`gh` остаётся обязательной trust dependency, как у текущего `$sync-base`. Если
`gh` отсутствует, не применять update, оставить рабочую копию и записать
`BLOCKED_GH_REQUIRED`. Новый installer проверяет наличие `gh` до включения
auto-pull. Добавление отдельного установщика GitHub CLI не входит в эту задачу.

## Session Startup and Same-Session Discovery

Развести два пути запуска.

### Managed entrypoints

Считать управляемыми entrypoints:

- кнопки Claude, Codex и OpenCode в LLM Foundation Launch Center;
- сгенерированные команды `claude-managed`, `codex-managed` и
  `opencode-managed`.

Foundation генерирует эти `.cmd` wrappers из release-bound шаблона внутри
проверенного engine и кладёт их в `.llm-foundation/bin`. Wrapper сначала
запускает target updater по фиксированному пути внутри target base, затем
передаёт исходные arguments одноимённому vendor executable без `-managed`.
Wrapper files входят в тот же snapshot и rollback, что target transaction.
Не менять PowerShell profile, shell aliases и vendor shortcuts.

Перед запуском vendor process entrypoint вызывает target-specific
`update-session-tools.ps1`. Скрипт живёт в target runtime и доставляется только
полным base package через `$sync-base` или installer. Он не обновляет сам себя
через session-tools channel.

Порядок managed launch:

1. Взять target lock с bounded wait.
2. Выполнить проверку и обновление session asset.
3. Завершить preflight с `0`, в том числе при offline/no-update.
4. Только после preflight запустить vendor client.

Ограничить весь preflight 30 секундами wall-clock, включая lock wait, latest
check, все `gh` subprocesses, download, verification, apply и cleanup. По
deadline остановить дочернее process tree, восстановить незавершённый atomic
replace, удалить только staging текущей transaction, освободить lock и
запустить vendor client с последней проверенной копией. Не продлевать timeout
последовательными per-command budgets.

Это единственная обязательная same-session гарантия для Codex и Claude: новый
skill уже находится на диске до запуска процесса и до построения discovery
контекста.

OpenCode использует тот же pre-launch путь. После отдельного V2 compatibility
canary можно дополнительно вызвать `ctx.skill.reload`, но beta plugin API не
является release blocker и не заменяет preflight.

### Direct vendor launch fallback

Оставить SessionStart-hook у каждого target. Hook вызывает тот же updater в
режиме `-HookFallback`, чтобы обновление всё равно происходило при прямом
запуске vendor client.

Не заявлять same-session availability для прямого запуска Codex или Claude,
пока canary не докажет порядок discovery. Если catalog уже построен, вывести
не более одной строки `TOOLS_APPLIED_NEXT_SESSION` и не перезапускать клиент.

Таким образом:

- auto-pull выполняется в начале каждой новой сессии и при managed, и при
  direct launch;
- same-session availability гарантируется для Launch Center и managed CLI;
- direct vendor shortcuts остаются совместимым fallback, но не управляемой
  same-session точкой.

## Session Tools Apply Contract

1. Хранить lock в
   `%USERPROFILE%/.llm-foundation/state/session-tools/<target>/update.lock`.
2. Скачать и проверить release во временном каталоге вне destination.
3. Собрать каждый tool в sibling staging directory.
4. Проверить destination ownership до замены.
5. Атомарно переименовать текущую managed copy в previous, затем staging в
   destination.
6. Записать state только после успешной замены всех tools.
7. При ошибке восстановить previous и удалить staging.
8. Хранить максимум одну previous copy на tool.

State schema `1` содержит target, release tag/version, release manifest hash,
session manifest hash, tool/file hashes, destination, ownership marker и время
проверки. Не записывать cwd, имя пользователя, prompt или transcript.

Брать baseline ownership из проверенного
`<target-base>/runtime/session-tools-baseline.json`. При первом запуске:

- принять существующий destination как managed только при полном совпадении с
  baseline hashes;
- считать несовпадающий каталог unmanaged collision и не перезаписывать;
- создать state после успешной проверки baseline.

Логировать target, tag, result code и redacted reason. Не логировать домашний
путь, URL с query, response body и содержимое tool files.

## Preserve Local Skills

До публикации target release изменить package managed surface:

- убрать общий skills root из `exact_directories`;
- добавить отдельный exact directory для каждого package-owned skill;
- не объявлять и не удалять остальные каталоги внутри skills root.

Добавить optional `retired_managed_paths` в package manifest. Удалять retired
skill только если предыдущий Foundation state доказывает, что тот же path был
package-managed. Не удалять неизвестный или изменённый локально path.

Foundation vNext принимает как старый manifest без новых полей, так и новый
manifest с granular skill directories, `retired_managed_paths` и
`session_tools_baseline` и `shared_tools`. Protocol остаётся `1`; список команд
и network=`offline` не меняются.

### Session-owned baseline

Не включать `ru-writing-style` одновременно в base `managed_surface` и
session-tools state. В target repository skill остаётся обычным source и
catalog entry, но release builder исключает его destination из normal target
files и помещает bytes в специальный package payload:

`session-tools-baseline/tools/ru-writing-style/...`.

Добавить optional `session_tools_baseline` в package manifest schema `1`:

- `manifest_path`;
- `manifest_sha256`;
- `tools` с тем же строгим schema, что session asset;
- `retired_tool_ids`.

Foundation валидирует baseline payload, устанавливает его в target skills root
и создаёт session-tools ownership/state в той же package transaction. Не
включать эти destinations в base managed-surface digest и package-owned file
hashes. Foundation doctor:

- сверяет destination с session-tools state, а не с package baseline bytes;
- принимает более новый verified session release без downgrade;
- устанавливает baseline только при missing state/destination;
- блокирует unmanaged collision;
- удаляет retired tool только при совпадении ownership marker.

Session updater и Foundation используют один state schema и один lock. После
auto-pull изменение session-owned skill не создаёт base drift. Foundation
также кладёт проверенную копию baseline manifest в
`<target-base>/runtime/session-tools-baseline.json`; updater использует её
только для recovery первого state, а не как package hash после auto-pull.

Проверить миграцию минимум на трёх homes:

- чистый;
- текущая широкая managed surface без локальных additions;
- широкая managed surface плюс unmanaged local skill.

## Legacy Claude

Оставить `~/.claude/scripts/auto-pull.ps1` и его первый SessionStart-hook
рабочими. Legacy contour уже получает `ru-writing-style` через Git.

Не заменять legacy `settings.json` пакетом `claude-base-v2` и не запускать
native migration в текущей задаче. Доставлять OfficeCLI на legacy workstation
через новый Foundation Installer shared-tool workflow. Миграцию legacy Claude
на immutable session assets оформить отдельной задачей после canary.

Не переносить `git pull --rebase --autostash` в новые пользовательские базы.
Auto-pull consumer не выполняет push. Legacy owner auto-push остаётся вне scope.

## OfficeCLI Package Contract

### Canonical source

Добавить shared source:

- id: `officecli`;
- target: `shared`;
- version: `1.0.143`;
- URL:
  `https://github.com/iOfficeAI/OfficeCLI/releases/download/v1.0.143/officecli-win-x64.exe`;
- SHA-256:
  `d4d4c10fced307e209744cf98a56b003a6e613424fd651b08469274704afd2c6`;
- artifact kind: `portable-exe`;
- install mode: `foundation-shared`;
- version arguments: `--version`;
- version pattern:
  `(?<![0-9A-Za-z])v?(?<version>[0-9]+\.[0-9]+\.[0-9]+)(?![0-9A-Za-z.])`;
- signature required: `false`;
- license: `Apache-2.0`.

Build pipeline получает размер из фактически проверенного asset и не принимает
ручное значение. Не выполнять upstream `officecli install`.

Foundation release verifier проверяет `shared-tools.lock.json`, payload и их
binding в release manifest. Target builder повторно проверяет Foundation
package acceptance, source record, bytes и SHA-256 перед копированием payload.

### Package manifest schema

Добавить необязательный отсортированный array `shared_tools` в package manifest
schema `1`. Для каждой записи требовать только известные поля:

- `id`;
- `version`;
- `payload_path`;
- `sha256`;
- `bytes`;
- `install_path`;
- `version_arguments`;
- `version_pattern` с named capture `version`;
- `timeout_seconds`;
- `path_entry`;
- `environment`;
- `shim`.

Для OfficeCLI закрепить payload path
`shared-tools/officecli/officecli.exe` и private install path
`.llm-foundation/libexec/officecli/officecli.exe`. Не помещать upstream EXE в
PATH. В `shim` объявить command path `.llm-foundation/bin/officecli.cmd` и
политику:

- всегда устанавливать process `OFFICECLI_SKIP_UPDATE=1`;
- пустой вызов отклонять с `BLOCKED_BARE_INVOCATION`;
- отклонять первые arguments `install`, `skills`, `mcp`, `update` и
  `self-update` с `BLOCKED_MANAGED_INSTALL`;
- остальные arguments передавать private EXE без изменения.

Объект `shim` имеет exact fields `schema_version`, `template_id`,
`command_path`, `empty_invocation`, `blocked_first_arguments` и
`process_environment`. Использовать `schema_version=1` и release-bound
`template_id=officecli-managed-v1`. Foundation генерирует bytes только из
этого встроенного template, записывает emitted SHA-256 в shared state и
сверяет его в doctor.

В `environment` дополнительно объявить current-user
`OFFICECLI_SKIP_UPDATE=1`, чтобы прямой запуск private EXE также не включал
upstream background updater. Shim не разрешает bare self-install, который иначе
копирует binary, устанавливает skills в обнаруженные agent catalogs и может
регистрировать MCP fallback.

Включить payload в обычный sorted `files` array. Разрешить его вне target
managed surface только при полном совпадении с записью `shared_tools`.
Foundation проверяет bytes и SHA-256 до записи.

### Backward-compatible bootstrap

Сохранить Foundation engine manifest schema `1`, protocol `1`, network
`offline` и существующие пять команд. Старый `$sync-base`:

1. проверяет immutable target release;
2. извлекает из ZIP новый Foundation engine;
3. передаёт этому engine весь ZIP;
4. новый engine читает optional `shared_tools` и встроенный OfficeCLI payload.

Не выполнять сетевое скачивание из Foundation. Это сохраняет один-command
bootstrap для уже установленной native base без обновления старого sync script.

Новый GUI installer получает тот же проверенный target package и вызывает тот
же Foundation workflow `plan -> install -> doctor`, а при ошибке `rollback`.
Удалить OfficeCLI из самостоятельного managed-bin execution
`ClientBootstrap.cs`; оставить там только vendor client bootstrap.

### Version detection

Проверять private exact install path, а не случайный `officecli` из PATH. Для
каждого запуска version probe:

- установить `OFFICECLI_SKIP_UPDATE=1` в process environment;
- вызвать `<install_path> --version`;
- ограничить время 10 секундами;
- ограничить объединённый stdout/stderr 4 KiB;
- требовать exit code `0`;
- извлечь ровно один semver через pinned `version_pattern`;
- сравнить parsed semver с package version.

Закрепить .NET-compatible pattern:

`(?<![0-9A-Za-z])v?(?<version>[0-9]+\.[0-9]+\.[0-9]+)(?![0-9A-Za-z.])`.

Требовать ровно одно совпадение во всём bounded output и exact equality capture
`version` с package version для состояния `exact`.

Состояния plan:

- `missing` — файла нет;
- `exact` — version и SHA-256 совпадают;
- `older` — распознанная версия ниже package version;
- `newer` — распознанная версия выше package version;
- `conflict` — unmanaged file, wrong hash при exact version, неоднозначный или
  неуспешный version probe.

Для `newer` завершить plan кодом `BLOCKED_NO_DOWNGRADE` до любой мутации. Для
`conflict` не перезаписывать файл без доказанного managed ownership.

## Foundation Transaction and Rollback

Foundation владеет одной транзакцией base package плюс shared tools.

До первой мутации записать journal в target transaction state. Snapshot
содержит:

- текущую target managed surface;
- прежний private OfficeCLI EXE и `officecli.cmd`, если они были;
- прежний current-user PATH;
- прежнее значение `OFFICECLI_SKIP_UPDATE` и факт его отсутствия;
- прежний shared-tool state;
- прежний target managed wrapper, если он был.

Порядок install:

1. Проверить package, client contract и shared tool plan.
2. Создать durable snapshot и journal.
3. Установить granular target surface.
4. Атомарно заменить private OfficeCLI EXE через temp file в том же каталоге.
5. Идемпотентно добавить `.llm-foundation/bin` в current-user PATH.
6. Установить current-user `OFFICECLI_SKIP_UPDATE=1`.
7. Создать или обновить `officecli.cmd` и target managed wrapper.
8. Проверить, что command resolution находит shim, а не private EXE.
9. Оставить journal до отдельного успешного `doctor`.

Doctor проверяет package state, exact private binary SHA-256, exact parsed
version, shim hash/behavior, PATH, persistent environment и повторный version
probe через private EXE и public shim. После PASS удалить rollback payload и
пометить transaction committed.

При install/doctor failure `$sync-base` и GUI вызывают тот же Foundation
`rollback`. Он восстанавливает target surface, private binary, shim, PATH,
environment и shared state. При прерывании следующий `plan` возвращает
`ROLLBACK_REQUIRED`.

Хранить shared state в
`%USERPROFILE%/.llm-foundation/state/shared-tools/officecli/current.json`.
Не использовать отдельный `.llm-foundation/clients/officecli` record.

## Repository Responsibilities

### `llm-foundation-installer`

- добавить canonical OfficeCLI shared source;
- расширить Foundation schema `1` для granular skills, retired paths и shared
  tools;
- включать проверенный OfficeCLI payload в target packages;
- сделать Foundation единственным runtime owner OfficeCLI;
- перевести GUI на тот же Foundation transaction;
- добавить managed launch preflight для трёх клиентов;
- обновить build contract, verifier, canary и focused tests;
- не менять dirty основной checkout.

### `codex-base`

- перенести `ru-writing-style` из feature-ветки на свежий main;
- добавить OfficeCLI cold reference;
- публиковать session-tools asset и release binding;
- добавить updater, baseline и managed CLI entrypoint;
- заменить version-only SessionStart на updater плюс release check;
- перейти на granular managed skill directories;
- объявить OfficeCLI в package `shared_tools`;
- обновить counts, docs, reports и tests.

### `claude-base-v2`

- добавить тот же skill, reference, asset builder, updater и baseline;
- добавить managed CLI entrypoint и SessionStart fallback;
- перейти на granular managed skill directories;
- объявить OfficeCLI в package `shared_tools`;
- обновить counts, docs, reports и tests;
- не объявлять provider-blocked live canary успешным.

### `opencode-base`

- добавить тот же skill, reference, asset builder, updater и baseline;
- добавить managed CLI entrypoint и SessionStart fallback;
- перейти на granular managed skill directories;
- объявить OfficeCLI в package `shared_tools`;
- обновить counts, docs, reports и tests;
- сохранить provider и immutable-integrity gates.

## Migration Order

1. Выпустить Foundation vNext с backward-compatible protocol `1` и тестами
   старых package manifests.
2. Обновить target builders: granular skills, embedded shared tool, session
   asset, managed preflight.
3. Прогнать migration tests с unmanaged local skills и interrupted transaction.
4. Прогнать isolated new-install через GUI и existing-install через старый
   `$sync-base`.
5. Прогнать live managed-launch same-session canary для каждого клиента.
6. Публиковать target release только после собственных release gates.
7. Дать действующим native users одну bootstrap-команду `$sync-base`.
8. После bootstrap доставлять новые декларативные tools автоматически.

Не публиковать target package, требующий Foundation vNext, пока Foundation
release и его acceptance не проверены. Не мигрировать legacy Claude в этом
порядке.

## Error Handling

- Network unavailable: log, keep last verified state, start client.
- Missing `gh`: return `BLOCKED_GH_REQUIRED`, keep state, start client.
- Release or attestation invalid: reject update, keep current tool.
- Session manifest invalid: reject full snapshot, not a partial subset.
- Hash mismatch: reject snapshot and restore previous.
- Local unmanaged collision: keep local directory and name the conflicting id.
- Busy lock: bounded wait, then fail-open with `SKIPPED_LOCK_BUSY`.
- OfficeCLI wrong hash/version: rollback whole Foundation transaction.
- OfficeCLI newer: stop before mutation with `BLOCKED_NO_DOWNGRADE`.
- Rollback failure: preserve journal and return hard failure.

## Acceptance

### Session tools

- Первичная установка `ru-writing-style` из пустого managed target.
- No-op при том же stable tag и manifest hash.
- Обновление changed skill до запуска vendor process через каждый managed
  entrypoint.
- SessionStart fallback при direct launch не блокирует session.
- Сохранение unmanaged local skill во время `$sync-base` и session update.
- Отклонение mutable/raw source, path traversal, executable extension,
  duplicate key/id/path, symlink и hash mismatch.
- Проверка file/count/expanded-size limits.
- Fail-open при offline, timeout и занятом lock.
- Общий 30-sec wall-clock timeout завершает дочерние processes и cleanup до
  запуска vendor client.
- Rollback при прерывании atomic replace.
- UTF-8 test с кириллицей без `PYTHONIOENCODING`.
- Live same-session discovery отдельно для Claude, Codex и OpenCode через
  Launch Center и managed CLI.

### OfficeCLI

- Старый `$sync-base` принимает новый package manifest и извлекает Foundation
  protocol `1`.
- План missing/exact/older/newer/conflict.
- Установка exact binary по закреплённому SHA-256 из package payload.
- Один shared install для каждого target package.
- Идемпотентный PATH и `OFFICECLI_SKIP_UPDATE=1`.
- Probe: exact path, exit `0`, 10-sec timeout, 4-KiB output cap, один semver.
- No-downgrade для newer version до мутации.
- Wrong hash и wrong version не оставляют частичную установку.
- Bare `officecli` завершается `BLOCKED_BARE_INVOCATION` и не изменяет agent
  skills, MCP config, private binary или PATH.
- `officecli install`, `skills`, `mcp`, `update` и `self-update` завершаются
  `BLOCKED_MANAGED_INSTALL` без изменений.
- Doctor проверяет private binary, shim, command resolution, version, hash,
  PATH, environment и state.
- Rollback восстанавливает private binary, shim, PATH, environment и target
  base.
- Новый installer и старый `$sync-base` дают эквивалентный final state.

### Release boundaries

- Прогнать focused и full repository tests каждого изменённого repository.
- Прогнать target acceptance и live canary там, где provider доступен.
- Зафиксировать `NOT_PASS`, если provider или immutable gate заблокирован.
- Не публиковать release автоматически в рамках реализации.

## Non-goals

- Не устанавливать EXE на SessionStart.
- Не переносить MCP, plugins, hooks пользователя и credentials через auto-pull.
- Не объединять три target repositories.
- Не удалять legacy Claude auto-push.
- Не устанавливать GitHub CLI скрыто в рамках этой задачи.
- Не гарантировать same-session discovery для прямых vendor shortcuts без
  отдельного доказательства.
- Не исправлять несвязанные dirty files или release blockers.

## Done When

- `ru-writing-style` находится во всех трёх target bases и активной локальной
  установке Codex.
- После bootstrap новый декларативный skill подтягивается при каждой новой
  сессии и доступен в той же сессии при запуске через managed entrypoint.
- Прямой запуск vendor client выполняет fail-open SessionStart fallback без
  ложного заявления same-session availability.
- OfficeCLI устанавливается новым installer и старым `$sync-base`, проходит
  doctor и имеет проверенный rollback.
- Legacy Claude auto-pull продолжает работать.
- Локальные unmanaged skills и пользовательские настройки не изменены.
- Все проверки перечислены с фактическим результатом; непройденные release
  gates не представлены как PASS.
