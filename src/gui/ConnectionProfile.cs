using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Security;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Runtime.InteropServices;

namespace LlmFoundationInstaller
{
    internal sealed class ConnectionAuth
    {
        public string mode { get; set; }
        public string username { get; set; }
    }

    internal sealed class ProxyProfile
    {
        public string type { get; set; }
        public string host { get; set; }
        public int port { get; set; }
        public ConnectionAuth auth { get; set; }
    }

    internal sealed class ConnectionProfile
    {
        public int schema_version { get; set; }
        public string mode { get; set; }
        public ProxyProfile proxy { get; set; }
    }

    internal sealed class ConnectionStateResult
    {
        public string status { get; set; }
        public ConnectionProfile profile { get; set; }
        public string credential_state { get; set; }
    }

    internal sealed class ConnectionProcessState
    {
        public string mode { get; set; }
        public bool uses_proxy { get; set; }
        public string proxy_type { get; set; }
        public string proxy_scheme { get; set; }
        public string auth_mode { get; set; }
        public bool credential_applied { get; set; }
    }

    internal static class ConnectionStore
    {
        private static readonly HashSet<string> Modes =
            new HashSet<string>(
                new[] { "Direct", "Proxy" },
                StringComparer.Ordinal
            );
        private static readonly HashSet<string> ProxyTypes =
            new HashSet<string>(
                new[] { "HTTP", "HTTPS", "SOCKS5" },
                StringComparer.Ordinal
            );
        private static readonly HashSet<string> AuthModes =
            new HashSet<string>(
                new[] { "None", "UsernamePassword" },
                StringComparer.Ordinal
            );
        private static readonly Regex SafeHost = new Regex(
            @"^[A-Za-z0-9._:%\[\]-]+$",
            RegexOptions.CultureInvariant
        );
        private static readonly byte[] Entropy = Encoding.UTF8.GetBytes(
            "llm-foundation-connection-v1"
        );

        public static ConnectionProfile ParseAndValidate(string path)
        {
            if (!File.Exists(path))
            {
                throw new ArgumentException("Connection profile file is missing");
            }
            ConnectionProfile profile = new JavaScriptSerializer()
                .Deserialize<ConnectionProfile>(
                    File.ReadAllText(path, Encoding.UTF8)
                );
            return Validate(profile);
        }

        public static ConnectionProfile Validate(ConnectionProfile profile)
        {
            if (profile == null || profile.schema_version != 1 ||
                !Modes.Contains(profile.mode))
            {
                throw new ArgumentException(
                    "Connection profile schema or mode is invalid"
                );
            }
            if (profile.mode != "Proxy")
            {
                return new ConnectionProfile
                {
                    schema_version = 1,
                    mode = profile.mode,
                    proxy = null
                };
            }
            if (profile.proxy == null ||
                !ProxyTypes.Contains(profile.proxy.type) ||
                String.IsNullOrWhiteSpace(profile.proxy.host) ||
                !SafeHost.IsMatch(profile.proxy.host) ||
                profile.proxy.port < 1 ||
                profile.proxy.port > 65535 ||
                profile.proxy.auth == null ||
                !AuthModes.Contains(profile.proxy.auth.mode))
            {
                throw new ArgumentException("Proxy profile is invalid");
            }
            string username = null;
            if (profile.proxy.auth.mode == "UsernamePassword")
            {
                if (String.IsNullOrWhiteSpace(profile.proxy.auth.username))
                {
                    throw new ArgumentException(
                        "Proxy username is required"
                    );
                }
                username = profile.proxy.auth.username;
            }
            return new ConnectionProfile
            {
                schema_version = 1,
                mode = "Proxy",
                proxy = new ProxyProfile
                {
                    type = profile.proxy.type,
                    host = profile.proxy.host,
                    port = profile.proxy.port,
                    auth = new ConnectionAuth
                    {
                        mode = profile.proxy.auth.mode,
                        username = username
                    }
                }
            };
        }

        public static ConnectionStateResult Save(
            string home,
            ConnectionProfile input,
            string password
        )
        {
            SecureString secure = null;
            if (!String.IsNullOrEmpty(password))
            {
                secure = new SecureString();
                foreach (char character in password)
                {
                    secure.AppendChar(character);
                }
                secure.MakeReadOnly();
            }
            try
            {
                return Save(home, input, secure);
            }
            finally
            {
                if (secure != null)
                {
                    secure.Dispose();
                }
            }
        }

        public static ConnectionStateResult Save(
            string home,
            ConnectionProfile input,
            SecureString password
        )
        {
            ConnectionProfile profile = Validate(input);
            string root = StateRoot(home);
            Directory.CreateDirectory(root);
            string profilePath = Path.Combine(root, "connection.json");
            string credentialPath = Path.Combine(root, "connection.cred");
            byte[] priorProfile = File.Exists(profilePath)
                ? File.ReadAllBytes(profilePath)
                : null;
            byte[] priorCredential = File.Exists(credentialPath)
                ? File.ReadAllBytes(credentialPath)
                : null;
            bool needsPassword = profile.mode == "Proxy" &&
                profile.proxy.auth.mode == "UsernamePassword";
            bool hasNewPassword = password != null && password.Length > 0;
            bool canReuseCredential = needsPassword &&
                !hasNewPassword &&
                priorCredential != null &&
                PriorCredentialMatches(priorProfile, profile);
            if (needsPassword && !hasNewPassword && !canReuseCredential)
            {
                throw new ArgumentException("Proxy password is required");
            }
            try
            {
                if (needsPassword && hasNewPassword)
                {
                    IntPtr pointer = Marshal.SecureStringToBSTR(password);
                    byte[] plain = null;
                    try
                    {
                        int byteCount = Marshal.ReadInt32(pointer, -4);
                        plain = new byte[byteCount];
                        Marshal.Copy(pointer, plain, 0, byteCount);
                        byte[] protectedValue = ProtectedData.Protect(
                            plain,
                            Entropy,
                            DataProtectionScope.CurrentUser
                        );
                        WriteAtomic(
                            credentialPath,
                            Encoding.ASCII.GetBytes(
                                Convert.ToBase64String(protectedValue) + "\n"
                            )
                        );
                        Array.Clear(protectedValue, 0, protectedValue.Length);
                    }
                    finally
                    {
                        if (plain != null)
                        {
                            Array.Clear(plain, 0, plain.Length);
                        }
                        Marshal.ZeroFreeBSTR(pointer);
                    }
                }
                byte[] serialized = Encoding.UTF8.GetBytes(
                    new JavaScriptSerializer().Serialize(profile) + "\n"
                );
                WriteAtomic(profilePath, serialized);
                if (!needsPassword && File.Exists(credentialPath))
                {
                    File.Delete(credentialPath);
                }
            }
            catch
            {
                Restore(profilePath, priorProfile);
                Restore(credentialPath, priorCredential);
                throw;
            }
            return new ConnectionStateResult
            {
                status = "READY",
                profile = profile,
                credential_state = needsPassword ? "protected" : "none"
            };
        }

        public static ConnectionStateResult Load(string home)
        {
            string root = StateRoot(home);
            string profilePath = Path.Combine(root, "connection.json");
            if (!File.Exists(profilePath))
            {
                return new ConnectionStateResult
                {
                    status = "READY",
                    profile = new ConnectionProfile
                    {
                        schema_version = 1,
                        mode = "Direct",
                        proxy = null
                    },
                    credential_state = "none"
                };
            }
            ConnectionProfile stored =
                new JavaScriptSerializer().Deserialize<ConnectionProfile>(
                    File.ReadAllText(profilePath, Encoding.UTF8)
                );
            // Режим VPN убран (решение владельца 2026-09-02): он означал
            // «прокси не нужен», то есть был Direct с другим текстом.
            // Профили, сохранённые прежними сборками, читаем как Direct.
            if (stored != null && stored.mode == "VPN")
            {
                stored.mode = "Direct";
                stored.proxy = null;
            }
            ConnectionProfile profile = Validate(stored);
            bool needsPassword = profile.mode == "Proxy" &&
                profile.proxy.auth.mode == "UsernamePassword";
            string credentialPath = Path.Combine(root, "connection.cred");
            if (needsPassword)
            {
                if (!File.Exists(credentialPath))
                {
                    throw new InvalidOperationException(
                        "Protected proxy credential is missing"
                    );
                }
                byte[] encrypted = Convert.FromBase64String(
                    File.ReadAllText(credentialPath, Encoding.ASCII).Trim()
                );
                byte[] plain = ProtectedData.Unprotect(
                    encrypted,
                    Entropy,
                    DataProtectionScope.CurrentUser
                );
                Array.Clear(plain, 0, plain.Length);
                Array.Clear(encrypted, 0, encrypted.Length);
            }
            return new ConnectionStateResult
            {
                status = "READY",
                profile = profile,
                credential_state = needsPassword ? "protected" : "none"
            };
        }

        public static ConnectionProcessState ConfigureProcessEnvironment(
            string home,
            ProcessStartInfo start
        )
        {
            if (start == null || start.UseShellExecute)
            {
                throw new ArgumentException(
                    "A non-shell child process is required"
                );
            }
            ConnectionStateResult loaded = Load(home);
            ConnectionProfile profile = loaded.profile;
            foreach (string name in new[]
            {
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
                "LLM_FOUNDATION_CONNECTION_MODE"
            })
            {
                start.EnvironmentVariables.Remove(name);
            }
            start.EnvironmentVariables[
                "LLM_FOUNDATION_CONNECTION_MODE"
            ] = profile.mode;
            if (profile.mode != "Proxy")
            {
                start.EnvironmentVariables["NO_PROXY"] = "*";
                return new ConnectionProcessState
                {
                    mode = profile.mode,
                    uses_proxy = false,
                    proxy_type = null,
                    proxy_scheme = null,
                    auth_mode = "None",
                    credential_applied = false
                };
            }

            string scheme = profile.proxy.type == "HTTP"
                ? "http"
                : (profile.proxy.type == "HTTPS"
                    ? "https"
                    : "socks5h");
            bool authenticated =
                profile.proxy.auth.mode == "UsernamePassword";
            string password = null;
            byte[] plain = null;
            byte[] encrypted = null;
            try
            {
                string userInfo = "";
                if (authenticated)
                {
                    string credentialPath = Path.Combine(
                        StateRoot(home),
                        "connection.cred"
                    );
                    if (!File.Exists(credentialPath))
                    {
                        throw new InvalidOperationException(
                            "Protected proxy credential is missing"
                        );
                    }
                    encrypted = Convert.FromBase64String(
                        File.ReadAllText(
                            credentialPath,
                            Encoding.ASCII
                        ).Trim()
                    );
                    plain = ProtectedData.Unprotect(
                        encrypted,
                        Entropy,
                        DataProtectionScope.CurrentUser
                    );
                    password = Encoding.Unicode.GetString(plain);
                    if (String.IsNullOrEmpty(password))
                    {
                        throw new InvalidOperationException(
                            "Protected proxy credential is empty"
                        );
                    }
                    userInfo =
                        Uri.EscapeDataString(profile.proxy.auth.username)
                        + ":"
                        + Uri.EscapeDataString(password)
                        + "@";
                }
                string host = profile.proxy.host;
                if (host.Contains(":") &&
                    !(host.StartsWith("[") && host.EndsWith("]")))
                {
                    host = "[" + host + "]";
                }
                string proxyUri =
                    scheme + "://" + userInfo + host + ":" +
                    profile.proxy.port.ToString(
                        System.Globalization.CultureInfo.InvariantCulture
                    );
                start.EnvironmentVariables["HTTP_PROXY"] = proxyUri;
                start.EnvironmentVariables["HTTPS_PROXY"] = proxyUri;
                if (profile.proxy.type == "SOCKS5")
                {
                    start.EnvironmentVariables["ALL_PROXY"] = proxyUri;
                }
            }
            finally
            {
                password = null;
                if (plain != null)
                {
                    Array.Clear(plain, 0, plain.Length);
                }
                if (encrypted != null)
                {
                    Array.Clear(encrypted, 0, encrypted.Length);
                }
            }
            return new ConnectionProcessState
            {
                mode = "Proxy",
                uses_proxy = true,
                proxy_type = profile.proxy.type,
                proxy_scheme = scheme,
                auth_mode = profile.proxy.auth.mode,
                credential_applied = authenticated
            };
        }

        public static T WithProxyCredential<T>(
            string home,
            Func<ConnectionProfile, string, T> action
        )
        {
            if (action == null)
            {
                throw new ArgumentNullException("action");
            }
            ConnectionProfile profile = Load(home).profile;
            if (profile.mode != "Proxy")
            {
                throw new InvalidOperationException(
                    "A saved proxy profile is required"
                );
            }
            bool authenticated =
                profile.proxy.auth.mode == "UsernamePassword";
            byte[] encrypted = null;
            byte[] plain = null;
            string password = null;
            try
            {
                if (authenticated)
                {
                    string credentialPath = Path.Combine(
                        StateRoot(home),
                        "connection.cred"
                    );
                    encrypted = Convert.FromBase64String(
                        File.ReadAllText(
                            credentialPath,
                            Encoding.ASCII
                        ).Trim()
                    );
                    plain = ProtectedData.Unprotect(
                        encrypted,
                        Entropy,
                        DataProtectionScope.CurrentUser
                    );
                    password = Encoding.Unicode.GetString(plain);
                }
                return action(profile, password);
            }
            finally
            {
                password = null;
                if (plain != null)
                {
                    Array.Clear(plain, 0, plain.Length);
                }
                if (encrypted != null)
                {
                    Array.Clear(encrypted, 0, encrypted.Length);
                }
            }
        }

        private static string StateRoot(string home)
        {
            string fullHome = Path.GetFullPath(home);
            if (!Directory.Exists(fullHome))
            {
                throw new ArgumentException("Target home does not exist");
            }
            return Path.Combine(fullHome, ".llm-foundation");
        }

        private static void WriteAtomic(string path, byte[] payload)
        {
            string parent = Path.GetDirectoryName(path);
            Directory.CreateDirectory(parent);
            string temporary = Path.Combine(
                parent,
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

        private static void Restore(string path, byte[] prior)
        {
            if (prior == null)
            {
                if (File.Exists(path))
                {
                    File.Delete(path);
                }
                return;
            }
            WriteAtomic(path, prior);
        }

        private static bool PriorCredentialMatches(
            byte[] priorProfile,
            ConnectionProfile candidate
        )
        {
            if (priorProfile == null)
            {
                return false;
            }
            try
            {
                ConnectionProfile prior = Validate(
                    new JavaScriptSerializer().Deserialize<ConnectionProfile>(
                        Encoding.UTF8.GetString(priorProfile)
                    )
                );
                return prior.mode == "Proxy" &&
                    prior.proxy.auth.mode == "UsernamePassword" &&
                    String.Equals(
                        prior.proxy.type,
                        candidate.proxy.type,
                        StringComparison.Ordinal
                    ) &&
                    String.Equals(
                        prior.proxy.host,
                        candidate.proxy.host,
                        StringComparison.Ordinal
                    ) &&
                    prior.proxy.port == candidate.proxy.port &&
                    String.Equals(
                        prior.proxy.auth.username,
                        candidate.proxy.auth.username,
                        StringComparison.Ordinal
                    );
            }
            catch
            {
                return false;
            }
        }
    }
}
