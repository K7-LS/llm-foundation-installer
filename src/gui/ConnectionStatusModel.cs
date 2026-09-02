using System;
using System.Collections.Generic;
using System.Globalization;

namespace LlmFoundationInstaller
{
    internal sealed class ConnectionStatus
    {
        public string text { get; set; }
        public string tone { get; set; }
    }

    internal static class ConnectionStatusModel
    {
        public const string ToneInfo = "info";
        public const string ToneOk = "ok";
        public const string ToneWarn = "warn";

        private static ConnectionStatus Make(string text, string tone)
        {
            return new ConnectionStatus { text = text, tone = tone };
        }

        public static ConnectionStatus ProxyGuidance()
        {
            return Make(
                "Заполните сервер, порт, логин и пароль, затем нажмите " +
                "«Сохранить и проверить».",
                ToneInfo
            );
        }

        public static ConnectionStatus ModeIdle()
        {
            return Make("Напрямую: прокси не используется.", ToneOk);
        }

        public static ConnectionStatus SavedProfileNeedsAttention(
            string message
        )
        {
            return Make(
                "Сохранённый профиль требует внимания: " + message,
                ToneWarn
            );
        }

        public static ConnectionStatus Testing(bool isProxy)
        {
            return Make(
                isProxy
                    ? "Запускаем SingBox и проверяем маршрут сквозным запросом…"
                    : "Проверяем доступ к GitHub через выбранный режим…",
                ToneInfo
            );
        }

        public static ConnectionStatus SingBoxRoutePass()
        {
            return Make(
                "Маршрут SingBox проверен сквозным запросом.",
                ToneOk
            );
        }

        public static ConnectionStatus ConnectionReady(
            string mode,
            bool usesProxy,
            string proxyType,
            long elapsedMs
        )
        {
            return Make(
                "Соединение проверено: " + mode +
                (usesProxy ? " / " + proxyType : "") +
                " · " + elapsedMs.ToString(CultureInfo.InvariantCulture) +
                " мс.",
                ToneOk
            );
        }

        public static ConnectionStatus TestFailed(
            string reason,
            bool singBoxCleanupVerified,
            bool wasSingBoxSession
        )
        {
            return Make(
                DescribeTestFailure(reason) +
                (wasSingBoxSession && singBoxCleanupVerified
                    ? " Временная сессия SingBox уже очищена; " +
                        "следующая проверка начнётся с чистого состояния."
                    : " Нажмите «Сбросить маршрут» перед повтором."),
                ToneWarn
            );
        }

        public static ConnectionStatus TestException(string message)
        {
            return Make("Проверка не выполнена: " + message, ToneWarn);
        }

        public static ConnectionStatus StoppingRoute()
        {
            return Make(
                "Останавливаем маршрут SingBox и восстанавливаем системный " +
                "прокси…",
                ToneInfo
            );
        }

        public static ConnectionStatus StopResult(
            bool cleanupVerified,
            string reason
        )
        {
            return Make(
                cleanupVerified
                    ? "Маршрут SingBox остановлен. Системный прокси " +
                        "восстановлен."
                    : "Маршрут остановлен не полностью: " +
                        (reason ?? "проверьте системный прокси вручную."),
                cleanupVerified ? ToneOk : ToneWarn
            );
        }

        public static ConnectionStatus ResettingRoutes()
        {
            return Make(
                "Сбрасываем только управляемые маршруты SingBox и " +
                "восстанавливаем системный прокси…",
                ToneInfo
            );
        }

        public static ConnectionStatus ResetResult(
            bool cleanupVerified,
            bool externalProxyPreserved,
            string reason
        )
        {
            return Make(
                cleanupVerified
                    ? (externalProxyPreserved
                        ? "Сброс завершён. Текущий внешний proxy " +
                            "сохранён, зависшая запись K-7 архивирована. " +
                            "Можно запускать заново."
                        : "Сброс завершён. Управляемые сессии " +
                            "SingBox закрыты, системный proxy " +
                            "восстановлен. Можно запускать заново.")
                    : "Сброс выполнен не полностью (" +
                        (reason ?? "RESET_FAILED") +
                        "). Закройте другой Launch Center и повторите.",
                cleanupVerified ? ToneOk : ToneWarn
            );
        }

        public static ConnectionStatus ResetException(string message)
        {
            return Make("Сброс не выполнен: " + message, ToneWarn);
        }

        public static ConnectionStatus Saved(string mode)
        {
            return Make(
                mode == "Direct"
                    ? "Прямое подключение сохранено: прокси отключён."
                    : "Прокси сохранён; пароль защищён Windows DPAPI.",
                ToneOk
            );
        }

        public static ConnectionStatus SaveFailed(string message)
        {
            return Make("Не сохранено: " + message, ToneWarn);
        }

        public static string DescribeTestFailure(string reason)
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
                    "прокси на этом порту и повторите проверку.";
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

        private static readonly string[] KnownFailureReasons = new[]
        {
            "RUNTIME_BUNDLE_ARCHIVE_MISSING",
            "RUNTIME_ARCHIVE_INTEGRITY_FAILED",
            "RUNTIME_INSTALL_FAILED",
            "CONFIG_CHECK_FAILED",
            "LOCAL_PROXY_NOT_READY",
            "ROUTE_PROBE_FAILED",
            "PROXY_AUTH_FAILED",
            "PROXY_ACCESS_DENIED",
            "PROXY_TLS_FAILED",
            "PROXY_DNS_FAILED",
            "PROXY_TIMEOUT",
            "PROXY_CONNECT_FAILED",
            "PROXY_UPSTREAM_FAILED",
            "SESSION_CLEANUP_FAILED",
            "CONNECTION_TEST_FAILED"
        };

        public static Dictionary<string, object> CanonicalCatalog()
        {
            List<Dictionary<string, object>> statuses =
                new List<Dictionary<string, object>>();
            Action<string, ConnectionStatus> add =
                delegate(string key, ConnectionStatus status)
                {
                    statuses.Add(new Dictionary<string, object>
                    {
                        { "key", key },
                        { "text", status.text },
                        { "tone", status.tone }
                    });
                };
            add("proxy_guidance", ProxyGuidance());
            add("mode_idle_direct", ModeIdle());
            add("saved_profile_attention", SavedProfileNeedsAttention(
                "<причина>"
            ));
            add("testing_proxy", Testing(true));
            add("testing_direct", Testing(false));
            add("singbox_route_pass", SingBoxRoutePass());
            add("connection_ready", ConnectionReady("Direct", false, null, 42));
            add("test_failed", TestFailed("CONNECTION_TEST_FAILED", false, false));
            add("test_exception", TestException("<причина>"));
            add("stopping_route", StoppingRoute());
            add("stop_ok", StopResult(true, null));
            add("stop_partial", StopResult(false, null));
            add("resetting_routes", ResettingRoutes());
            add("reset_ok_external_preserved", ResetResult(true, true, null));
            add("reset_ok_managed", ResetResult(true, false, null));
            add("reset_partial", ResetResult(false, false, null));
            add("reset_exception", ResetException("<причина>"));
            add("saved_direct", Saved("Direct"));
            add("saved_proxy", Saved("Proxy"));
            add("save_failed", SaveFailed("<причина>"));

            List<Dictionary<string, object>> failures =
                new List<Dictionary<string, object>>();
            foreach (string reason in KnownFailureReasons)
            {
                failures.Add(new Dictionary<string, object>
                {
                    { "reason", reason },
                    { "text", DescribeTestFailure(reason) }
                });
            }

            return new Dictionary<string, object>
            {
                { "schema_version", 1 },
                { "statuses", statuses },
                { "test_failures", failures }
            };
        }
    }
}
