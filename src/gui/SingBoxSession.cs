using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class SingBoxSessionResult
    {
        public string status { get; set; }
        public int listen_port { get; set; }
        public bool uses_proxy { get; set; }
        public bool cleanup_verified { get; set; }
        public bool secret_redacted { get; set; }
        public List<string> lifecycle { get; set; }
        public string reason { get; set; }
    }

    internal sealed class RunningSingBoxSession
    {
        internal Process process { get; set; }
        internal string session_root { get; set; }
        internal int listen_port { get; set; }
        internal string nonce { get; set; }
        internal List<string> lifecycle { get; set; }
        internal SingBoxSessionResult stop_result { get; set; }
    }

    internal static class SingBoxSession
    {
        public static SingBoxSessionResult TestCycle(
            string bundleRoot,
            string home,
            string targetId,
            string route
        )
        {
            if (!RuntimeBootstrap.Load(bundleRoot).test_only)
            {
                return Failed(
                    0,
                    new List<string>(),
                    true,
                    "TEST_SESSION_DISABLED"
                );
            }
            RunningSingBoxSession running = null;
            try
            {
                running = Start(
                    bundleRoot,
                    home,
                    targetId,
                    route
                );
                return StopVerified(running);
            }
            catch (Exception exception)
            {
                bool cleanup = CleanupFailedStart(running);
                return Failed(
                    running == null ? 0 : running.listen_port,
                    running == null
                        ? new List<string>()
                        : running.lifecycle,
                    cleanup,
                    StableReason(exception)
                );
            }
        }

        public static SingBoxSessionResult TestRoute(
            string bundleRoot,
            string home,
            string route,
            string endpoint
        )
        {
            RunningSingBoxSession running = null;
            string failure = null;
            try
            {
                running = Start(
                    bundleRoot,
                    home,
                    "connection-test",
                    route
                );
                HttpWebRequest request = (HttpWebRequest)
                    WebRequest.Create(endpoint);
                request.UserAgent = "K7-AI-Launch-Center";
                request.Proxy = new ExplicitWebProxy(
                    "http://127.0.0.1:" +
                    running.listen_port.ToString()
                );
                request.Timeout = 15000;
                request.ReadWriteTimeout = 15000;
                using (HttpWebResponse response = (HttpWebResponse)
                    request.GetResponse())
                {
                    int status = (int)response.StatusCode;
                    if (status < 200 || status >= 400)
                    {
                        throw new InvalidOperationException(
                            "ROUTE_PROBE_FAILED"
                        );
                    }
                }
                running.lifecycle.Add("ROUTE_PROBE_PASS");
            }
            catch (Exception exception)
            {
                if (running == null)
                {
                    return Failed(
                        0,
                        new List<string>(),
                        true,
                        StableReason(exception),
                        true
                    );
                }
                failure = ProbeFailureReason(exception);
            }

            SingBoxSessionResult result = StopVerified(running);
            result.uses_proxy = true;
            if (result.cleanup_verified)
            {
                result.lifecycle.Remove("RUNTIME_STOPPED");
                result.lifecycle.Remove("TEMP_REMOVED");
                result.lifecycle.Add("CLEANUP_VERIFIED");
            }
            if (failure != null)
            {
                result.status = "FAILED";
                if (result.cleanup_verified)
                {
                    result.reason = failure;
                }
            }
            return result;
        }

        public static RunningSingBoxSession Start(
            string bundleRoot,
            string home,
            string targetId,
            string route
        )
        {
            RuntimeBootstrapResult runtime = RuntimeBootstrap.EnsureInstalled(
                bundleRoot,
                home
            );
            if (runtime.status != "VERIFIED")
            {
                throw new InvalidOperationException(
                    RuntimeBootstrap.FailureReason(runtime)
                );
            }
            int port = FindFreePort();
            if (port == 0)
            {
                throw new InvalidOperationException(
                    "LOCAL_PORT_UNAVAILABLE"
                );
            }
            string root = Path.Combine(
                Path.GetFullPath(home),
                ".llm-foundation",
                "launcher-state",
                "sessions",
                Guid.NewGuid().ToString("N")
            );
            Directory.CreateDirectory(root);
            ApplyCurrentUserAcl(root);
            string nonce = Guid.NewGuid().ToString("N");
            string configPath = Path.Combine(root, "config.json");
            string statePath = Path.Combine(root, "owned-state.json");
            string routingTargetId = targetId == "connection-test"
                ? "opencode-cli"
                : targetId;
            List<string> lifecycle = new List<string>
            {
                "PROFILE_VALIDATED",
                "RUNTIME_VERIFIED"
            };
            RunningSingBoxSession running = new RunningSingBoxSession
            {
                process = null,
                session_root = root,
                listen_port = port,
                nonce = nonce,
                lifecycle = lifecycle
            };
            try
            {
                Dictionary<string, object> document = null;
                ConnectionStore.WithProxyCredential(
                    home,
                    delegate(
                        ConnectionProfile profile,
                        string password
                    )
                    {
                        document = SingBoxConfig.Create(
                            profile,
                            password,
                            routingTargetId,
                            route,
                            port
                        );
                        return true;
                    }
                );
                File.WriteAllText(
                    configPath,
                    new JavaScriptSerializer().Serialize(document) +
                        "\n",
                    new UTF8Encoding(false)
                );
                if (RunCheck(
                        runtime.executable_path,
                        configPath) != 0)
                {
                    throw new InvalidOperationException(
                        "CONFIG_CHECK_FAILED"
                    );
                }
                lifecycle.Add("CONFIG_CHECKED");
                Process process = Process.Start(
                    RuntimeStartInfo(
                        runtime.executable_path,
                        "run",
                        configPath
                    )
                );
                if (process == null)
                {
                    throw new InvalidOperationException(
                        "RUNTIME_START_FAILED"
                    );
                }
                running.process = process;
                File.WriteAllText(
                    statePath,
                    new JavaScriptSerializer().Serialize(
                        new Dictionary<string, object>
                        {
                            { "schema_version", 1 },
                            { "nonce", nonce },
                            {
                                "owner_pid",
                                Process.GetCurrentProcess().Id
                            },
                            { "process_id", process.Id },
                            { "listen_port", port },
                            {
                                "executable_path",
                                runtime.executable_path
                            },
                            {
                                "executable_sha256",
                                BundleIntegrity.Sha256(
                                    runtime.executable_path
                                )
                            }
                        }
                    ) + "\n",
                    new UTF8Encoding(false)
                );
                WaitForOwnedListener(running, statePath);
                lifecycle.Add("LOCAL_PROXY_READY");
                File.Delete(configPath);
                if (File.Exists(configPath))
                {
                    throw new InvalidOperationException(
                        "SECRET_CONFIG_REMOVE_FAILED"
                    );
                }
                return running;
            }
            catch
            {
                CleanupFailedStart(running);
                throw;
            }
        }

        public static SingBoxSessionResult StopVerified(
            RunningSingBoxSession running
        )
        {
            if (running == null)
            {
                return Failed(
                    0,
                    new List<string>(),
                    false,
                    "SESSION_NOT_RUNNING"
                );
            }
            bool processStopped = false;
            bool tempRemoved = false;
            try
            {
                if (running.process != null &&
                    !running.process.HasExited)
                {
                    running.process.Kill();
                    running.process.WaitForExit(10000);
                }
                processStopped = running.process == null ||
                    running.process.HasExited;
                if (processStopped)
                {
                    running.lifecycle.Add("RUNTIME_STOPPED");
                }
            }
            catch
            {
                processStopped = false;
            }
            finally
            {
                if (running.process != null)
                {
                    running.process.Dispose();
                }
            }
            try
            {
                if (processStopped &&
                    Directory.Exists(running.session_root))
                {
                    Directory.Delete(running.session_root, true);
                }
                tempRemoved = !Directory.Exists(
                    running.session_root
                );
                if (tempRemoved)
                {
                    running.lifecycle.Add("TEMP_REMOVED");
                }
            }
            catch
            {
                tempRemoved = false;
            }
            bool cleanup = processStopped && tempRemoved;
            return new SingBoxSessionResult
            {
                status = cleanup ? "PASS" : "FAILED",
                listen_port = running.listen_port,
                uses_proxy = false,
                cleanup_verified = cleanup,
                secret_redacted = true,
                lifecycle = running.lifecycle,
                reason = cleanup ? null : "SESSION_CLEANUP_FAILED"
            };
        }

        public static SingBoxSessionResult RecoverOwnedSessions(
            string home,
            int expectedOwnerPid
        )
        {
            if (expectedOwnerPid < 1)
            {
                return Failed(
                    0,
                    new List<string>(),
                    false,
                    "OWNED_SESSION_RECOVERY_FAILED"
                );
            }
            return RecoverOwnedSessionsInternal(
                home,
                expectedOwnerPid,
                false
            );
        }

        public static SingBoxSessionResult RecoverOrphanedSessions(
            string home
        )
        {
            return RecoverOwnedSessionsInternal(home, null, true);
        }

        public static SingBoxSessionResult ResetManagedSessions(
            string home
        )
        {
            SingBoxSessionResult result = RecoverOwnedSessionsInternal(
                home,
                null,
                false
            );
            result.uses_proxy = true;
            if (result.cleanup_verified)
            {
                result.lifecycle.Add("MANAGED_SESSIONS_RESET");
            }
            return result;
        }

        private static SingBoxSessionResult RecoverOwnedSessionsInternal(
            string home,
            int? expectedOwnerPid,
            bool orphanedOnly
        )
        {
            List<string> lifecycle = new List<string>();
            string sessionsRoot;
            try
            {
                sessionsRoot = Path.Combine(
                    Path.GetFullPath(home),
                    ".llm-foundation",
                    "launcher-state",
                    "sessions"
                );
            }
            catch
            {
                return Failed(
                    0,
                    lifecycle,
                    false,
                    "OWNED_SESSION_RECOVERY_FAILED"
                );
            }
            if (!Directory.Exists(sessionsRoot))
            {
                return new SingBoxSessionResult
                {
                    status = "PASS",
                    listen_port = 0,
                    uses_proxy = false,
                    cleanup_verified = true,
                    secret_redacted = true,
                    lifecycle = lifecycle,
                    reason = null
                };
            }
            try
            {
                foreach (string sessionRoot in Directory.GetDirectories(
                    sessionsRoot
                ))
                {
                    if ((File.GetAttributes(sessionRoot) &
                            FileAttributes.ReparsePoint) != 0)
                    {
                        throw new InvalidOperationException(
                            "OWNED_SESSION_MISMATCH"
                        );
                    }
                    string statePath = Path.Combine(
                        sessionRoot,
                        "owned-state.json"
                    );
                    if (!File.Exists(statePath))
                    {
                        throw new InvalidOperationException(
                            "OWNED_SESSION_MISMATCH"
                        );
                    }
                    Dictionary<string, object> state =
                        new JavaScriptSerializer().Deserialize<
                            Dictionary<string, object>
                        >(
                            File.ReadAllText(
                                statePath,
                                new UTF8Encoding(false, true)
                            )
                        );
                    int schema = Convert.ToInt32(
                        state["schema_version"]
                    );
                    int ownerPid = Convert.ToInt32(
                        state["owner_pid"]
                    );
                    if (expectedOwnerPid.HasValue &&
                        ownerPid != expectedOwnerPid.Value)
                    {
                        continue;
                    }
                    if (orphanedOnly && IsProcessAlive(ownerPid))
                    {
                        continue;
                    }
                    int processId = Convert.ToInt32(
                        state["process_id"]
                    );
                    string nonce = state["nonce"] as string;
                    string executablePath =
                        state["executable_path"] as string;
                    string executableSha256 =
                        state["executable_sha256"] as string;
                    if (schema != 1 ||
                        ownerPid < 1 ||
                        processId < 1 ||
                        String.IsNullOrWhiteSpace(nonce) ||
                        String.IsNullOrWhiteSpace(executablePath) ||
                        String.IsNullOrWhiteSpace(executableSha256) ||
                        !File.Exists(executablePath) ||
                        !String.Equals(
                            BundleIntegrity.Sha256(executablePath),
                            executableSha256,
                            StringComparison.OrdinalIgnoreCase))
                    {
                        throw new InvalidOperationException(
                            "OWNED_SESSION_MISMATCH"
                        );
                    }
                    Process process = null;
                    try
                    {
                        process = Process.GetProcessById(processId);
                    }
                    catch (ArgumentException)
                    {
                        process = null;
                    }
                    if (process != null)
                    {
                        using (process)
                        {
                            string runningPath = process.MainModule
                                .FileName;
                            if (!String.Equals(
                                    Path.GetFullPath(runningPath),
                                    Path.GetFullPath(executablePath),
                                    StringComparison.OrdinalIgnoreCase))
                            {
                                throw new InvalidOperationException(
                                    "OWNED_SESSION_MISMATCH"
                                );
                            }
                            if (!process.HasExited)
                            {
                                process.Kill();
                                process.WaitForExit(10000);
                            }
                            if (!process.HasExited)
                            {
                                throw new InvalidOperationException(
                                    "OWNED_SESSION_RECOVERY_FAILED"
                                );
                            }
                        }
                        lifecycle.Add("RUNTIME_STOPPED");
                    }
                    Directory.Delete(sessionRoot, true);
                    if (Directory.Exists(sessionRoot))
                    {
                        throw new InvalidOperationException(
                            "OWNED_SESSION_RECOVERY_FAILED"
                        );
                    }
                    lifecycle.Add("TEMP_REMOVED");
                }
                return new SingBoxSessionResult
                {
                    status = "PASS",
                    listen_port = 0,
                    uses_proxy = false,
                    cleanup_verified = true,
                    secret_redacted = true,
                    lifecycle = lifecycle,
                    reason = null
                };
            }
            catch (InvalidOperationException exception)
            {
                return Failed(
                    0,
                    lifecycle,
                    false,
                    exception.Message
                );
            }
            catch
            {
                return Failed(
                    0,
                    lifecycle,
                    false,
                    "OWNED_SESSION_RECOVERY_FAILED"
                );
            }
        }

        private static bool IsProcessAlive(int processId)
        {
            if (processId < 1)
            {
                return false;
            }
            try
            {
                using (Process process =
                    Process.GetProcessById(processId))
                {
                    return !process.HasExited;
                }
            }
            catch
            {
                return false;
            }
        }

        private static string ProbeFailureReason(Exception exception)
        {
            WebException web = exception as WebException;
            if (web == null)
            {
                return "ROUTE_PROBE_FAILED";
            }
            HttpWebResponse response = web.Response as HttpWebResponse;
            if (response != null)
            {
                int status = (int)response.StatusCode;
                if (status == 407)
                {
                    return "PROXY_AUTH_FAILED";
                }
                if (status == 401 || status == 403)
                {
                    return "PROXY_ACCESS_DENIED";
                }
            }
            switch (web.Status)
            {
                case WebExceptionStatus.Timeout:
                    return "PROXY_TIMEOUT";
                case WebExceptionStatus.NameResolutionFailure:
                case WebExceptionStatus.ProxyNameResolutionFailure:
                    return "PROXY_DNS_FAILED";
                case WebExceptionStatus.TrustFailure:
                case WebExceptionStatus.SecureChannelFailure:
                    return "PROXY_TLS_FAILED";
                case WebExceptionStatus.ConnectFailure:
                    return "PROXY_CONNECT_FAILED";
                case WebExceptionStatus.ConnectionClosed:
                case WebExceptionStatus.ReceiveFailure:
                case WebExceptionStatus.SendFailure:
                    return "PROXY_UPSTREAM_FAILED";
                default:
                    return "ROUTE_PROBE_FAILED";
            }
        }

        private static int RunCheck(
            string executable,
            string configPath
        )
        {
            using (Process process = Process.Start(
                RuntimeStartInfo(executable, "check", configPath)
            ))
            {
                if (process == null)
                {
                    return -1;
                }
                if (!process.WaitForExit(15000))
                {
                    process.Kill();
                    process.WaitForExit(5000);
                    return -1;
                }
                return process.ExitCode;
            }
        }

        private static ProcessStartInfo RuntimeStartInfo(
            string executable,
            string command,
            string configPath
        )
        {
            return new ProcessStartInfo
            {
                FileName = executable,
                Arguments = command + " -c " +
                    QuoteArgument(configPath),
                WorkingDirectory = Path.GetDirectoryName(executable),
                UseShellExecute = false,
                CreateNoWindow = true
            };
        }

        private static void WaitForOwnedListener(
            RunningSingBoxSession running,
            string statePath
        )
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(10);
            while (DateTime.UtcNow < deadline)
            {
                if (running.process.HasExited)
                {
                    throw new InvalidOperationException(
                        "RUNTIME_EXITED_BEFORE_READY"
                    );
                }
                Dictionary<string, object> state =
                    new JavaScriptSerializer()
                        .Deserialize<Dictionary<string, object>>(
                            File.ReadAllText(
                                statePath,
                                new UTF8Encoding(false, true)
                            )
                        );
                if (!String.Equals(
                        state["nonce"] as string,
                        running.nonce,
                        StringComparison.Ordinal) ||
                    Convert.ToInt32(state["process_id"]) !=
                        running.process.Id ||
                    Convert.ToInt32(state["listen_port"]) !=
                        running.listen_port)
                {
                    throw new InvalidOperationException(
                        "OWNED_STATE_MISMATCH"
                    );
                }
                try
                {
                    using (TcpClient client = new TcpClient())
                    {
                        IAsyncResult pending = client.BeginConnect(
                            IPAddress.Loopback,
                            running.listen_port,
                            null,
                            null
                        );
                        if (pending.AsyncWaitHandle.WaitOne(250) &&
                            client.Connected)
                        {
                            client.EndConnect(pending);
                            return;
                        }
                    }
                }
                catch
                {
                }
                Thread.Sleep(100);
            }
            throw new InvalidOperationException(
                "LOCAL_PROXY_NOT_READY"
            );
        }

        private static int FindFreePort()
        {
            for (int port = 18082; port <= 18120; port++)
            {
                TcpListener listener = null;
                try
                {
                    listener = new TcpListener(
                        IPAddress.Loopback,
                        port
                    );
                    listener.Start();
                    return port;
                }
                catch
                {
                }
                finally
                {
                    if (listener != null)
                    {
                        listener.Stop();
                    }
                }
            }
            return 0;
        }

        private static void ApplyCurrentUserAcl(string path)
        {
            SecurityIdentifier user = WindowsIdentity
                .GetCurrent()
                .User;
            DirectorySecurity security = new DirectorySecurity();
            security.SetAccessRuleProtection(true, false);
            security.AddAccessRule(new FileSystemAccessRule(
                user,
                FileSystemRights.FullControl,
                InheritanceFlags.ContainerInherit |
                    InheritanceFlags.ObjectInherit,
                PropagationFlags.None,
                AccessControlType.Allow
            ));
            new DirectoryInfo(path).SetAccessControl(security);
        }

        private static bool CleanupFailedStart(
            RunningSingBoxSession running
        )
        {
            if (running == null)
            {
                return true;
            }
            try
            {
                if (running.process != null &&
                    !running.process.HasExited)
                {
                    running.process.Kill();
                    running.process.WaitForExit(10000);
                }
                if (running.process != null)
                {
                    running.process.Dispose();
                }
                if (Directory.Exists(running.session_root))
                {
                    Directory.Delete(running.session_root, true);
                }
                return !Directory.Exists(running.session_root);
            }
            catch
            {
                return false;
            }
        }

        private static string StableReason(Exception exception)
        {
            string message = exception == null
                ? ""
                : exception.Message;
            foreach (string reason in new[]
            {
                "RUNTIME_NOT_VERIFIED",
                "RUNTIME_SOURCE_LOCK_INVALID",
                "RUNTIME_ARCHIVE_INVALID",
                "RUNTIME_ARCHIVE_INTEGRITY_FAILED",
                "RUNTIME_ARCHIVE_ENTRY_UNSAFE",
                "RUNTIME_ALREADY_PRESENT_INVALID",
                "RUNTIME_INSTALL_FAILED",
                "RUNTIME_BUNDLE_ARCHIVE_MISSING",
                "RUNTIME_NOT_INSTALLED",
                "RUNTIME_LAYOUT_INVALID",
                "RUNTIME_EXECUTABLE_INTEGRITY_FAILED",
                "RUNTIME_VERIFY_FAILED",
                "LOCAL_PORT_UNAVAILABLE",
                "CONFIG_CHECK_FAILED",
                "RUNTIME_START_FAILED",
                "RUNTIME_EXITED_BEFORE_READY",
                "OWNED_STATE_MISMATCH",
                "SECRET_CONFIG_REMOVE_FAILED",
                "LOCAL_PROXY_NOT_READY"
            })
            {
                if (String.Equals(
                        message,
                        reason,
                        StringComparison.Ordinal))
                {
                    return reason;
                }
            }
            return "SESSION_START_FAILED";
        }

        private static string QuoteArgument(string value)
        {
            return "\"" + value.Replace("\\", "\\\\")
                .Replace("\"", "\\\"") + "\"";
        }

        private static SingBoxSessionResult Failed(
            int port,
            List<string> lifecycle,
            bool cleanup,
            string reason,
            bool usesProxy = false
        )
        {
            return new SingBoxSessionResult
            {
                status = "FAILED",
                listen_port = port,
                uses_proxy = usesProxy,
                cleanup_verified = cleanup,
                secret_redacted = true,
                lifecycle = lifecycle,
                reason = reason
            };
        }

        private sealed class ExplicitWebProxy : IWebProxy
        {
            private readonly Uri proxy;

            public ExplicitWebProxy(string address)
            {
                proxy = new Uri(address, UriKind.Absolute);
            }

            public ICredentials Credentials { get; set; }

            public Uri GetProxy(Uri destination)
            {
                return proxy;
            }

            public bool IsBypassed(Uri host)
            {
                return false;
            }
        }
    }
}
