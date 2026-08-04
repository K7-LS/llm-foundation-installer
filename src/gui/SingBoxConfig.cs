using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class RoutingDomainCatalog
    {
        public int schema_version { get; set; }
        public List<string> exact_domains { get; set; }
        public List<string> common_suffixes { get; set; }
        public Dictionary<string, List<string>> targets { get; set; }
    }

    internal sealed class SingBoxConfigSummary
    {
        public string status { get; set; }
        public string target_id { get; set; }
        public string route { get; set; }
        public int listen_port { get; set; }
        public bool uses_tls { get; set; }
        public bool uses_auth { get; set; }
        public string route_final { get; set; }
        public bool secret_redacted { get; set; }
    }

    internal static class SingBoxConfig
    {
        private const string RoutingResource =
            "LauncherRoutingDomains.json";

        public static SingBoxConfigSummary WriteTestConfig(
            string bundleRoot,
            string home,
            string targetId,
            string route,
            int listenPort,
            string outputPath
        )
        {
            if (!RuntimeBootstrap.Load(bundleRoot).test_only)
            {
                throw new InvalidOperationException(
                    "Test config export is disabled"
                );
            }
            Dictionary<string, object> document = null;
            SingBoxConfigSummary summary =
                ConnectionStore.WithProxyCredential(
                    home,
                    delegate(
                        ConnectionProfile profile,
                        string password
                    )
                    {
                        document = Create(
                            profile,
                            password,
                            targetId,
                            route,
                            listenPort
                        );
                        return Summary(
                            profile,
                            targetId,
                            route,
                            listenPort
                        );
                    }
                );
            string full = Path.GetFullPath(outputPath);
            string parent = Path.GetDirectoryName(full);
            Directory.CreateDirectory(parent);
            File.WriteAllText(
                full,
                new JavaScriptSerializer().Serialize(document) + "\n",
                new UTF8Encoding(false)
            );
            return summary;
        }

        internal static Dictionary<string, object> Create(
            ConnectionProfile profile,
            string password,
            string targetId,
            string route,
            int listenPort
        )
        {
            if (profile == null ||
                profile.mode != "Proxy" ||
                profile.proxy == null ||
                (route != "SingBoxHttp" &&
                    route != "SingBoxHttps") ||
                listenPort < 1024 ||
                listenPort > 65535)
            {
                throw new ArgumentException(
                    "SingBox configuration request is invalid"
                );
            }
            bool usesTls = route == "SingBoxHttps";
            if ((usesTls && profile.proxy.type != "HTTPS") ||
                (!usesTls && profile.proxy.type != "HTTP"))
            {
                throw new ArgumentException(
                    "SingBox route differs from proxy profile type"
                );
            }
            bool usesAuth =
                profile.proxy.auth.mode == "UsernamePassword";
            if (usesAuth && String.IsNullOrEmpty(password))
            {
                throw new InvalidOperationException(
                    "Protected proxy credential is empty"
                );
            }
            RoutingDomainCatalog routing = LoadRouting();
            List<string> targetSuffixes;
            if (!routing.targets.TryGetValue(
                    targetId,
                    out targetSuffixes))
            {
                throw new ArgumentException(
                    "Launch target has no reviewed routing domain set"
                );
            }
            List<string> suffixes = routing.common_suffixes
                .Concat(targetSuffixes)
                .Distinct(StringComparer.Ordinal)
                .OrderBy(value => value, StringComparer.Ordinal)
                .ToList();

            Dictionary<string, object> upstream =
                new Dictionary<string, object>();
            upstream["type"] = "http";
            upstream["tag"] = "upstream";
            upstream["server"] = profile.proxy.host;
            upstream["server_port"] = profile.proxy.port;
            if (usesAuth)
            {
                upstream["username"] =
                    profile.proxy.auth.username;
                upstream["password"] = password;
            }
            if (usesTls)
            {
                upstream["tls"] = new Dictionary<string, object>
                {
                    { "enabled", true },
                    { "server_name", profile.proxy.host },
                    { "insecure", false },
                    { "alpn", new[] { "http/1.1" } }
                };
            }
            List<object> rules = new List<object>
            {
                new Dictionary<string, object>
                {
                    { "ip_is_private", true },
                    { "action", "route" },
                    { "outbound", "direct" }
                },
                new Dictionary<string, object>
                {
                    { "domain_suffix", suffixes },
                    { "action", "route" },
                    { "outbound", "upstream" }
                },
                new Dictionary<string, object>
                {
                    { "domain", routing.exact_domains },
                    { "action", "route" },
                    { "outbound", "upstream" }
                }
            };
            string finalOutbound = String.Equals(
                    targetId,
                    "chrome-browser",
                    StringComparison.Ordinal)
                ? "upstream"
                : "direct";
            return new Dictionary<string, object>
            {
                {
                    "log",
                    new Dictionary<string, object>
                    {
                        { "level", "warn" },
                        { "timestamp", true }
                    }
                },
                {
                    "inbounds",
                    new object[]
                    {
                        new Dictionary<string, object>
                        {
                            { "type", "mixed" },
                            { "tag", "local-mixed" },
                            { "listen", "127.0.0.1" },
                            { "listen_port", listenPort },
                            { "set_system_proxy", false }
                        }
                    }
                },
                {
                    "outbounds",
                    new object[]
                    {
                        upstream,
                        new Dictionary<string, object>
                        {
                            { "tag", "direct" },
                            { "type", "direct" }
                        }
                    }
                },
                {
                    "route",
                    new Dictionary<string, object>
                    {
                        { "auto_detect_interface", true },
                        { "rules", rules },
                        { "final", finalOutbound }
                    }
                }
            };
        }

        private static SingBoxConfigSummary Summary(
            ConnectionProfile profile,
            string targetId,
            string route,
            int listenPort
        )
        {
            return new SingBoxConfigSummary
            {
                status = "CONFIG_WRITTEN",
                target_id = targetId,
                route = route,
                listen_port = listenPort,
                uses_tls = route == "SingBoxHttps",
                uses_auth =
                    profile.proxy.auth.mode == "UsernamePassword",
                route_final = String.Equals(
                        targetId,
                        "chrome-browser",
                        StringComparison.Ordinal)
                    ? "upstream"
                    : "direct",
                secret_redacted = true
            };
        }

        private static RoutingDomainCatalog LoadRouting()
        {
            using (Stream stream = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream(RoutingResource))
            {
                if (stream == null)
                {
                    throw new InvalidOperationException(
                        "Routing domain resource is missing"
                    );
                }
                using (StreamReader reader = new StreamReader(
                    stream,
                    new UTF8Encoding(false, true)
                ))
                {
                    RoutingDomainCatalog value =
                        new JavaScriptSerializer()
                            .Deserialize<RoutingDomainCatalog>(
                                reader.ReadToEnd()
                            );
                    if (value == null ||
                        value.schema_version != 1 ||
                        value.targets == null ||
                        value.exact_domains == null ||
                        value.common_suffixes == null)
                    {
                        throw new InvalidOperationException(
                            "Routing domain resource is invalid"
                        );
                    }
                    return value;
                }
            }
        }
    }
}
