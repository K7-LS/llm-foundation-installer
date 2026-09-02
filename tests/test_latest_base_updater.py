from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "gui" / "BaseReleaseUpdater.cs"
BUILD = ROOT / "tools" / "build-gui.ps1"


def _app() -> str:
    """GUI-код одной строкой: типы разнесены по модулям (Ф2)."""
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src" / "gui").glob("*.cs"))
    )


def test_latest_base_sources_are_exact_and_native() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "K7-LS/codex-base" in source
    assert "K7-LS/claude-base-v2" in source
    assert "K7-LS/opencode-base" in source
    assert "/releases/latest" in source
    assert '"codex-v"' in source
    assert '"claude-v"' in source
    assert '"opencode-v"' in source


def test_latest_base_is_fail_closed_before_cache_activation() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    required = (
        "release.draft",
        "release.prerelease",
        "!release.immutable",
        'manifest.channel, "stable"',
        "manifest.foundation_engine_version",
        "manifest.foundation_engine_manifest_sha256",
        "manifest.requires.immutable_release",
        "manifest.requires.release_attestation",
        "DigestValue(packageAsset.digest)",
        "manifest.asset.bytes != packageAsset.size",
        "WriteAtomic(destination, payload)",
    )
    for contract in required:
        assert contract in source


def test_installer_resolves_latest_before_client_and_foundation_plan() -> None:
    app = _app()
    workflow = app.split("foreach (TargetRow row in selected)", 1)[1]
    assert workflow.index("RunBaseReleaseResolveAsync") < workflow.index(
        "RunClientPlanAsync"
    )
    assert "row.latest_base_package_path" in app
    assert "row.latest_base_manifest_path" in app
    assert "row.latest_base_manifest_sha256" in app
    assert 'arguments.Add("-ReleaseManifest")' in app
    assert 'arguments.Add("-ReleaseManifestSha256")' in app


def test_embedded_package_is_only_explicit_latest_failure_fallback() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    app = _app()
    assert 'status = "EMBEDDED_FALLBACK"' in source
    assert "used_embedded_fallback = true" in source
    assert "использован проверенный embedded fallback" in app
    assert 'args[0] == "--latest-base-json"' in app


def test_latest_base_source_is_compiled_into_every_gui() -> None:
    project = (
        ROOT / "src" / "gui" / "LlmFoundationInstaller.csproj"
    ).read_text(encoding="utf-8")
    assert '<Compile Include="BaseReleaseUpdater.cs" />' in project


def test_installer_prompts_for_every_unknown_before_reconcile() -> None:
    app = _app()
    assert '"BLOCKED_USER_DECISION"' in app
    assert 'MessageBoxButton.YesNoCancel' in app
    assert 'arguments.Add("-LocalExceptionPath")' in app
    assert 'arguments.Add("-ConfirmRemoveUnknown")' in app
    assert "row.local_exception_paths" in app
    assert "row.confirm_remove_unknown" in app
    assert "IsLegacyFoundationDirectory" in app
    assert "применить рекомендуемый вариант" in app
    assert "локальные " in app
    assert '"настройки не удаляются.' in app
    assert "ShowOwnedMessage" in app
    # Подсказка «ожидает решения… нажмите Сформировать план» заменена
    # ОСОЗНАННО: движок отдаёт BLOCKED_USER_DECISION и без списка
    # unknown_entries, и тогда диалога за этой кнопкой нет — получался
    # замкнутый круг. Теперь показывается фактическая причина, а
    # приглашение к выбору остаётся только когда решение доступно.
    assert "ожидает решения по старой базе" not in app
    assert "UnknownDecisionReason" in app
    assert "остановлен движком" in app


def test_session_tool_collision_offers_backup_instead_of_dead_end() -> None:
    # Движок отказывается ставить session tool поверх каталога, которого нет
    # в его состоянии и который отличается от пакета: это скилл прежней
    # установки. Для неизвестных записей выбор предлагается, а здесь был
    # жёсткий INVALID_PACKAGE без выхода — наблюдалось на рабочей станции
    # сотрудника (acad-recreation блокировал установку Claude целиком).
    app = _app()
    assert "ResolveSessionToolCollision" in app
    assert "Unmanaged session tool collision:" in app

    window = app.split("private static bool ResolveSessionToolCollision", 1)[1]
    window = window.split("private static string SessionToolCollisionId", 1)[0]
    # Пользователь решает сам, копия сохраняется
    assert "ShowOwnedMessage" in window
    assert "MessageBoxButton.YesNo" in window
    assert "backups" in window and "session-tools" in window
    assert "Directory.Move" in window
    assert "MessageBoxResult.Yes" in window

    # Имя скилла — один безопасный сегмент: путь не собирается из чужого текста
    parser = app.split("private static string SessionToolCollisionId", 1)[1]
    parser = parser.split("private static string FindSessionToolDirectory", 1)[0]
    assert "IndexOfAny" in parser
    assert "toolId.Length <= 64" in parser

    # Каталог не угадывается, а ищется фактический, и только внутри профиля
    finder = app.split("private static string FindSessionToolDirectory", 1)[1]
    finder = finder.split("private static bool IsUnknownDecisionRequired", 1)[0]
    assert "Directory.Exists" in finder
    assert "StartsWith" in finder
    assert "ReparsePoint" in finder


def test_unknown_entries_are_read_as_json_arrays_not_typed_arrays() -> None:
    # JavaScriptSerializer отдаёт JSON-массивы как ArrayList. Код приводил
    # их к object[], получал null и молча возвращался — диалог выбора по
    # неизвестным записям не показывался НИ РАЗУ: пользователь видел либо
    # предложение снова нажать «Сформировать план», либо текст ошибки.
    # Проверено на живом ответе движка: as object[] -> null, через
    # IEnumerable -> 39 записей.
    app = _app()
    window = app.split("private static bool ResolveUnknownDecisions", 1)[1]
    window = window.split("private static string SessionToolCollisionId", 1)[0]
    assert "as object[]" not in window, (
        "JSON-массив нельзя приводить к object[]: JavaScriptSerializer "
        "возвращает ArrayList, приведение даёт null"
    )
    assert "IEnumerable" in window
    assert "Cast<object>()" in window
    # строка тоже IEnumerable — она не должна сойти за список записей
    assert "is string" in window


def test_foundation_process_timeout_is_real() -> None:
    # Замечание Codex к плану переработки: stdout и stderr читались
    # синхронно ДО WaitForExit(timeout). Повисший движок держал stdout
    # открытым, ReadToEnd не возвращался, и таймаут не наступал никогда;
    # переполненный stderr во время чтения stdout давал pipe-deadlock.
    # Статический gate: в запуске движка нет синхронного ReadToEnd, оба
    # потока читаются асинхронно, ожидание ограничено.
    source = (ROOT / "src" / "gui" / "FoundationWorkflow.cs").read_text(
        encoding="utf-8"
    )
    assert "ReadToEnd()" not in source
    assert source.count("ReadToEndAsync()") == 2
    assert "WaitForExit(TimeoutMilliseconds)" in source
    assert "Task.WaitAll(" in source
    assert "process.Kill()" in source

def test_plan_action_count_is_not_read_through_typed_array_cast() -> None:
    # Второе место того же корня, что и диалог по неизвестным записям:
    # «actions» из ответа движка проверялись как object[], а JavaScriptSerializer
    # отдаёт ArrayList — счётчик всегда был 0, и пользователь видел
    # «0 файлов, backup и doctor» в каждом плане.
    app = _app()
    assert "is object[]" not in app
    assert "((object[])" not in app
    window = app.split('"actions",', 1)[1].split("planLines.Add", 1)[0]
    assert "ICollection" in window
    assert "is string" in window


def test_partial_install_is_not_reported_as_full_success() -> None:
    # Ревью Codex: успех одной базы показывался как общий «Установка
    # завершена», даже если остальные выбранные цели упали. Частичный
    # результат должен называться частичным: другой тон статуса, шаг
    # «Готово» не подсвечен как пройденный, в тексте — кто прошёл.
    app = _app()
    assert "bool partial = completed.Count < selected.Count;" in app
    assert "Установка завершена частично: " in app
    assert 'partial ? "warning" : "success"' in app
    assert "InstallerView.SetWorkflowStep(view, 7, !partial);" in app
    assert "MessageBoxImage.Warning" in app
