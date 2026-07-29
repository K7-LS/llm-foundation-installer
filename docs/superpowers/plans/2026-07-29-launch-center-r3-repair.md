# Launch Center r3 Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** сделать Employee/Owner Launch Center самостоятельным рабочим запускником с настройкой и сквозной проверкой SingBox, полным набором целей Codex/OpenCode/VS Code и безопасным маршрутом Store Codex через временный системный proxy.

**Architecture:** четыре WPF-представления используют единый именованный контракт соединения, но Launch Center сохраняет отдельные HTTP/HTTPS route IDs. Обычные Win32-клиенты получают локальный proxy через окружение процесса; AppX Codex использует отдельную owned system-proxy lease с mutex, атомарным state, watchdog и compare-and-swap восстановлением. Runtime-проверка запускает реальную тестовую сессию sing-box и выполняет HTTP-запрос через её локальный порт.

**Tech Stack:** C# WPF/.NET Framework, Windows Registry и Win32 API, PowerShell build scripts, Python 3.12 + pytest, GitHub protected branches/releases.

## Global Constraints

- Не выполнять runtime AI/model calls.
- Все новые пользовательские тексты и документы — на русском; программные ID, `Tag`, JSON reason и manifest IDs остаются invariant.
- Employee Launch Center показывает ровно `codex-desktop`, `codex-cli`, `vscode-codex`, `opencode-desktop`, `opencode-cli`.
- Owner Launch Center UI точно равен своему `LaunchTargetCatalog.ForEdition`, включая owner-only цели и `vscode-codex`.
- VS Code принимается только из согласованных путей, с валидной Authenticode-подписью Microsoft Corporation; версия не pinned. Расширение проверяется по `publisher=OpenAI`, `name=chatgpt`; официальный URL — `https://marketplace.visualstudio.com/items?itemName=OpenAI.chatgpt`.
- Любая system-proxy операция ограничена текущим пользователем, единственной lease, owned-state, watchdog и compare-and-swap. Неподтверждённое восстановление блокирует новую AppX-сессию.
- CI-тесты system proxy работают только с изолированным `HKCU\Software\K7AITests\...`; реальный `Internet Settings` тестами не изменяется.
- Пароль не появляется в UI-статусах, stdout/stderr, JSON/evidence, логах и release-отчётах.
- Существующий `workplace-pilot-v0.3.0-r2` неизменяем. Новый результат — только immutable prerelease r3 после protected-main CI.
- Стабильный `employee-v0.3.0` запрещён до успешного физического clean-PC pilot.

## File Map

- `src/gui/InstallerApp.cs` — создание четырёх WPF-представлений, общий connection workflow, Launch Center actions и test-only CLI.
- `src/gui/LaunchCenterEmployeeView.xaml` — Employee-карточки, маршруты, proxy-поля, Save/Test/Stop.
- `src/gui/LaunchCenterOwnerView.xaml` — Owner-карточки и тот же connection contract.
- `src/gui/LaunchTarget.cs` — synthetic integration-target `vscode-codex` и resolution routing.
- `src/gui/VsCodeIntegration.cs` — поиск/проверка Code.exe и manifest официального расширения.
- `src/gui/SingBoxSession.cs` — конкретные runtime reasons и сквозной route probe.
- `src/gui/ClientLauncher.cs` — process-only и AppX lifecycle, активная сессия и Stop route.
- `src/gui/SystemProxyLease.cs` — registry snapshot/apply/recover, mutex, watchdog contract.
- `tools/build-gui.ps1` — включение новых C# source files в детерминированную компиляцию.
- `tests/test_gui.py` — четыре WPF-контракта и connection workflow.
- `tests/test_launch_center.py` — каталог, VS Code, runtime и launch integration.
- `tests/test_system_proxy_lease.py` — реальный lifecycle на изолированном тестовом registry key.

---

### Task 1: Единый Connection UI в четырёх представлениях

**Files:**
- Modify: `tests/test_gui.py`
- Modify: `src/gui/InstallerApp.cs`
- Modify: `src/gui/LaunchCenterEmployeeView.xaml`
- Modify: `src/gui/LaunchCenterOwnerView.xaml`

**Interfaces:**
- Consumes: `ConnectionStore.Save`, `ConnectionProbe.Run`, существующие Installer controls.
- Produces: `ConnectionUi.Bind(UserControl view, string bundleRoot, bool loadState)` и общий `ConnectionUiContract` для Installer/LaunchCenter.

- [ ] **Step 1: Добавить RED-тесты реального WPF-контракта.**

  Параметризовать `InstallerEmployeeView.xaml`, `InstallerOwnerView.xaml`,
  `LaunchCenterEmployeeView.xaml`, `LaunchCenterOwnerView.xaml`. Для каждого
  собранного EXE вызвать новый read-only test command:

  ```powershell
  .\LLMFoundationInstaller.exe --ui-connection-state-json SingBoxHttps
  ```

  Ожидаемый literal-result:

  ```python
  assert value["mode"] == "Proxy"
  assert value["proxy_type"] == "HTTPS"
  assert value["proxy_settings"] == "Visible"
  assert value["fields"] == ["server", "port", "login", "password"]
  assert value["save_enabled"] is True
  assert value["test_enabled"] is True
  assert value["stop_enabled"] is False
  ```

  Отдельные вызовы `Direct`, `VPN`, `SingBoxHttp` закрепляют скрытие и invariant
  mapping `HTTP`/`HTTPS`. Тест обязан падать на текущих Launch Center, где
  controls отсутствуют.

- [ ] **Step 2: Запустить RED.**

  ```powershell
  py -3.12 -m pytest tests/test_gui.py -k "four_view_connection_contract or launch_center_connection_state" -vv
  ```

  Expected: FAIL из-за отсутствующей команды/контролов в Launch Center.

- [ ] **Step 3: Добавить proxy controls в оба Launch Center XAML.**

  Сохранить route IDs `RouteDirect`, `RouteVpn`, `RouteHttp`, `RouteHttps`.
  Добавить общие имена:

  ```xml
  <Grid x:Name="ProxySettings" Visibility="Collapsed">
    <TextBox x:Name="ProxyHost" />
    <TextBox x:Name="ProxyPort" />
    <TextBox x:Name="ProxyUsername" />
    <PasswordBox x:Name="ProxyPassword" />
    <Button x:Name="SaveConnection" Content="Сохранить" />
    <Button x:Name="TestConnection" Content="Сохранить и проверить" />
    <Button x:Name="StopRoute" Content="Остановить маршрут" IsEnabled="False" />
    <TextBlock x:Name="ConnectionStatus" />
  </Grid>
  ```

  Поля получают видимые подписи «Сервер», «Порт», «Логин», «Пароль». Под ними:

  ```text
  1. Заполните данные. 2. Нажмите «Сохранить и проверить».
  3. Запустите клиент. Launch Center сам запускает и останавливает sing-box;
  отдельный скрипт не нужен.
  ```

- [ ] **Step 4: Реализовать общий adapter и test command.**

  `ConnectionUiContract.Resolve(view)` находит Installer radio
  (`DirectMode`/`VpnMode`/`ProxyMode` + `ProxyType`) либо Launch Center radio.
  `SelectedProxyType()` возвращает только literal `HTTP`/`HTTPS`, не UI-текст.
  `ConnectionUi.Bind` принимает `bundleRoot`; `InstallerView.Create` вызывает
  его для обеих product role до `LaunchCenterActions.Bind`.

  `--ui-connection-state-json <route>` доступен только при неинтерактивном
  запуске, переключает реальные radio controls, вызывает binding и описывает
  состояние без чтения пароля.

- [ ] **Step 5: Довести focused tests до GREEN и проверить Installer regression.**

  ```powershell
  py -3.12 -m pytest tests/test_gui.py -k "four_view_connection_contract or launch_center_connection_state or singbox_connection_ui or connection_profile" -vv
  ```

  Expected: PASS; существующий Installer Employee/Owner остаётся рабочим.

- [ ] **Step 6: Commit.**

  ```powershell
  git add tests/test_gui.py src/gui/InstallerApp.cs src/gui/LaunchCenterEmployeeView.xaml src/gui/LaunchCenterOwnerView.xaml
  git commit -m "fix: добавить настройку SingBox в Launch Center"
  ```

### Task 2: Полный каталог запусков и доверенный VS Code

**Files:**
- Modify: `tests/test_launch_center.py`
- Create: `src/gui/VsCodeIntegration.cs`
- Modify: `src/gui/LaunchTarget.cs`
- Modify: `src/gui/InstallerApp.cs`
- Modify: `src/gui/LaunchCenterEmployeeView.xaml`
- Modify: `src/gui/LaunchCenterOwnerView.xaml`
- Modify: `tools/build-gui.ps1`

**Interfaces:**
- Produces: `VsCodeIntegration.Resolve(string home) -> LaunchTargetResolution`.
- Produces: synthetic `LaunchTarget { target_id="vscode-codex", client_id="codex-desktop", role="desktop", display_name="VS Code — Codex" }`.
- Test seam: `--resolve-vscode-record-json <home> <record>` принимает только validated test record при embedded `client-sources.lock.json.test_only == true`.

- [ ] **Step 1: Добавить RED-контракт каталога и UI selection.**

  Employee `--product-json` должен вернуть literal:

  ```python
  assert value["targets"] == [
      "codex-cli",
      "codex-desktop",
      "opencode-cli",
      "opencode-desktop",
      "vscode-codex",
  ]
  ```

  Сравнить множество `Tag` реального Employee Launch Center с этими пятью
  ID. Для Owner сравнить `Tag` с его `--product-json` без отдельного
  hardcode. `--ui-launch-selection-json vscode-codex` должен дать
  `selection_visual == "VISIBLE"` и кнопку «Запустить VS Code →».

- [ ] **Step 2: Добавить RED trust tests VS Code.**

  Test record содержит полный real-shape contract:

  ```json
  {
    "executable_path": "C:\\fixture\\Code.exe",
    "signature_status": "Valid",
    "signer_subject": "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US",
    "extension_publisher": "OpenAI",
    "extension_name": "chatgpt",
    "extension_path": "C:\\fixture\\.vscode\\extensions\\openai.chatgpt-1.0.0",
    "code_running": false
  }
  ```

  Валидная запись даёт `RESOLVED`. `NotSigned`, другой signer, иной publisher,
  иной extension name и `code_running=true` дают отдельные stable reasons:
  `VSCODE_SIGNATURE_INVALID`, `VSCODE_PUBLISHER_INVALID`,
  `CODEX_EXTENSION_NOT_VERIFIED`, `VSCODE_ALREADY_RUNNING`.

- [ ] **Step 3: Запустить RED.**

  ```powershell
  py -3.12 -m pytest tests/test_launch_center.py -k "complete_target_catalog or vscode" -vv
  ```

  Expected: FAIL — synthetic target/resolver/UI отсутствуют.

- [ ] **Step 4: Реализовать VsCodeIntegration и synthetic target.**

  Нормальный resolver ищет Code.exe только по согласованным путям и `PATH`,
  проверяет Authenticode через `WinVerifyTrust`, затем signer subject.
  Manifest ищется в `%USERPROFILE%\.vscode\extensions\openai.chatgpt-*` и
  принимается по двум полям JSON. Missing extension возвращает reason и
  `official_url`, не устанавливает расширение.

  При `code_running=true` возвращается блокировка с русским действием:
  сохранить работу, закрыть все окна VS Code и повторить запуск.

- [ ] **Step 5: Добавить карточки и parity tests до GREEN.**

  Employee получает пять карточек. Owner получает все свои product targets,
  включая `claude-code`, плюс `vscode-codex`. `SelectionLabel`,
  `TargetDisplayName`, `TargetProviderName` обрабатывают VS Code отдельно.

  ```powershell
  py -3.12 -m pytest tests/test_launch_center.py -k "product_role_exposes or complete_target_catalog or vscode or ui_launch_selection" -vv
  ```

  Expected: PASS.

- [ ] **Step 6: Commit.**

  ```powershell
  git add tests/test_launch_center.py src/gui/VsCodeIntegration.cs src/gui/LaunchTarget.cs src/gui/InstallerApp.cs src/gui/LaunchCenterEmployeeView.xaml src/gui/LaunchCenterOwnerView.xaml tools/build-gui.ps1
  git commit -m "feat: добавить полный каталог Codex и OpenCode"
  ```

### Task 3: Сквозная проверка SingBox и прозрачные runtime reasons

**Files:**
- Modify: `tests/test_launcher_runtime.py`
- Modify: `tests/test_gui.py`
- Modify: `src/gui/RuntimeBootstrap.cs`
- Modify: `src/gui/SingBoxSession.cs`
- Modify: `src/gui/InstallerApp.cs`

**Interfaces:**
- Produces: `SingBoxSession.TestRoute(string bundleRoot, string home, string route, string endpoint) -> SingBoxSessionResult`.
- `SingBoxSession.Start` передаёт `RuntimeBootstrapResult.reason` без замены на общий код.

- [ ] **Step 1: Добавить RED-тесты конкретных runtime failures.**

  На hermetic home/bundle закрепить:

  ```python
  assert missing["reason"] == "RUNTIME_BUNDLE_ARCHIVE_MISSING"
  assert tampered["reason"] == "RUNTIME_ARCHIVE_INTEGRITY_FAILED"
  ```

  Текущий код должен упасть с `RUNTIME_NOT_VERIFIED`.

- [ ] **Step 2: Добавить RED end-to-end route probe.**

  Использовать существующий fake sing-box/runtime fixture и локальный HTTP
  endpoint. Fake runtime поднимает локальный HTTP proxy, записывает каждый
  forwarded request и завершает работу по команде.

  ```python
  assert result["status"] == "PASS"
  assert result["uses_proxy"] is True
  assert result["cleanup_verified"] is True
  assert result["lifecycle"][-2:] == ["ROUTE_PROBE_PASS", "CLEANUP_VERIFIED"]
  assert upstream.received_paths == ["/route-check"]
  ```

  Неработающий local proxy даёт `ROUTE_PROBE_FAILED`, даже если прямой endpoint
  доступен.

- [ ] **Step 3: Запустить RED.**

  ```powershell
  py -3.12 -m pytest tests/test_launcher_runtime.py tests/test_gui.py -k "runtime_reason or route_probe" -vv
  ```

  Expected: FAIL по общему reason и отсутствующему сквозному probe.

- [ ] **Step 4: Реализовать TestRoute и подключить к Save/Test.**

  `TestRoute`:

  ```csharp
  RunningSingBoxSession session = Start(bundleRoot, home, "connection-test", route);
  try
  {
      HttpWebRequest request = (HttpWebRequest)WebRequest.Create(endpoint);
      request.Proxy = new WebProxy("http://127.0.0.1:" + session.listen_port);
      request.Timeout = 15000;
      using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
      {
          if ((int)response.StatusCode < 200 || (int)response.StatusCode >= 400)
              throw new InvalidOperationException("ROUTE_PROBE_FAILED");
      }
      session.lifecycle.Add("ROUTE_PROBE_PASS");
  }
  finally
  {
      StopVerified(session);
  }
  ```

  Connection UI для proxy после `ConnectionStore.Save` вызывает `TestRoute`;
  Direct/VPN сохраняют существующий `ConnectionProbe.Run`. Status показывает
  русский шаг и stable reason, не credentials.

- [ ] **Step 5: GREEN и секретный negative check.**

  ```powershell
  py -3.12 -m pytest tests/test_launcher_runtime.py tests/test_gui.py -k "runtime_reason or route_probe or connection_profile or password" -vv
  ```

  Expected: PASS; fixture password отсутствует в stdout/stderr/JSON.

- [ ] **Step 6: Commit.**

  ```powershell
  git add tests/test_launcher_runtime.py tests/test_gui.py src/gui/RuntimeBootstrap.cs src/gui/SingBoxSession.cs src/gui/InstallerApp.cs
  git commit -m "fix: проверять маршрут SingBox сквозным запросом"
  ```

### Task 4: Безопасная system-proxy lease для Store Codex

**Files:**
- Create: `tests/test_system_proxy_lease.py`
- Create: `src/gui/SystemProxyLease.cs`
- Modify: `src/gui/ClientLauncher.cs`
- Modify: `src/gui/InstallerApp.cs`
- Modify: `tools/build-gui.ps1`

**Interfaces:**
- Produces: `SystemProxyLease.Acquire(string home, int localPort)`.
- Produces: `SystemProxyLease.Recover(string home) -> ProxyRecoveryResult`.
- Produces: `SystemProxyLease.StopActiveRoute()`.
- Internal watchdog CLI: `--system-proxy-watchdog <ownerPid> <home>`.
- Test-only CLI requires registry key under `HKCU\Software\K7AITests\`.

- [ ] **Step 1: Добавить RED registry lifecycle tests.**

  На уникальном тестовом key проверить literal scenarios:

  - snapshot → apply → normal restore;
  - apply → owner process dies → watchdog restore;
  - applied values externally changed → CAS refuses overwrite and returns
    `SYSTEM_PROXY_CHANGED_EXTERNALLY`;
  - unresolved state blocks second acquire;
  - two concurrent acquire attempts: один `ACQUIRED`, второй
    `SYSTEM_PROXY_LEASE_BUSY`;
  - state file write failure leaves registry untouched.

  Tests use actual HKCU test subkey and delete only that exact unique subkey in
  fixture cleanup.

- [ ] **Step 2: Запустить RED.**

  ```powershell
  py -3.12 -m pytest tests/test_system_proxy_lease.py -vv
  ```

  Expected: FAIL — type/test commands отсутствуют.

- [ ] **Step 3: Реализовать owned state, mutex и CAS.**

  State содержит schema, SID, owner PID, phase, original и applied values с
  registry kinds. Порядок:

  ```text
  mutex acquired
  -> startup recovery
  -> atomic PREPARED state
  -> watchdog started
  -> registry apply
  -> atomic APPLIED state
  ```

  Restore сравнивает каждое текущее значение с `applied`; несовпадение не
  перезаписывается. Только подтверждённый restore удаляет state и освобождает
  lease. `AppDomain.ProcessExit` просит restore, watchdog остаётся аварийной
  гарантией.

- [ ] **Step 4: Интегрировать AppX path и StopRoute.**

  Удалить ветку `PROCESS_PROXY_NOT_SUPPORTED`. AppX SingBox:

  ```text
  reject already-running Codex
  -> SingBoxSession.Start
  -> SystemProxyLease.Acquire(local port)
  -> exact AppX activation
  -> wait/stop signal
  -> lease restore
  -> SingBoxSession.StopVerified
  ```

  Ошибка любого шага выполняет восстановление в обратном порядке.
  `ClientLauncher.StopActiveRoute()` идемпотентно восстанавливает proxy и
  останавливает owned sing-box, не убивая Codex. Кнопка `StopRoute` вызывает
  этот метод; закрытие окна покрывает watchdog.

- [ ] **Step 5: Добавить test-only AppX integration path и довести до GREEN.**

  Только для embedded `client-sources.lock.test_only=true` test command
  подменяет AppX activator на подписанный fixture process, но использует
  настоящие `SingBoxSession`, `SystemProxyLease` и registry test key. Проверить
  success, activation failure, stop signal и owner crash.

  ```powershell
  py -3.12 -m pytest tests/test_system_proxy_lease.py tests/test_launch_center.py -k "system_proxy or appx_singbox or stop_route" -vv
  ```

  Expected: PASS; process-only launch tests дополнительно подтверждают, что
  registry не менялся.

- [ ] **Step 6: Commit.**

  ```powershell
  git add tests/test_system_proxy_lease.py tests/test_launch_center.py src/gui/SystemProxyLease.cs src/gui/ClientLauncher.cs src/gui/InstallerApp.cs tools/build-gui.ps1
  git commit -m "fix: безопасно маршрутизировать Store Codex через SingBox"
  ```

### Task 5: Визуальный QA, полный прогон и документация

**Files:**
- Modify: `docs/ИНСТРУКЦИЯ-СОТРУДНИКУ.md`
- Modify: `README.md` only if current release instructions need the r3 behavior.
- Create: ignored QA outputs under `.work/launch-center-r3-qa/`.

**Interfaces:**
- Consumes: complete branch Tasks 1–4.
- Produces: two 1440×900 Launch Center PNGs and zero-model acceptance evidence.

- [ ] **Step 1: Обновить русскую инструкцию.**

  Описать распаковку всего ZIP, пять Employee-режимов, Save/Test, конкретные
  runtime ошибки, временный системный proxy только для Store Codex, кнопку
  Stop и автоматическое восстановление. Не писать, что clean-PC pilot пройден.

- [ ] **Step 2: Запустить focused suites и полный pytest фоном.**

  ```powershell
  py -3.12 -m pytest tests/test_gui.py tests/test_launch_center.py tests/test_launcher_runtime.py tests/test_system_proxy_lease.py -q
  py -3.12 -m pytest -q
  ```

  Expected: PASS без warning/error; JUnit фиксирует точное число tests.

- [ ] **Step 3: Собрать и отрендерить оба Launch Center.**

  ```powershell
  pwsh -NoProfile -File .\tools\build-gui.ps1 -OutputRoot .\.work\launch-center-r3-qa\employee -Edition Employee -ProductRole LaunchCenter -DistributionMode Preview
  pwsh -NoProfile -File .\tools\build-gui.ps1 -OutputRoot .\.work\launch-center-r3-qa\owner -Edition Owner -ProductRole LaunchCenter -DistributionMode Preview
  & .\.work\launch-center-r3-qa\employee\LLMFoundationInstaller.exe --render-preview .\.work\launch-center-r3-qa\employee.png
  & .\.work\launch-center-r3-qa\owner\LLMFoundationInstaller.exe --render-preview .\.work\launch-center-r3-qa\owner.png
  ```

- [ ] **Step 4: Visual QA в native resolution.**

  Подтвердить:

  - Employee: все пять карточек читаемы без обрезки;
  - Owner: полный owner catalog;
  - proxy fields и обе кнопки видны;
  - подсказка не перекрывает status cards;
  - выбранные client/route визуально однозначны;
  - пароль/fixture secrets нигде не видны.

  При дефекте добавить RED UI-state/render test до XAML-исправления.

- [ ] **Step 5: Run acceptance.**

  ```powershell
  py -3.12 .\tools\run-acceptance.py
  ```

  Expected: PASS, `model_requests = 0`.

- [ ] **Step 6: Commit docs/visual fixes.**

  ```powershell
  git add docs/ИНСТРУКЦИЯ-СОТРУДНИКУ.md README.md src/gui/*.xaml tests
  git commit -m "docs: описать рабочий сценарий Launch Center"
  ```

### Task 6: Review, protected-main merge и immutable prerelease r3

**Files:**
- Read: `.github/workflows/*`, `tools/build-edition.ps1`, `tools/pilot_release.py`, `tools/installer_release_verifier.py`.
- Modify after verified release only: project `Claude/STATUS.md`, top of `Claude/ЖУРНАЛ СЕССИЙ.md`, and the scoped session report sections.

**Interfaces:**
- Consumes: clean reviewed branch, complete tests, acceptance, visual PASS.
- Produces: protected-main merge and independently verified immutable r3 assets.

- [ ] **Step 1: Whole-branch review.**

  Проверить diff от merge-base на spec compliance, concurrency/registry safety,
  credential leakage, edition parity и отсутствие стабильного release. Все
  Critical/Important findings исправить одним fix wave и выполнить scoped
  re-review.

- [ ] **Step 2: Проверить чистую ветку и создать PR.**

  ```powershell
  $mergeBase = git merge-base main HEAD
  git diff --check "$mergeBase..HEAD"
  git status --short
  git push -u origin fix/launch-center-r3
  gh pr create --base main --head fix/launch-center-r3
  ```

  Дождаться обоих required Windows checks. Не ослаблять protection.

- [ ] **Step 3: Merge и post-merge CI.**

  Merge только при green required checks. Проверить `origin/main` и оба
  post-merge Windows runs на exact merge SHA.

- [ ] **Step 4: Построить новый r3 из exact main SHA.**

  Employee ZIP содержит Installer, Launch Center, runtime archive и manifest.
  Owner ZIP остаётся owner-only/non-distributable. README/report честно
  содержат `CLEAN_PC_PILOT=PENDING` и запрет `employee-v0.3.0`.

- [ ] **Step 5: Опубликовать новый immutable prerelease.**

  Создать новый тег `workplace-pilot-v0.3.0-r3`, `draft=false`,
  `prerelease=true`, immutable. Не перемещать r2 и не создавать stable tag.

- [ ] **Step 6: Независимо скачать и проверить каждый asset.**

  Сверить API digest, локальный SHA-256, размер, ZIP entries, manifest, exact
  EXE self-test, product targets и русские тексты. Аудитор возвращает
  `PASSED` с явным списком свойств.

- [ ] **Step 7: Обновить проектный checkpoint и передать пользователю.**

  Записать merge SHA, CI URLs, tag, Employee ZIP URL/SHA-256, честный
  `CLEAN_PC_PILOT=PENDING` и конкретный сценарий повторной проверки рабочего
  ПК. Стабильный релиз остаётся запрещён.

## Plan Self-Review

- Spec coverage: четыре UI — Task 1; пять Employee и полный Owner catalog,
  VS Code trust — Task 2; runtime reason/end-to-end probe — Task 3; system
  proxy lease/watchdog/CAS/Stop — Task 4; visual/full acceptance — Task 5;
  review/protected main/immutable r3 — Task 6.
- Placeholder scan: пусто; каждый шаг содержит проверяемое действие и
  ожидаемый результат.
- Type consistency: `vscode-codex` создаётся Task 2 и разрешается
  `VsCodeIntegration`; `SingBoxSession.TestRoute` создаётся Task 3 и вызывается
  общим Connection UI; `SystemProxyLease` создаётся Task 4 и используется
  только AppX path.
- Safety: CI никогда не пишет в реальный Internet Settings; AppX restore
  fail-closed; process-only пути не меняют системный proxy; secrets не входят
  в evidence.
