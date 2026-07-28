using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class LauncherSessionResult
    {
        public string status { get; set; }
        public string transport { get; set; }
        public bool uses_proxy { get; set; }
        public bool cleanup_verified { get; set; }
        public int process_exit_code { get; set; }
        public string target_id { get; set; }
        public string executable_path { get; set; }
        public string executable_sha256 { get; set; }
        public string reason { get; set; }
    }

    internal static class ClientLauncher
    {
        private static readonly string[] ProxyVariables = new[]
        {
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy"
        };

        public static LauncherSessionResult StartAndWait(
            LaunchTargetResolution target,
            string route
        )
        {
            if (target == null || target.status != "RESOLVED")
            {
                return Failed(
                    target,
                    route,
                    "EXACT_TARGET_NOT_RESOLVED",
                    true,
                    -1
                );
            }
            if (route != "Direct" && route != "VPN")
            {
                return Failed(
                    target,
                    route,
                    "ROUTE_NOT_IMPLEMENTED",
                    true,
                    -1
                );
            }
            Process process = null;
            try
            {
                ProcessStartInfo start = new ProcessStartInfo
                {
                    FileName = target.executable_path,
                    WorkingDirectory = Path.GetDirectoryName(
                        target.executable_path
                    ),
                    UseShellExecute = false,
                    CreateNoWindow = target.role == "cli"
                };
                foreach (string variable in ProxyVariables)
                {
                    start.EnvironmentVariables.Remove(variable);
                }
                process = Process.Start(start);
                if (process == null)
                {
                    return Failed(
                        target,
                        route,
                        "PROCESS_START_FAILED",
                        true,
                        -1
                    );
                }
                if (!process.WaitForExit(300000))
                {
                    try
                    {
                        process.Kill();
                        process.WaitForExit(10000);
                    }
                    catch
                    {
                        return Failed(
                            target,
                            route,
                            "PROCESS_TIMEOUT_CLEANUP_UNPROVEN",
                            false,
                            -1
                        );
                    }
                    return Failed(
                        target,
                        route,
                        "PROCESS_TIMEOUT",
                        process.HasExited,
                        process.HasExited ? process.ExitCode : -1
                    );
                }
                int exitCode = process.ExitCode;
                return new LauncherSessionResult
                {
                    status = exitCode == 0 ? "PASS" : "FAILED",
                    transport = route,
                    uses_proxy = false,
                    cleanup_verified = process.HasExited,
                    process_exit_code = exitCode,
                    target_id = target.target_id,
                    executable_path = target.executable_path,
                    executable_sha256 = target.sha256,
                    reason = exitCode == 0
                        ? null
                        : "CLIENT_EXIT_NONZERO"
                };
            }
            catch
            {
                return Failed(
                    target,
                    route,
                    "PROCESS_START_FAILED",
                    process == null || process.HasExited,
                    process != null && process.HasExited
                        ? process.ExitCode
                        : -1
                );
            }
            finally
            {
                if (process != null)
                {
                    process.Dispose();
                }
            }
        }

        private static LauncherSessionResult Failed(
            LaunchTargetResolution target,
            string route,
            string reason,
            bool cleanupVerified,
            int exitCode
        )
        {
            return new LauncherSessionResult
            {
                status = "FAILED",
                transport = route,
                uses_proxy = false,
                cleanup_verified = cleanupVerified,
                process_exit_code = exitCode,
                target_id = target == null ? null : target.target_id,
                executable_path = target == null
                    ? null
                    : target.executable_path,
                executable_sha256 = target == null
                    ? null
                    : target.sha256,
                reason = reason
            };
        }
    }

    internal sealed class SiblingProductResolution
    {
        public string status { get; set; }
        public string edition_id { get; set; }
        public string product_role { get; set; }
        public string executable_path { get; set; }
        public string sha256 { get; set; }
        public string reason { get; set; }
    }

    internal static class ProductHandoff
    {
        public static SiblingProductResolution Resolve(
            EditionProfile current,
            string siblingRoot
        )
        {
            string root;
            try
            {
                root = Path.GetFullPath(siblingRoot);
            }
            catch
            {
                return Blocked("SIBLING_PATH_INVALID");
            }
            string manifestPath = Path.Combine(
                root,
                "bundle-manifest.json"
            );
            string executable = Path.Combine(
                root,
                "LLMFoundationInstaller.exe"
            );
            if (!File.Exists(manifestPath) ||
                !File.Exists(executable) ||
                IsReparse(manifestPath) ||
                IsReparse(executable))
            {
                return Blocked("SIBLING_NOT_FOUND");
            }
            try
            {
                Dictionary<string, object> manifest =
                    new JavaScriptSerializer()
                        .Deserialize<Dictionary<string, object>>(
                            File.ReadAllText(
                                manifestPath,
                                new UTF8Encoding(false, true)
                            )
                        );
                string editionId = manifest["edition_id"] as string;
                string productRole = manifest["product_role"] as string;
                if (!String.Equals(
                        editionId,
                        current.edition_id,
                        StringComparison.Ordinal))
                {
                    return Blocked("SIBLING_EDITION_MISMATCH");
                }
                if (!String.Equals(
                        productRole,
                        "LaunchCenter",
                        StringComparison.Ordinal))
                {
                    return Blocked("SIBLING_PRODUCT_MISMATCH");
                }
                Dictionary<string, object> artifacts =
                    manifest["artifacts"] as
                        Dictionary<string, object>;
                Dictionary<string, object> executableRecord =
                    artifacts == null
                        ? null
                        : artifacts["LLMFoundationInstaller.exe"] as
                            Dictionary<string, object>;
                string expected = executableRecord == null
                    ? null
                    : executableRecord["sha256"] as string;
                string actual = BundleIntegrity.Sha256(executable);
                if (String.IsNullOrWhiteSpace(expected) ||
                    !String.Equals(
                        expected,
                        actual,
                        StringComparison.OrdinalIgnoreCase))
                {
                    return Blocked("SIBLING_INTEGRITY_FAILED");
                }
                return new SiblingProductResolution
                {
                    status = "RESOLVED",
                    edition_id = editionId,
                    product_role = productRole,
                    executable_path = executable,
                    sha256 = actual,
                    reason = null
                };
            }
            catch
            {
                return Blocked("SIBLING_MANIFEST_INVALID");
            }
        }

        private static bool IsReparse(string path)
        {
            return (File.GetAttributes(path) &
                FileAttributes.ReparsePoint) != 0;
        }

        private static SiblingProductResolution Blocked(string reason)
        {
            return new SiblingProductResolution
            {
                status = "BLOCKED",
                edition_id = null,
                product_role = null,
                executable_path = null,
                sha256 = null,
                reason = reason
            };
        }
    }
}
