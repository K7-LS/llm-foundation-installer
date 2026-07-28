# Передача: два AI-запускника через sing-box

## Результат

В корне проекта находятся два самостоятельных файла:

- `Start-AI-SingBox-HTTPS.ps1` — HTTP CONNECT-прокси с TLS до upstream;
- `Start-AI-SingBox-HTTP.ps1` — обычный HTTP CONNECT-прокси без TLS.

Оба файла используют sing-box как локальный маршрутизатор. Proxifier, TUN,
служба Windows и административные права не требуются.

## Поддерживаемые режимы

При запуске отображается меню:

1. ChatGPT Desktop;
2. Claude Desktop;
3. OpenCode;
4. Codex CLI;
5. Claude CLI;
6. VS Code — Codex;
7. VS Code — Claude.

Меню можно пропустить:

```powershell
.\Start-AI-SingBox-HTTPS.ps1 -Mode ChatGPT
.\Start-AI-SingBox-HTTP.ps1 -Mode CodexCLI
```

Допустимые значения `-Mode`:

```text
ChatGPT
ClaudeDesktop
OpenCode
CodexCLI
ClaudeCLI
VSCodeCodex
VSCodeClaude
```

## Первый запуск

1. Запустить нужный файл в Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Start-AI-SingBox-HTTPS.ps1
```

2. Если `sing-box.exe` не найден, запускник покажет официальный архив:

```text
https://github.com/SagerNet/sing-box/releases/download/v1.13.14/sing-box-1.13.14-windows-amd64.zip
```

3. Распаковать архив и указать полный путь к `sing-box.exe`.
4. Ввести адрес, порт, пользователя и пароль прокси.
5. Выбрать режим запуска.

Адрес, порт, пользователь и путь к sing-box сохраняются в профиле пользователя.
Пароль сохраняется отдельно через Windows DPAPI и не записывается открытым
текстом в постоянный конфиг.

## Раздельные настройки

HTTPS-запускник:

```text
%USERPROFILE%\.ai-singbox-https.json
%USERPROFILE%\.ai-singbox-https.cred
```

HTTP-запускник:

```text
%USERPROFILE%\.ai-singbox-http.json
%USERPROFILE%\.ai-singbox-http.cred
```

Сбросить всё:

```powershell
.\Start-AI-SingBox-HTTPS.ps1 -Reset
.\Start-AI-SingBox-HTTP.ps1 -Reset
```

Сбросить только пароль:

```powershell
.\Start-AI-SingBox-HTTPS.ps1 -ResetPassword
.\Start-AI-SingBox-HTTP.ps1 -ResetPassword
```

## Что делает запускник

1. Находит выбранное приложение и sing-box.
2. Создаёт уникальный временный конфиг.
3. Проверяет его командой `sing-box check`.
4. Сохраняет текущие системные настройки прокси Windows.
5. Поднимает локальный mixed-прокси на свободном порту `18082–18120`.
6. Передаёт локальный адрес через системный прокси и
   `HTTP_PROXY`/`HTTPS_PROXY`.
7. Направляет выбранный процесс и AI-домены через upstream.
8. Оставляет остальной трафик на маршруте `direct`.
9. Ждёт закрытия приложения.
10. Останавливает sing-box, удаляет временный конфиг и восстанавливает Windows.

Для ChatGPT Desktop, Claude Desktop и OpenCode существующий экземпляр
завершается принудительно, чтобы новый процесс унаследовал окружение.

Claude Desktop определяется и отслеживается по полному пути к исполняемому
файлу. Запущенный `claude.exe` от Claude CLI не считается desktop-приложением
и не закрывается.

VS Code принудительно не закрывается. Если `Code.exe` уже запущен, запускник
попросит закрыть его вручную.

После восстановления запускник повторно сверяет значения системного прокси.
Ошибка остановки sing-box, восстановления Windows или удаления временного
конфига считается фатальной: зелёное сообщение об успешной очистке в таком
случае не выводится.

## Проверка для другого Codex

### 1. Синтаксис и контракт

Из корня проекта:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File .\tests\Test-AI-SingBox-Launchers.ps1
```

PASS:

```text
Failed: 0
All launcher tests passed.
```

### 2. Проверка конфигов настоящим sing-box

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File .\tests\Test-AI-SingBox-Launchers.ps1 `
    -SingBoxPath "C:\полный\путь\к\sing-box.exe"
```

PASS:

- оба PowerShell-файла парсятся;
- оба внутренних self-test завершаются с кодом 0;
- `sing-box check` принимает HTTPS- и HTTP-конфиг;
- тестовые временные папки удалены.

### 3. Live-проверка HTTPS

Запустить:

```powershell
.\Start-AI-SingBox-HTTPS.ps1 -Mode ChatGPT
```

Проверить:

- загрузилась история чатов;
- открываются проекты;
- отображаются лимиты аккаунта;
- работает обычный чат;
- работает Work/Codex;
- Chrome и Яндекс.Браузер не закрываются;
- обычные сайты используют прямой IP;
- после полного закрытия ChatGPT процесс sing-box, запущенный запускником,
  завершён;
- исходные `ProxyEnable`, `ProxyServer`, `ProxyOverride` и `AutoConfigURL`
  восстановлены.

### 4. Live-проверка HTTP

Повторить предыдущий список с:

```powershell
.\Start-AI-SingBox-HTTP.ps1 -Mode ChatGPT
```

Использовать реквизиты обычного HTTP CONNECT-прокси без TLS.

### 5. Остальные режимы

По очереди проверить:

```powershell
.\Start-AI-SingBox-HTTPS.ps1 -Mode ClaudeDesktop
.\Start-AI-SingBox-HTTPS.ps1 -Mode OpenCode
.\Start-AI-SingBox-HTTPS.ps1 -Mode CodexCLI
.\Start-AI-SingBox-HTTPS.ps1 -Mode ClaudeCLI
.\Start-AI-SingBox-HTTPS.ps1 -Mode VSCodeCodex
.\Start-AI-SingBox-HTTPS.ps1 -Mode VSCodeClaude
```

Для VS Code сначала закрыть все окна и убедиться, что `Code.exe` отсутствует.

## PASS/FAIL

PASS:

- выбранное приложение использует upstream-прокси;
- история, проекты, лимиты и Work/Codex доступны;
- обычные программы не получают внешний IP upstream;
- временный системный прокси полностью восстанавливается;
- пароль отсутствует в логах и постоянном JSON.

FAIL:

- приложение было запущено до запускника и продолжило использовать старое
  окружение;
- после закрытия остался изменённый системный прокси;
- `sing-box check` вернул ненулевой код;
- обычный сайт вышел через upstream вместо `direct`;
- в исходниках, инструкции или выводе появился пароль.

## Известные ловушки

- Не запускать Proxifier параллельно: на целевом корпоративном ПК его Portable
  Engine конфликтовал с Chromium.
- Не вставлять в PowerShell текст приглашения `PS C:\...>` или вывод предыдущей
  команды.
- Не использовать HTTPS-запускник для обычного HTTP-прокси и наоборот.
- Не копировать пароль, `Proxy-Authorization` или готовый proxy URL в чат.
- Не отключать проверку сертификата в HTTPS-варианте.
- Не обходить блокировки Windows Defender; использовать только разрешённый
  официальный `sing-box.exe`.
- CLI должен завершиться, а desktop-приложение должно быть полностью закрыто,
  прежде чем запускник выполнит очистку.

## Границы автоматической проверки

Автоматические тесты подтверждают структуру скриптов и корректность JSON для
sing-box. Полную работу аккаунта, Work/Codex, desktop-оболочки и конкретного
upstream можно подтвердить только интерактивным live-тестом с действующими
реквизитами. Другой Codex не должен читать или печатать сохранённые DPAPI-файлы.
