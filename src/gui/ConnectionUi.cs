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
    internal static class ConnectionUi
    {
        public static bool TrySaveCurrent(
            UserControl view,
            out string error
        )
        {
            return SaveCurrent(ConnectionUiContract.Resolve(view), out error);
        }

        public static void Bind(
            UserControl view,
            string bundleRoot,
            bool loadState
        )
        {
            ConnectionUiContract contract = ConnectionUiContract.Resolve(view);

            Action updateMode = delegate
            {
                bool isProxy = contract.IsProxy;
                contract.ProxySettings.IsEnabled = isProxy;
                contract.ProxySettings.Visibility = isProxy
                    ? Visibility.Visible
                    : Visibility.Collapsed;
                if (isProxy)
                {
                    contract.Status.Text =
                        "Заполните сервер, порт, логин и пароль, затем нажмите " +
                        "«Сохранить и проверить».";
                    contract.Status.Foreground = new SolidColorBrush(
                        Color.FromRgb(49, 87, 199)
                    );
                }
                else
                {
                    contract.Status.Text = contract.Vpn.IsChecked == true
                        ? "VPN: прокси не требуется."
                        : "Напрямую: прокси не используется.";
                    contract.Status.Foreground = new SolidColorBrush(
                        Color.FromRgb(22, 122, 88)
                    );
                }
            };
            RoutedEventHandler checkedHandler = delegate
            {
                updateMode();
            };
            foreach (RadioButton route in contract.Routes)
            {
                route.Checked += checkedHandler;
            }

            SelectionChangedEventHandler updateAuth = delegate
            {
                bool enabled = contract.ProxyAuth == null ||
                    SelectedTag(contract.ProxyAuth) == "UsernamePassword";
                contract.ProxyUsername.IsEnabled = enabled;
                contract.ProxyPassword.IsEnabled = enabled;
            };
            if (contract.ProxyAuth != null)
            {
                contract.ProxyAuth.SelectionChanged += updateAuth;
            }

            bool preserveStatus = false;
            if (loadState)
            {
                try
                {
                    ApplyProfile(
                        ConnectionStore.Load(UserHome()).profile,
                        contract
                    );
                }
                catch (Exception exception)
                {
                    contract.Status.Text = "Сохранённый профиль требует внимания: " +
                        exception.Message;
                    contract.Status.Foreground = new SolidColorBrush(
                        Color.FromRgb(161, 92, 0)
                    );
                    preserveStatus = true;
                }
            }
            if (!preserveStatus)
            {
                updateMode();
            }
            updateAuth(null, null);

            Func<bool> saveCurrent = delegate
            {
                string error;
                return SaveCurrent(contract, out error);
            };
            contract.Save.Click += delegate
            {
                saveCurrent();
            };
            contract.Test.Click += async delegate
            {
                if (!saveCurrent())
                {
                    return;
                }
                contract.Test.IsEnabled = false;
                contract.Save.IsEnabled = false;
                object originalContent = contract.Test.Content;
                string route = contract.IsProxy
                    ? (contract.SelectedProxyType() == "HTTPS"
                        ? "SingBoxHttps"
                        : "SingBoxHttp")
                    : contract.Mode;
                contract.Test.Content = "Проверка…";
                contract.Status.Text = contract.IsProxy
                    ? "Запускаем SingBox и проверяем маршрут сквозным запросом…"
                    : "Проверяем доступ к GitHub через выбранный режим…";
                contract.Status.Foreground = new SolidColorBrush(
                    Color.FromRgb(49, 87, 199)
                );
                try
                {
                    object result = await Task.Run(
                        delegate
                        {
                            return TestConnection(
                                bundleRoot,
                                UserHome(),
                                route,
                                ProductConfig.LoadEmbedded()
                                    .connection_probe_url
                            );
                        }
                    );
                    SingBoxSessionResult singBox =
                        result as SingBoxSessionResult;
                    ConnectionProbeResult connection =
                        result as ConnectionProbeResult;
                    if (singBox != null && singBox.status == "PASS")
                    {
                        contract.Status.Text =
                            "Маршрут SingBox проверен сквозным запросом.";
                        contract.Status.Foreground = new SolidColorBrush(
                            Color.FromRgb(22, 122, 88)
                        );
                    }
                    else if (connection != null &&
                        connection.status == "READY")
                    {
                        contract.Status.Text = "Соединение проверено: " +
                            connection.mode +
                            (connection.uses_proxy
                                ? " / " + connection.proxy_type
                                : "") +
                            " · " + connection.elapsed_ms.ToString(
                                CultureInfo.InvariantCulture
                            ) + " мс.";
                        contract.Status.Foreground = new SolidColorBrush(
                            Color.FromRgb(22, 122, 88)
                        );
                    }
                    else
                    {
                        string reason = singBox != null
                            ? singBox.reason
                            : (connection != null
                                ? connection.error
                                : "CONNECTION_TEST_FAILED");
                        contract.Status.Text =
                            DescribeTestFailure(reason) +
                            (singBox != null &&
                                singBox.cleanup_verified
                                ? " Временная сессия SingBox уже очищена; " +
                                    "следующая проверка начнётся с чистого состояния."
                                : " Нажмите «Сбросить маршрут» перед повтором.");
                        contract.Status.Foreground = new SolidColorBrush(
                            Color.FromRgb(161, 92, 0)
                        );
                    }
                }
                catch (Exception exception)
                {
                    contract.Status.Text = "Проверка не выполнена: " +
                        exception.Message;
                    contract.Status.Foreground = new SolidColorBrush(
                        Color.FromRgb(161, 92, 0)
                    );
                }
                finally
                {
                    contract.Test.Content = originalContent;
                    contract.Test.IsEnabled = true;
                    contract.Save.IsEnabled = true;
                }
            };
            if (contract.Stop != null)
            {
                contract.Stop.IsEnabled = false;
                contract.Stop.Click += async delegate
                {
                    contract.Stop.IsEnabled = false;
                    contract.Status.Text =
                        "Останавливаем маршрут SingBox и восстанавливаем системный прокси…";
                    contract.Status.Foreground = new SolidColorBrush(
                        Color.FromRgb(49, 87, 199)
                    );
                    SingBoxSessionResult stopped = await Task.Run(
                        delegate
                        {
                            return ClientLauncher.StopActiveRoute();
                        }
                    );
                    contract.Status.Text = stopped.cleanup_verified
                        ? "Маршрут SingBox остановлен. Системный прокси восстановлен."
                        : "Маршрут остановлен не полностью: " +
                            (stopped.reason ?? "проверьте системный прокси вручную.");
                    contract.Status.Foreground = new SolidColorBrush(
                        stopped.cleanup_verified
                            ? Color.FromRgb(22, 122, 88)
                            : Color.FromRgb(161, 92, 0)
                    );
                };
            }
            if (contract.Reset != null)
            {
                contract.Reset.IsEnabled = true;
                contract.Reset.Click += async delegate
                {
                    contract.Reset.IsEnabled = false;
                    contract.Test.IsEnabled = false;
                    contract.Save.IsEnabled = false;
                    contract.Status.Text =
                        "Сбрасываем только управляемые маршруты SingBox и " +
                        "восстанавливаем системный прокси…";
                    contract.Status.Foreground = new SolidColorBrush(
                        Color.FromRgb(49, 87, 199)
                    );
                    try
                    {
                        SingBoxSessionResult reset = await Task.Run(
                            delegate
                            {
                                return ClientLauncher.ResetManagedRoute(
                                    UserHome()
                                );
                            }
                        );
                        bool externalProxyPreserved = reset.lifecycle != null &&
                            reset.lifecycle.Contains(
                                "EXTERNAL_PROXY_PRESERVED"
                            );
                        contract.Status.Text = reset.cleanup_verified
                            ? (externalProxyPreserved
                                ? "Сброс завершён. Текущий внешний proxy " +
                                    "сохранён, зависшая запись K-7 архивирована. " +
                                    "Можно запускать заново."
                                : "Сброс завершён. Управляемые сессии " +
                                    "SingBox закрыты, системный proxy " +
                                    "восстановлен. Можно запускать заново.")
                            : "Сброс выполнен не полностью (" +
                                (reset.reason ?? "RESET_FAILED") +
                                "). Закройте другой Launch Center и повторите.";
                        contract.Status.Foreground = new SolidColorBrush(
                            reset.cleanup_verified
                                ? Color.FromRgb(22, 122, 88)
                                : Color.FromRgb(161, 92, 0)
                        );
                    }
                    catch (Exception exception)
                    {
                        contract.Status.Text = "Сброс не выполнен: " +
                            exception.Message;
                        contract.Status.Foreground = new SolidColorBrush(
                            Color.FromRgb(161, 92, 0)
                        );
                    }
                    finally
                    {
                        contract.Reset.IsEnabled = true;
                        contract.Test.IsEnabled = true;
                        contract.Save.IsEnabled = true;
                    }
                };
            }
        }

        internal static string DescribeTestFailure(string reason)
        {
            string stableReason = String.IsNullOrWhiteSpace(reason)
                ? "CONNECTION_TEST_FAILED"
                : reason;
            string action;
            if (stableReason == "RUNTIME_BUNDLE_ARCHIVE_MISSING")
            {
                action = "Распакуйте весь ZIP: архив runtime должен лежать рядом " +
                    "с запускником.";
            }
            else if (
                stableReason == "RUNTIME_ARCHIVE_INTEGRITY_FAILED" ||
                stableReason == "RUNTIME_EXECUTABLE_INTEGRITY_FAILED" ||
                stableReason == "RUNTIME_ARCHIVE_INVALID")
            {
                action = "Архив runtime повреждён. Скачайте установщик заново " +
                    "и полностью распакуйте ZIP.";
            }
            else if (
                stableReason == "RUNTIME_INSTALL_FAILED" ||
                stableReason == "RUNTIME_ALREADY_PRESENT_INVALID" ||
                stableReason == "RUNTIME_LAYOUT_INVALID" ||
                stableReason == "RUNTIME_NOT_INSTALLED" ||
                stableReason == "RUNTIME_VERIFY_FAILED" ||
                stableReason == "RUNTIME_SOURCE_LOCK_INVALID" ||
                stableReason == "RUNTIME_ARCHIVE_ENTRY_UNSAFE")
            {
                action = "Runtime SingBox не удалось установить. Запустите " +
                    "проверку из полностью распакованного ZIP.";
            }
            else if (stableReason == "CONFIG_CHECK_FAILED")
            {
                action = "Проверьте сервер, порт, логин и пароль, сохраните " +
                    "параметры и повторите проверку.";
            }
            else if (
                stableReason == "LOCAL_PROXY_NOT_READY" ||
                stableReason == "LOCAL_PORT_UNAVAILABLE" ||
                stableReason == "RUNTIME_START_FAILED" ||
                stableReason == "RUNTIME_EXITED_BEFORE_READY")
            {
                action = "SingBox не запустил локальный прокси. Закройте другой " +
                    "VPN или прокси и повторите проверку.";
            }
            else if (stableReason == "ROUTE_PROBE_FAILED")
            {
                action = "SingBox запущен, но запрос через него не прошёл. " +
                    "Проверьте сервер, порт, логин, пароль и доступность прокси.";
            }
            else if (stableReason == "PROXY_AUTH_FAILED")
            {
                action = "Прокси отклонил авторизацию. Проверьте логин и пароль.";
            }
            else if (stableReason == "PROXY_ACCESS_DENIED")
            {
                action = "Прокси запретил запрос. Проверьте доступ для этого " +
                    "сервера и учётной записи.";
            }
            else if (stableReason == "PROXY_TLS_FAILED")
            {
                action = "Не удалось установить защищённое соединение с прокси. " +
                    "Проверьте, что выбран тип HTTPS и порт действительно TLS.";
            }
            else if (stableReason == "PROXY_DNS_FAILED")
            {
                action = "Имя прокси-сервера не разрешается. Проверьте адрес.";
            }
            else if (stableReason == "PROXY_TIMEOUT")
            {
                action = "Прокси не ответил за 15 секунд. Проверьте порт и " +
                    "доступность сервера.";
            }
            else if (stableReason == "PROXY_CONNECT_FAILED")
            {
                action = "Соединение с прокси не установлено. Проверьте адрес, " +
                    "порт и блокировку сети.";
            }
            else if (stableReason == "PROXY_UPSTREAM_FAILED")
            {
                action = "Локальный SingBox запустился, но внешний прокси закрыл " +
                    "соединение. Проверьте тип HTTP/HTTPS, порт и учётные данные.";
            }
            else if (
                stableReason == "SESSION_CLEANUP_FAILED" ||
                stableReason == "SECRET_CONFIG_REMOVE_FAILED")
            {
                action = "Не удалось безопасно очистить временную сессию SingBox. " +
                    "Закройте Launch Center, убедитесь, что sing-box.exe завершён, " +
                    "и запустите проверку снова.";
            }
            else
            {
                action = "Повторите проверку. Если ошибка сохраняется, запустите " +
                    "Launch Center из полностью распакованного ZIP.";
            }
            return "Проверка не пройдена (" + stableReason + "). " + action;
        }

        internal static object TestConnection(
            string bundleRoot,
            string home,
            string route,
            string endpoint
        )
        {
            if (route == "SingBoxHttp" ||
                route == "SingBoxHttps")
            {
                return SingBoxSession.TestRoute(
                    bundleRoot,
                    home,
                    route,
                    endpoint
                );
            }
            if (route == "Direct" || route == "VPN")
            {
                return ConnectionProbe.Run(home, endpoint);
            }
            throw new InvalidOperationException(
                "CONNECTION_ROUTE_INVALID"
            );
        }

        internal static bool ApplyRoute(UserControl view, string route)
        {
            ConnectionUiContract contract = ConnectionUiContract.Resolve(view);
            if (route == "Direct")
            {
                contract.Direct.IsChecked = true;
                return true;
            }
            if (route == "VPN")
            {
                contract.Vpn.IsChecked = true;
                return true;
            }
            if (route == "SingBoxHttp")
            {
                if (contract.Http != null)
                {
                    contract.Http.IsChecked = true;
                }
                else
                {
                    contract.Proxy.IsChecked = true;
                    SelectTag(contract.ProxyType, "HTTP");
                }
                return true;
            }
            if (route == "SingBoxHttps")
            {
                if (contract.Https != null)
                {
                    contract.Https.IsChecked = true;
                }
                else
                {
                    contract.Proxy.IsChecked = true;
                    SelectTag(contract.ProxyType, "HTTPS");
                }
                return true;
            }
            return false;
        }

        internal static Dictionary<string, object> DescribeState(
            UserControl view
        )
        {
            ConnectionUiContract contract = ConnectionUiContract.Resolve(view);
            bool proxy = contract.IsProxy;
            TextBlock routeDetail = view.FindName(
                "RouteDetail"
            ) as TextBlock;
            return new Dictionary<string, object>
            {
                { "mode", contract.Mode },
                { "proxy_type", proxy ? contract.SelectedProxyType() : null },
                {
                    "proxy_settings",
                    contract.ProxySettings.Visibility == Visibility.Visible
                        ? "Visible"
                        : "Collapsed"
                },
                {
                    "fields",
                    new List<string>
                    {
                        "server", "port", "login", "password"
                    }
                },
                { "save_enabled", contract.Save.IsEnabled },
                { "test_enabled", contract.Test.IsEnabled },
                {
                    "stop_enabled",
                    contract.Stop != null && contract.Stop.IsEnabled
                },
                {
                    "reset_enabled",
                    contract.Reset != null && contract.Reset.IsEnabled
                },
                {
                    "status_wrapping",
                    contract.Status.TextWrapping.ToString()
                },
                {
                    "singbox_route_count",
                    (contract.Proxy == null ? 0 : 1) +
                    (contract.Http == null ? 0 : 1) +
                    (contract.Https == null ? 0 : 1)
                },
                {
                    "proxy_type_selector",
                    contract.ProxyType != null
                },
                { "status_text", contract.Status.Text },
                {
                    "route_detail",
                    routeDetail == null ? null : routeDetail.Text
                }
            };
        }

        private static bool SaveCurrent(
            ConnectionUiContract contract,
            out string error
        )
        {
            error = null;
            try
            {
                ConnectionProfile profile = BuildProfile(contract);
                using (System.Security.SecureString secure =
                    contract.ProxyPassword.SecurePassword.Copy())
                {
                    secure.MakeReadOnly();
                    ConnectionStateResult result = ConnectionStore.Save(
                        UserHome(),
                        profile,
                        secure.Length > 0 ? secure : null
                    );
                    contract.ProxyPassword.Clear();
                    contract.Status.Text = result.profile.mode == "VPN"
                        ? "VPN сохранён: отсутствие прокси не является ошибкой."
                        : (result.profile.mode == "Direct"
                            ? "Прямое подключение сохранено: прокси отключён."
                            : "Прокси сохранён; пароль защищён Windows DPAPI.");
                    contract.Status.Foreground = new SolidColorBrush(
                        Color.FromRgb(22, 122, 88)
                    );
                }
                return true;
            }
            catch (Exception exception)
            {
                error = exception.Message;
                contract.Status.Text = "Не сохранено: " + error;
                contract.Status.Foreground = new SolidColorBrush(
                    Color.FromRgb(161, 92, 0)
                );
                return false;
            }
        }

        private static ConnectionProfile BuildProfile(
            ConnectionUiContract contract
        )
        {
            if (!contract.IsProxy)
            {
                return new ConnectionProfile
                {
                    schema_version = 1,
                    mode = contract.Mode,
                    proxy = null
                };
            }
            int portValue;
            if (!Int32.TryParse(contract.ProxyPort.Text, out portValue))
            {
                throw new ArgumentException("Порт должен быть числом.");
            }
            string authMode = contract.ProxyAuth == null
                ? (String.IsNullOrWhiteSpace(contract.ProxyUsername.Text)
                    ? "None"
                    : "UsernamePassword")
                : SelectedTag(contract.ProxyAuth);
            return ConnectionStore.Validate(new ConnectionProfile
            {
                schema_version = 1,
                mode = "Proxy",
                proxy = new ProxyProfile
                {
                    type = contract.SelectedProxyType(),
                    host = contract.ProxyHost.Text.Trim(),
                    port = portValue,
                    auth = new ConnectionAuth
                    {
                        mode = authMode,
                        username = authMode == "UsernamePassword"
                            ? contract.ProxyUsername.Text.Trim()
                            : null
                    }
                }
            });
        }

        private static void ApplyProfile(
            ConnectionProfile profile,
            ConnectionUiContract contract
        )
        {
            if (profile.mode == "Direct")
            {
                contract.Direct.IsChecked = true;
                return;
            }
            if (profile.mode == "VPN")
            {
                contract.Vpn.IsChecked = true;
                return;
            }
            ApplyRoute(
                contract.View,
                profile.proxy.type == "HTTPS" ? "SingBoxHttps" : "SingBoxHttp"
            );
            contract.ProxyHost.Text = profile.proxy.host;
            contract.ProxyPort.Text = profile.proxy.port.ToString(
                CultureInfo.InvariantCulture
            );
            if (contract.ProxyAuth != null)
            {
                SelectTag(contract.ProxyAuth, profile.proxy.auth.mode);
            }
            contract.ProxyUsername.Text = profile.proxy.auth.username ?? "";
        }

        internal static string SelectedTag(ComboBox combo)
        {
            ComboBoxItem item = combo == null
                ? null
                : combo.SelectedItem as ComboBoxItem;
            return item == null || item.Tag == null
                ? ""
                : Convert.ToString(item.Tag, CultureInfo.InvariantCulture);
        }

        internal static void SelectTag(ComboBox combo, string value)
        {
            if (combo == null)
            {
                return;
            }
            foreach (object candidate in combo.Items)
            {
                ComboBoxItem item = candidate as ComboBoxItem;
                if (item != null && String.Equals(
                        Convert.ToString(item.Tag, CultureInfo.InvariantCulture),
                        value,
                        StringComparison.Ordinal
                    ))
                {
                    combo.SelectedItem = item;
                    return;
                }
            }
        }

        private static string UserHome()
        {
            return Environment.GetFolderPath(
                Environment.SpecialFolder.UserProfile
            );
        }
    }

    internal sealed class ConnectionUiContract
    {
        public UserControl View { get; private set; }
        public RadioButton Direct { get; private set; }
        public RadioButton Vpn { get; private set; }
        public RadioButton Proxy { get; private set; }
        public RadioButton Http { get; private set; }
        public RadioButton Https { get; private set; }
        public Grid ProxySettings { get; private set; }
        public ComboBox ProxyType { get; private set; }
        public TextBox ProxyHost { get; private set; }
        public TextBox ProxyPort { get; private set; }
        public ComboBox ProxyAuth { get; private set; }
        public TextBox ProxyUsername { get; private set; }
        public PasswordBox ProxyPassword { get; private set; }
        public Button Save { get; private set; }
        public Button Test { get; private set; }
        public Button Stop { get; private set; }
        public Button Reset { get; private set; }
        public TextBlock Status { get; private set; }

        public IEnumerable<RadioButton> Routes
        {
            get
            {
                return new[] { Direct, Vpn, Proxy, Http, Https }
                    .Where(route => route != null);
            }
        }

        public bool IsProxy
        {
            get
            {
                return (Proxy != null && Proxy.IsChecked == true) ||
                    (Http != null && Http.IsChecked == true) ||
                    (Https != null && Https.IsChecked == true);
            }
        }

        public string Mode
        {
            get
            {
                return IsProxy
                    ? "Proxy"
                    : (Vpn.IsChecked == true ? "VPN" : "Direct");
            }
        }

        public string SelectedProxyType()
        {
            if (Https != null && Https.IsChecked == true)
            {
                return "HTTPS";
            }
            if (Http != null && Http.IsChecked == true)
            {
                return "HTTP";
            }
            return ConnectionUi.SelectedTag(ProxyType);
        }

        public static ConnectionUiContract Resolve(UserControl view)
        {
            ConnectionUiContract contract = new ConnectionUiContract();
            contract.View = view;
            contract.Direct = Required<RadioButton>(view, "DirectMode", "RouteDirect");
            contract.Vpn = Required<RadioButton>(view, "VpnMode", "RouteVpn");
            contract.Proxy = Optional<RadioButton>(view, "ProxyMode");
            contract.Http = Optional<RadioButton>(view, "RouteHttp");
            contract.Https = Optional<RadioButton>(view, "RouteHttps");
            if (contract.Proxy == null &&
                (contract.Http == null || contract.Https == null))
            {
                throw new InvalidOperationException(
                    "Не найдены элементы маршрута прокси"
                );
            }
            contract.ProxySettings = Required<Grid>(view, "ProxySettings");
            contract.ProxyType = Optional<ComboBox>(view, "ProxyType");
            contract.ProxyHost = Required<TextBox>(view, "ProxyHost");
            contract.ProxyPort = Required<TextBox>(view, "ProxyPort");
            contract.ProxyAuth = Optional<ComboBox>(view, "ProxyAuth");
            contract.ProxyUsername = Required<TextBox>(view, "ProxyUsername");
            contract.ProxyPassword = Required<PasswordBox>(view, "ProxyPassword");
            contract.Save = Required<Button>(view, "SaveConnection");
            contract.Test = Required<Button>(view, "TestConnection");
            contract.Stop = Optional<Button>(view, "StopRoute");
            contract.Reset = Optional<Button>(view, "ResetRoute");
            contract.Status = Required<TextBlock>(view, "ConnectionStatus");
            return contract;
        }

        private static T Required<T>(UserControl view, params string[] names)
            where T : class
        {
            T control = Optional<T>(view, names);
            if (control == null)
            {
                throw new InvalidOperationException(
                    "Не найден элемент подключения: " + String.Join("/", names)
                );
            }
            return control;
        }

        private static T Optional<T>(UserControl view, params string[] names)
            where T : class
        {
            foreach (string name in names)
            {
                T control = view.FindName(name) as T;
                if (control != null)
                {
                    return control;
                }
            }
            return null;
        }
    }
}
