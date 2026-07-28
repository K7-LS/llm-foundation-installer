# K-7 AI Foundation — инструкция сотрудника

## Что входит

Employee edition содержит только:

- Codex Desktop и Codex CLI;
- OpenCode CLI.

В комплекте два приложения:

- `K7-AI-Foundation-Employee-InternalUnsigned.exe` — установка и
  восстановление баз;
- `K7-AI-Launch-Center-Employee-InternalUnsigned.exe` — ежедневный запуск
  клиента через выбранный маршрут.

Оба EXE работают от текущего пользователя, не требуют прав администратора и
не читают логины, OAuth-токены, API-ключи, cookies или историю клиентов.

## Живая инструкция

В правом верхнем углу Installer и Launch Center есть кнопка
**«Инструкция»**. Она открывает встроенный интерактивный дашборд:

1. **Старт** — короткий сценарий для текущего приложения.
2. **Маршруты** — различия Direct, VPN, SingBox HTTP и SingBox HTTPS.
3. **Безопасность** — границы доступа и проверки пакетов.
4. **Восстановление** — `doctor`, отчёты и rollback.

Дашборд берёт редакцию, роль продукта и состояние bundle из самого EXE. Для
его открытия не выполняются model calls, загрузки или входы в аккаунт.

## Установка

1. Поместите рядом Installer, Launch Center и
   `sing-box-1.13.14-windows-amd64.zip`.
2. Сверьте SHA-256 всех файлов с `bundle-manifest.json`.
3. Запустите Installer обычным пользователем.
4. Оставьте выбранными Codex и OpenCode.
5. Выберите маршрут и нажмите **«Установить выбранное»**.
6. Войдите в каждый сервис только в открывшемся официальном клиенте.
7. После успешного `doctor` используйте Launch Center для ежедневной работы.

`InternalUnsigned` может вызвать предупреждение Windows
**Unknown Publisher** или SmartScreen. Это ожидаемо для внутреннего
неподписанного EXE. Предупреждение не заменяет проверку SHA-256 и не означает,
что файл стал публично доверенным.

## Маршруты Launch Center

- **Direct** — прямое соединение; унаследованные proxy-переменные очищаются
  только у запускаемого процесса.
- **VPN** — используется уже включённая системная VPN-маршрутизация.
- **SingBox HTTP** — локальный HTTP relay только для запускаемого процесса.
- **SingBox HTTPS** — локальный CONNECT/HTTPS relay только для запускаемого
  процесса.

При первом SingBox-запуске Launch Center извлекает runtime из лежащего рядом
архива, проверяет pinned SHA-256 и устанавливает его в:

```text
%USERPROFILE%\.llm-foundation\runtimes\sing-box\1.13.14\
```

Повреждённый или неожиданно изменённый runtime не перезаписывается
автоматически: маршрут блокируется с диагностическим кодом.

Режим соединения — только транспорт. Этот транспорт не подтверждает право
использования сервиса, не меняет правила провайдера и не должен применяться
для обхода региональных ограничений, блокировки аккаунта или safeguard
policy. Каждый сотрудник использует отдельную допустимую учётную запись.

## Ежедневный запуск

1. Откройте AI Launch Center.
2. Выберите Codex или OpenCode.
3. Выберите Direct, VPN, SingBox HTTP или SingBox HTTPS.
4. Нажмите **«Запустить»**.

Launch Center разрешает только точную цель, принятую edition contract:

- Store-приложение проверяется по AppX identity и manifest;
- CLI проверяется по локальной managed-command записи и SHA-256;
- подмена пути, файла или runtime блокирует запуск;
- AppX через process-local SingBox не запускается, потому что безопасно
  передать ему локальное proxy-окружение невозможно; используйте Direct или
  системный VPN.

## Диагностика и rollback

Локальные отчёты:

```text
%USERPROFILE%\.llm-foundation\reports\
```

Команды Foundation:

```text
plan
install
doctor
inventory
rollback
```

Rollback возвращает только управляемую поверхность базы. Он не удаляет
проекты, историю, авторизацию, cookies, memories и данные клиента.

## Сборка Employee edition

Непредварительная сборка требует accepted package roots и точный runtime
archive:

```powershell
pwsh -NoProfile -File .\tools\build-edition.ps1 `
  -OutputRoot .\dist\employee-internal `
  -Edition Employee `
  -DistributionMode InternalUnsigned `
  -PackageRoot <accepted-codex-opencode-packages> `
  -FoundationPackageRoot <accepted-foundation-package> `
  -ClientSourcesLock .\client-sources.lock.json `
  -RuntimeSourcesLock .\runtime-sources.lock.json `
  -RuntimeArchive .\.work\runtime-cache\sing-box-1.13.14-windows-amd64.zip
```

Результат содержит два EXE, runtime archive и общий
`bundle-manifest.json`. Сборщик проверяет SHA-256 runtime до копирования и
после него.

## Канонический релизный конвейер

```powershell
py -3.12 .\tools\hub_canary.py `
  --execute-approved-hub-canary `
  --bundle .\dist\employee-internal `
  --output .\dist\employee-hub-canary.json

py -3.12 .\tools\installer_release.py `
  --bundle .\dist\employee-internal `
  --hub-canary .\dist\employee-hub-canary.json `
  --output .\dist\employee-draft-0.3.0
```

Canary работает только во временных изолированных home и не делает model
calls. Clean-PC pilot обязан проверить теми же байтами Installer, Launch
Center, Codex/OpenCode, интерактивную Инструкцию и все четыре маршрута.
Перечень подтверждений выводит:

```powershell
py -3.12 .\tools\pilot_evidence.py --help
```

После pilot PASS формируется stable-набор без пересборки и публикуется
immutable release `employee-v0.3.0`. Проверка после публикации:

```powershell
py -3.12 .\tools\installer_release_verifier.py `
  --stable-root .\dist\employee-stable-0.3.0 `
  --output .\dist\employee-v0.3.0-release-verification.json
```

## Перед выдачей сотруднику

- Codex и OpenCode package acceptance — PASS.
- Foundation package acceptance — PASS.
- Runtime archive совпадает с `runtime-sources.lock.json`.
- Полный Windows test suite — PASS.
- Четыре финальных preview проверены визуально.
- Чистый Windows x64 pilot пройден теми же байтами.
- Remote release immutable, а каждый asset повторно проверен после публикации.

`Preview` не является employee-релизом. `InternalUnsigned` разрешён только для
контролируемого внутреннего распространения после всех перечисленных гейтов.
