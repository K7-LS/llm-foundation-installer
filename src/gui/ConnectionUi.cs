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
                    ApplyStatus(
                        contract,
                        ConnectionStatusModel.ProxyGuidance()
                    );
                }
                else
                {
                    ApplyStatus(
                        contract,
                        ConnectionStatusModel.ModeIdle()
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
                    ApplyStatus(
                        contract,
                        ConnectionStatusModel.SavedProfileNeedsAttention(
                            exception.Message
                        )
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
                ApplyStatus(
                    contract,
                    ConnectionStatusModel.Testing(contract.IsProxy)
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
                        ApplyStatus(
                            contract,
                            ConnectionStatusModel.SingBoxRoutePass()
                        );
                    }
                    else if (connection != null &&
                        connection.status == "READY")
                    {
                        ApplyStatus(
                            contract,
                            ConnectionStatusModel.ConnectionReady(
                                connection.mode,
                                connection.uses_proxy,
                                connection.proxy_type,
                                connection.elapsed_ms
                            )
                        );
                    }
                    else
                    {
                        string reason = singBox != null
                            ? singBox.reason
                            : (connection != null
                                ? connection.error
                                : "CONNECTION_TEST_FAILED");
                        ApplyStatus(
                            contract,
                            ConnectionStatusModel.TestFailed(
                                reason,
                                singBox != null &&
                                    singBox.cleanup_verified,
                                singBox != null
                            )
                        );
                    }
                }
                catch (Exception exception)
                {
                    ApplyStatus(
                        contract,
                        ConnectionStatusModel.TestException(
                            exception.Message
                        )
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
                    ApplyStatus(
                        contract,
                        ConnectionStatusModel.StoppingRoute()
                    );
                    SingBoxSessionResult stopped = await Task.Run(
                        delegate
                        {
                            return ClientLauncher.StopActiveRoute();
                        }
                    );
                    ApplyStatus(
                        contract,
                        ConnectionStatusModel.StopResult(
                            stopped.cleanup_verified,
                            stopped.reason
                        )
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
                    ApplyStatus(
                        contract,
                        ConnectionStatusModel.ResettingRoutes()
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
                        ApplyStatus(
                            contract,
                            ConnectionStatusModel.ResetResult(
                                reset.cleanup_verified,
                                externalProxyPreserved,
                                reset.reason
                            )
                        );
                    }
                    catch (Exception exception)
                    {
                        ApplyStatus(
                            contract,
                            ConnectionStatusModel.ResetException(
                                exception.Message
                            )
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

        private static void ApplyStatus(
            ConnectionUiContract contract,
            ConnectionStatus status
        )
        {
            contract.Status.Text = status.text;
            contract.Status.Foreground = new SolidColorBrush(
                StatusToneColor(status.tone)
            );
        }

        private static Color StatusToneColor(string tone)
        {
            return StatusPalette.Tone(tone);
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
            if (route == "Direct")
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
                    ApplyStatus(
                        contract,
                        ConnectionStatusModel.Saved(result.profile.mode)
                    );
                }
                return true;
            }
            catch (Exception exception)
            {
                error = exception.Message;
                ApplyStatus(
                    contract,
                    ConnectionStatusModel.SaveFailed(error)
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
                return new[] { Direct, Proxy, Http, Https }
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
                return IsProxy ? "Proxy" : "Direct";
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
