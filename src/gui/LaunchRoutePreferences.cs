using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class LaunchRoutePreferences
    {
        public int schema_version { get; set; }
        public Dictionary<string, string> target_routes { get; set; }
    }

    internal sealed class LaunchRouteSelection
    {
        public string status { get; set; }
        public string target_id { get; set; }
        public string route { get; set; }
    }

    internal static class LaunchRouteStore
    {
        private static readonly HashSet<string> Routes =
            new HashSet<string>(
                new[]
                {
                    "Direct",
                    "SingBoxHttp",
                    "SingBoxHttps"
                },
                StringComparer.Ordinal
            );
        private static readonly Regex SafeTargetId = new Regex(
            @"^[a-z0-9][a-z0-9-]{0,63}$",
            RegexOptions.CultureInvariant
        );

        public static LaunchRoutePreferences Load(string home)
        {
            string path = StatePath(home);
            if (!File.Exists(path))
            {
                return Empty();
            }
            LaunchRoutePreferences value = new JavaScriptSerializer()
                .Deserialize<LaunchRoutePreferences>(
                    File.ReadAllText(path, new UTF8Encoding(false, true))
                );
            // Режим VPN убран (решение владельца 2026-09-02). На уже принятых
            // станциях лежат маршруты, сохранённые прежними сборками:
            // без миграции Validate отверг бы весь файл, и вместе с VPN
            // пропал бы нужный SingBox-маршрут соседней цели.
            if (value != null && value.target_routes != null)
            {
                foreach (string targetId in value.target_routes.Keys.ToList())
                {
                    if (String.Equals(
                            value.target_routes[targetId],
                            "VPN",
                            StringComparison.Ordinal))
                    {
                        value.target_routes[targetId] = "Direct";
                    }
                }
            }
            return Validate(value);
        }

        public static LaunchRouteSelection Save(
            string home,
            string targetId,
            string route
        )
        {
            ValidateTarget(targetId);
            ValidateRoute(route);
            LaunchRoutePreferences value = Load(home);
            value.target_routes[targetId] = route;
            value.target_routes = value.target_routes
                .OrderBy(row => row.Key, StringComparer.Ordinal)
                .ToDictionary(
                    row => row.Key,
                    row => row.Value,
                    StringComparer.Ordinal
                );
            string path = StatePath(home);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            WriteAtomic(
                path,
                new UTF8Encoding(false).GetBytes(
                    new JavaScriptSerializer().Serialize(value) + "\n"
                )
            );
            return new LaunchRouteSelection
            {
                status = "SAVED",
                target_id = targetId,
                route = route
            };
        }

        public static string Resolve(string home, string targetId)
        {
            ValidateTarget(targetId);
            string route;
            return Load(home).target_routes.TryGetValue(targetId, out route)
                ? route
                : "Direct";
        }

        private static LaunchRoutePreferences Empty()
        {
            return new LaunchRoutePreferences
            {
                schema_version = 1,
                target_routes = new Dictionary<string, string>(
                    StringComparer.Ordinal
                )
            };
        }

        private static LaunchRoutePreferences Validate(
            LaunchRoutePreferences value
        )
        {
            if (value == null || value.schema_version != 1 ||
                value.target_routes == null)
            {
                throw new ArgumentException(
                    "Launcher route preferences are invalid"
                );
            }
            Dictionary<string, string> normalized =
                new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, string> row in value.target_routes)
            {
                ValidateTarget(row.Key);
                ValidateRoute(row.Value);
                normalized.Add(row.Key, row.Value);
            }
            value.target_routes = normalized;
            return value;
        }

        private static void ValidateTarget(string targetId)
        {
            if (String.IsNullOrWhiteSpace(targetId) ||
                !SafeTargetId.IsMatch(targetId))
            {
                throw new ArgumentException("Launch target ID is invalid");
            }
        }

        private static void ValidateRoute(string route)
        {
            if (String.IsNullOrWhiteSpace(route) || !Routes.Contains(route))
            {
                throw new ArgumentException("Launch route is invalid");
            }
        }

        private static string StatePath(string home)
        {
            string fullHome = Path.GetFullPath(home);
            if (!Directory.Exists(fullHome))
            {
                throw new ArgumentException("Target home does not exist");
            }
            return Path.Combine(
                fullHome,
                ".llm-foundation",
                "launcher-routes.json"
            );
        }

        private static void WriteAtomic(string path, byte[] payload)
        {
            string temporary = Path.Combine(
                Path.GetDirectoryName(path),
                "." + Path.GetFileName(path) + ".tmp-" +
                Guid.NewGuid().ToString("N")
            );
            try
            {
                File.WriteAllBytes(temporary, payload);
                if (File.Exists(path))
                {
                    File.Replace(temporary, path, null, true);
                }
                else
                {
                    File.Move(temporary, path);
                }
            }
            finally
            {
                if (File.Exists(temporary))
                {
                    File.Delete(temporary);
                }
            }
        }
    }
}
