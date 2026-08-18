using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    [Flags]
    internal enum ActivateOptions
    {
        None = 0
    }

    [ComImport]
    [Guid("45BA127D-10A8-46EA-8AB7-56EA9078943C")]
    internal class ApplicationActivationManager
    {
    }

    [ComImport]
    [Guid("2E941141-7F97-4756-BA1D-9DECDE894A3D")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IApplicationActivationManager
    {
        [PreserveSig]
        int ActivateApplication(
            [MarshalAs(UnmanagedType.LPWStr)] string appUserModelId,
            [MarshalAs(UnmanagedType.LPWStr)] string arguments,
            ActivateOptions options,
            out uint processId
        );
    }

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
        public List<string> lifecycle { get; set; }
        public string reason { get; set; }
    }

    internal static class ClientLauncher
    {
        private static readonly object RouteSync = new object();
        private static RunningSingBoxSession activeSingBox;
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

        static ClientLauncher()
        {
            AppDomain.CurrentDomain.ProcessExit += delegate
            {
                try
                {
                    StopActiveRoute();
                }
                catch
                {
                }
            };
        }

        public static LauncherSessionResult StartAndWait(
            LaunchTargetResolution target,
            string route,
            string bundleRoot = null,
            string home = null
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
            if (route == "SingBoxHttp" ||
                route == "SingBoxHttps")
            {
                return StartThroughSingBox(
                    target,
                    route,
                    bundleRoot,
                    home
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
            if (String.Equals(
                    target.launch_mode,
                    "appx",
                    StringComparison.Ordinal) &&
                String.IsNullOrWhiteSpace(target.activation_id))
            {
                return Failed(
                    target,
                    route,
                    "APPX_ACTIVATION_ID_MISSING",
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
                process = StartExactTarget(target, start);
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
                if (target.role == "desktop")
                {
                    if (!process.WaitForExit(1000))
                    {
                        return new LauncherSessionResult
                        {
                            status = "PASS",
                            transport = route,
                            uses_proxy = false,
                            cleanup_verified = true,
                            process_exit_code = -1,
                            target_id = target.target_id,
                            executable_path = target.executable_path,
                            executable_sha256 = target.sha256,
                            lifecycle = new List<string>
                            {
                                "EXACT_CLIENT_STARTED",
                                "CLIENT_RUNNING"
                            },
                            reason = null
                        };
                    }
                }
                else if (!process.WaitForExit(300000))
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
                    lifecycle = new List<string>
                    {
                        "EXACT_CLIENT_STARTED",
                        "CLIENT_EXITED"
                    },
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

        internal static LauncherSessionResult StartAndWaitForTest(
            LaunchTargetResolution target,
            string route,
            string bundleRoot,
            string home,
            string registrySubkey
        )
        {
            LauncherSessionResult result =
                route == "SingBoxHttp" ||
                route == "SingBoxHttps"
                ? StartThroughSingBox(
                    target,
                    route,
                    bundleRoot,
                    home,
                    registrySubkey
                )
                : StartAndWait(target, route, bundleRoot, home);
            if (result.lifecycle == null)
            {
                result.lifecycle = new List<string>();
            }
            result.lifecycle.Insert(
                0,
                "TEST_SYSTEM_PROXY_GUARD_ACTIVE"
            );
            return result;
        }

        private static LauncherSessionResult StartThroughSingBox(
            LaunchTargetResolution target,
            string route,
            string bundleRoot,
            string home,
            string testRegistrySubkey = null,
            string testFixtureArguments = null,
            bool testActivationFailure = false
        )
        {
            if (String.IsNullOrWhiteSpace(bundleRoot) ||
                String.IsNullOrWhiteSpace(home))
            {
                return Failed(
                    target,
                    route,
                    "SINGBOX_CONTEXT_MISSING",
                    true,
                    -1
                );
            }
            if (String.Equals(
                    target.launch_mode,
                    "appx",
                    StringComparison.Ordinal) &&
                testRegistrySubkey == null &&
                IsExactTargetRunning(target))
            {
                return Failed(
                    target,
                    route,
                    "APPX_ALREADY_RUNNING",
                    true,
                    -1
                );
            }
            RunningSingBoxSession session = null;
            Process client = null;
            bool registered = false;
            bool systemProxyAcquired = false;
            try
            {
                if (String.Equals(
                        target.launch_mode,
                        "appx",
                        StringComparison.Ordinal))
                {
                    SingBoxSessionResult recovered =
                        SingBoxSession.RecoverOrphanedSessions(home);
                    if (!recovered.cleanup_verified)
                    {
                        throw new InvalidOperationException(
                            recovered.reason
                        );
                    }
                }
                session = SingBoxSession.Start(
                    bundleRoot,
                    home,
                    target.target_id,
                    route
                );
                if (String.Equals(
                        target.launch_mode,
                        "appx",
                        StringComparison.Ordinal))
                {
                    ProxyRecoveryResult lease =
                        testRegistrySubkey == null
                        ? SystemProxyLease.Acquire(
                            home,
                            session.listen_port
                        )
                        : SystemProxyLease.Acquire(
                            home,
                            session.listen_port,
                            testRegistrySubkey
                        );
                    if (lease.status != "ACQUIRED")
                    {
                        throw new InvalidOperationException(
                            lease.reason
                        );
                    }
                    systemProxyAcquired = true;
                }
                lock (RouteSync)
                {
                    if (activeSingBox != null)
                    {
                        throw new InvalidOperationException(
                            "ROUTE_ALREADY_ACTIVE"
                        );
                    }
                    activeSingBox = session;
                    registered = true;
                }
                PauseAfterRouteRegistrationForTest(
                    testRegistrySubkey
                );
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
                string localProxy = "http://127.0.0.1:" +
                    session.listen_port.ToString();
                if (String.Equals(
                        target.launch_mode,
                        "chrome",
                        StringComparison.Ordinal))
                {
                    string profile = Path.Combine(
                        Path.GetFullPath(home),
                        ".llm-foundation",
                        "browser",
                        "chrome-proxy-profile"
                    );
                    Directory.CreateDirectory(profile);
                    start.Arguments =
                        QuoteLaunchArgument(
                            "--proxy-server=" + localProxy
                        ) + " " +
                        QuoteLaunchArgument(
                            "--user-data-dir=" + profile
                        );
                }
                start.EnvironmentVariables["HTTP_PROXY"] = localProxy;
                start.EnvironmentVariables["HTTPS_PROXY"] = localProxy;
                start.EnvironmentVariables["http_proxy"] = localProxy;
                start.EnvironmentVariables["https_proxy"] = localProxy;
                const string noProxy = "localhost,127.0.0.1,::1";
                start.EnvironmentVariables["NO_PROXY"] = noProxy;
                start.EnvironmentVariables["no_proxy"] = noProxy;
                start.EnvironmentVariables[
                    "LLM_FOUNDATION_CONNECTION_MODE"
                ] = route;
                lock (RouteSync)
                {
                    if (!Object.ReferenceEquals(
                            activeSingBox,
                            session))
                    {
                        throw new InvalidOperationException(
                            "ROUTE_STOPPED_BEFORE_CLIENT_START"
                        );
                    }
                    if (testRegistrySubkey != null)
                    {
                        if (testActivationFailure)
                        {
                            throw new InvalidOperationException(
                                "APPX_ACTIVATION_FAILED"
                            );
                        }
                        client = StartTestAppxTarget(
                            target,
                            start,
                            testFixtureArguments
                        );
                    }
                    else
                    {
                        client = StartExactProcessTarget(
                            target,
                            start
                        );
                    }
                }
                if (client == null)
                {
                    throw new InvalidOperationException(
                        "CLIENT_START_FAILED"
                    );
                }
                session.lifecycle.Add("EXACT_CLIENT_STARTED");
                if (String.Equals(
                        target.launch_mode,
                        "appx",
                        StringComparison.Ordinal))
                {
                    session.lifecycle.Add(
                        "PACKAGED_EXECUTABLE_STARTED_WITH_PROXY_ENV"
                    );
                }
                if (target.role == "desktop")
                {
                    client.WaitForExit();
                }
                else if (!client.WaitForExit(300000))
                {
                    client.Kill();
                    client.WaitForExit(10000);
                    throw new InvalidOperationException(
                        "CLIENT_TIMEOUT"
                    );
                }
                int exitCode = client.ExitCode;
                session.lifecycle.Add("CLIENT_EXITED");
                SingBoxSessionResult stopped =
                    StopRegisteredRoute(session);
                registered = false;
                session = null;
                bool passed = exitCode == 0 &&
                    stopped.status == "PASS";
                return new LauncherSessionResult
                {
                    status = passed ? "PASS" : "FAILED",
                    transport = route,
                    uses_proxy = true,
                    cleanup_verified =
                        stopped.cleanup_verified,
                    process_exit_code = exitCode,
                    target_id = target.target_id,
                    executable_path = target.executable_path,
                    executable_sha256 = target.sha256,
                    lifecycle = stopped.lifecycle,
                    reason = passed
                        ? null
                        : (exitCode == 0
                            ? stopped.reason
                            : "CLIENT_EXIT_NONZERO")
                };
            }
            catch (Exception exception)
            {
                bool cleanup = true;
                List<string> lifecycle = session == null
                    ? new List<string>()
                    : session.lifecycle;
                if (session != null)
                {
                    SingBoxSessionResult stopped;
                    if (registered)
                    {
                        stopped = StopRegisteredRoute(session);
                    }
                    else
                    {
                        ProxyRecoveryResult proxy =
                            systemProxyAcquired
                            ? SystemProxyLease.StopActiveRoute()
                            : null;
                        stopped = SingBoxSession.StopVerified(session);
                        if (proxy != null &&
                            !proxy.cleanup_verified)
                        {
                            stopped.status = "FAILED";
                            stopped.cleanup_verified = false;
                            stopped.reason = proxy.reason;
                        }
                    }
                    cleanup = stopped.cleanup_verified;
                    lifecycle = stopped.lifecycle;
                    if (!cleanup &&
                        !String.IsNullOrWhiteSpace(stopped.reason))
                    {
                        exception = new InvalidOperationException(
                            stopped.reason
                        );
                    }
                    registered = false;
                }
                return new LauncherSessionResult
                {
                    status = "FAILED",
                    transport = route,
                    uses_proxy = true,
                    cleanup_verified = cleanup,
                    process_exit_code = client != null &&
                        client.HasExited
                        ? client.ExitCode
                        : -1,
                    target_id = target.target_id,
                    executable_path = target.executable_path,
                    executable_sha256 = target.sha256,
                    lifecycle = lifecycle,
                    reason = StableSingBoxReason(exception)
                };
            }
            finally
            {
                if (client != null)
                {
                    client.Dispose();
                }
            }
        }

        internal static LauncherSessionResult StartAppxThroughSingBoxForTest(
            LaunchTargetResolution target,
            string route,
            string bundleRoot,
            string home,
            string registrySubkey,
            string fixtureArguments,
            bool activationFailure
        )
        {
            return StartThroughSingBox(
                target,
                route,
                bundleRoot,
                home,
                registrySubkey,
                fixtureArguments,
                activationFailure
            );
        }

        internal static LauncherSessionResult
            StartAppxWithRouteConflictForTest(
                LaunchTargetResolution target,
                string route,
                string bundleRoot,
                string home,
                string registrySubkey,
                string fixtureArguments
            )
        {
            RunningSingBoxSession blocker = null;
            bool registered = false;
            try
            {
                blocker = SingBoxSession.Start(
                    bundleRoot,
                    home,
                    "connection-test",
                    route
                );
                lock (RouteSync)
                {
                    if (activeSingBox != null)
                    {
                        throw new InvalidOperationException(
                            "ROUTE_ALREADY_ACTIVE"
                        );
                    }
                    activeSingBox = blocker;
                    registered = true;
                }
                return StartThroughSingBox(
                    target,
                    route,
                    bundleRoot,
                    home,
                    registrySubkey,
                    fixtureArguments,
                    false
                );
            }
            finally
            {
                if (registered)
                {
                    StopActiveRoute();
                }
                else if (blocker != null)
                {
                    SingBoxSession.StopVerified(blocker);
                }
            }
        }

        public static SingBoxSessionResult StopActiveRoute()
        {
            lock (RouteSync)
            {
                return StopActiveRouteLocked();
            }
        }

        public static SingBoxSessionResult ResetManagedRoute(string home)
        {
            SingBoxSessionResult active = StopActiveRoute();
            SingBoxSessionResult sessions =
                SingBoxSession.ResetManagedSessions(home);
            ProxyRecoveryResult proxy =
                SystemProxyLease.ResetPreservingExternalChanges(home);
            List<string> lifecycle = new List<string>();
            if (active.lifecycle != null)
            {
                lifecycle.AddRange(active.lifecycle);
            }
            if (sessions.lifecycle != null)
            {
                lifecycle.AddRange(sessions.lifecycle);
            }
            if (proxy.lifecycle != null)
            {
                lifecycle.AddRange(proxy.lifecycle);
            }
            bool cleanup = active.cleanup_verified &&
                sessions.cleanup_verified &&
                proxy.cleanup_verified;
            return new SingBoxSessionResult
            {
                status = cleanup ? "PASS" : "FAILED",
                listen_port = 0,
                uses_proxy = true,
                cleanup_verified = cleanup,
                secret_redacted = true,
                lifecycle = lifecycle,
                reason = !active.cleanup_verified
                    ? active.reason
                    : (!sessions.cleanup_verified
                        ? sessions.reason
                        : (!proxy.cleanup_verified
                            ? proxy.reason
                            : null))
            };
        }

        private static SingBoxSessionResult StopRegisteredRoute(
            RunningSingBoxSession session
        )
        {
            lock (RouteSync)
            {
                if (session != null &&
                    session.stop_result != null)
                {
                    return session.stop_result;
                }
                if (Object.ReferenceEquals(
                        activeSingBox,
                        session))
                {
                    return StopActiveRouteLocked();
                }
                return new SingBoxSessionResult
                {
                    status = "FAILED",
                    listen_port = session == null
                        ? 0
                        : session.listen_port,
                    uses_proxy = true,
                    cleanup_verified = false,
                    secret_redacted = true,
                    lifecycle = session == null
                        ? new List<string>()
                        : session.lifecycle,
                    reason = "OWNED_SESSION_MISMATCH"
                };
            }
        }

        private static SingBoxSessionResult StopActiveRouteLocked()
        {
            RunningSingBoxSession session = activeSingBox;
            activeSingBox = null;
            ProxyRecoveryResult proxy =
                SystemProxyLease.StopActiveRoute();
            SingBoxSessionResult singBox = session == null
                ? new SingBoxSessionResult
                {
                    status = "PASS",
                    listen_port = 0,
                    uses_proxy = true,
                    cleanup_verified = true,
                    secret_redacted = true,
                    lifecycle = new List<string>(),
                    reason = null
                }
                : SingBoxSession.StopVerified(session);
            bool cleanup = proxy.cleanup_verified &&
                singBox.cleanup_verified;
            if (proxy.cleanup_verified &&
                proxy.lifecycle != null &&
                proxy.lifecycle.Count > 0)
            {
                singBox.lifecycle.Add("SYSTEM_PROXY_RESTORED");
            }
            singBox.status = cleanup ? "PASS" : "FAILED";
            singBox.uses_proxy = true;
            singBox.cleanup_verified = cleanup;
            singBox.reason = !proxy.cleanup_verified
                ? proxy.reason
                : (!singBox.cleanup_verified
                    ? singBox.reason
                    : null);
            if (session != null)
            {
                session.stop_result = singBox;
            }
            return singBox;
        }

        internal static bool HasActiveRoute()
        {
            lock (RouteSync)
            {
                return activeSingBox != null;
            }
        }

        private static Process StartTestAppxTarget(
            LaunchTargetResolution target,
            ProcessStartInfo start,
            string arguments
        )
        {
            if (target == null ||
                String.IsNullOrWhiteSpace(target.executable_path) ||
                !File.Exists(target.executable_path) ||
                !String.Equals(
                    BundleIntegrity.Sha256(target.executable_path),
                    target.sha256,
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "TARGET_INTEGRITY_CHANGED"
                );
            }
            start.Arguments = arguments ?? "";
            start.CreateNoWindow = true;
            return Process.Start(start);
        }

        private static string QuoteLaunchArgument(string value)
        {
            return "\"" + (value ?? "").Replace("\"", "\\\"") + "\"";
        }

        private static void PauseAfterRouteRegistrationForTest(
            string registrySubkey
        )
        {
            if (String.IsNullOrWhiteSpace(registrySubkey) ||
                !SystemProxyLease.IsAllowedTestRegistrySubkey(
                    registrySubkey))
            {
                return;
            }
            string ready = Environment.GetEnvironmentVariable(
                "K7_ROUTE_REGISTERED_READY"
            );
            string resume = Environment.GetEnvironmentVariable(
                "K7_ROUTE_REGISTERED_CONTINUE"
            );
            if (String.IsNullOrWhiteSpace(ready) ||
                String.IsNullOrWhiteSpace(resume))
            {
                return;
            }
            File.WriteAllText(ready, "ready", new UTF8Encoding(false));
            DateTime deadline = DateTime.UtcNow.AddSeconds(30);
            while (!File.Exists(resume) &&
                DateTime.UtcNow < deadline)
            {
                Thread.Sleep(25);
            }
            if (!File.Exists(resume))
            {
                throw new InvalidOperationException(
                    "ROUTE_TEST_SYNC_TIMEOUT"
                );
            }
        }

        private static bool IsExactTargetRunning(
            LaunchTargetResolution target
        )
        {
            if (target == null ||
                String.IsNullOrWhiteSpace(target.executable_path))
            {
                return false;
            }
            string expected = Path.GetFullPath(
                target.executable_path
            );
            string processName = Path.GetFileNameWithoutExtension(
                expected
            );
            foreach (Process process in Process.GetProcessesByName(
                processName
            ))
            {
                using (process)
                {
                    try
                    {
                        if (String.Equals(
                                Path.GetFullPath(
                                    process.MainModule.FileName
                                ),
                                expected,
                                StringComparison.OrdinalIgnoreCase))
                        {
                            return true;
                        }
                    }
                    catch
                    {
                    }
                }
            }
            return false;
        }

        private static Process StartExactTarget(
            LaunchTargetResolution target,
            ProcessStartInfo start
        )
        {
            if (target == null ||
                String.IsNullOrWhiteSpace(target.executable_path) ||
                !File.Exists(target.executable_path) ||
                !String.Equals(
                    BundleIntegrity.Sha256(target.executable_path),
                    target.sha256,
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "TARGET_INTEGRITY_CHANGED"
                );
            }
            if (!String.Equals(
                    target.launch_mode,
                    "appx",
                    StringComparison.Ordinal))
            {
                return Process.Start(start);
            }
            IApplicationActivationManager manager =
                (IApplicationActivationManager)
                    new ApplicationActivationManager();
            uint processId;
            int result = manager.ActivateApplication(
                target.activation_id,
                null,
                ActivateOptions.None,
                out processId
            );
            Marshal.ThrowExceptionForHR(result);
            if (processId == 0)
            {
                throw new InvalidOperationException(
                    "APPX_ACTIVATION_FAILED"
                );
            }
            return Process.GetProcessById((int)processId);
        }

        private static Process StartExactProcessTarget(
            LaunchTargetResolution target,
            ProcessStartInfo start
        )
        {
            if (target == null ||
                String.IsNullOrWhiteSpace(target.executable_path) ||
                !File.Exists(target.executable_path) ||
                !String.Equals(
                    BundleIntegrity.Sha256(target.executable_path),
                    target.sha256,
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "TARGET_INTEGRITY_CHANGED"
                );
            }
            return Process.Start(start);
        }

        private static string StableSingBoxReason(Exception exception)
        {
            string message = exception == null
                ? ""
                : exception.Message;
            foreach (string reason in new[]
            {
                "RUNTIME_NOT_VERIFIED",
                "LOCAL_PORT_UNAVAILABLE",
                "CONFIG_CHECK_FAILED",
                "RUNTIME_START_FAILED",
                "RUNTIME_EXITED_BEFORE_READY",
                "OWNED_STATE_MISMATCH",
                "SECRET_CONFIG_REMOVE_FAILED",
                "LOCAL_PROXY_NOT_READY",
                "CLIENT_START_FAILED",
                "CLIENT_TIMEOUT",
                "APPX_ACTIVATION_FAILED",
                "APPX_ALREADY_RUNNING",
                "ROUTE_ALREADY_ACTIVE",
                "ROUTE_STOPPED_BEFORE_CLIENT_START",
                "SYSTEM_PROXY_LEASE_BUSY",
                "SYSTEM_PROXY_CHANGED_EXTERNALLY",
                "SYSTEM_PROXY_STATE_WRITE_FAILED",
                "SYSTEM_PROXY_ACQUIRE_FAILED",
                "SYSTEM_PROXY_RECOVERY_FAILED",
                "SYSTEM_PROXY_STATE_INVALID",
                "SYSTEM_PROXY_STATE_REMOVE_FAILED",
                "SYSTEM_PROXY_STATE_ARCHIVE_FAILED",
                "SYSTEM_PROXY_REFRESH_FAILED",
                "SYSTEM_PROXY_WATCHDOG_START_FAILED",
                "OWNED_SESSION_MISMATCH",
                "OWNED_SESSION_RECOVERY_FAILED",
                "SESSION_CLEANUP_FAILED"
            })
            {
                if (message.Contains(reason))
                {
                    return reason;
                }
            }
            return "SINGBOX_LAUNCH_FAILED";
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
                lifecycle = new List<string>(),
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
            if (!File.Exists(manifestPath) ||
                IsReparse(manifestPath))
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
                if (!String.Equals(
                        editionId,
                        current.edition_id,
                        StringComparison.Ordinal))
                {
                    return Blocked("SIBLING_EDITION_MISMATCH");
                }
                string productRole;
                string executableName;
                string expected;
                object productsValue;
                if (manifest.TryGetValue(
                        "products",
                        out productsValue))
                {
                    Dictionary<string, object> products =
                        productsValue as Dictionary<string, object>;
                    Dictionary<string, object> launchCenter =
                        products == null
                            ? null
                            : products["launch_center"] as
                                Dictionary<string, object>;
                    productRole = launchCenter == null
                        ? null
                        : launchCenter["product_role"] as string;
                    executableName = launchCenter == null
                        ? null
                        : launchCenter["file"] as string;
                    expected = launchCenter == null
                        ? null
                        : launchCenter["sha256"] as string;
                }
                else
                {
                    productRole = manifest["product_role"] as string;
                    executableName = "LLMFoundationInstaller.exe";
                    Dictionary<string, object> artifacts =
                        manifest["artifacts"] as
                            Dictionary<string, object>;
                    Dictionary<string, object> executableRecord =
                        artifacts == null
                            ? null
                            : artifacts[executableName] as
                                Dictionary<string, object>;
                    expected = executableRecord == null
                        ? null
                        : executableRecord["sha256"] as string;
                }
                if (!String.Equals(
                        productRole,
                        "LaunchCenter",
                        StringComparison.Ordinal))
                {
                    return Blocked("SIBLING_PRODUCT_MISMATCH");
                }
                if (String.IsNullOrWhiteSpace(executableName) ||
                    !String.Equals(
                        executableName,
                        Path.GetFileName(executableName),
                        StringComparison.Ordinal) ||
                    !executableName.EndsWith(
                        ".exe",
                        StringComparison.OrdinalIgnoreCase))
                {
                    return Blocked("SIBLING_MANIFEST_INVALID");
                }
                string executable = Path.Combine(
                    root,
                    executableName
                );
                if (!File.Exists(executable) ||
                    IsReparse(executable))
                {
                    return Blocked("SIBLING_NOT_FOUND");
                }
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
