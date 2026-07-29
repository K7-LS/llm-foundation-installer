# Ремонт определения Codex и UX SingBox — план реализации

> **Для агентных исполнителей:** ОБЯЗАТЕЛЬНЫЙ поднавык: `superpowers:subagent-driven-development` (рекомендуется) или `superpowers:executing-plans`. Шаги отмечаются чекбоксами.

**Цель:** правильно определять установленный Codex независимо от его версии и дать в обеих edition понятную немедленно доступную настройку SingBox.

**Архитектура:** `ClientBootstrap` уже безопасно проверяет Store identity `OpenAI.Codex`; общий `ProductCatalog` должен использовать эквивалентный Store-first probe вместо CLI-only вызова. Установленный клиент и источник допустимой установки — разные состояния: первое не требует pinned-совпадения, второе остаётся hash/lock-проверяемым. `ConnectionUi` управляет видимостью блока SingBox, а Employee/Owner XAML задают один контракт контролов через `x:Name` и `Tag`.

**Технологии:** C# WPF/.NET Framework, PowerShell build scripts, Python 3.12 + pytest, PowerShell 7 и Windows PowerShell 5.1.

## Глобальные ограничения

- Изменять только `src/gui/InstallerApp.cs`, `src/gui/ClientBootstrap.cs`, Employee/Owner XAML и связанные regression tests; lock-файлы не менять.
- Не выполнять model calls.
- Любая найденная версия установленного Codex отображается и принимается; скачивание разрешено только при отсутствии клиента и остаётся привязанным к существующему официальному source lock.
- `BLOCKED_NO_DOWNGRADE` нельзя возвращать для уже найденного Codex.
- Все новые пользовательские тексты — на русском; технические contract IDs/`Tag` не локализуются.
- `employee-v0.3.0` остаётся запрещён до PII-free physical clean-PC pilot. Новый prerelease — только повторная проверка рабочего ПК.

## Карта файлов

- `src/gui/InstallerApp.cs` — каталог, отображение статуса и биндинг маршрута WPF.
- `src/gui/ClientBootstrap.cs` — Store/CLI client probe и план установки.
- `src/gui/InstallerEmployeeView.xaml` — русская Employee-форма SingBox.
- `src/gui/InstallerOwnerView.xaml` — русская Owner-форма SingBox.
- `tests/test_gui.py` — compile/run regression tests, статический контракт XAML и rendering.
- `tools/build-gui.ps1`, `tools/build-edition.ps1`, `tools/run-acceptance.py` — не менять; использовать для сборки и evidence.

---

### Task 1: RED-контракт определения Codex и версионного решения

**Файлы:**
- Modify: `tests/test_gui.py` рядом с `test_gui_preflight_checks_clients_without_claiming_missing_packages` и текущими тестами `BLOCKED_NO_DOWNGRADE`.
- Read: `src/gui/InstallerApp.cs:184-274`, `src/gui/ClientBootstrap.cs:254-328,550-650`.

**Интерфейсы:**
- Consumes: `ProductCatalog.Inspect(string bundleRoot, bool detectClients = false) -> CatalogResult` и `ClientBootstrap.Plan(string bundleRoot, string home, string clientId) -> ClientPlanResult`.
- Produces: regression-контракт: Store-only Codex выдаёт `detected_version` и состояние `ready`; отличная установленная версия не выдаёт `unsupported` или `BLOCKED_NO_DOWNGRADE`; отсутствие клиента выдаёт `INSTALL_AVAILABLE`/`GUIDED_STORE` согласно существующему source kind.

- [ ] **Шаг 1: Добавить failing tests для Store-only и CLI-fallback определения.**

  Создать fixture-запуск с безопасным тестовым `PATH` без `codex.exe` и подменённым результатом Store probe через существующий JSON-record path/CLI mode. Проверить структуру результата:

  ```python
  codex = next(row for row in payload["targets"] if row["id"] == "codex")
  assert codex["detected_version"] == "26.721.4979.0"
  assert codex["client_state"] == "ready"
  assert codex["client_state"] != "unsupported"
  ```

  Второй тест оставляет Store probe missing и передаёт fixture `codex.exe --version`; он проверяет CLI fallback и ту же форму результата.

- [ ] **Шаг 2: Запустить новые RED-тесты.**

  Run:

  ```powershell
  py -3.12 -m pytest tests/test_gui.py -k "store_only_codex or cli_fallback_codex" -vv
  ```

  Expected: FAIL, потому что `ProductCatalog` сейчас вызывает только `ClientDetector.DetectVersion("codex-cli")`.

- [ ] **Шаг 3: Добавить failing tests для несовпадающей и отсутствующей версии.**

  Закрепить, что Store или CLI версия `2.0.0` при source version `1.0.0` даёт `READY`/accepted-installed action и не запускает download. Закрепить, что полный absence отдаёт существующую установочную ветку. Переписать старые downgrade assertions только там, где они относятся к Codex:

  ```python
  assert payload["status"] != "BLOCKED_NO_DOWNGRADE"
  assert payload["detected_version"] == "2.0.0"
  assert payload["action"] == "none"
  ```

- [ ] **Шаг 4: Запустить RED-тесты версий.**

  Run:

  ```powershell
  py -3.12 -m pytest tests/test_gui.py -k "codex and (downgrade or newer_version or version_mismatch or missing_client)" -vv
  ```

  Expected: FAIL на текущем `BLOCKED_NO_DOWNGRADE`/`unsupported` contract.

- [ ] **Шаг 5: Commit RED tests.**

  ```powershell
  git add tests/test_gui.py
  git commit -m "test: cover Codex Store detection and version acceptance"
  ```

### Task 2: Общий Store-first probe и версия без downgrade gate

**Файлы:**
- Modify: `src/gui/InstallerApp.cs:184-274,565-654,1098-1178,1794-1930`.
- Modify: `src/gui/ClientBootstrap.cs:550-650,688-740`.
- Test: `tests/test_gui.py` — тесты из Task 1.

**Интерфейсы:**
- Consumes: `ClientBootstrap.ProbeStore(bundleRoot, "codex-desktop") -> StoreClientResult`, `ClientDetector.DetectVersion("codex-cli") -> string|null`.
- Produces: `ClientDetectionResult { version, source }` в общем code path; `ProductCatalog` и `ClientBootstrap.Plan` согласованно различают installed/missing и не понижают Codex.

- [ ] **Шаг 1: Ввести минимальный общий результат определения Codex.**

  В `InstallerApp.cs` добавить внутренний тип с `string version` и `string source`; метод сначала пробует identity-validated `ClientBootstrap.ProbeStore` для Codex Desktop, затем использует `ClientDetector.DetectVersion("codex-cli")`. Любая ошибка probe трактуется как не найдено для каталога и не раскрывает исключение в UI.

  ```csharp
  internal sealed class ClientDetectionResult
  {
      public string version { get; set; }
      public string source { get; set; }
  }
  ```

- [ ] **Шаг 2: Использовать результат в ProductCatalog.**

  Заменить CLI-only вызов. Если `version != null`, выставить `client_state = "ready"` при accepted package и `present_unbound` для иной package state; ни одно сравнение с `supported_version` не формирует `unsupported`. UI отображает фактическую `detected_version` без текста «требуется <supported_version>» для установленного Codex.

- [ ] **Шаг 3: Исправить Plan/Install для уже найденного Codex.**

  В `ClientBootstrap.Plan` при source id, относящемся к Codex, вернуть `READY` с `action = "none"` для любой непустой detected version. В `Install` не вызывать download при этом `READY`. Не менять fail-closed downgrade logic других клиентов.

- [ ] **Шаг 4: Запустить Task 1 tests до green.**

  Run:

  ```powershell
  py -3.12 -m pytest tests/test_gui.py -k "store_only_codex or cli_fallback_codex or (codex and (downgrade or newer_version or version_mismatch or missing_client))" -vv
  ```

  Expected: PASS; тест отсутствующего Codex подтверждает исходную установочную ветку, tests existing non-Codex downgrade behaviour не меняются.

- [ ] **Шаг 5: Commit общего ремонта.**

  ```powershell
  git add src/gui/InstallerApp.cs src/gui/ClientBootstrap.cs tests/test_gui.py
  git commit -m "fix: accept detected Codex versions and Store install"
  ```

### Task 3: RED и реализация понятного SingBox UX в обеих edition

**Файлы:**
- Modify: `tests/test_gui.py` рядом с connection-profile tests `3834-4103` и render-preview tests.
- Modify: `src/gui/InstallerApp.cs:2619-2718`.
- Modify: `src/gui/InstallerEmployeeView.xaml:205-235`.
- Modify: `src/gui/InstallerOwnerView.xaml:125-151`.

**Интерфейсы:**
- Consumes: WPF names `ProxyMode`, `ProxySettings`, `ProxyType`, `ProxyHost`, `ProxyPort`, `ProxyAuth`, `ProxyUsername`, `ProxyPassword`, `TestConnection`.
- Produces: `ConnectionUi.Bind` sets `ProxySettings.Visibility` to `Visible` only for `ProxyMode`; `BuildProfile` receives invariant `HTTP`/`HTTPS` and `None`/`UsernamePassword` tags from both edition views.

- [ ] **Шаг 1: Добавить failing static/UI contracts для обеих XAML.**

  Параметризовать Employee и Owner resources. Проверить отсутствующий сейчас contract и русские next steps:

  ```python
  assert 'x:Name="ProxySettings"' in xaml
  assert 'Visibility="Collapsed"' in xaml
  assert 'Content="HTTP" Tag="HTTP"' in xaml
  assert 'Content="HTTPS" Tag="HTTPS"' in xaml
  assert 'Tag="None"' in xaml
  assert 'Tag="UsernamePassword"' in xaml
  assert "Сохранить и проверить" in xaml
  assert "Launch Center сам запускает и останавливает sing-box" in xaml
  ```

  Добавить source assertion для `settings.Visibility = isProxy ? Visibility.Visible : Visibility.Collapsed`.

- [ ] **Шаг 2: Запустить SingBox RED-контракт.**

  Run:

  ```powershell
  py -3.12 -m pytest tests/test_gui.py -k "singbox and (employee or owner or connection_ui)" -vv
  ```

  Expected: FAIL: Employee/Owner не имеют Tag/instruction, а `ConnectionUi` меняет только `IsEnabled`.

- [ ] **Шаг 3: Исправить ConnectionUi visibility и XAML Employee/Owner.**

  В `updateMode` добавить `settings.Visibility = isProxy ? Visibility.Visible : Visibility.Collapsed;` перед обновлением статуса. В каждой разметке сделать два ряда: первый — шесть русских подписей, второй — сами поля; добавить invariant Tags. Добавить TextBlock под полями с exact copy:

  ```text
  1. Заполните данные. 2. Нажмите «Сохранить и проверить». 3. Запустите маршрут в Launch Center. Launch Center сам запускает и останавливает sing-box; отдельный скрипт не нужен.
  ```

  Сохранить существующие `x:Name`, DPAPI/password flow и возможность Direct/VPN скрыть блок.

- [ ] **Шаг 4: Запустить tests и построить preview обеих edition.**

  Run:

  ```powershell
  py -3.12 -m pytest tests/test_gui.py -k "singbox or connection_profile or authenticated_connection_profiles" -vv
  pwsh -NoProfile -File .\tools\build-gui.ps1 -OutputRoot .\dist\qa-employee -Edition Employee -ProductRole Installer -DistributionMode Preview
  pwsh -NoProfile -File .\tools\build-gui.ps1 -OutputRoot .\dist\qa-owner -Edition Owner -ProductRole Installer -DistributionMode Preview
  ```

  Expected: PASS, два executable и два 1440×900 PNG preview.

- [ ] **Шаг 5: Выполнить visual QA.**

  Открыть оба PNG в native resolution. Подтвердить: выбор SingBox раскрывает блок, шесть подписей/контролов не обрезаны, русская подсказка читаема, кнопка «Сохранить и проверить» видна, нет пароля в статусе/превью.

- [ ] **Шаг 6: Commit SingBox UX.**

  ```powershell
  git add src/gui/InstallerApp.cs src/gui/InstallerEmployeeView.xaml src/gui/InstallerOwnerView.xaml tests/test_gui.py
  git commit -m "fix: reveal and explain SingBox settings in both editions"
  ```

### Task 4: Полная верификация, protected-main и prerelease для рабочего ПК

**Файлы:**
- Read: `tools/run-acceptance.py`, `tools/build-edition.ps1`, `tools/pilot_release.py`, `tools/installer_release_verifier.py`.
- Create: новый immutable prerelease assets/evidence под `.work/актуальное/` согласно существующему release workflow.
- Modify: project handoff only after verified completion: `Claude/STATUS.md`, `Claude/ЖУРНАЛ СЕССИЙ.md`, `session-reports/2026-07-29_russian-dashboard-release-repair/report.md`.

**Интерфейсы:**
- Consumes: clean committed tree, local full-suite result, visual QA result, GitHub protected CI verdict.
- Produces: immutable prerelease for working-PC retest, SHA-256 manifest and honest `CLEAN_PC_PILOT=PENDING` release metadata.

- [ ] **Шаг 1: Run full local test and acceptance gates.**

  Run:

  ```powershell
  py -3.12 -m pytest -q
  py -3.12 .\tools\run-acceptance.py
  ```

  Expected: all tests PASS in supported PowerShell coverage; acceptance evidence references the exact clean commit and has `model_requests = 0`.

- [ ] **Шаг 2: Review final diff and build candidates.**

  Verify `git diff HEAD~3..HEAD --check`, clean worktree, and build Employee + Owner artifacts through the existing `build-edition.ps1` commands. Verify the Employee package contains only Codex/OpenCode and the Owner package remains `distribution_allowed=false`.

- [ ] **Шаг 3: Publish through protected main only.**

  Create a PR from the repair branch, wait for both required Windows CI checks, merge without weakening branch protection, then verify `main == origin/main` and post-merge CI success. Do not move/replace `workplace-pilot-v0.3.0-r1` and do not create `employee-v0.3.0`.

- [ ] **Шаг 4: Publish a new immutable prerelease.**

  Build a new tag such as `workplace-pilot-v0.3.0-r2` from the verified protected-main SHA. Publish Employee pilot, Owner-only pilot, UI previews, Russian README/report and SHA256SUMS. Set `draft=false`, `prerelease=true`, `immutable=true`.

- [ ] **Шаг 5: Independently download assets and verify.**

  Download every GitHub asset by API, compare file size and SHA-256 to the local manifest, and record exact results. The prerelease report must retain `CLEAN_PC_PILOT=PENDING`, explain the two repaired user journeys and request a repeat physical working-PC test.

- [ ] **Шаг 6: Update project checkpoint.**

  Only after the preceding evidence is PASS, update the top snapshot/status/journal in Russian: commits, CI, tag, asset hashes, unresolved clean-PC gate and explicit prohibition of stable `employee-v0.3.0`.

## План self-review

- Спецификация покрыта: Store/CLI detection — Tasks 1–2; version acceptance and no downgrade — Tasks 1–2; both editions and signed SingBox fields — Task 3; visual QA/full test/CI/immutable prerelease — Task 4.
- Placeholder scan: незаполненных маркеров и нераскрытых ссылок нет.
- Consistency: Task 2 produces `ClientDetectionResult`, which is only consumed by `ProductCatalog`; Task 3 uses existing WPF control names and `BuildProfile` tags; Task 4 never promotes a stable Employee release.
