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
                launch.IsEnabled = !String.IsNullOrWhiteSpace(targetId);
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
                if (evidenceStatus != null)
                {
                    evidenceStatus.Text = String.Equals(
                        targetId,
                        "vscode-codex",
                        StringComparison.Ordinal
                    )
                        ? "Локальный ID OpenAI.chatgpt будет " +
                            "обнаружен при запуске"
                        : "Пакет проверен";
                }
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
                        if (routeScopeStatus != null)
                        {
                            routeScopeStatus.Text =
                                "Не удалось прочитать правило маршрута: " +
                                exception.Message;
                        }
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
                if (routeStatus != null)
                {
                    routeStatus.Text = routeLabel + " · готово";
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
                    routeScopeStatus.Text =
                        "Сохранено для «" +
                        TargetDisplayName(selectedTarget()) +
                        "»: " + routeLabel + ".";
                }
            };
            targetList.SelectionChanged += delegate
            {
                ApplyResolutionFeedback(view, null);
                refreshLabel();
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
                    }
                    catch (Exception exception)
                    {
                        if (routeScopeStatus != null)
                        {
                            routeScopeStatus.Text =
                                "Правило маршрута не сохранено: " +
                                exception.Message;
                        }
                    }
                }
            };
            RoutedEventHandler routeChanged = delegate
            {
                persistRoute();
                refreshRoute();
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
                    persistRoute();
                    refreshRoute();
                };
            }
            refreshLabel();
            refreshRoute();
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
                    if (evidenceStatus != null)
                    {
                        evidenceStatus.Text =
                            resolution.action ?? resolution.reason;
                        evidenceStatus.Foreground = new SolidColorBrush(
                            Color.FromRgb(252, 122, 77)
                        );
                    }
                    return;
                }
                launch.IsEnabled = false;
                if (stopRoute != null)
                {
                    stopRoute.IsEnabled = false;
                }
                if (routeStatus != null)
                {
                    routeStatus.Text = RouteLabel(route) +
                        " · запуск";
                }
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
                if (routeStatus != null)
                {
                    routeStatus.Text = RouteLabel(result.transport) +
                        " · " + ResultLabel(result.status);
                }
                if (evidenceStatus != null)
                {
                    evidenceStatus.Text = LaunchResultMessage(result);
                    evidenceStatus.Foreground = new SolidColorBrush(
                        result.status == "PASS"
                            ? Color.FromRgb(119, 203, 185)
                            : Color.FromRgb(252, 122, 77)
                    );
                }
                if (rollbackStatus != null)
                {
                    rollbackStatus.Text = result.cleanup_verified
                        ? "Очистка подтверждена"
                        : "Очистка не подтверждена";
                }
                if (stopRoute != null)
                {
                    stopRoute.IsEnabled = false;
                }
                refreshLabel();
            };
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
            if (result == null || String.IsNullOrWhiteSpace(result.reason))
            {
                return "Точный клиент проверен";
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
                ? "готово"
                : "ошибка";
        }
    }
}
