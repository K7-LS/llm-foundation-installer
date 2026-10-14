using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    // The UI only observes this process. Closing the UI must never stop it.
    internal static class LaunchSessionHost
    {
        private static readonly object Sync = new object();
        private static Session observed;

        private sealed class Session : IDisposable
        {
            internal EventWaitHandle stop, ready, stopped, clean, done;
            internal Process process;
            internal Session(string token, bool create)
            {
                stop = Open(token, "stop", create);
                ready = Open(token, "ready", create);
                stopped = Open(token, "stopped", create);
                clean = Open(token, "clean", create);
                done = Open(token, "done", create);
            }
            private static EventWaitHandle Open(string token, string suffix, bool create)
            {
                string name = "Local\\K7.LaunchSession." + token + "." + suffix;
                return create
                    ? new EventWaitHandle(false, EventResetMode.ManualReset, name)
                    : EventWaitHandle.OpenExisting(name);
            }
            public void Dispose()
            {
                stop.Dispose(); ready.Dispose(); stopped.Dispose(); clean.Dispose(); done.Dispose();
                if (process != null) process.Dispose();
            }
        }

        internal static LauncherSessionResult StartAndWait(
            LaunchTargetResolution target, string route, string bundleRoot, string home)
        {
            if (target == null || target.status != "RESOLVED")
                return ClientLauncher.StartAndWait(target, route, bundleRoot, home);
            if (target.role != "desktop" || (route != "SingBoxHttp" && route != "SingBoxHttps"))
                return ClientLauncher.StartAndWait(target, route, bundleRoot, home);

            if (String.IsNullOrEmpty(target.target_id) ||
                !System.Text.RegularExpressions.Regex.IsMatch(target.target_id, "^[a-z0-9-]+$"))
                return Failure(target, route, "EXACT_TARGET_NOT_RESOLVED", true);
            string token = Guid.NewGuid().ToString("N");
            Session session = new Session(token, true);
            lock (Sync)
            {
                if (observed != null)
                {
                    session.Dispose();
                    return Failure(target, route, "ROUTE_ALREADY_ACTIVE", true);
                }
                observed = session;
            }
            try
            {
                // Only fixed catalog IDs, a fixed route and a random event token
                // cross the command line. No profile, password or executable path.
                string executable = Process.GetCurrentProcess().MainModule.FileName;
                var start = new ProcessStartInfo
                {
                    FileName = executable,
                    Arguments = "--launch-session " + target.target_id + " " + route + " " + token,
                    WorkingDirectory = bundleRoot,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true
                };
                session.process = Process.Start(start);
                if (session.process == null)
                    return Failure(target, route, "SESSION_HOST_START_FAILED", true);
                // A surviving client can retain inherited stdout handles.
                // Detect owner exit independently, then bound output draining.
                var read = session.process.StandardOutput.ReadToEndAsync();
                session.process.WaitForExit();
                if (!read.Wait(2000))
                    return Failure(target, route, "SESSION_HOST_EXITED", false);
                string output = read.Result;
                if (String.IsNullOrWhiteSpace(output))
                    return Failure(target, route, "SESSION_HOST_EXITED", false);
                var result = new JavaScriptSerializer().Deserialize<LauncherSessionResult>(output);
                return result ?? Failure(target, route, "SESSION_HOST_RESULT_INVALID", false);
            }
            catch
            {
                return Failure(target, route, "SESSION_HOST_FAILED", session.process == null);
            }
            finally
            {
                lock (Sync)
                {
                    if (Object.ReferenceEquals(observed, session)) observed = null;
                    session.Dispose();
                }
            }
        }

        internal static bool IsActive()
        {
            lock (Sync)
                return observed == null ? ClientLauncher.HasActiveRoute() :
                    observed.ready.WaitOne(0) && !observed.stopped.WaitOne(0) && !observed.done.WaitOne(0);
        }

        internal static SingBoxSessionResult StopActiveRoute()
        {
            return StopObserved() ?? ClientLauncher.StopActiveRoute();
        }

        internal static SingBoxSessionResult ResetManagedRoute(string home)
        {
            SingBoxSessionResult stopped = StopActiveRoute();
            return stopped.cleanup_verified ? ClientLauncher.ResetManagedRoute(home) : stopped;
        }

        internal static SingBoxSessionResult StopObserved()
        {
            lock (Sync)
            {
                if (observed == null) return null;
                observed.stop.Set();
                bool acknowledged = observed.stopped.WaitOne(30000);
                bool verified = acknowledged && observed.clean.WaitOne(0);
                return new SingBoxSessionResult
                {
                    status = verified ? "PASS" : "FAILED",
                    cleanup_verified = verified, uses_proxy = true, secret_redacted = true,
                    lifecycle = new List<string> { "SESSION_HOST_STOP_REQUESTED" },
                    reason = verified ? null : "SESSION_HOST_STOP_NOT_VERIFIED"
                };
            }
        }

        internal static int Run(EditionProfile edition, string bundleRoot, string[] args)
        {
            Guid token;
            if (args.Length != 4 || !Guid.TryParseExact(args[3], "N", out token) ||
                (args[2] != "SingBoxHttp" && args[2] != "SingBoxHttps")) return 2;
            string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            edition.product_role = "LaunchCenter";
            LaunchTargetResolution target = LaunchTargetResolver.Resolve(edition, bundleRoot, home, args[1]);
            if (target.status != "RESOLVED" || target.role != "desktop")
            {
                Program.WriteOutput(new JavaScriptSerializer().Serialize(
                    Failure(target, args[2], "EXACT_TARGET_NOT_RESOLVED", true)));
                return 20;
            }
            return RunOwnedSession(args[3], delegate
            {
                return ClientLauncher.StartAndWait(target, args[2], bundleRoot, home);
            });
        }

        // Shared by the actual worker and the isolated process tests.
        internal static int RunOwnedSession(string token, Func<LauncherSessionResult> launch)
        {
            using (var session = new Session(token, false))
            {
                Thread observer = new Thread(delegate()
                {
                    while (!session.done.WaitOne(50))
                    {
                        if (!ClientLauncher.HasActiveRoute()) continue;
                        session.ready.Set();
                        if (!session.stop.WaitOne(0)) continue;
                        try
                        {
                            SingBoxSessionResult result = ClientLauncher.StopActiveRoute();
                            if (result.cleanup_verified) session.clean.Set();
                        }
                        catch { /* stopped is acknowledged, but cleanup remains unverified */ }
                        session.stopped.Set();
                        return;
                    }
                });
                observer.IsBackground = true;
                observer.Start();
                LauncherSessionResult outcome;
                try { outcome = launch(); }
                finally
                {
                    session.done.Set();
                    observer.Join();
                }
                if (outcome.cleanup_verified) session.clean.Set();
                session.stopped.Set();
                // The parent may already have closed its pipe. Cleanup is complete
                // before attempting to deliver the optional UI result.
                try { Program.WriteOutput(new JavaScriptSerializer().Serialize(outcome)); }
                catch (IOException) { }
                return outcome.status == "PASS" ? 0 : 20;
            }
        }

        private static LauncherSessionResult Failure(LaunchTargetResolution target,
            string route, string reason, bool cleanup)
        {
            return new LauncherSessionResult
            {
                status = "FAILED", transport = route, uses_proxy = true,
                cleanup_verified = cleanup, process_exit_code = -1,
                target_id = target == null ? null : target.target_id, lifecycle = new List<string>(), reason = reason
            };
        }
    }
}
