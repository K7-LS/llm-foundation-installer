using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace LlmFoundationInstaller
{
    internal static class InstallerActions
    {
        private sealed class WorkflowRunResult
        {
            public int code { get; set; }
            public string output { get; set; }
            public string error { get; set; }
        }

        public static void Bind(
            UserControl view,
            string bundleRoot,
            bool interactive
        )
        {
            if (!interactive)
            {
                return;
            }
            Button primary = view.FindName("PrimaryAction") as Button;
            TextBlock status = view.FindName("StatusText") as TextBlock;
            Button refresh = view.FindName(
                "RefreshEnvironment"
            ) as Button;
            if (primary == null || status == null)
            {
                return;
            }
            Action refreshAction = delegate
            {
                InstallerView.ApplyCatalog(
                    view,
                    ProductCatalog.Inspect(bundleRoot, true)
                );
            };
            if (refresh != null)
            {
                refresh.Click += delegate
                {
                    refreshAction();
                };
            }
            primary.Click += async delegate
            {
                await RunPlanAndInstallAsync(
                    view,
                    bundleRoot
                );
            };
        }

        private static async Task RunPlanAndInstallAsync(
            UserControl view,
            string bundleRoot
        )
        {
            PlatformCompatibility.RequireSupported();
            EditionProfile edition = EditionProfile.LoadEmbedded();
            CatalogResult catalog = ProductCatalog.Inspect(bundleRoot, true);
            List<TargetRow> selected = catalog.targets.Where(row =>
            {
                string prefix = row.id == "codex"
                    ? "Codex"
                    : (row.id == "claude" ? "Claude" : "OpenCode");
                CheckBox box = view.FindName(
                    prefix + "Selected"
                ) as CheckBox;
                return box != null && box.IsChecked == true &&
                    IsInstallableTarget(row, edition);
            }).ToList();
            if (selected.Count == 0)
            {
                SetStatus(
                    view,
                    "Нет выбранных принятых баз.",
                    "warning"
                );
                return;
            }
            string home = Environment.GetFolderPath(
                Environment.SpecialFolder.UserProfile
            );
            ProgressBar progress = view.FindName(
                "InstallProgress"
            ) as ProgressBar;
            string connectionError;
            InstallerView.SetWorkflowStep(view, 2, false);
            if (!ConnectionUi.TrySaveCurrent(
                    view,
                    out connectionError))
            {
                SetStatus(
                    view,
                    "Параметры соединения не сохранены: " +
                        connectionError,
                    "warning"
                );
                return;
            }
            SetBusy(view, catalog, true);
            if (progress != null)
            {
                progress.Minimum = 0;
                progress.Maximum = Math.Max(1, selected.Count * 8);
                progress.Value = 0;
                progress.Visibility = Visibility.Visible;
            }
            int completedOperations = 0;
            List<string> notices = new List<string>();
            List<TargetRow> clientReady = new List<TargetRow>();
            List<TargetRow> completed = new List<TargetRow>();
            try
            {
                InstallerView.SetWorkflowStep(view, 3, false);
                SetStatus(
                    view,
                    "Проверяются официальные клиенты и версии...",
                    "info"
                );
                foreach (TargetRow row in selected)
                {
                    SetStatus(
                        view,
                        "Проверяется последний stable-релиз базы " +
                            row.display_name + "...",
                        "info"
                    );
                    BaseReleaseResolution baseRelease =
                        await RunBaseReleaseResolveAsync(
                            bundleRoot,
                            home,
                            row.id
                        );
                    completedOperations++;
                    SetProgress(progress, completedOperations);
                    if (baseRelease.status == "LATEST")
                    {
                        row.latest_base_version = baseRelease.version;
                        row.latest_base_package_path =
                            baseRelease.package_path;
                        row.latest_base_manifest_path =
                            baseRelease.release_manifest_path;
                        row.latest_base_manifest_sha256 =
                            baseRelease.release_manifest_sha256;
                    }
                    else
                    {
                        notices.Add(
                            row.display_name +
                            ": latest stable недоступен или несовместим (" +
                            baseRelease.reason +
                            "); использован проверенный embedded fallback."
                        );
                    }
                    TargetClientPlanResult clientPlan =
                        await RunClientPlanAsync(
                            bundleRoot,
                            home,
                            row.id
                        );
                    completedOperations++;
                    SetProgress(progress, completedOperations);
                    if (clientPlan.status == "BLOCKED")
                    {
                        ClientPlanResult blocked =
                            clientPlan.clients.First(plan =>
                                plan.status ==
                                    "BLOCKED_NO_DOWNGRADE");
                        notices.Add(
                            row.display_name +
                            ": база пропущена — обнаружена версия " +
                            blocked.detected_version +
                            ", автоматический downgrade запрещён."
                        );
                        continue;
                    }
                    List<ClientPlanResult> guided =
                        clientPlan.clients.Where(plan =>
                            plan.status == "GUIDED_STORE"
                        ).ToList();
                    if (guided.Count > 0)
                    {
                        MessageBoxResult openStore = MessageBox.Show(
                            "Для " + row.display_name +
                            " требуется официальный Microsoft Store " +
                            "пакет OpenAI.Codex.\n\n" +
                            "Открыть точную карточку Store сейчас? " +
                            "После установки нажмите «Проверить снова». " +
                            "Остальные базы продолжат установку.",
                            "Требуется Codex Desktop",
                            MessageBoxButton.YesNo,
                            MessageBoxImage.Information
                        );
                        if (openStore == MessageBoxResult.Yes)
                        {
                            foreach (ClientPlanResult store in guided)
                            {
                                ClientBootstrap.OpenStoreSource(
                                    bundleRoot,
                                    store.client_id
                                );
                            }
                        }
                        notices.Add(
                            row.display_name +
                            ": ожидается установка Codex Desktop из Store."
                        );
                        continue;
                    }
                    List<ClientPlanResult> toInstall =
                        clientPlan.clients.Where(plan =>
                            plan.status == "INSTALL_AVAILABLE"
                        ).ToList();
                    if (toInstall.Count > 0)
                    {
                        MessageBoxResult clientApproval =
                            MessageBox.Show(
                                "Для " + row.display_name +
                                " будут скачаны и проверены официальные " +
                                "клиенты:\n\n" +
                                String.Join(
                                    "\n",
                                    toInstall.Select(plan =>
                                        "• " + plan.client_id + " " +
                                        plan.supported_version
                                    ).ToArray()
                                ) +
                                "\n\nSHA-256 и издатель проверяются до " +
                                "запуска. Учётные данные не читаются. " +
                                "Продолжить?",
                                "Установка официальных клиентов",
                                MessageBoxButton.YesNo,
                                MessageBoxImage.Question
                            );
                        if (clientApproval != MessageBoxResult.Yes)
                        {
                            notices.Add(
                                row.display_name +
                                ": установка клиентов отменена."
                            );
                            continue;
                        }
                    }
                    bool clientFailed = false;
                    foreach (ClientPlanResult client in toInstall)
                    {
                        SetStatus(
                            view,
                            "Загрузка и проверка " +
                                client.client_id + "...",
                            "info"
                        );
                        try
                        {
                            object installed =
                                await RunClientBootstrapAsync(
                                    bundleRoot,
                                    home,
                                    client.client_id
                                );
                            ClientPlanResult blocked =
                                installed as ClientPlanResult;
                            if (blocked != null)
                            {
                                notices.Add(
                                    row.display_name +
                                    ": клиент заблокирован политикой " +
                                    "downgrade."
                                );
                                clientFailed = true;
                                break;
                            }
                        }
                        catch (Exception exception)
                        {
                            notices.Add(
                                row.display_name +
                                ": клиент не установлен — " +
                                FirstUseful(exception.Message, null)
                            );
                            clientFailed = true;
                            break;
                        }
                        completedOperations++;
                        SetProgress(progress, completedOperations);
                    }
                    if (clientFailed)
                    {
                        continue;
                    }
                    TargetClientPlanResult verified =
                        await RunClientPlanAsync(
                            bundleRoot,
                            home,
                            row.id
                        );
                    if (verified.status != "READY")
                    {
                        notices.Add(
                            row.display_name +
                            ": клиенты не достигли состояния READY."
                        );
                        continue;
                    }
                    row.detected_version = verified.clients.First(plan =>
                        plan.client_id == row.client_id
                    ).detected_version;
                    row.client_state = "ready";
                    clientReady.Add(row);
                }

                if (clientReady.Count == 0)
                {
                    SetStatus(
                        view,
                        "Ни одна база не готова к установке. " +
                            String.Join(" ", notices.ToArray()),
                        "warning"
                    );
                    return;
                }

                InstallerView.SetWorkflowStep(view, 4, false);
                SetStatus(
                    view,
                    "Формируется проверяемый план баз...",
                    "info"
                );
                List<string> planLines = new List<string>();
                List<TargetRow> planned = new List<TargetRow>();
                foreach (TargetRow row in clientReady)
                {
                    WorkflowRunResult result = await RunFoundationAsync(
                        bundleRoot,
                        "plan",
                        row,
                        home
                    );
                    if (result.code == 20)
                    {
                        if (ResolveUnknownDecisions(row, result))
                        {
                            result = await RunFoundationAsync(
                                bundleRoot,
                                "plan",
                                row,
                                home
                            );
                        }
                        else if (IsUnknownDecisionRequired(result))
                        {
                            // Движок отдаёт BLOCKED_USER_DECISION в двух
                            // формах: со списком unknown_entries и ошибкой
                            // без него. Во втором случае диалог выбора не
                            // появлялся никогда, а подсказка предлагала снова
                            // нажать «Сформировать план» — пользователь
                            // получал замкнутый круг без выхода. Показываем
                            // фактическую причину от движка.
                            SetStatus(
                                view,
                                "План для " + row.display_name +
                                    " остановлен движком: " +
                                    UnknownDecisionReason(result) +
                                    " Данные не изменены.",
                                "warning"
                            );
                            notices.Add(
                                row.display_name +
                                ": ожидается решение пользователя."
                            );
                            continue;
                        }
                    }
                    if (result.code != 0 &&
                        ResolveSessionToolCollision(home, result))
                    {
                        // Скилл от прежней установки убран в backup —
                        // планируем заново.
                        result = await RunFoundationAsync(
                            bundleRoot,
                            "plan",
                            row,
                            home
                        );
                    }
                    if (result.code != 0)
                    {
                        SetStatus(
                            view,
                            "План заблокирован для " +
                                row.display_name + ": " +
                                FirstUseful(result.error, result.output),
                            "warning"
                        );
                        notices.Add(
                            row.display_name +
                            ": план базы заблокирован."
                        );
                        continue;
                    }
                    Dictionary<string, object> plan =
                        new JavaScriptSerializer().Deserialize<
                            Dictionary<string, object>
                        >(result.output);
                    // Тот же корень, что и у диалога по неизвестным записям:
                    // JavaScriptSerializer отдаёт массив как ArrayList, а не
                    // object[]. Проверка на типизированный массив всегда была ложной, и
                    // в плане пользователю всегда показывалось «0 файлов».
                    object actionsValue;
                    ICollection actionItems = plan.TryGetValue(
                        "actions",
                        out actionsValue
                    ) && !(actionsValue is string)
                        ? actionsValue as ICollection
                        : null;
                    int actions = actionItems == null ? 0 : actionItems.Count;
                    planLines.Add(
                        row.display_name +
                        (String.IsNullOrWhiteSpace(row.latest_base_version)
                            ? " (embedded)"
                            : " base " + row.latest_base_version) +
                        ": " + actions + " файлов, backup и doctor"
                    );
                    planned.Add(row);
                    completedOperations++;
                    SetProgress(progress, completedOperations);
                }
                if (planned.Count == 0)
                {
                    return;
                }
                MessageBoxResult approval = MessageBox.Show(
                    "Будет установлено:\n\n" +
                    String.Join("\n", planLines.ToArray()) +
                    "\n\nАвторизация, сессии, проекты и состояние клиентов " +
                    "останутся без изменений. Продолжить?",
                    "План установки",
                    MessageBoxButton.YesNo,
                    MessageBoxImage.Question
                );
                if (approval != MessageBoxResult.Yes)
                {
                    SetStatus(
                        view,
                        "План сформирован; установка отменена пользователем.",
                        "info"
                    );
                    return;
                }
                InstallerView.SetWorkflowStep(view, 5, false);
                foreach (TargetRow row in planned)
                {
                    SetStatus(
                        view,
                        "Устанавливается " + row.display_name +
                            ": backup и атомарное применение...",
                        "info"
                    );
                    WorkflowRunResult install = await RunFoundationAsync(
                        bundleRoot,
                        "install",
                        row,
                        home
                    );
                    completedOperations++;
                    SetProgress(progress, completedOperations);
                    if (install.code != 0)
                    {
                        notices.Add(
                            row.display_name + ": установка остановлена — " +
                            FirstUseful(
                                install.error,
                                install.output
                            )
                        );
                        continue;
                    }
                    SetStatus(
                        view,
                        "Проверяется " + row.display_name +
                            " через Foundation doctor...",
                        "info"
                    );
                    WorkflowRunResult doctor = await RunFoundationAsync(
                        bundleRoot,
                        "doctor",
                        row,
                        home
                    );
                    completedOperations++;
                    SetProgress(progress, completedOperations);
                    if (doctor.code == 0)
                    {
                        completed.Add(row);
                        continue;
                    }
                    SetStatus(
                        view,
                        "Doctor не пройден для " + row.display_name +
                            "; выполняется автоматический rollback...",
                        "warning"
                    );
                    WorkflowRunResult rollback = await RunFoundationAsync(
                        bundleRoot,
                        "rollback",
                        row,
                        home
                    );
                    notices.Add(
                        rollback.code == 0
                            ? row.display_name +
                                ": doctor не пройден; предыдущая " +
                                "версия восстановлена."
                            : row.display_name +
                                ": критическая ошибка doctor/rollback; " +
                                "используйте Foundation inventory."
                    );
                }
                if (completed.Count == 0)
                {
                    SetStatus(
                        view,
                        "Установка баз не завершена. " +
                            String.Join(" ", notices.ToArray()),
                        "warning"
                    );
                    return;
                }

                InstallerView.SetWorkflowStep(view, 6, false);
                OpenAuthorizationActions(completed);
                SuccessReportResult report = TryWriteSuccessReport(
                    home,
                    completed
                );
                InstallerView.SetWorkflowStep(view, 7, true);
                string noticeText = notices.Count == 0
                    ? ""
                    : " Ограничения: " +
                        String.Join(" ", notices.ToArray());
                SetStatus(
                    view,
                    report.written
                        ? "Установка завершена. Doctor пройден. Отчёт: " +
                            report.path + noticeText
                        : "Установка завершена. Doctor пройден. " +
                            "Локальный отчёт не сохранён: " + report.error +
                            noticeText,
                    "success"
                );
                MessageBox.Show(
                    "Рабочая среда установлена и проверена.\n\n" +
                    "Авторизация выполняется только в самих клиентах.\n" +
                    "Для обновлений используйте $sync-base.\n" +
                    (report.written
                        ? "Локальный отчёт: " + report.path
                        : "Локальный отчёт не сохранён: " + report.error) +
                    (notices.Count == 0
                        ? ""
                        : "\n\nНе завершено:\n" +
                            String.Join("\n", notices.ToArray())),
                    "LLM Foundation",
                    MessageBoxButton.OK,
                    MessageBoxImage.Information
                );
            }
            catch (Exception exception)
            {
                SetStatus(
                    view,
                    "Операция остановлена: " +
                        FirstUseful(exception.Message, null),
                    "warning"
                );
            }
            finally
            {
                if (progress != null)
                {
                    progress.Visibility = Visibility.Collapsed;
                }
                SetBusy(view, catalog, false);
            }
        }

        private static Task<TargetClientPlanResult> RunClientPlanAsync(
            string bundleRoot,
            string home,
            string target
        )
        {
            return Task.Run(delegate
            {
                return ClientBootstrap.PlanTarget(
                    bundleRoot,
                    home,
                    target
                );
            });
        }

        private static Task<BaseReleaseResolution>
            RunBaseReleaseResolveAsync(
                string bundleRoot,
                string home,
                string target
            )
        {
            return Task.Run(delegate
            {
                return BaseReleaseUpdater.ResolveLatestOrFallback(
                    bundleRoot,
                    home,
                    target
                );
            });
        }

        private static Task<object> RunClientBootstrapAsync(
            string bundleRoot,
            string home,
            string clientId
        )
        {
            return Task.Run(delegate
            {
                string staging = Path.Combine(
                    Path.GetFullPath(home),
                    ".llm-foundation",
                    "staging",
                    "clients"
                );
                return ClientBootstrap.Install(
                    bundleRoot,
                    home,
                    clientId,
                    staging
                );
            });
        }

        private static void OpenAuthorizationActions(
            List<TargetRow> targets
        )
        {
            MessageBoxResult open = MessageBox.Show(
                "Базы установлены. Следующий шаг — интерактивная " +
                "авторизация в выбранных клиентах.\n\n" +
                "Codex: войдите через ChatGPT в приложении.\n" +
                "Claude: выполните вход в окне Claude Code.\n" +
                "OpenCode: запустите /connect → OpenAI → " +
                "ChatGPT Plus/Pro.\n\n" +
                "Установщик не читает и не переносит токены. " +
                "Открыть клиенты сейчас?",
                "Интерактивная авторизация",
                MessageBoxButton.YesNo,
                MessageBoxImage.Information
            );
            if (open != MessageBoxResult.Yes)
            {
                return;
            }
            foreach (TargetRow row in targets)
            {
                if (row.id == "codex")
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = "explorer.exe",
                        Arguments =
                            "shell:AppsFolder\\" +
                            "OpenAI.Codex_2p2nqsd0c76g0!App",
                        UseShellExecute = true
                    });
                }
                else
                {
                    string command = row.id == "claude"
                        ? "claude"
                        : "opencode";
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = Environment.GetEnvironmentVariable(
                            "COMSPEC"
                        ) ?? "cmd.exe",
                        Arguments = "/d /k " + command,
                        UseShellExecute = true
                    });
                }
            }
        }

        private static Task<WorkflowRunResult> RunFoundationAsync(
            string bundleRoot,
            string command,
            TargetRow row,
            string home
        )
        {
            return Task.Run(delegate
            {
                string output;
                string error;
                int code = FoundationWorkflow.Run(
                    bundleRoot,
                    command,
                    row.id,
                    home,
                    row.detected_version,
                    row.latest_base_package_path,
                    row.latest_base_manifest_path,
                    row.latest_base_manifest_sha256,
                    row.local_exception_paths,
                    row.confirm_remove_unknown,
                    out output,
                    out error
                );
                return new WorkflowRunResult
                {
                    code = code,
                    output = output,
                    error = error
                };
            });
        }

        private static bool ResolveUnknownDecisions(
            TargetRow row,
            WorkflowRunResult result
        )
        {
            Dictionary<string, object> payload;
            try
            {
                payload = new JavaScriptSerializer().Deserialize<
                    Dictionary<string, object>
                >(result.output);
            }
            catch
            {
                return false;
            }
            object status;
            object unknownValue;
            if (!payload.TryGetValue("status", out status) ||
                !String.Equals(
                    Convert.ToString(status, CultureInfo.InvariantCulture),
                    "BLOCKED_USER_DECISION",
                    StringComparison.Ordinal
                ) ||
                !payload.TryGetValue("unknown_entries", out unknownValue))
            {
                return false;
            }
            // JavaScriptSerializer отдаёт JSON-массивы как ArrayList, а не
            // как object[]: приведение к object[] давало null, и диалог
            // выбора не показывался НИ РАЗУ — пользователь видел либо
            // предложение снова нажать кнопку, либо текст ошибки движка.
            IEnumerable unknownItems = unknownValue as IEnumerable;
            if (unknownItems == null || unknownValue is string)
            {
                return false;
            }
            List<object> unknown = unknownItems.Cast<object>().ToList();
            if (unknown.Count == 0)
            {
                return false;
            }
            List<string> keep = new List<string>();
            List<Dictionary<string, object>> entries =
                new List<Dictionary<string, object>>();
            foreach (object value in unknown)
            {
                Dictionary<string, object> entry =
                    value as Dictionary<string, object>;
                if (entry == null)
                {
                    return false;
                }
                object pathValue;
                string path = entry.TryGetValue("path", out pathValue)
                    ? Convert.ToString(
                        pathValue,
                        CultureInfo.InvariantCulture
                    )
                    : "";
                if (String.IsNullOrWhiteSpace(path))
                {
                    return false;
                }
                entries.Add(entry);
            }
            int legacyCount = entries.Count(entry =>
                IsLegacyFoundationDirectory(EntryText(entry, "path")));
            int preserveCount = entries.Count - legacyCount;
            MessageBoxResult recommended = ShowOwnedMessage(
                "Найдены данные от предыдущей базы и локальные " +
                    "дополнения:\n\n" +
                    "• старая управляемая база: " + legacyCount +
                    " — перенести в backup и заменить;\n" +
                    "• локальные MCP, плагины и другие дополнения: " +
                    preserveCount + " — сохранить как исключения.\n\n" +
                    "Это рекомендуемый безопасный вариант: локальные " +
                    "настройки не удаляются.\n\n" +
                    "Да — применить рекомендуемый вариант.\n" +
                    "Нет — проверить каждый пункт вручную.\n" +
                    "Отмена — остановить без изменений.",
                "Sync Base: миграция предыдущей установки",
                MessageBoxButton.YesNoCancel,
                MessageBoxImage.Warning
            );
            if (recommended == MessageBoxResult.Cancel)
            {
                return false;
            }
            if (recommended == MessageBoxResult.Yes)
            {
                keep.AddRange(entries
                    .Select(entry => EntryText(entry, "path"))
                    .Where(path =>
                        !IsLegacyFoundationDirectory(path)));
            }
            else
            {
                foreach (Dictionary<string, object> entry in entries)
                {
                    string path = EntryText(entry, "path");
                    string kind = EntryText(entry, "kind", "component");
                    string source = EntryText(entry, "source", "unknown");
                    string risk = EntryText(entry, "risk", "UNREVIEWED");
                    MessageBoxResult answer = ShowOwnedMessage(
                        "Найден компонент вне manifest:\n\n" +
                        "Путь: " + path + "\n" +
                        "Тип: " + kind + "\n" +
                        "Источник: " + source + "\n" +
                        "Риск: " + risk + "\n\n" +
                        "Да — удалить из активного профиля с backup.\n" +
                        "Нет — оставить как локальное исключение " +
                        "(drift, повторное подтверждение при следующем Sync).\n" +
                        "Отмена — остановить установку.",
                        "Sync Base: решение пользователя",
                        MessageBoxButton.YesNoCancel,
                        MessageBoxImage.Warning
                    );
                    if (answer == MessageBoxResult.Cancel)
                    {
                        return false;
                    }
                    if (answer == MessageBoxResult.No)
                    {
                        keep.Add(path);
                    }
                }
            }
            row.local_exception_paths = keep;
            row.confirm_remove_unknown = true;
            return true;
        }

        private static string UnknownDecisionReason(
            WorkflowRunResult result
        )
        {
            string message = "";
            try
            {
                Dictionary<string, object> payload =
                    new JavaScriptSerializer().Deserialize<
                        Dictionary<string, object>
                    >(result.output);
                object value;
                if (payload.TryGetValue("message", out value))
                {
                    message = Convert.ToString(
                        value,
                        CultureInfo.InvariantCulture
                    );
                }
                if (String.IsNullOrWhiteSpace(message) &&
                    payload.TryGetValue("code", out value))
                {
                    message = Convert.ToString(
                        value,
                        CultureInfo.InvariantCulture
                    );
                }
            }
            catch
            {
            }
            if (String.IsNullOrWhiteSpace(message))
            {
                message = FirstUseful(result.error, result.output);
            }
            if (String.IsNullOrWhiteSpace(message))
            {
                message = "решение по неизвестным записям не получено";
            }
            return message.Trim();
        }

        // Корни скиллов трёх клиентов (из managed_surface их пакетов):
        // Claude — .claude/skills, Codex — .agents/skills,
        // OpenCode — .config/opencode/skills. Без третьего корня коллизия
        // скилла OpenCode оставалась тупиком (ревью Codex, 2026-09-02).
        // В этапе 3 список должен строиться от манифеста пакета.
        private static readonly string[] SessionToolRoots =
        {
            ".claude/skills",
            ".agents/skills",
            ".config/opencode/skills",
        };

        private static bool ResolveSessionToolCollision(
            string home,
            WorkflowRunResult result
        )
        {
            // Движок отказывается ставить session tool поверх каталога,
            // которого нет в его состоянии и который отличается от пакета:
            // это скилл прежней установки, и затирать его молча нельзя.
            // Для неизвестных записей выбор пользователю предлагается, а
            // здесь возвращался жёсткий отказ без выхода. Предлагаем то же
            // самое решение: перенести в backup и продолжить.
            string toolId = SessionToolCollisionId(result);
            if (String.IsNullOrWhiteSpace(toolId))
            {
                return false;
            }
            string source = FindSessionToolDirectory(home, toolId);
            if (source == null)
            {
                return false;
            }
            MessageBoxResult answer = ShowOwnedMessage(
                "Скилл «" + toolId + "» уже установлен на этом " +
                    "компьютере и отличается от версии в пакете.\n\n" +
                    "Путь: " + source + "\n\n" +
                    "Да — перенести в backup и поставить версию из " +
                    "пакета (копия сохранится, ничего не удаляется).\n" +
                    "Нет — остановить установку и разобраться вручную.",
                "Установка: скилл от прежней установки",
                MessageBoxButton.YesNo,
                MessageBoxImage.Warning
            );
            if (answer != MessageBoxResult.Yes)
            {
                return false;
            }
            try
            {
                string backupRoot = Path.Combine(
                    Path.GetFullPath(home),
                    ".llm-foundation",
                    "backups",
                    "session-tools"
                );
                Directory.CreateDirectory(backupRoot);
                string stamp = DateTime.UtcNow.ToString(
                    "yyyyMMddTHHmmssZ",
                    CultureInfo.InvariantCulture
                );
                Directory.Move(
                    source,
                    Path.Combine(backupRoot, stamp + "-" + toolId)
                );
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static string SessionToolCollisionId(
            WorkflowRunResult result
        )
        {
            const string marker = "Unmanaged session tool collision:";
            foreach (string payload in new[] { result.output, result.error })
            {
                if (String.IsNullOrWhiteSpace(payload))
                {
                    continue;
                }
                int index = payload.IndexOf(marker, StringComparison.Ordinal);
                if (index < 0)
                {
                    continue;
                }
                string tail = payload.Substring(index + marker.Length);
                foreach (char terminator in new[]
                {
                    '"',
                    '\n',
                    '\r',
                })
                {
                    int stop = tail.IndexOf(terminator);
                    if (stop >= 0)
                    {
                        tail = tail.Substring(0, stop);
                    }
                }
                string toolId = tail.Trim();
                // Имя скилла — один безопасный сегмент пути
                if (toolId.Length > 0 &&
                    toolId.Length <= 64 &&
                    toolId.IndexOfAny(new[]
                    {
                        '/',
                        '\\',
                        ':',
                        '.',
                        ' ',
                    }) < 0)
                {
                    return toolId;
                }
            }
            return null;
        }

        private static string FindSessionToolDirectory(
            string home,
            string toolId
        )
        {
            // Корень скиллов задаётся манифестом пакета, поэтому не
            // угадываем его, а ищем фактически существующий каталог.
            string root = Path.GetFullPath(home)
                .TrimEnd(Path.DirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            foreach (string relative in SessionToolRoots)
            {
                string candidate = Path.GetFullPath(
                    Path.Combine(
                        root,
                        relative.Replace('/', Path.DirectorySeparatorChar),
                        toolId
                    )
                );
                if (candidate.StartsWith(
                        root,
                        StringComparison.OrdinalIgnoreCase) &&
                    Directory.Exists(candidate) &&
                    (File.GetAttributes(candidate) &
                        FileAttributes.ReparsePoint) == 0)
                {
                    return candidate;
                }
            }
            return null;
        }

        private static bool IsUnknownDecisionRequired(
            WorkflowRunResult result
        )
        {
            try
            {
                Dictionary<string, object> payload =
                    new JavaScriptSerializer().Deserialize<
                        Dictionary<string, object>
                    >(result.output);
                object status;
                return payload.TryGetValue("status", out status) &&
                    String.Equals(
                        Convert.ToString(
                            status,
                            CultureInfo.InvariantCulture
                        ),
                        "BLOCKED_USER_DECISION",
                        StringComparison.Ordinal
                    );
            }
            catch
            {
                return false;
            }
        }

        private static string EntryText(
            Dictionary<string, object> entry,
            string name,
            string fallback = ""
        )
        {
            object value;
            return entry != null && entry.TryGetValue(name, out value)
                ? Convert.ToString(value, CultureInfo.InvariantCulture)
                : fallback;
        }

        private static bool IsLegacyFoundationDirectory(string path)
        {
            return !String.IsNullOrWhiteSpace(path) &&
                System.Text.RegularExpressions.Regex.IsMatch(
                    path,
                    @"^\.(?:codex|claude)/base/foundation/" +
                        @"[0-9]+\.[0-9]+\.[0-9]+$|" +
                        @"^\.config/opencode/base/foundation/" +
                        @"[0-9]+\.[0-9]+\.[0-9]+$",
                    System.Text.RegularExpressions.RegexOptions.CultureInvariant
                );
        }

        private static MessageBoxResult ShowOwnedMessage(
            string message,
            string title,
            MessageBoxButton buttons,
            MessageBoxImage image
        )
        {
            Window owner = Application.Current == null
                ? null
                : Application.Current.MainWindow;
            return owner == null
                ? MessageBox.Show(message, title, buttons, image)
                : MessageBox.Show(owner, message, title, buttons, image);
        }

        private static void SetProgress(
            ProgressBar progress,
            int value
        )
        {
            if (progress != null)
            {
                progress.Value = Math.Min(progress.Maximum, value);
            }
        }

        private static void SetBusy(
            UserControl view,
            CatalogResult catalog,
            bool busy
        )
        {
            EditionProfile edition = EditionProfile.LoadEmbedded();
            Button primary = view.FindName("PrimaryAction") as Button;
            Button refresh = view.FindName(
                "RefreshEnvironment"
            ) as Button;
            if (primary != null)
            {
                primary.IsEnabled = !busy && catalog.install_enabled;
                primary.Content = busy
                    ? "Выполняется..."
                    : "Сформировать план";
            }
            if (refresh != null)
            {
                refresh.IsEnabled = !busy;
            }
            foreach (TargetRow row in catalog.targets)
            {
                string prefix = row.id == "codex"
                    ? "Codex"
                    : (row.id == "claude" ? "Claude" : "OpenCode");
                CheckBox box = view.FindName(
                    prefix + "Selected"
                ) as CheckBox;
                if (box != null)
                {
                    box.IsEnabled = !busy &&
                        IsInstallableTarget(row, edition);
                }
            }
        }

        private static bool IsInstallableTarget(
            TargetRow row,
            EditionProfile edition
        )
        {
            return row != null &&
                edition != null &&
                row.package_state == "accepted";
        }

        private static void SetStatus(
            UserControl view,
            string message,
            string state
        )
        {
            TextBlock status = view.FindName("StatusText") as TextBlock;
            Border banner = view.FindName("StatusBanner") as Border;
            Color foreground = state == "success"
                ? StatusPalette.Ok
                : (state == "info"
                    ? StatusPalette.Info
                    : StatusPalette.Warn);
            Color background = state == "success"
                ? StatusPalette.OkSurface
                : (state == "info"
                    ? StatusPalette.InfoSurface
                    : StatusPalette.WarnSurface);
            if (status != null)
            {
                status.Text = message;
                status.Foreground = new SolidColorBrush(foreground);
            }
            if (banner != null)
            {
                banner.Background = new SolidColorBrush(background);
            }
        }

        internal static SuccessReportResult TryWriteSuccessReport(
            string home,
            List<TargetRow> targets
        )
        {
            try
            {
                return new SuccessReportResult
                {
                    written = true,
                    path = WriteSuccessReport(home, targets),
                    error = null
                };
            }
            catch (Exception exception)
            {
                return new SuccessReportResult
                {
                    written = false,
                    path = null,
                    error = FirstUseful(exception.Message, null)
                };
            }
        }

        private static string WriteSuccessReport(
            string home,
            List<TargetRow> targets
        )
        {
            string root = Path.Combine(
                Path.GetFullPath(home),
                ".llm-foundation",
                "reports"
            );
            Directory.CreateDirectory(root);
            string name = "install-" +
                DateTime.UtcNow.ToString(
                    "yyyyMMdd-HHmmss",
                    CultureInfo.InvariantCulture
                ) + "Z-" +
                Guid.NewGuid().ToString("N").Substring(0, 8) +
                ".json";
            string path = Path.Combine(root, name);
            object[] installed = targets.Select(row =>
                (object)new Dictionary<string, object>
                {
                    { "target", row.id },
                    { "client_id", row.client_id },
                    { "client_version", row.detected_version },
                    { "result", "DOCTOR_PASS" }
                }
            ).ToArray();
            Dictionary<string, object> report =
                new Dictionary<string, object>
                {
                    { "schema_version", 1 },
                    { "created_at_utc", DateTime.UtcNow.ToString("o") },
                    {
                        "installer_version",
                        Assembly.GetExecutingAssembly()
                            .GetName().Version.ToString(3)
                    },
                    { "targets", installed },
                    {
                        "network_during_install",
                        "official-client-downloads-only"
                    },
                    { "reverse_flow", false },
                    { "result", "PASS" }
                };
            string temporary = path + ".tmp-" +
                Guid.NewGuid().ToString("N");
            File.WriteAllText(
                temporary,
                new JavaScriptSerializer().Serialize(report) + "\n",
                new UTF8Encoding(false)
            );
            File.Move(temporary, path);
            return path;
        }

        private static string FirstUseful(string error, string output)
        {
            string value = !String.IsNullOrWhiteSpace(error)
                ? error
                : output;
            value = (value ?? "неизвестная ошибка").Trim();
            return value.Length <= 220 ? value : value.Substring(0, 220);
        }
    }
}
