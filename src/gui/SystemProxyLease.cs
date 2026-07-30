using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Win32;

namespace LlmFoundationInstaller
{
    internal sealed class ProxyRecoveryResult
    {
        public string status { get; set; }
        public bool cleanup_verified { get; set; }
        public List<string> lifecycle { get; set; }
        public string reason { get; set; }
    }

    internal sealed class ProxyRegistryValue
    {
        public string name { get; set; }
        public bool exists { get; set; }
        public object value { get; set; }
        public int kind { get; set; }
    }

    internal sealed class SystemProxyLeaseState
    {
        public int schema_version { get; set; }
        public string sid { get; set; }
        public int owner_pid { get; set; }
        public string phase { get; set; }
        public string registry_subkey { get; set; }
        public List<ProxyRegistryValue> original { get; set; }
        public List<ProxyRegistryValue> applied { get; set; }
    }

    internal sealed class ActiveSystemProxyLease
    {
        public string state_path { get; set; }
        public SystemProxyLeaseState state { get; set; }
        public Mutex mutex { get; set; }
        public List<string> lifecycle { get; set; }
    }

    internal static class SystemProxyLease
    {
        private const string DefaultRegistrySubkey =
            @"Software\Microsoft\Windows\CurrentVersion\Internet Settings";
        private const string TestRegistryPrefix =
            @"Software\K7AITests\";
        private const int InternetOptionRefresh = 37;
        private const int InternetOptionSettingsChanged = 39;
        private static readonly object Sync = new object();
        private static ActiveSystemProxyLease active;

        [DllImport("wininet.dll", SetLastError = true)]
        private static extern bool InternetSetOption(
            IntPtr internet,
            int option,
            IntPtr buffer,
            int bufferLength
        );

        static SystemProxyLease()
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

        public static ProxyRecoveryResult Acquire(
            string home,
            int localPort
        )
        {
            return Acquire(home, localPort, DefaultRegistrySubkey);
        }

        internal static ProxyRecoveryResult Acquire(
            string home,
            int localPort,
            string registrySubkey
        )
        {
            if (localPort < 1 || localPort > 65535)
            {
                return Failed("SYSTEM_PROXY_PORT_INVALID");
            }
            lock (Sync)
            {
                if (active != null)
                {
                    return Failed("SYSTEM_PROXY_LEASE_BUSY");
                }
            }

            Mutex mutex = null;
            bool acquired = false;
            try
            {
                mutex = new Mutex(false, MutexName());
                try
                {
                    acquired = mutex.WaitOne(0, false);
                }
                catch (AbandonedMutexException)
                {
                    acquired = true;
                }
                if (!acquired)
                {
                    mutex.Dispose();
                    return Failed("SYSTEM_PROXY_LEASE_BUSY");
                }

                string statePath;
                try
                {
                    statePath = StatePath(home);
                }
                catch
                {
                    ReleaseMutex(mutex, true);
                    return Failed("SYSTEM_PROXY_STATE_WRITE_FAILED");
                }

                ProxyRecoveryResult recovery = RecoverState(
                    statePath,
                    registrySubkey
                );
                if (!recovery.cleanup_verified)
                {
                    ReleaseMutex(mutex, true);
                    return recovery;
                }

                SystemProxyLeaseState state;
                try
                {
                    state = new SystemProxyLeaseState
                    {
                        schema_version = 1,
                        sid = CurrentSid(),
                        owner_pid = Process.GetCurrentProcess().Id,
                        phase = "PREPARED",
                        registry_subkey = registrySubkey,
                        original = Snapshot(registrySubkey),
                        applied = AppliedValues(localPort)
                    };
                    WriteStateAtomic(statePath, state);
                    StartWatchdog(
                        Process.GetCurrentProcess().Id,
                        home,
                        registrySubkey
                    );
                }
                catch
                {
                    try
                    {
                        if (File.Exists(statePath))
                        {
                            File.Delete(statePath);
                        }
                    }
                    catch
                    {
                    }
                    ReleaseMutex(mutex, true);
                    return Failed("SYSTEM_PROXY_STATE_WRITE_FAILED");
                }

                List<string> lifecycle = new List<string>
                {
                    "PREPARED"
                };
                try
                {
                    ApplyValues(registrySubkey, state.applied);
                    NotifySystemProxyChanged(registrySubkey);
                    state.phase = "APPLIED";
                    WriteStateAtomic(statePath, state);
                    lifecycle.Add("APPLIED");
                }
                catch
                {
                    ProxyRecoveryResult rollback = RestoreState(
                        statePath,
                        state
                    );
                    ReleaseMutex(mutex, true);
                    return rollback.cleanup_verified
                        ? Failed("SYSTEM_PROXY_STATE_WRITE_FAILED")
                        : rollback;
                }

                lock (Sync)
                {
                    active = new ActiveSystemProxyLease
                    {
                        state_path = statePath,
                        state = state,
                        mutex = mutex,
                        lifecycle = lifecycle
                    };
                }
                return new ProxyRecoveryResult
                {
                    status = "ACQUIRED",
                    cleanup_verified = true,
                    lifecycle = new List<string>(lifecycle),
                    reason = null
                };
            }
            catch
            {
                if (mutex != null)
                {
                    ReleaseMutex(mutex, acquired);
                }
                return Failed("SYSTEM_PROXY_ACQUIRE_FAILED");
            }
        }

        public static ProxyRecoveryResult Recover(string home)
        {
            return Recover(home, DefaultRegistrySubkey);
        }

        public static ProxyRecoveryResult Watchdog(
            int ownerPid,
            string home
        )
        {
            return Watchdog(ownerPid, home, DefaultRegistrySubkey);
        }

        internal static ProxyRecoveryResult Watchdog(
            int ownerPid,
            string home,
            string registrySubkey
        )
        {
            string statePath;
            try
            {
                statePath = StatePath(home);
            }
            catch
            {
                return Failed("SYSTEM_PROXY_STATE_INVALID");
            }
            while (File.Exists(statePath))
            {
                bool ownerAlive = IsProcessAlive(ownerPid);
                if (!ownerAlive)
                {
                    break;
                }
                Thread.Sleep(100);
            }
            if (!File.Exists(statePath))
            {
                if (IsProcessAlive(ownerPid))
                {
                    return new ProxyRecoveryResult
                    {
                        status = "RESTORED",
                        cleanup_verified = true,
                        lifecycle = new List<string>(),
                        reason = null
                    };
                }
                SingBoxSessionResult cleanWithoutState =
                    SingBoxSession.RecoverOwnedSessions(
                        home,
                        ownerPid
                    );
                return new ProxyRecoveryResult
                {
                    status = cleanWithoutState.cleanup_verified
                        ? "RESTORED"
                        : "FAILED",
                    cleanup_verified =
                        cleanWithoutState.cleanup_verified,
                    lifecycle = cleanWithoutState.lifecycle,
                    reason = cleanWithoutState.reason
                };
            }
            ProxyRecoveryResult proxy = Recover(
                home,
                registrySubkey
            );
            SingBoxSessionResult sessions =
                SingBoxSession.RecoverOwnedSessions(home, ownerPid);
            bool proxyCleanup = proxy.cleanup_verified;
            bool sessionsCleanup = sessions.cleanup_verified;
            if (proxy.lifecycle == null)
            {
                proxy.lifecycle = new List<string>();
            }
            if (sessions.lifecycle != null)
            {
                proxy.lifecycle.AddRange(sessions.lifecycle);
            }
            if (!proxyCleanup || !sessionsCleanup)
            {
                return new ProxyRecoveryResult
                {
                    status = "FAILED",
                    cleanup_verified = false,
                    lifecycle = proxy.lifecycle,
                    reason = !proxyCleanup
                        ? proxy.reason
                        : sessions.reason
                };
            }
            return proxy;
        }

        internal static ProxyRecoveryResult Recover(
            string home,
            string registrySubkey
        )
        {
            Mutex mutex = null;
            bool acquired = false;
            try
            {
                mutex = new Mutex(false, MutexName());
                try
                {
                    acquired = mutex.WaitOne(0, false);
                }
                catch (AbandonedMutexException)
                {
                    acquired = true;
                }
                if (!acquired)
                {
                    mutex.Dispose();
                    return Failed("SYSTEM_PROXY_LEASE_BUSY");
                }
                ProxyRecoveryResult result = RecoverState(
                    StatePath(home),
                    registrySubkey
                );
                ReleaseMutex(mutex, true);
                return result;
            }
            catch
            {
                if (mutex != null)
                {
                    ReleaseMutex(mutex, acquired);
                }
                return Failed("SYSTEM_PROXY_RECOVERY_FAILED");
            }
        }

        public static ProxyRecoveryResult StopActiveRoute()
        {
            ActiveSystemProxyLease lease;
            lock (Sync)
            {
                lease = active;
                active = null;
            }
            if (lease == null)
            {
                return new ProxyRecoveryResult
                {
                    status = "RESTORED",
                    cleanup_verified = true,
                    lifecycle = new List<string>(),
                    reason = null
                };
            }

            ProxyRecoveryResult result = RestoreState(
                lease.state_path,
                lease.state
            );
            if (result.cleanup_verified)
            {
                lease.lifecycle.Add("RESTORED");
            }
            result.lifecycle = new List<string>(lease.lifecycle);
            ReleaseMutex(lease.mutex, true);
            return result;
        }

        internal static bool IsAllowedTestRegistrySubkey(string subkey)
        {
            return !String.IsNullOrWhiteSpace(subkey) &&
                subkey.StartsWith(
                    TestRegistryPrefix,
                    StringComparison.Ordinal) &&
                subkey.Length > TestRegistryPrefix.Length &&
                !subkey.EndsWith(@"\", StringComparison.Ordinal);
        }

        private static ProxyRecoveryResult RecoverState(
            string statePath,
            string registrySubkey
        )
        {
            if (!File.Exists(statePath))
            {
                return new ProxyRecoveryResult
                {
                    status = "RESTORED",
                    cleanup_verified = true,
                    lifecycle = new List<string>(),
                    reason = null
                };
            }
            SystemProxyLeaseState state;
            try
            {
                state = new JavaScriptSerializer()
                    .Deserialize<SystemProxyLeaseState>(
                        File.ReadAllText(
                            statePath,
                            new UTF8Encoding(false, true)
                        )
                    );
            }
            catch
            {
                return Failed("SYSTEM_PROXY_STATE_INVALID");
            }
            if (state == null ||
                state.schema_version != 1 ||
                !String.Equals(
                    state.sid,
                    CurrentSid(),
                    StringComparison.Ordinal) ||
                !String.Equals(
                    state.registry_subkey,
                    registrySubkey,
                    StringComparison.Ordinal) ||
                (state.phase != "PREPARED" &&
                    state.phase != "APPLIED") ||
                state.original == null ||
                state.applied == null)
            {
                return Failed("SYSTEM_PROXY_STATE_INVALID");
            }
            return RestoreState(statePath, state);
        }

        private static ProxyRecoveryResult RestoreState(
            string statePath,
            SystemProxyLeaseState state
        )
        {
            bool changedExternally = false;
            try
            {
                foreach (ProxyRegistryValue applied in state.applied)
                {
                    ProxyRegistryValue current = ReadValue(
                        state.registry_subkey,
                        applied.name
                    );
                    ProxyRegistryValue original = FindValue(
                        state.original,
                        applied.name
                    );
                    if (!ValueEquals(current, applied) &&
                        !ValueEquals(current, original))
                    {
                        changedExternally = true;
                    }
                }
                if (changedExternally)
                {
                    return Failed(
                        "SYSTEM_PROXY_CHANGED_EXTERNALLY"
                    );
                }
                PauseBeforeRestoreForTest(state.registry_subkey);
                foreach (ProxyRegistryValue applied in state.applied)
                {
                    ProxyRegistryValue current = ReadValue(
                        state.registry_subkey,
                        applied.name
                    );
                    if (ValueEquals(current, applied))
                    {
                        RestoreValue(
                            state.registry_subkey,
                            FindValue(state.original, applied.name)
                        );
                    }
                    else if (!ValueEquals(
                        current,
                        FindValue(state.original, applied.name)
                    ))
                    {
                        changedExternally = true;
                    }
                }
                if (changedExternally)
                {
                    return Failed(
                        "SYSTEM_PROXY_CHANGED_EXTERNALLY"
                    );
                }
                foreach (ProxyRegistryValue original in state.original)
                {
                    ProxyRegistryValue current = ReadValue(
                        state.registry_subkey,
                        original.name
                    );
                    if (!ValueEquals(current, original))
                    {
                        return Failed(
                            "SYSTEM_PROXY_CHANGED_EXTERNALLY"
                        );
                    }
                }
                NotifySystemProxyChanged(state.registry_subkey);
                if (File.Exists(statePath))
                {
                    File.Delete(statePath);
                }
                if (File.Exists(statePath))
                {
                    return Failed(
                        "SYSTEM_PROXY_STATE_REMOVE_FAILED"
                    );
                }
                return new ProxyRecoveryResult
                {
                    status = "RESTORED",
                    cleanup_verified = true,
                    lifecycle = new List<string>
                    {
                        "RESTORED"
                    },
                    reason = null
                };
            }
            catch
            {
                return Failed("SYSTEM_PROXY_RECOVERY_FAILED");
            }
        }

        private static List<ProxyRegistryValue> Snapshot(
            string registrySubkey
        )
        {
            return new List<ProxyRegistryValue>
            {
                ReadValue(registrySubkey, "ProxyEnable"),
                ReadValue(registrySubkey, "ProxyServer")
            };
        }

        private static List<ProxyRegistryValue> AppliedValues(
            int localPort
        )
        {
            return new List<ProxyRegistryValue>
            {
                new ProxyRegistryValue
                {
                    name = "ProxyEnable",
                    exists = true,
                    value = 1,
                    kind = (int)RegistryValueKind.DWord
                },
                new ProxyRegistryValue
                {
                    name = "ProxyServer",
                    exists = true,
                    value = "127.0.0.1:" +
                        localPort.ToString(
                            System.Globalization.CultureInfo.InvariantCulture
                        ),
                    kind = (int)RegistryValueKind.String
                }
            };
        }

        private static void ApplyValues(
            string registrySubkey,
            List<ProxyRegistryValue> values
        )
        {
            foreach (ProxyRegistryValue value in values)
            {
                RestoreValue(registrySubkey, value);
            }
        }

        private static void PauseBeforeRestoreForTest(
            string registrySubkey
        )
        {
            if (!IsAllowedTestRegistrySubkey(registrySubkey))
            {
                return;
            }
            string ready = Environment.GetEnvironmentVariable(
                "K7_PROXY_CAS_READY"
            );
            string resume = Environment.GetEnvironmentVariable(
                "K7_PROXY_CAS_CONTINUE"
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
                    "SYSTEM_PROXY_TEST_SYNC_TIMEOUT"
                );
            }
        }

        private static void NotifySystemProxyChanged(string registrySubkey)
        {
            if (!String.Equals(
                    registrySubkey,
                    DefaultRegistrySubkey,
                    StringComparison.Ordinal))
            {
                return;
            }
            bool changed = InternetSetOption(
                IntPtr.Zero,
                InternetOptionSettingsChanged,
                IntPtr.Zero,
                0
            );
            bool refreshed = InternetSetOption(
                IntPtr.Zero,
                InternetOptionRefresh,
                IntPtr.Zero,
                0
            );
            if (!changed || !refreshed)
            {
                throw new InvalidOperationException(
                    "SYSTEM_PROXY_REFRESH_FAILED"
                );
            }
        }

        private static ProxyRegistryValue ReadValue(
            string registrySubkey,
            string name
        )
        {
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(
                registrySubkey,
                false
            ))
            {
                if (key == null)
                {
                    throw new InvalidOperationException(
                        "SYSTEM_PROXY_REGISTRY_KEY_MISSING"
                    );
                }
                object value = key.GetValue(
                    name,
                    null,
                    RegistryValueOptions.DoNotExpandEnvironmentNames
                );
                if (value == null)
                {
                    return new ProxyRegistryValue
                    {
                        name = name,
                        exists = false,
                        value = null,
                        kind = -1
                    };
                }
                return new ProxyRegistryValue
                {
                    name = name,
                    exists = true,
                    value = value,
                    kind = (int)key.GetValueKind(name)
                };
            }
        }

        private static void RestoreValue(
            string registrySubkey,
            ProxyRegistryValue value
        )
        {
            if (value == null ||
                String.IsNullOrWhiteSpace(value.name))
            {
                throw new InvalidOperationException(
                    "SYSTEM_PROXY_STATE_INVALID"
                );
            }
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(
                registrySubkey,
                true
            ))
            {
                if (key == null)
                {
                    throw new InvalidOperationException(
                        "SYSTEM_PROXY_REGISTRY_KEY_MISSING"
                    );
                }
                if (!value.exists)
                {
                    key.DeleteValue(value.name, false);
                    return;
                }
                RegistryValueKind kind =
                    (RegistryValueKind)value.kind;
                object normalized = kind == RegistryValueKind.DWord
                    ? (object)Convert.ToInt32(value.value)
                    : Convert.ToString(value.value);
                key.SetValue(value.name, normalized, kind);
            }
        }

        private static ProxyRegistryValue FindValue(
            List<ProxyRegistryValue> values,
            string name
        )
        {
            foreach (ProxyRegistryValue value in values)
            {
                if (String.Equals(
                        value.name,
                        name,
                        StringComparison.Ordinal))
                {
                    return value;
                }
            }
            throw new InvalidOperationException(
                "SYSTEM_PROXY_STATE_INVALID"
            );
        }

        private static bool ValueEquals(
            ProxyRegistryValue left,
            ProxyRegistryValue right
        )
        {
            if (left == null || right == null ||
                left.exists != right.exists)
            {
                return false;
            }
            if (!left.exists)
            {
                return true;
            }
            if (left.kind != right.kind)
            {
                return false;
            }
            if (left.kind == (int)RegistryValueKind.DWord)
            {
                return Convert.ToInt32(left.value) ==
                    Convert.ToInt32(right.value);
            }
            return String.Equals(
                Convert.ToString(left.value),
                Convert.ToString(right.value),
                StringComparison.Ordinal
            );
        }

        private static string StatePath(string home)
        {
            string root = Path.Combine(
                Path.GetFullPath(home),
                ".llm-foundation"
            );
            Directory.CreateDirectory(root);
            return Path.Combine(root, "system-proxy-lease.json");
        }

        private static void WriteStateAtomic(
            string path,
            SystemProxyLeaseState state
        )
        {
            string temporary = path + ".tmp-" +
                Guid.NewGuid().ToString("N");
            byte[] bytes = new UTF8Encoding(false).GetBytes(
                new JavaScriptSerializer().Serialize(state) + "\n"
            );
            try
            {
                using (FileStream stream = new FileStream(
                    temporary,
                    FileMode.CreateNew,
                    FileAccess.Write,
                    FileShare.None,
                    4096,
                    FileOptions.WriteThrough
                ))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
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

        private static void StartWatchdog(
            int ownerPid,
            string home,
            string registrySubkey
        )
        {
            string executable = Process.GetCurrentProcess()
                .MainModule
                .FileName;
            bool testRegistry = IsAllowedTestRegistrySubkey(
                registrySubkey
            );
            string arguments = "--system-proxy-watchdog " +
                ownerPid.ToString(
                    System.Globalization.CultureInfo.InvariantCulture
                ) + " " + QuoteArgument(home);
            if (testRegistry)
            {
                arguments += " " + QuoteArgument(registrySubkey);
            }
            using (Process watchdog = Process.Start(
                new ProcessStartInfo
                {
                    FileName = executable,
                    Arguments = arguments,
                    WorkingDirectory = Path.GetDirectoryName(executable),
                    UseShellExecute = false,
                    CreateNoWindow = true
                }
            ))
            {
                if (watchdog == null)
                {
                    throw new InvalidOperationException(
                        "SYSTEM_PROXY_WATCHDOG_START_FAILED"
                    );
                }
                if (watchdog.WaitForExit(100) &&
                    watchdog.ExitCode != 0)
                {
                    throw new InvalidOperationException(
                        "SYSTEM_PROXY_WATCHDOG_START_FAILED"
                    );
                }
            }
        }

        private static string QuoteArgument(string value)
        {
            return "\"" + (value ?? "").Replace("\"", "\\\"") +
                "\"";
        }

        private static string MutexName()
        {
            return @"Local\K7AI.SystemProxyLease." + CurrentSid();
        }

        private static string CurrentSid()
        {
            SecurityIdentifier sid = WindowsIdentity
                .GetCurrent()
                .User;
            if (sid == null)
            {
                throw new InvalidOperationException(
                    "SYSTEM_PROXY_SID_MISSING"
                );
            }
            return sid.Value;
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

        private static void ReleaseMutex(
            Mutex mutex,
            bool acquired
        )
        {
            if (mutex == null)
            {
                return;
            }
            if (acquired)
            {
                try
                {
                    mutex.ReleaseMutex();
                }
                catch
                {
                }
            }
            mutex.Dispose();
        }

        private static ProxyRecoveryResult Failed(string reason)
        {
            return new ProxyRecoveryResult
            {
                status = "FAILED",
                cleanup_verified = false,
                lifecycle = new List<string>(),
                reason = reason
            };
        }
    }
}
