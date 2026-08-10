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

Schema `1` сохраняет лимит до 32 tools для детерминированной сборки и
forward-compatible проверки, но runtime transaction protocol `1` принимает
ровно один tool на asset. Singular journal связывает один staging, previous и
destination; updater и promotion отклоняют zero/multi-tool asset до mutation с
`BLOCKED_MULTI_TOOL_ASSET`. Поддержку нескольких destinations вводить только
новой версией transaction protocol с per-tool durable operations и общей
recovery проверкой полного snapshot.

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
Foundation release asset содержит engine files, `shared-tools.lock.json`,
`shared-tools/officecli/officecli.exe`, собранный Foundation shim
`support/officecli-shim.exe` и `support/officecli-command-policy.json`.
Foundation release manifest связывает их hashes и sizes. Target builders
принимают только проверенный Foundation release плюс `package-acceptance.json`
и включают те же OfficeCLI, shim и policy bytes в каждый target ZIP, который
требует tool. Foundation остаётся единственным runtime transaction owner для
нового installer и `$sync-base`.

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

Foundation устанавливает release-bound compiled launchers
`claude-managed.exe`, `codex-managed.exe` и `opencode-managed.exe` в
`.llm-foundation/bin`. Все три собираются тем же .NET Framework toolchain,
получают уже разобранный Windows `string[] args` и определяют fixed target по
собственному filename. Launcher files входят в тот же snapshot и rollback, что
target transaction. Не менять PowerShell profile, shell aliases и vendor
shortcuts.

Launcher читает только committed target receipt с exact fields
`schema_version`, `target`, `launcher_path`, `launcher_sha256`, `updater_path`
и `vendor_executable_path`. Foundation проверяет эти пути и launcher hash до
commit. Launcher отклоняет missing, tampered или target-mismatched receipt до
запуска дочерних процессов.

Для updater launcher использует exact
`%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe` с
`UseShellExecute=false` и `Arguments`, сформированным
`WindowsArgv.Serialize(string[] args)`. Передаются только fixed tokens
`-NoLogo`, `-NoProfile`, `-NonInteractive`, `-ExecutionPolicy`, `Bypass`,
`-File`, exact `updater_path`, `-ManagedPreflight`, а также пары
`-TransactionId <guid>`, `-StartTick <int64>`,
`-MutationCutoffTick <int64>`, `-KillTick <int64>`,
`-HardDeadlineTick <int64>` и `-StopwatchFrequency <int64>`. Значения создаёт
launcher из одного `Stopwatch.GetTimestamp()`/`Stopwatch.Frequency`, они не
принимаются от пользователя и сериализуются как отдельные argv tokens.
Updater требует canonical GUID, положительные decimal integers, частоту равную
локальной `Stopwatch.Frequency` и строгий порядок
`start < mutation-cutoff < kill < hard-deadline`; иначе завершает preflight до
mutation. Пользовательские arguments не передаются updater и не
интерпретируются PowerShell. Не использовать `cmd.exe`, `%ComSpec%`, `-Command`,
string interpolation или shell operators.

Launcher назначает updater process в Windows Job Object с
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` и измеряет единый 30-секундный monotonic
deadline. Первые 22 секунды доступны для lock/network/verify/staging; updater
не начинает mutation после этого cutoff. До 25-й секунды updater завершает
локальный apply или cooperative cleanup. Если updater ещё работает, launcher
закрывает job и завершает его process tree. Последние 5 секунд зарезервированы
для in-process recovery внутри compiled launcher по durable journal, без
нового дочернего процесса.

После успешного preflight или подтверждённого recovery launcher запускает
exact `vendor_executable_path` с `UseShellExecute=false`; исходный
`string[] args` передаётся только через тот же `WindowsArgv.Serialize` без
повторного parsing. Если launcher-side recovery не может доказать consistent
destination/state до hard deadline, managed entrypoint возвращает
`BLOCKED_SESSION_RECOVERY` и не запускает vendor поверх повреждённого managed
state. Прямой vendor shortcut остаётся доступен как отдельный fail-open путь.

Launch Center проверяет committed receipt и hash, затем запускает exact
target-specific managed launcher тем же безопасным process-argument path.
Он не вызывает updater или vendor executable отдельно. Так GUI, ручная
managed-команда и VPN/SingBox environment используют одну реализацию preflight;
отдельной логики обновления в GUI нет.

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
check, все `gh` subprocesses, download, verification, apply и recovery. Соблюдать
cutoffs 22/25/30 из launcher contract; не продлевать timeout последовательными
per-command budgets. При timeout до mutation удалить только staging текущей
transaction и запустить vendor с последней проверенной копией. При timeout во
время mutation закрыть updater Job Object, выполнить launcher-side recovery и
запустить vendor только после доказанного consistent state.

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

До создания staging или иной transaction-owned filesystem entry записать
`%USERPROFILE%/.llm-foundation/state/session-tools/<target>/active-transaction.json`
через temp file, write-through flush и atomic replace. Exact journal schema `1`
содержит launcher-generated transaction id, monotonic tick contract, phase,
target, committed receipt hash, previous state hash,
staging/previous/destination paths, ожидаемые hashes и для каждой операции
`intent`/`applied`. Начальная phase `created` связывает ещё отсутствующий
staging path; phase `staged` записывается после его полной проверки. Каждый
`intent` durable до filesystem mutation; `applied` durable сразу после неё.
Пути обязаны находиться в проверенных target state и skills roots; reparse
ancestors отклоняются.

Один и тот же recovery algorithm реализовать в updater для cooperative error и
в compiled launcher для killed updater. Он сначала валидирует journal, receipt,
path roots и hashes, затем идемпотентно восстанавливает previous destination и
previous state либо признаёт полностью committed new state. Он удаляет только
transaction-owned staging/previous, journal и stale lock marker. Existing
active journal восстанавливается до network check при следующем запуске.
Не начинать новый apply, пока active journal не закрыт.

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
  `\A(?:officecli[ \t]+)?v?(?<version>(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))\z`;
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
- `bundle_version`;
- `compatibility_epoch`;
- `minimum_compatible_version`;
- `maximum_exclusive_version`;
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
PATH. В `shim` объявить public command
`.llm-foundation/bin/officecli.exe`. Это собранный Foundation binary, а не
shell-wrapper и не upstream EXE.

Объект `shim` имеет exact fields `schema_version`, `payload_path`, `sha256`,
`bytes`, `command_path`, `policy_payload_path`, `policy_install_path`,
`policy_sha256`, `policy_bytes` и `process_environment`. Для первого rollout
использовать package path `support/officecli-command-policy.json` и installed
path `.llm-foundation/libexec/officecli/officecli-command-policy.json`.
Использовать `schema_version=1`. Foundation проверяет и атомарно устанавливает
готовые release-bound shim и policy bytes; runtime не генерирует их из
локального template.

Shim собирается существующим .NET Framework toolchain, получает уже
разобранный Windows `string[] args` и не вызывает shell. Для запуска private
EXE использовать `ProcessStartInfo` с `UseShellExecute=false`, exact
`FileName` и `Arguments`, сформированным только функцией
`WindowsArgv.Serialize(string[] args)`. Она реализует обратное преобразование
для Windows `CommandLineToArgvW`/CRT:

- пустой argument сериализовать как `""`;
- argument без whitespace и `"` передавать без кавычек;
- остальные arguments заключать в `"`;
- перед literal `"` сериализовать последовательность из `n` backslashes как
  `2n+1` backslashes;
- перед closing `"` сериализовать конечную последовательность из `n`
  backslashes как `2n` backslashes;
- между сериализованными arguments ставить один ASCII space.

Не использовать `cmd.exe`, PowerShell, `ShellExecute`, interpolation или иной
quoting path. Сравнивать command tokens через ASCII-normalization и
`OrdinalIgnoreCase`; не-ASCII command token отклонять. Применить строгую
grammar:

- пустой вызов отклонять с `BLOCKED_BARE_INVOCATION`;
- разрешить одиночные `--version`, `--help`, `-h` и `-?`;
- перед command разрешить не более одного exact global option `--json`;
- отклонять `--`, неизвестный leading option и tokens с префиксом `/` или `@`;
- определить command как следующий token и проверить его по allowlist;
- отклонять неизвестный command с `BLOCKED_UNKNOWN_COMMAND`;
- отклонять `install`, `skills`, `skill`, `mcp`, `mcp-serve`, `config`,
  `update`, `self-update`, `__update-check__` и `__resident-serve__` с
  `BLOCKED_MANAGED_INSTALL`;
- после принятого command передать исходный argv private EXE только через
  `WindowsArgv.Serialize`.

Release-bound policy для `v1.0.143` содержит exact allowlist `open`, `close`,
`watch`, `unwatch`, `mark`, `unmark`, `get-marks`, `goto`, `view`, `get`,
`query`, `set`, `add`, `remove`, `move`, `swap`, `refresh`, `raw`, `raw-set`,
`add-part`, `validate`, `save`, `batch`, `dump`, `import`, `create`, `merge`,
`plugins`, `help` и `load_skill`. Смена OfficeCLI version требует нового
проверенного policy asset и shim tests; нельзя автоматически наследовать новые
upstream commands.

В `process_environment` и persistent current-user `environment` объявить
`OFFICECLI_NO_AUTO_INSTALL=1` и `OFFICECLI_SKIP_UPDATE=1`. Первая переменная
отключает upstream `MaybeAutoInstall`, вторая — background updater. Bare вызов
дополнительно блокируется shim до запуска private EXE. Это не даёт штатному
пути OfficeCLI копировать binary, устанавливать skills в обнаруженные agent
catalogs или регистрировать MCP fallback.

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

Новый GUI installer получает тот же проверенный target package и вызывает
Foundation `install`. Эта команда сама выполняет
`plan -> snapshot -> install -> doctor -> commit/rollback` под одним global
lock. Не вызывать отдельный rollback после успешного committed install.
Необязательный внешний post-commit `doctor` является новой read-only
проверкой: его failure показать пользователю, но не восстанавливать уже
committed transaction. Удалить OfficeCLI из самостоятельного managed-bin
execution `ClientBootstrap.cs`; оставить там только vendor client bootstrap.

### Version detection

Проверять private exact install path, а не случайный `officecli` из PATH. Для
каждого запуска version probe:

- установить `OFFICECLI_NO_AUTO_INSTALL=1` и `OFFICECLI_SKIP_UPDATE=1` в
  process environment;
- вызвать `<install_path> --version`;
- ограничить время 10 секундами;
- ограничить объединённый stdout/stderr 4 KiB;
- требовать exit code `0`;
- удалить только один конечный `CRLF` или `LF`, затем сопоставить весь
  оставшийся output с pinned `version_pattern`;
- сравнить parsed semver с package version.

Закрепить .NET-compatible pattern:

`\A(?:officecli[ \t]+)?v?(?<version>(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))\z`.

Требовать одно совпадение всего bounded output и exact equality capture
`version` с package version для состояния `exact`. Отклонять лишние строки,
leading/trailing whitespace, four-component version, prerelease и build
suffix. Компилировать с `CultureInvariant | IgnoreCase` и match timeout 100 ms.
Добавить negative tests `v1.1.0.143`, `v9.1.0.143`,
`v1.0.143-alpha` и `1.0.143+build`.

`bundle_version` — монотонный semver всего managed комплекта: upstream EXE,
Foundation shim, policy и environment contract. Увеличивать его при изменении
любого элемента, даже если upstream `version` не изменилась. Первый rollout
использует `bundle_version=1.0.0`.

Состояния plan:

- `missing` — файла нет;
- `exact` — совпадают `bundle_version`, OfficeCLI version/SHA-256,
  compatibility epoch, shim hash, policy hash и environment contract;
- `managed-older` — committed bundle version ниже package bundle version и
  publisher monotonicity contract допускает обновление;
- `compatible-newer` — committed bundle version выше package bundle version,
  OfficeCLI version входит в
  `[minimum_compatible_version, maximum_exclusive_version)` и имеет тот же
  `compatibility_epoch`;
- `incompatible-newer` — committed bundle version выше candidate, но OfficeCLI
  version или compatibility epoch несовместимы;
- `conflict` — unmanaged file, wrong hash при exact version, неоднозначный или
  неуспешный version probe.

Сначала проверять ownership. Любой существующий EXE, shim или policy без
committed Foundation receipt классифицировать как `conflict`, независимо от
распознанной version. Состояния `managed-older` и `compatible-newer` допустимы
только при совпадении текущего полного bundle с этим receipt. Одинаковый
`bundle_version` при различии любого identity field означает `conflict`, а не
update. Committed bundle с меньшим номером, но более новой upstream version,
также означает `conflict`: publisher нарушил monotonicity contract.

Для `compatible-newer` сохранить установленный shared tool без изменения и
продолжить target base transaction. Требовать совпадение EXE, shim и policy
hashes с shared state от ранее committed Foundation transaction и проверять
сохранённый digest принятого package/release manifest. Для
`incompatible-newer` завершить plan кодом `BLOCKED_NO_DOWNGRADE` до любой
мутации. Для `conflict` не перезаписывать файл без доказанного managed
ownership.

Для первого rollout все три target packages объявляют candidate `1.0.143`,
`bundle_version=1.0.0`, range `[1.0.143,2.0.0)` и
`compatibility_epoch=officecli-managed-v1`. Номер upstream version сам по себе
не доказывает совместимость: новый Foundation release может сохранить epoch
только после shim/command compatibility matrix с предыдущим candidate;
несовместимый release получает новый epoch. Publisher gate сравнивает bundle
version, range и epoch во всех трёх stable target manifests. Следующее
обновление OfficeCLI сначала публикует новый Foundation asset и policy с
большим bundle version, затем target packages с совместимым range. Старый
target package не понижает уже установленный совместимый shared tool или его
более новую policy при той же upstream version.

## Foundation Transaction and Rollback

Foundation владеет одной user-global транзакцией base package плюс shared
tools. Все Foundation install для Codex, Claude и OpenCode сериализовать через
exclusive lock
`%USERPROFILE%/.llm-foundation/state/foundation/install.lock`.

Команда `install` держит global lock непрерывно на всём внутреннем workflow
`plan -> snapshot -> install -> doctor -> commit/rollback`. Отдельные команды
`plan`, `doctor` и `rollback` также берут этот lock; `install` повторяет plan
после захвата, поэтому результат внешнего `plan` не является разрешением на
мутацию. При изменении target surface дополнительно брать
`state/session-tools/<target>/update.lock` в фиксированном порядке: сначала
global Foundation lock, затем target lock. SessionStart updater берёт только
target lock и не читает и не меняет shared OfficeCLI state.

Хранить durable transactions в
`state/foundation/transactions/<transaction-id>/journal.json`, а единственный
active pointer — в `state/foundation/active.json`. Journal содержит
`transaction_id`, target, package/release digests, все snapshot paths, прежний
shared-tool receipt, PATH/environment и фазу. До первой мутации атомарно
записать journal и active pointer. Перед каждой заменой дописать и сбросить на
диск operation record с path, expected-before hash/absence и intended-after
hash/absence; после atomic replace отметить operation applied. Committed target
и shared receipts не перезаписывать до успешного doctor.

После захвата свободного global lock наличие незавершённого `active.json`
означает stale transaction. `plan` возвращает `ROLLBACK_REQUIRED` до любой
мутации. `rollback` работает только под global lock и только когда
`transaction_id` совпадает в active pointer и journal. Для каждой operation
текущее состояние обязано совпасть с declared before или after fingerprint;
иначе вернуть hard conflict и не восстанавливать snapshot. Cleanup удаляет
только каталог своей transaction. После commit active pointer уже отсутствует,
поэтому foreign/stale rollback не может восстановить shared state поверх
committed transaction другого target.

Snapshot содержит:

- текущую target managed surface и target state;
- прежние private OfficeCLI EXE, public shim и policy, если они были;
- прежний current-user PATH;
- прежние значения `OFFICECLI_NO_AUTO_INSTALL` и `OFFICECLI_SKIP_UPDATE` с
  признаками их отсутствия;
- прежний shared-tool state и provenance receipt;
- прежний target managed launcher и receipt, если они были.

Порядок install под обоими locks:

1. Проверить package, client contract, active pointer и shared tool plan.
2. Создать durable snapshot, journal и active pointer.
3. Установить granular target surface.
4. Для missing/managed-older атомарно заменить private OfficeCLI EXE, shim и
   policy через temp files в соответствующих каталогах; для compatible-newer
   сохранить весь проверенный shared tool комплект.
5. Идемпотентно добавить `.llm-foundation/bin` в current-user PATH.
6. Установить current-user `OFFICECLI_NO_AUTO_INSTALL=1` и
   `OFFICECLI_SKIP_UPDATE=1`.
7. Установить или обновить target managed launcher и receipt.
8. Проверить, что command resolution находит Foundation shim, а не private EXE.
9. Выполнить doctor в той же locked transaction.
10. При PASS атомарно записать committed target/shared receipts, удалить
    active pointer, затем удалить rollback payload.

Doctor проверяет package state, private binary SHA-256, строго parsed version,
shim и policy hashes/behavior, command resolution, PATH, обе persistent
environment variables и повторный version probe через private EXE и public
shim. Для compatible-newer он проверяет сохранённый committed provenance и
не требует hash старого candidate package.

При install/doctor failure тот же engine вызывает rollback до освобождения
global lock. Он восстанавливает target surface, private binary, shim, policy,
PATH, environment и shared state только для своего transaction id. При
прерывании следующий `plan` возвращает `ROLLBACK_REQUIRED`.

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
- Busy session lock: bounded wait, then fail-open with `SKIPPED_LOCK_BUSY`.
- Busy Foundation global lock: bounded wait, затем
  `BLOCKED_FOUNDATION_BUSY` без мутации; второй `$sync-base` можно повторить.
- OfficeCLI wrong hash/version: rollback whole Foundation transaction.
- OfficeCLI compatible-newer: preserve verified shared tool and continue target
  base install.
- OfficeCLI incompatible-newer: stop before mutation with
  `BLOCKED_NO_DOWNGRADE`.
- Rollback failure: preserve journal and return hard failure.

## Acceptance

### Session tools

- Первичная установка `ru-writing-style` из пустого managed target.
- No-op при том же stable tag и manifest hash.
- Обновление changed skill до запуска vendor process через каждый managed
  entrypoint.
- Эквивалентный результат managed CLI и Launch Center при одном package/state:
  оба запускают exact target launcher; shell не вызывается.
- Launcher argv round-trip для empty argument, spaces, tabs, literal quotes,
  trailing backslashes, backslashes перед quote, `%`, `!`, `^`, `&`, `|`,
  `<`, `>` и кириллицы; все значения доходят до fake vendor executable без
  повторной интерпретации.
- Updater получает только fixed arguments; до-mutation timeout закрывает Job
  Object и запускает fake vendor с прежним state не позднее 30 секунд.
- Updater отклоняет malformed GUID/ticks, неверную Stopwatch frequency и любой
  порядок deadline, отличный от launcher contract, до создания staging.
- Инъекция kill в phases `created` и `staged` доказывает удаление только
  journal-bound staging текущей transaction и сохранение соседних каталогов.
- Инъекция kill после каждого `intent` и `applied` перехода доказывает, что
  launcher-side recovery восстанавливает byte-identical destination/state,
  удаляет только transaction-owned staging/journal и лишь затем запускает fake
  vendor. Повторный recovery является no-op.
- Tampered/unsafe journal и recovery, не завершившийся к hard deadline, дают
  `BLOCKED_SESSION_RECOVERY`; fake vendor через managed entrypoint не стартует.
- SessionStart fallback при direct launch не блокирует session.
- Сохранение unmanaged local skill во время `$sync-base` и session update.
- Отклонение mutable/raw source, path traversal, executable extension,
  duplicate key/id/path, symlink и hash mismatch.
- Проверка file/count/expanded-size limits.
- Отклонение zero/multi-tool asset в runtime transaction protocol `1` до
  mutation с `BLOCKED_MULTI_TOOL_ASSET`.
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
- План missing/exact/managed-older/compatible-newer/incompatible-newer/conflict;
  exact включает весь bundle, а не только upstream EXE.
- Установка exact binary по закреплённому SHA-256 из package payload.
- Один shared install для каждого target package.
- Идемпотентный PATH, `OFFICECLI_NO_AUTO_INSTALL=1` и
  `OFFICECLI_SKIP_UPDATE=1`.
- Probe: exact path, exit `0`, 10-sec timeout, 4-KiB output cap и full-output
  regex; negative cases four-component, prerelease, build suffix и лишняя
  строка.
- Compatible-newer сохраняется с проверкой committed provenance, version range
  и compatibility epoch; mismatch epoch даёт incompatible-newer;
  incompatible-newer не вызывает downgrade и блокируется до мутации.
- При одинаковой OfficeCLI `1.0.143` newer bundle сохраняет более новые shim и
  policy; older bundle обновляет их. Одинаковый bundle version с другим hash
  даёт conflict.
- Wrong hash и wrong version не оставляют частичную установку.
- Bare `officecli` завершается `BLOCKED_BARE_INVOCATION` и не изменяет agent
  skills, MCP config, private binary или PATH.
- Проверить case variants, singular `skill`, leading `--json`, неизвестные
  options, `--`, `/`, `@`, stateful/internal aliases и неизвестный command.
  Они завершаются управляемым block code без запуска private EXE.
- Allowlisted commands передают точный argv в fake private executable без
  shell parsing; round-trip покрывает empty argument, spaces, tabs, literal
  quotes, trailing backslashes, backslashes перед quote и кириллицу.
  `--version` и help forms проходят отдельные exact paths.
- В isolated user profile снять до/после snapshot `.claude/skills`,
  `.agents/skills`, `.config/opencode/skills`, MCP configs, private binary,
  PATH и environment. Bare/blocked вызовы не меняют snapshot.
- Doctor проверяет private binary, shim, policy, command resolution, version,
  hashes, PATH, обе environment variables и state.
- Два параллельных `$sync-base` для разных targets сериализуются global lock:
  второй ждёт или получает `BLOCKED_FOUNDATION_BUSY`, не откатывает чужой
  commit и после повтора получает единый shared state.
- Stale journal требует rollback с exact transaction id; foreign id не
  восстанавливается и не удаляется.
- Rollback восстанавливает private binary, shim, policy, PATH, environment,
  shared state и target base.
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
