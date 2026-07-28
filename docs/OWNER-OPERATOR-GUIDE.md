# K-7 AI Foundation Owner — operating guide

## Контур владельца

Owner edition содержит:

- Codex Desktop и Codex CLI;
- Claude Code;
- OpenCode CLI.

Контракт редакции жёсткий:

```text
owner_controlled=true
distribution_allowed=false
перераспространение запрещено
```

В комплекте два приложения:

- `K7-AI-Foundation-Owner-InternalUnsigned.exe`;
- `K7-AI-Launch-Center-Owner-InternalUnsigned.exe`.

Owner build предназначен только владельцу. Его нельзя передавать сотрудникам,
публиковать как employee build или использовать как доказательство допуска
провайдера.

## OPERATING GUIDE

Кнопка **OPERATING GUIDE** в Installer и Launch Center открывает встроенную
hi-tech консоль с четырьмя живыми разделами:

1. `01 / СТАРТ`
2. `02 / МАРШРУТЫ`
3. `03 / БЕЗОПАСНОСТЬ`
4. `04 / ВОССТАНОВЛЕНИЕ`

Консоль показывает точную edition/product роль, набор целей и наличие
bundle-manifest. Она не делает model calls, не выполняет авторизацию и не
отправляет телеметрию.

## Состояния Claude

Наличие проверенного локального пакета и готовность провайдера — независимые
состояния:

- `OWNER_CANDIDATE` — пакет можно установить владельцу, но provider marker не
  подтверждён;
- `PROVIDER_READY` — отдельный актуальный provider evidence прошёл проверку;
- `FULL_RELEASE_CLAUDE: NOT_PASS` — честный релизный статус, пока guarded
  provider call не дал завершённого допустимого evidence.

Owner Installer может нести и устанавливать `OWNER_CANDIDATE`. Это не
переводит `FULL_RELEASE_CLAUDE` в PASS и не разрешает распространение.

Каждый вход выполняется владельцем интерактивно в официальном клиенте.
Region bypass, ban bypass и safeguard bypass не используются. VPN или proxy
остаются транспортом, а не способом изменить eligibility аккаунта.

Перед новым evidence сверяются актуальные первоисточники:

- [Supported countries & regions](https://www.anthropic.com/supported-countries);
- [Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms).

Обезличенный provider evidence создаётся локально:

```powershell
pwsh -NoProfile -File .\tools\new-provider-eligibility-evidence.ps1 `
  -OutputPath .\provider-eligibility-evidence.json `
  -ConfirmEmployeeLocationEligibility `
  -ConfirmOrganizationEligibility `
  -ConfirmIndividualAccounts `
  -ConfirmNoRegionOrBanBypass `
  -ConfirmNoUnattendedConsumerAutomation
```

`ProviderEligibilityEvidence` действует не более 7 суток. Он не содержит ФИО,
IP, email, страну или account ID. Автоматизированный или без участия человека
consumer-доступ не подразумевается.

## Установка

1. Поместите рядом Owner Installer, Owner Launch Center и
   `sing-box-1.13.14-windows-amd64.zip`.
2. Сверьте SHA-256 с `bundle-manifest.json`.
3. Запустите Installer обычным пользователем.
4. Проверьте отдельные состояния Codex, Claude и OpenCode.
5. Выберите маршрут и установите нужные цели.
6. Выполните вход только внутри официального клиента.
7. Проверьте `doctor` и сохраните локальный отчёт.

`InternalUnsigned` может показать Unknown Publisher или SmartScreen. Owner
edition остаётся внутренним неподписанным кандидатом и не выдаётся за
публично подписанный продукт.

## Маршрутная матрица

- **Direct** — прямой запуск без унаследованного proxy.
- **VPN** — системная VPN-маршрутизация.
- **SingBox HTTP** — process-local HTTP relay.
- **SingBox HTTPS** — process-local HTTPS/CONNECT relay.

При первом SingBox-запуске runtime устанавливается из локального
hash-bound архива в:

```text
%USERPROFILE%\.llm-foundation\runtimes\sing-box\1.13.14\
```

Повреждённый существующий runtime блокирует запуск и не заменяется молча.
AppX-клиент нельзя безопасно активировать с process-local proxy environment;
для него разрешены Direct и системный VPN.

## Точные цели Launch Center

- Store-клиент проверяется по package family, publisher, architecture,
  signature kind, application ID, entry point и executable.
- Managed CLI проверяется по `current.json`, ожидаемому относительному пути и
  SHA-256 executable.
- Перед каждым запуском целевая целостность проверяется повторно.
- Launch Center управляет только запущенным им процессом и своим локальным
  SingBox-сеансом.

## Восстановление

Отчёты:

```text
%USERPROFILE%\.llm-foundation\reports\
```

Рабочие команды:

```text
plan
install
doctor
inventory
rollback
```

Rollback не удаляет проекты, историю, авторизацию, memories, cookies или
пользовательские данные вне управляемой поверхности.

## Сборка Owner edition

```powershell
pwsh -NoProfile -File .\tools\build-edition.ps1 `
  -OutputRoot .\dist\owner-internal `
  -Edition Owner `
  -DistributionMode InternalUnsigned `
  -PackageRoot <accepted-owner-packages> `
  -FoundationPackageRoot <accepted-foundation-package> `
  -OwnerCandidateRoot <claude-owner-candidate> `
  -ClientSourcesLock .\client-sources.lock.json `
  -RuntimeSourcesLock .\runtime-sources.lock.json `
  -RuntimeArchive .\.work\runtime-cache\sing-box-1.13.14-windows-amd64.zip
```

Provider evidence передаётся отдельным параметром только когда оно реально
есть и актуально:

```text
-ProviderEligibilityEvidence <provider-eligibility-evidence.json>
```

Без него Claude остаётся `OWNER_CANDIDATE`; никакой PASS не синтезируется.

## Перед использованием

- Codex/OpenCode package acceptance — PASS.
- Claude package acceptance отделён от provider readiness.
- Foundation package acceptance — PASS.
- Runtime archive совпадает с pinned lock.
- Полный test suite и визуальный QA четырёх интерфейсов — PASS.
- `distribution_allowed=false` сохранён в Owner manifest.

Owner candidate можно использовать локально под контролем владельца.
Employee distribution и публичная публикация Owner edition запрещены.
