using System;
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
    internal static class LaunchCenterActions
    {
        public static void Bind(
            UserControl view,
            string bundleRoot,
            bool interactive
        )
        {
            ListBox targetList = view.FindName(
                "LaunchTargetList"
            ) as ListBox;
            Button launch = view.FindName("LaunchSelected") as Button;
            TextBlock routeStatus = view.FindName(
                "RouteStatus"
            ) as TextBlock;
            TextBlock routeDetail = view.FindName(
                "RouteDetail"
            ) as TextBlock;
            TextBlock evidenceStatus = view.FindName(
                "EvidenceStatus"
            ) as TextBlock;
            Brush idleEvidenceForeground = evidenceStatus == null
                ? null
                : evidenceStatus.Foreground;
            TextBlock rollbackStatus = view.FindName(
                "RollbackStatus"
            ) as TextBlock;
            RadioButton direct = view.FindName(
                "RouteDirect"
            ) as RadioButton;
            RadioButton http = view.FindName(
                "RouteHttp"
            ) as RadioButton;
            RadioButton https = view.FindName(
                "RouteHttps"
            ) as RadioButton;
            RadioButton proxy = view.FindName(
                "ProxyMode"
            ) as RadioButton;
            ComboBox proxyType = view.FindName(
                "ProxyType"
            ) as ComboBox;
            TextBlock selectedClientName = view.FindName(
                "SelectedClientName"
            ) as TextBlock;
            TextBlock selectedRouteName = view.FindName(
                "SelectedRouteName"
            ) as TextBlock;
            TextBlock selectedProviderName = view.FindName(
                "SelectedProviderName"
            ) as TextBlock;
            Button officialLink = view.FindName(
                "LaunchOfficialLink"
            ) as Button;
            Button stopRoute = view.FindName("StopRoute") as Button;
            TextBlock routeScopeStatus = view.FindName(
                "RouteScopeStatus"
            ) as TextBlock;
            if (targetList == null || launch == null)
            {
                return;
            }
            string home = Environment.GetFolderPath(
                Environment.SpecialFolder.UserProfile
            );
            bool applyingSavedRoute = false;
            bool launching = false;
            ListBoxItem activeSelection = null;
            string routePreferenceError = null;
            TextBlock healthStatus = view.FindName(
                "LaunchHealthStatus"
            ) as TextBlock;
            Action<string, bool> setHealth = delegate(string text, bool warning)
            {
                if (healthStatus != null)
                {
                    healthStatus.Text = text;
                    healthStatus.Foreground = new SolidColorBrush(
                        warning
                            ? Color.FromRgb(155, 58, 28)
                            : Color.FromRgb(84, 111, 119)
                    );
                }
            };
            Func<string> selectedTarget = delegate
            {
                ListBoxItem selected =
                    targetList.SelectedItem as ListBoxItem;
                return selected == null ? null : selected.Tag as string;
            };
            Action refreshLabel = delegate
            {
                ListBoxItem selected =
                    targetList.SelectedItem as ListBoxItem;
                string targetId = selected == null
                    ? null
                    : selected.Tag as string;
                launch.IsEnabled = !launching &&
                    !String.IsNullOrWhiteSpace(targetId);
                launch.Content = SelectionLabel(targetId);
                if (selectedClientName != null)
                {
                    selectedClientName.Text =
                        TargetDisplayName(targetId);
                }
                if (selectedProviderName != null)
                {
                    selectedProviderName.Text =
                        TargetProviderName(targetId);
                }
            };
            Action applySavedRoute = delegate
            {
                string targetId = selectedTarget();
                routePreferenceError = null;
                if (interactive &&
                    !String.IsNullOrWhiteSpace(targetId))
                {
                    try
                    {
                        applyingSavedRoute = true;
                        ConnectionUi.ApplyRoute(
                            view,
                            LaunchRouteStore.Resolve(home, targetId)
                        );
                    }
                    catch (Exception exception)
                    {
                        ConnectionUi.ApplyRoute(view, "Direct");
                        routePreferenceError =
                            "Не удалось прочитать правило маршрута: " +
                            exception.Message;
                    }
                    finally
                    {
                        applyingSavedRoute = false;
                    }
                }
            };
            Func<string> selectedRoute = delegate
            {
                if (direct != null && direct.IsChecked == true)
                {
                    return "Direct";
                }
                if (proxy != null && proxy.IsChecked == true)
                {
                    return ConnectionUi.SelectedTag(proxyType) == "HTTPS"
                        ? "SingBoxHttps"
                        : "SingBoxHttp";
                }
                if (http != null && http.IsChecked == true)
                {
                    return "SingBoxHttp";
                }
                if (https != null && https.IsChecked == true)
                {
                    return "SingBoxHttps";
                }
                return "Direct";
            };
            Action refreshRoute = delegate
            {
                string route = selectedRoute();
                string routeLabel = RouteLabel(route);
                if (selectedRouteName != null)
                {
                    selectedRouteName.Text =
                        routeLabel.ToUpperInvariant();
                }
                if (routeDetail != null)
                {
                    routeDetail.Text = RouteScopeDescription(
                        selectedTarget(),
                        route
                    );
                }
                if (routeScopeStatus != null &&
                    !String.IsNullOrWhiteSpace(selectedTarget()))
                {
                    routeScopeStatus.Text = routePreferenceError ??
                        "Маршрут для «" +
                        TargetDisplayName(selectedTarget()) +
                        "»: " + routeLabel + ".";
                }
            };
            Action resetOperationStatus = delegate
            {
                ApplyResolutionFeedback(view, null);
                if (routeStatus != null)
                {
                    routeStatus.Text = RouteLabel(selectedRoute()) +
                        " · не проверен";
                }
                if (evidenceStatus != null)
                {
                    evidenceStatus.Text = "Проверка при запуске";
                    evidenceStatus.ToolTip = null;
                    evidenceStatus.Foreground = idleEvidenceForeground;
                }
                if (rollbackStatus != null)
                {
                    rollbackStatus.Text = "Запуск ещё не выполнялся";
                }
                setHealth("ОЖИДАНИЕ", false);
            };
            targetList.SelectionChanged += delegate
            {
                if (launching)
                {
                    targetList.SelectedItem = activeSelection;
                    return;
                }
                ApplyResolutionFeedback(view, null);
                applySavedRoute();
                refreshLabel();
                refreshRoute();
                resetOperationStatus();
            };
            Action persistRoute = delegate
            {
                if (interactive && !applyingSavedRoute &&
                    !String.IsNullOrWhiteSpace(selectedTarget()))
                {
                    try
                    {
                        LaunchRouteStore.Save(
                            home,
                            selectedTarget(),
                            selectedRoute()
                        );
                        routePreferenceError = null;
                    }
                    catch (Exception exception)
                    {
                        routePreferenceError =
                            "Правило маршрута не сохранено: " +
                            exception.Message;
                    }
                }
            };
            RoutedEventHandler routeChanged = delegate
            {
                if (applyingSavedRoute || launching)
                {
                    return;
                }
                persistRoute();
                refreshRoute();
                resetOperationStatus();
            };
            if (direct != null)
            {
                direct.Checked += routeChanged;
            }
            if (http != null)
            {
                http.Checked += routeChanged;
            }
            if (https != null)
            {
                https.Checked += routeChanged;
            }
            if (proxy != null)
            {
                proxy.Checked += routeChanged;
            }
            if (proxyType != null)
            {
                proxyType.SelectionChanged += delegate
                {
                    if (applyingSavedRoute || launching)
                    {
                        return;
                    }
                    persistRoute();
                    refreshRoute();
                    resetOperationStatus();
                };
            }
            applySavedRoute();
            refreshLabel();
            refreshRoute();
            resetOperationStatus();
            if (!interactive)
            {
                return;
            }
            if (officialLink != null)
            {
                officialLink.Click += delegate
                {
                    string officialUrl = officialLink.Tag as string;
                    if (!String.Equals(
                            officialUrl,
                            VsCodeIntegration.OfficialMarketplaceUrl,
                            StringComparison.Ordinal))
                    {
                        return;
                    }
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = officialUrl,
                        UseShellExecute = true
                    });
                };
            }
            launch.Click += async delegate
            {
                if (launching)
                {
                    return;
                }
                ListBoxItem selected =
                    targetList.SelectedItem as ListBoxItem;
                string targetId = selected == null
                    ? null
                    : selected.Tag as string;
                if (String.IsNullOrWhiteSpace(targetId))
                {
                    return;
                }
                string route = selectedRoute();
                Button connectionTest = view.FindName(
                    "TestConnection"
                ) as Button;
                if (connectionTest != null && Object.Equals(
                        connectionTest.ReadLocalValue(
                            UIElement.IsEnabledProperty
                        ),
                        false))
                {
                    SetLaunchMessage(view,
                        "Дождитесь завершения проверки подключения.", true);
                    return;
                }
                launching = true;
                activeSelection = selected;
                Dictionary<Control, object> enabledState =
                    SuspendLaunchControls(view);
                bool launchStarted = false;
                refreshLabel();
                ApplyResolutionFeedback(view, null);
                resetOperationStatus();
                setHealth("ПРОВЕРКА", false);
                try
                {
                    EditionProfile edition = EditionProfile.LoadEmbedded();
                    LaunchTargetResolution resolution =
                        LaunchTargetResolver.Resolve(
                            edition,
                            bundleRoot,
                            home,
                            targetId
                        );
                    ApplyResolutionFeedback(view, resolution);
                    if (resolution.status != "RESOLVED")
                    {
                        SetLaunchMessage(view,
                            resolution.action ?? resolution.reason, true);
                        setHealth("НУЖНО ВНИМАНИЕ", true);
                        if (routeStatus != null)
                        {
                            routeStatus.Text = RouteLabel(route) +
                                " · запуск не выполнен";
                        }
                        return;
                    }
                    setHealth("ЗАПУСК", false);
                    if (stopRoute != null)
                    {
                        stopRoute.IsEnabled = false;
                    }
                    if (routeStatus != null)
                    {
                        routeStatus.Text = RouteLabel(route) + " · запуск";
                    }
                    launchStarted = true;
                    Task<LauncherSessionResult> launchTask = Task.Run(
                        () => ClientLauncher.StartAndWait(
                            resolution,
                            route,
                            bundleRoot,
                            home
                        )
                    );
                    bool singBoxRoute =
                        route == "SingBoxHttp" ||
                        route == "SingBoxHttps";
                    if (singBoxRoute && stopRoute != null)
                    {
                        while (!launchTask.IsCompleted &&
                            !ClientLauncher.HasActiveRoute())
                        {
                            await Task.Delay(50);
                        }
                        stopRoute.IsEnabled =
                            ClientLauncher.HasActiveRoute();
                    }
                    LauncherSessionResult result = await launchTask;
                    if (result == null)
                    {
                        throw new InvalidOperationException(
                            "Запуск не вернул результат."
                        );
                    }
                    if (routeStatus != null)
                    {
                        routeStatus.Text = RouteLabel(result.transport) +
                            " · " + ResultLabel(result.status);
                    }
                    bool warning = result.status != "PASS" ||
                        !result.cleanup_verified;
                    SetLaunchMessage(view, LaunchResultMessage(result), warning);
                    setHealth(warning ? "ОШИБКА" : "ЗАПУСК ВЫПОЛНЕН", warning);
                    if (rollbackStatus != null)
                    {
                        rollbackStatus.Text = result.cleanup_verified
                            ? "Очистка подтверждена"
                            : "Очистка не подтверждена";
                    }
                }
                catch (Exception exception)
                {
                    if (routeStatus != null)
                    {
                        routeStatus.Text = RouteLabel(route) + " · ошибка";
                    }
                    SetLaunchMessage(view,
                        "Запуск не выполнен: " + exception.Message, true);
                    setHealth("ОШИБКА", true);
                    if (rollbackStatus != null)
                    {
                        rollbackStatus.Text = launchStarted
                            ? "Очистка не подтверждена"
                            : "Запуск ещё не выполнялся";
                    }
                }
                finally
                {
                    launching = false;
                    activeSelection = null;
                    RestoreLaunchControls(enabledState);
                    if (stopRoute != null)
                    {
                        stopRoute.IsEnabled = ClientLauncher.HasActiveRoute();
                    }
                    refreshLabel();
                }
            };
        }

        private static Dictionary<Control, object> SuspendLaunchControls(
            UserControl view
        )
        {
            Dictionary<Control, object> state =
                new Dictionary<Control, object>();
            foreach (string name in new[] {
                "LaunchTargetList", "RouteDirect", "RouteHttp", "RouteHttps",
                "ProxyMode", "ProxyType", "ProxyAuth", "ProxyHost",
                "ProxyPort", "ProxyUsername", "ProxyPassword", "SaveConnection",
                "TestConnection", "ResetRoute", "OpenChromeProxy"
            })
            {
                Control control = view.FindName(name) as Control;
                if (control != null)
                {
                    state[control] = control.ReadLocalValue(
                        UIElement.IsEnabledProperty
                    );
                    control.IsEnabled = false;
                }
            }
            return state;
        }

        private static void RestoreLaunchControls(
            Dictionary<Control, object> state
        )
        {
            foreach (KeyValuePair<Control, object> item in state)
            {
                if (item.Value == DependencyProperty.UnsetValue)
                {
                    item.Key.ClearValue(UIElement.IsEnabledProperty);
                }
                else
                {
                    item.Key.SetValue(UIElement.IsEnabledProperty, item.Value);
                }
                item.Key.CoerceValue(UIElement.IsEnabledProperty);
            }
        }

        private static void SetLaunchMessage(
            UserControl view,
            string message,
            bool warning
        )
        {
            TextBlock evidence = view.FindName("EvidenceStatus") as TextBlock;
            if (evidence != null)
            {
                evidence.Text = message;
                evidence.ToolTip = message;
                evidence.Foreground = new SolidColorBrush(
                    warning
                        ? Color.FromRgb(252, 122, 77)
                        : Color.FromRgb(119, 203, 185)
                );
            }
            TextBlock guidance = view.FindName("LaunchGuidance") as TextBlock;
            if (guidance != null)
            {
                guidance.Text = warning ? message : "";
                guidance.Visibility = warning
                    ? Visibility.Visible
                    : Visibility.Collapsed;
            }
        }

        internal static string RouteScopeDescription(
            string targetId,
            string route
        )
        {
            if (route == "Direct")
            {
                return "Только этот клиент запускается напрямую";
            }
            if (String.Equals(
                    targetId,
                    "codex-desktop",
                    StringComparison.Ordinal))
            {
                return "Store Codex требует временный системный proxy; " +
                    "на время сеанса он может затронуть другие приложения";
            }
            return "Proxy передаётся только процессу выбранного клиента";
        }

        internal static string LaunchResultMessage(
            LauncherSessionResult result
        )
        {
            if (result == null)
            {
                return "Нет результата запуска.";
            }
            if (String.IsNullOrWhiteSpace(result.reason))
            {
                return result.status == "PASS"
                    ? (result.cleanup_verified
                        ? "Запуск выполнен."
                        : "Запуск выполнен; очистка маршрута не подтверждена.")
                    : "Запуск не выполнен (" +
                        (result.status ?? "UNKNOWN") + ").";
            }
            if (String.Equals(
                    result.reason,
                    "APPX_ALREADY_RUNNING",
                    StringComparison.Ordinal))
            {
                return "Codex уже запущен. Полностью закройте Codex и прежний " +
                    "K7 launcher, затем повторите запуск.";
            }
            if (String.Equals(
                    result.reason,
                    "SYSTEM_PROXY_CHANGED_EXTERNALLY",
                    StringComparison.Ordinal))
            {
                return "Системный proxy изменён после прежнего запуска. " +
                    "Нажмите «Сбросить маршрут»: текущие настройки будут " +
                    "сохранены, зависшая запись — архивирована.";
            }
            if (String.Equals(
                    result.reason,
                    "SYSTEM_PROXY_LEASE_BUSY",
                    StringComparison.Ordinal))
            {
                return "Маршрут уже занят другим Launch Center. Закройте его " +
                    "или остановите маршрут и повторите запуск.";
            }
            return result.reason;
        }

        internal static void ApplyResolutionFeedback(
            UserControl view,
            LaunchTargetResolution resolution
        )
        {
            TextBlock guidance = view.FindName(
                "LaunchGuidance"
            ) as TextBlock;
            Button officialLink = view.FindName(
                "LaunchOfficialLink"
            ) as Button;
            string action = resolution == null
                ? null
                : resolution.action;
            string officialUrl = resolution == null
                ? null
                : resolution.official_url;
            if (guidance != null)
            {
                guidance.Text = action ?? "";
                guidance.Visibility = String.IsNullOrWhiteSpace(action)
                    ? Visibility.Collapsed
                    : Visibility.Visible;
            }
            if (officialLink != null)
            {
                bool exactOfficialUrl = String.Equals(
                    officialUrl,
                    VsCodeIntegration.OfficialMarketplaceUrl,
                    StringComparison.Ordinal
                );
                officialLink.Tag = exactOfficialUrl
                    ? officialUrl
                    : null;
                officialLink.Visibility = exactOfficialUrl
                    ? Visibility.Visible
                    : Visibility.Collapsed;
            }
        }

        internal static Dictionary<string, object>
            DescribeResolutionFeedback(
                UserControl view,
                LaunchTargetResolution resolution
            )
        {
            TextBlock guidance = view.FindName(
                "LaunchGuidance"
            ) as TextBlock;
            Button officialLink = view.FindName(
                "LaunchOfficialLink"
            ) as Button;
            return new Dictionary<string, object>
            {
                {
                    "resolution_reason",
                    resolution == null ? null : resolution.reason
                },
                {
                    "action_text",
                    guidance == null ? null : guidance.Text
                },
                {
                    "action_visibility",
                    guidance == null
                        ? null
                        : guidance.Visibility.ToString()
                },
                {
                    "official_url",
                    officialLink == null
                        ? null
                        : officialLink.Tag as string
                },
                {
                    "official_link_visibility",
                    officialLink == null
                        ? null
                        : officialLink.Visibility.ToString()
                },
                {
                    "official_link_content",
                    officialLink == null
                        ? null
                        : Convert.ToString(
                            officialLink.Content,
                            CultureInfo.InvariantCulture
                        )
                }
            };
        }

        internal static bool SelectTarget(
            UserControl view,
            string targetId
        )
        {
            ListBox targetList = view.FindName(
                "LaunchTargetList"
            ) as ListBox;
            if (targetList == null)
            {
                return false;
            }
            foreach (object candidate in targetList.Items)
            {
                ListBoxItem item = candidate as ListBoxItem;
                if (item != null && String.Equals(
                        item.Tag as string,
                        targetId,
                        StringComparison.Ordinal
                    ))
                {
                    targetList.SelectedItem = item;
                    item.ApplyTemplate();
                    return true;
                }
            }
            return false;
        }

        internal static Dictionary<string, object> DescribeSelection(
            UserControl view
        )
        {
            ListBox targetList = view.FindName(
                "LaunchTargetList"
            ) as ListBox;
            Button launch = view.FindName("LaunchSelected") as Button;
            TextBlock client = view.FindName(
                "SelectedClientName"
            ) as TextBlock;
            TextBlock provider = view.FindName(
                "SelectedProviderName"
            ) as TextBlock;
            TextBlock route = view.FindName(
                "SelectedRouteName"
            ) as TextBlock;
            TextBlock evidence = view.FindName(
                "EvidenceStatus"
            ) as TextBlock;
            ListBoxItem selected = targetList == null
                ? null
                : targetList.SelectedItem as ListBoxItem;
            Border frame = null;
            if (selected != null)
            {
                selected.ApplyTemplate();
                frame = selected.Template == null
                    ? null
                    : selected.Template.FindName(
                        "SelectionFrame",
                        selected
                    ) as Border;
            }
            bool visible = frame != null &&
                frame.BorderThickness.Left >= 2 &&
                frame.BorderBrush != null &&
                frame.BorderBrush != Brushes.Transparent;
            Dictionary<string, object> state =
                new Dictionary<string, object>();
            state["selected_target"] = selected == null
                ? null
                : selected.Tag as string;
            state["button_content"] = launch == null
                ? null
                : Convert.ToString(
                    launch.Content,
                    CultureInfo.InvariantCulture
                );
            state["button_enabled"] =
                launch != null && launch.IsEnabled;
            state["selection_visual"] =
                visible ? "VISIBLE" : "MISSING";
            state["client_display"] =
                client == null ? null : client.Text;
            state["provider_display"] =
                provider == null ? null : provider.Text;
            state["route_display"] =
                route == null ? null : route.Text;
            state["evidence_status"] =
                evidence == null ? null : evidence.Text;
            return state;
        }

        private static string SelectionLabel(string targetId)
        {
            if (String.IsNullOrWhiteSpace(targetId))
            {
                return "Выберите клиент";
            }
            if (String.Equals(
                    targetId,
                    "chrome-browser",
                    StringComparison.Ordinal))
            {
                return "Запустить Chrome →";
            }
            if (String.Equals(
                    targetId,
                    "vscode-codex",
                    StringComparison.Ordinal))
            {
                return "Запустить VS Code →";
            }
            if (targetId.StartsWith(
                    "codex",
                    StringComparison.Ordinal
                ))
            {
                return "Запустить Codex →";
            }
            if (targetId.StartsWith(
                    "claude",
                    StringComparison.Ordinal
                ))
            {
                return "Запустить Claude →";
            }
            if (targetId.StartsWith(
                    "opencode",
                    StringComparison.Ordinal
                ))
            {
                return "Запустить OpenCode →";
            }
            return "Запустить выбранный клиент →";
        }

        private static string TargetDisplayName(string targetId)
        {
            if (String.IsNullOrWhiteSpace(targetId))
            {
                return "НЕ ВЫБРАНО";
            }
            if (String.Equals(
                    targetId,
                    "chrome-browser",
                    StringComparison.Ordinal))
            {
                return "GOOGLE CHROME";
            }
            if (String.Equals(
                    targetId,
                    "vscode-codex",
                    StringComparison.Ordinal))
            {
                return "VS CODE — CODEX";
            }
            if (targetId.StartsWith(
                    "codex",
                    StringComparison.Ordinal
                ))
            {
                return "CODEX";
            }
            if (targetId.StartsWith(
                    "claude",
                    StringComparison.Ordinal
                ))
            {
                return "CLAUDE";
            }
            return "OPENCODE CLI";
        }

        private static string TargetProviderName(string targetId)
        {
            if (String.IsNullOrWhiteSpace(targetId))
            {
                return "НЕ ВЫБРАН";
            }
            if (String.Equals(
                    targetId,
                    "chrome-browser",
                    StringComparison.Ordinal))
            {
                return "ВЫБРАННЫЙ ПРОКСИ";
            }
            if (String.Equals(
                    targetId,
                    "vscode-codex",
                    StringComparison.Ordinal))
            {
                return "OPENAI";
            }
            if (targetId.StartsWith(
                    "codex",
                    StringComparison.Ordinal
                ))
            {
                return "OPENAI";
            }
            if (targetId.StartsWith(
                    "claude",
                    StringComparison.Ordinal
                ))
            {
                return "ANTHROPIC";
            }
            return "ВЫБИРАЕТ КЛИЕНТ";
        }

        private static string RouteLabel(string route)
        {
            switch ((route ?? "").ToLowerInvariant())
            {
                case "direct":
                    return "Напрямую";
                case "singboxhttp":
                    return "SingBox HTTP";
                case "singboxhttps":
                    return "SingBox HTTPS";
                default:
                    return route ?? "";
            }
        }

        private static string ResultLabel(string status)
        {
            return String.Equals(
                    status,
                    "PASS",
                    StringComparison.OrdinalIgnoreCase
                )
                ? "запуск выполнен"
                : "ошибка";
        }
    }
}
