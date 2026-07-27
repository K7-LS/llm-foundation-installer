# LLM Foundation Installer — руководство оператора

## Назначение

Установщик обслуживает три независимые рабочие среды:

- Codex Desktop и Codex CLI;
- Claude Code;
- OpenCode Desktop и CLI.

EXE содержит проверенный Foundation engine и принятые пакеты баз. Если
официального клиента нет, установщик загружает его только по записи из
`client-sources.lock.json`, проверяет SHA-256 и издателя, а затем выполняет
установку от имени текущего пользователя. Права администратора не требуются.

Установщик не запрашивает, не читает и не переносит LLM-логины, OAuth-токены,
API-ключи или cookies. Авторизация выполняется интерактивно внутри самих
клиентов после установки.

## Семь этапов на экране

1. **Система** — Windows, архитектура, пакеты баз и текущие клиенты.
2. **Соединение** — Direct, VPN или Proxy.
3. **Клиенты** — загрузка, SHA-256, подпись и издатель.
4. **План баз** — точный список изменений управляемой поверхности.
5. **Установка** — backup, атомарное применение, `doctor`, rollback.
6. **Авторизация** — вход только в Codex, Claude и OpenCode.
7. **Готово** — локальный отчёт, `$sync-base` и команды восстановления.

У каждой базы независимый результат. Если одна база заблокирована, остальные
могут продолжить установку.

## Правила версий клиентов

- Точная принятая версия не изменяется.
- Отсутствующая или более старая версия может быть установлена.
- Более новая либо отличающаяся версия не понижается автоматически:
  блокируется только соответствующая база.
- Клиенты, установленные этим EXE, не удаляются при rollback базы.
- Пользовательская авторизация также не удаляется при rollback.

Codex Desktop открывается по точному Microsoft Store Product ID
`9PLM9XGG6VKS`. После установки проверяются:

```text
Name          OpenAI.Codex
Publisher     CN=50BDFD77-8903-4850-9FFE-6E8522F64D5B
Architecture  X64
SignatureKind Store
```

Поиск `winget search Codex` не используется: он может выбрать одноимённое
стороннее приложение.

Codex CLI `0.146.0-alpha.3.1` устанавливается release-specific скриптом
`install.ps1` из официального тега OpenAI
`rust-v0.146.0-alpha.3.1`. Его SHA-256:
`397cad1d3091728fc59531018c4b2cd99b49b51b36c6ad42f7ec304d8da8ba4f`.
Корневой `chatgpt.com/codex/install.ps1` для этой версии не используется:
его текущая проверка формата версии отклоняет суффикс `alpha.3.1`. Скрипт
релиза сначала скачивается в staging, проходит SHA-256 и AST-проверку, и
только затем запускается с `-Release 0.146.0-alpha.3.1`.

## Режимы соединения

### Direct

Прямой доступ. Для дочернего процесса очищаются унаследованные
`HTTP_PROXY`, `HTTPS_PROXY` и `ALL_PROXY`; задаётся `NO_PROXY=*`.

### VPN

Самостоятельный режим на системной VPN-маршрутизации. Отсутствие proxy
полностью допустимо и не является предупреждением или блокером. Устаревшие
proxy-переменные дочернему процессу не передаются.

### Proxy

Поддерживаются:

- HTTP;
- HTTPS;
- SOCKS5 с удалённым DNS (`socks5h`);
- без авторизации;
- username/password.

Профиль без секрета хранится в
`%USERPROFILE%\.llm-foundation\connection.json`. Пароль хранится отдельно и
защищён Windows DPAPI текущего пользователя. Секрет передаётся процессу
загрузки только во временном окружении и не попадает в argv, manifest,
evidence или отчёт. `curl -v` с реальными учётными данными запрещён.

Загрузка идёт во временный `.part`. Только после проверки размера, SHA-256,
подписи и издателя файл атомарно попадает в staging. Прерванный или подменённый
файл не запускается.

## Граница допустимого использования провайдеров

Режим соединения — только транспорт. Он не подтверждает право использования
конкретного сервиса в текущем регионе и не меняет правила провайдера.

Для Claude оператор обязан проверить актуальный
[список поддерживаемых стран и регионов](https://www.anthropic.com/supported-countries)
и применимые [Consumer Terms](https://www.anthropic.com/legal/consumer-terms).
VPN или Proxy не должен использоваться для обхода регионального ограничения,
блокировки аккаунта, продуктового контроля или защитного механизма.

Каждому сотруднику требуется отдельная допустимая учётная запись. Общие логины,
пароли, API-ключи и аккаунты не выдаются. Автоматизированный или без участия
человека доступ допустим только в явно разрешённом провайдером контуре.

Установщик не определяет геолокацию, не отправляет сведения об аккаунте и не
может установить причину safeguard-блокировки. Для заблокированного аккаунта
используется официальный appeal/review, а не технический обход.

### Provider eligibility evidence

Перед employee-сборкой оператор создаёт обезличенное подтверждение:

```powershell
pwsh -NoProfile -File .\tools\new-provider-eligibility-evidence.ps1 `
  -OutputPath .\provider-eligibility-evidence.json `
  -ConfirmEmployeeLocationEligibility `
  -ConfirmOrganizationEligibility `
  -ConfirmIndividualAccounts `
  -ConfirmNoRegionOrBanBypass `
  -ConfirmNoUnattendedConsumerAutomation
```

`ProviderEligibilityEvidence` действует не более 7 суток. Файл содержит только
контрольные отметки, UTC-время и канонические ссылки — без ФИО, страны, IP,
email или account ID. Он встраивается в EXE и связывается SHA-256 с bundle.
Просроченный, изменённый или неполный evidence блокирует только Claude.

## Что сохраняется

Foundation изменяет только объявленную target-пакетом поверхность. Не
затрагиваются:

- авторизация, OAuth и API-ключи;
- сессии, история и архивы;
- memories, SQLite и state;
- проекты и рабочие папки;
- browser/computer-use state;
- внешние imports;
- пользовательские данные вне управляемой поверхности.

Неизвестные старые agents/skills резервируются и выводятся из активного
discovery, но не удаляются безвозвратно. Rollback возвращает предыдущую
управляемую поверхность побайтно.

## Одностороннее обновление

```text
hub → immutable release → verify → plan → install → doctor → consumer
```

Сотрудник явно запускает `$sync-base`; `/sync-base` остаётся текстовым
алиасом. Consumer не отправляет на hub feedback, session-report, телеметрию,
рабочие документы, локальные изменения или учётные данные. TTL-проверка
обновления выполняется не чаще одного раза в сутки и молчит, если новой
стабильной версии нет.

## Диагностика

Foundation поддерживает:

```text
plan
install
doctor
inventory
rollback
```

Локальные отчёты находятся в:

```text
%USERPROFILE%\.llm-foundation\reports\
```

В отчёте отдельно указаны состояние каждой базы, версия клиента, результат
`doctor`, сохранённые данные и незавершённые шаги. В отчёте нет секретов.

## Сборка для сотрудников

Режим задаётся явно:

```text
-DistributionMode Preview | InternalUnsigned | PublicSigned
```

### Preview

Диагностика и synthetic-проверки. Распространение сотрудникам запрещено.

### InternalUnsigned

Допустимый внутренний employee-релиз после трёх target PASS, provider evidence,
client source lock, отдельного immutable Foundation 0.2.1, hub-canary и чистого
пилота. Сертификат не требуется.
Windows может показать `Unknown Publisher` или SmartScreen — это ожидаемое
предупреждение, а не утверждение о доверенной подписи.

На release-машине требуются Python 3.12, PowerShell 7, Windows PowerShell 5.1
и Microsoft Visual Studio Build Tools с Roslyn/MSBuild. Сборка намеренно
отказывается от legacy Framework `csc.exe`: без Roslyn нельзя доказать
побайтовую повторяемость EXE.

```powershell
pwsh -NoProfile -File .\tools\build-gui.ps1 `
  -OutputRoot .\dist\employee `
  -PackageRoot <accepted-packages-root> `
  -FoundationPackageRoot <accepted-foundation-0.2.1-root> `
  -ProviderEligibilityEvidence .\provider-eligibility-evidence.json `
  -DistributionMode InternalUnsigned
```

В `accepted-foundation-0.2.1-root` обязательны:

- `foundation-engine-0.2.1.zip`;
- `release-manifest.json`;
- `acceptance-evidence.json` с `FOUNDATION_SYNTHETIC: PASS`;
- `release-verification.json` после `gh release verify` и
  `gh release verify-asset`;
- `package-acceptance.json`.

Employee-сборка извлекает engine именно из этого ZIP и сверяет каждый байт.
Локальная пересборка `src/foundation.ps1` разрешена только для `Preview`.
Каждый из трёх target manifests обязан содержать SHA-256 того же
`engine-manifest.json`; несовпадение блокирует сборку.

В manifest должны быть:

```json
{
  "distribution_mode": "internal_unsigned",
  "signature": "unsigned-internal",
  "employee_release": true,
  "employee_distribution_allowed": true,
  "public_distribution_allowed": false,
  "windows_warning_expected": true
}
```

### PublicSigned

Дополнительно требует валидную timestamped Authenticode-подпись. Этот режим
реализован, но текущим решением владельца имеет статус
`PUBLIC_SIGNED_RELEASE: DEFERRED_BY_OWNER`.

## Пилот и выпуск

До массовой установки один чистый Windows x64 ПК сотрудника без admin должен
пройти:

- реальный Direct/VPN/Proxy-сценарий;
- Codex Desktop и точный CLI;
- Claude Code;
- OpenCode Desktop/CLI и OpenAI `/connect` OAuth;
- simple-chat без tool/reviewer;
- discovery 16 агентов и 37 навыков;
- `$sync-base`;
- rollback и сохранение пользовательских данных.

Пилот выполняется теми же байтами, которые загружены в Draft Release. При
ошибке draft не публикуется; создаётся новый release candidate. Stable release
публикуется без пересборки и после публикации проверяется `gh release verify`
и `gh release verify-asset`.

### Контролируемая последовательность

1. Запустить commit-bound `run-acceptance.py` на чистом worktree.
2. Подготовить и опубликовать `foundation-engine-v0.2.1`; создать локальный
   post-publication `package-acceptance.json`.
3. Выполнить минимальные provider-гейты трёх target-баз и опубликовать их
   immutable releases.
4. Собрать `InternalUnsigned` только из четырёх принятых package roots.
5. Выполнить zero-model hub canary:

   ```powershell
   py -3.12 .\tools\hub_canary.py `
     --execute-approved-hub-canary `
     --bundle .\dist\employee `
     --home <isolated-canary-home> `
     --output .\dist\hub-canary.json
   ```

6. Создать draft assets без пересборки EXE:

   ```powershell
   py -3.12 .\tools\installer_release.py `
     --bundle .\dist\employee `
     --hub-canary .\dist\hub-canary.json `
     --output .\dist\installer-draft-0.3.0
   ```

7. На чистом ПК пройти чек-лист и создать обезличенное evidence через
   `pilot_evidence.py`. Скрипт требует отдельное подтверждение каждого пункта,
   чистой Windows x64 и отсутствия admin; он не записывает имя ПК, ФИО, IP,
   email или идентификатор аккаунта.
8. Выполнить `pilot_release.py`. Меняются только post-pilot metadata/evidence;
   EXE остаётся побайтно тем же, что прошёл hub canary и пилот.
9. Опубликовать stable assets как immutable `installer-v0.3.0`.
10. Запустить:

   ```powershell
   py -3.12 .\tools\installer_release_verifier.py `
     --stable-root .\dist\installer-stable-0.3.0 `
     --output .\dist\installer-v0.3.0-release-verification.json
   ```

   Verifier требует `draft=false`, `prerelease=false`, `immutable=true`,
   точный remote asset inventory и отдельный successful attestation для
   каждого опубликованного файла.

Неподписанный internal EXE нельзя выдавать как публично доверенный подписанный
продукт. `InternalUnsigned` предназначен только для контролируемого внутреннего
распространения.
