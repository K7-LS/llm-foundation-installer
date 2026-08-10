using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace Foundation.ManagedLauncher
{
    internal sealed class LaunchReceipt
    {
        internal string Target { get; private set; }
        internal string UpdaterPath { get; private set; }
        internal string VendorExecutablePath { get; private set; }
        internal string ReceiptSha256 { get; private set; }

        internal static LaunchReceipt Read(string launcherPath, string target)
        {
            string receiptPath = Path.ChangeExtension(launcherPath, ".receipt.json");
            if (!File.Exists(receiptPath))
            {
                throw new InvalidOperationException("committed receipt is missing");
            }

            string content = File.ReadAllText(receiptPath, new UTF8Encoding(false, true));
            if (SessionRecovery.ContainsUnicodeEscapeInPropertyName(content))
            {
                throw new InvalidOperationException("receipt contains escaped property name");
            }
            JavaScriptSerializer serializer = new JavaScriptSerializer();
            Dictionary<string, object> receipt = serializer.DeserializeObject(content)
                as Dictionary<string, object>;
            string[] required = new[]
            {
                "schema_version", "target", "launcher_path", "launcher_sha256",
                "updater_path", "vendor_executable_path"
            };
            if (receipt == null || receipt.Count != required.Length ||
                required.Any(key => !receipt.ContainsKey(key)) ||
                HasDuplicateKeys(content, required))
            {
                throw new InvalidOperationException("receipt schema is invalid");
            }

            int schemaVersion;
            if (!Int32.TryParse(Convert.ToString(receipt["schema_version"]), out schemaVersion) ||
                schemaVersion != 1 || !String.Equals(Convert.ToString(receipt["target"]), target,
                    StringComparison.Ordinal) || !PathsEqual(Convert.ToString(receipt["launcher_path"]), launcherPath) ||
                !String.Equals(Convert.ToString(receipt["launcher_sha256"]), Sha256(launcherPath),
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("receipt does not bind this launcher");
            }

            string updaterPath = RequireExistingPath(receipt, "updater_path");
            string vendorPath = RequireExistingPath(receipt, "vendor_executable_path");
            return new LaunchReceipt
            {
                Target = target,
                UpdaterPath = updaterPath,
                VendorExecutablePath = vendorPath,
                ReceiptSha256 = Sha256(receiptPath)
            };
        }

        private static string RequireExistingPath(Dictionary<string, object> receipt, string key)
        {
            string value = Convert.ToString(receipt[key]);
            if (String.IsNullOrWhiteSpace(value) || !Path.IsPathRooted(value) || !File.Exists(value))
            {
                throw new InvalidOperationException("receipt path is invalid: " + key);
            }
            return Path.GetFullPath(value);
        }

        private static bool HasDuplicateKeys(string json, IEnumerable<string> keys)
        {
            foreach (string key in keys)
            {
                int count = 0;
                int index = 0;
                string token = "\"" + key + "\"";
                while ((index = json.IndexOf(token, index, StringComparison.Ordinal)) >= 0)
                {
                    count++;
                    index += token.Length;
                }
                if (count != 1)
                {
                    return true;
                }
            }
            return false;
        }

        private static bool PathsEqual(string left, string right)
        {
            return !String.IsNullOrWhiteSpace(left) && Path.IsPathRooted(left) &&
                String.Equals(Path.GetFullPath(left), Path.GetFullPath(right),
                    StringComparison.OrdinalIgnoreCase);
        }

        internal static string Sha256(string path)
        {
            using (SHA256 hash = SHA256.Create())
            using (FileStream input = File.OpenRead(path))
            {
                return BitConverter.ToString(hash.ComputeHash(input)).Replace("-", String.Empty)
                    .ToLowerInvariant();
            }
        }
    }

    internal static class WindowsArgv
    {
        internal static string Serialize(string[] arguments)
        {
            if (arguments == null || arguments.Length == 0)
            {
                return String.Empty;
            }
            return String.Join(" ", arguments.Select(Quote));
        }

        private static string Quote(string argument)
        {
            if (String.IsNullOrEmpty(argument))
            {
                return "\"\"";
            }
            if (argument.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
            {
                return argument;
            }

            StringBuilder result = new StringBuilder("\"");
            int backslashes = 0;
            foreach (char character in argument)
            {
                if (character == '\\')
                {
                    backslashes++;
                    continue;
                }
                if (character == '"')
                {
                    result.Append('\\', backslashes * 2 + 1);
                    result.Append('"');
                    backslashes = 0;
                    continue;
                }
                result.Append('\\', backslashes);
                result.Append(character);
                backslashes = 0;
            }
            result.Append('\\', backslashes * 2);
            result.Append('"');
            return result.ToString();
        }
    }

    internal static class NativeJob
    {
        private const uint JobObjectExtendedLimitInformation = 9;
        private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
        private const uint CREATE_SUSPENDED = 0x00000004;
        private const uint CREATE_NO_WINDOW = 0x08000000;

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS
        {
            internal ulong ReadOperationCount;
            internal ulong WriteOperationCount;
            internal ulong OtherOperationCount;
            internal ulong ReadTransferCount;
            internal ulong WriteTransferCount;
            internal ulong OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            internal long PerProcessUserTimeLimit;
            internal long PerJobUserTimeLimit;
            internal uint LimitFlags;
            internal UIntPtr MinimumWorkingSetSize;
            internal UIntPtr MaximumWorkingSetSize;
            internal uint ActiveProcessLimit;
            internal IntPtr Affinity;
            internal uint PriorityClass;
            internal uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            internal JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            internal IO_COUNTERS IoInfo;
            internal UIntPtr ProcessMemoryLimit;
            internal UIntPtr JobMemoryLimit;
            internal UIntPtr PeakProcessMemoryUsed;
            internal UIntPtr PeakJobMemoryUsed;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct STARTUPINFO
        {
            internal int cb;
            internal string lpReserved;
            internal string lpDesktop;
            internal string lpTitle;
            internal int dwX;
            internal int dwY;
            internal int dwXSize;
            internal int dwYSize;
            internal int dwXCountChars;
            internal int dwYCountChars;
            internal int dwFillAttribute;
            internal int dwFlags;
            internal short wShowWindow;
            internal short cbReserved2;
            internal IntPtr lpReserved2;
            internal IntPtr hStdInput;
            internal IntPtr hStdOutput;
            internal IntPtr hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct PROCESS_INFORMATION
        {
            internal IntPtr hProcess;
            internal IntPtr hThread;
            internal uint dwProcessId;
            internal uint dwThreadId;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr job, uint informationClass,
            IntPtr information, uint informationLength);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateProcess(string applicationName, StringBuilder commandLine,
            IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles, uint creationFlags,
            IntPtr environment, string currentDirectory, ref STARTUPINFO startupInfo,
            out PROCESS_INFORMATION processInformation);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint ResumeThread(IntPtr thread);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);

        internal static IntPtr CreateKillOnCloseJob()
        {
            IntPtr job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero)
            {
                throw new InvalidOperationException("cannot create Job Object");
            }
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            int size = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
            IntPtr pointer = Marshal.AllocHGlobal(size);
            try
            {
                Marshal.StructureToPtr(limits, pointer, false);
                if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, pointer, (uint)size))
                {
                    CloseHandle(job);
                    throw new InvalidOperationException("cannot configure Job Object");
                }
                return job;
            }
            finally
            {
                Marshal.FreeHGlobal(pointer);
            }
        }

        internal static void Assign(IntPtr job, Process process)
        {
            if (!AssignProcessToJobObject(job, process.Handle))
            {
                throw new InvalidOperationException("cannot assign updater to Job Object");
            }
        }

        internal sealed class ContainedUpdater : IDisposable
        {
            internal Process Process { get; private set; }
            private IntPtr nativeProcessHandle;

            internal ContainedUpdater(Process process, IntPtr processHandle)
            {
                Process = process;
                nativeProcessHandle = processHandle;
            }

            internal int GetExitCode()
            {
                uint exitCode;
                if (nativeProcessHandle == IntPtr.Zero ||
                    !GetExitCodeProcess(nativeProcessHandle, out exitCode))
                {
                    throw new InvalidOperationException("cannot read updater exit code");
                }
                return unchecked((int)exitCode);
            }

            public void Dispose()
            {
                if (Process != null)
                {
                    Process.Dispose();
                    Process = null;
                }
                if (nativeProcessHandle != IntPtr.Zero)
                {
                    CloseHandle(nativeProcessHandle);
                    nativeProcessHandle = IntPtr.Zero;
                }
            }
        }

        internal static ContainedUpdater StartContained(IntPtr job, string filename, string arguments)
        {
            STARTUPINFO startup = new STARTUPINFO();
            startup.cb = Marshal.SizeOf(typeof(STARTUPINFO));
            PROCESS_INFORMATION information;
            StringBuilder commandLine = new StringBuilder(
                WindowsArgv.Serialize(new[] { filename }) + " " + arguments);
            if (!CreateProcess(filename, commandLine, IntPtr.Zero, IntPtr.Zero, false,
                CREATE_SUSPENDED | CREATE_NO_WINDOW, IntPtr.Zero, null, ref startup, out information))
            {
                throw new InvalidOperationException("cannot create suspended updater");
            }
            bool transferredProcessHandle = false;
            try
            {
                if (!AssignProcessToJobObject(job, information.hProcess))
                {
                    throw new InvalidOperationException("cannot assign suspended updater to Job Object");
                }
                if (ResumeThread(information.hThread) == UInt32.MaxValue)
                {
                    throw new InvalidOperationException("cannot resume updater");
                }
                ContainedUpdater updater = new ContainedUpdater(
                    Process.GetProcessById((int)information.dwProcessId), information.hProcess);
                transferredProcessHandle = true;
                return updater;
            }
            finally
            {
                CloseHandle(information.hThread);
                if (!transferredProcessHandle)
                {
                    CloseHandle(information.hProcess);
                }
            }
        }

        internal static void Close(IntPtr job)
        {
            if (job != IntPtr.Zero)
            {
                CloseHandle(job);
            }
        }
    }

    internal static class Program
    {
        private const int BlockedRecoveryExitCode = 70;
        private static readonly TimeSpan MutationCutoff = TimeSpan.FromSeconds(22);
        private static readonly TimeSpan KillCutoff = TimeSpan.FromSeconds(25);
        private static readonly TimeSpan HardDeadline = TimeSpan.FromSeconds(30);

        private enum UpdaterResult
        {
            Success,
            UpdaterFailed
        }

        private static int Main(string[] args)
        {
            try
            {
                string launcherPath = Path.GetFullPath(Process.GetCurrentProcess().MainModule.FileName);
                string target = ResolveTarget(launcherPath);
                LaunchReceipt receipt = LaunchReceipt.Read(launcherPath, target);
                long startTick = Stopwatch.GetTimestamp();
                long deadlineTick = AddSeconds(startTick, HardDeadline);
                string userProfile = Environment.GetEnvironmentVariable("USERPROFILE");
                if (String.IsNullOrWhiteSpace(userProfile))
                {
                    userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                }
                if (!SessionRecovery.TryRecover(userProfile, receipt, deadlineTick))
                {
                    Console.Error.WriteLine("BLOCKED_SESSION_RECOVERY");
                    return BlockedRecoveryExitCode;
                }
                RunUpdater(receipt, startTick, deadlineTick);
                bool hasRecoveryJournal = SessionRecovery.HasActiveJournal(userProfile, receipt.Target);
                if (hasRecoveryJournal &&
                    !SessionRecovery.TryRecover(userProfile, receipt, deadlineTick))
                {
                    Console.Error.WriteLine("BLOCKED_SESSION_RECOVERY");
                    return BlockedRecoveryExitCode;
                }
                return RunVendor(receipt.VendorExecutablePath, args);
            }
            catch (Exception error)
            {
                Console.Error.WriteLine("BLOCKED_MANAGED_LAUNCHER: " + error.Message);
                return 69;
            }
        }

        private static string ResolveTarget(string launcherPath)
        {
            string filename = Path.GetFileName(launcherPath);
            string[] known = new[] { "claude-managed.exe", "codex-managed.exe", "opencode-managed.exe" };
            if (!known.Contains(filename, StringComparer.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("managed launcher filename is invalid");
            }
            return filename.Substring(0, filename.IndexOf("-managed.exe", StringComparison.OrdinalIgnoreCase));
        }

        private static UpdaterResult RunUpdater(LaunchReceipt receipt, long startTick, long hardDeadlineTick)
        {
            long mutationTick = AddSeconds(startTick, MutationCutoff);
            long killTick = AddSeconds(startTick, KillCutoff);
            string[] updaterArguments = new[]
            {
                "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", receipt.UpdaterPath, "-ManagedPreflight", "-TransactionId",
                Guid.NewGuid().ToString("D"), "-StartTick", startTick.ToString(),
                "-MutationCutoffTick", mutationTick.ToString(), "-KillTick", killTick.ToString(),
                "-HardDeadlineTick", hardDeadlineTick.ToString(), "-StopwatchFrequency",
                Stopwatch.Frequency.ToString()
            };
            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
                "WindowsPowerShell\\v1.0\\powershell.exe");
            start.Arguments = WindowsArgv.Serialize(updaterArguments);
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            IntPtr job = NativeJob.CreateKillOnCloseJob();
            try
            {
                using (NativeJob.ContainedUpdater updater = NativeJob.StartContained(job, start.FileName, start.Arguments))
                {
                    Process process = updater.Process;
                    while (!process.HasExited && Stopwatch.GetTimestamp() < killTick)
                    {
                        Thread.Sleep(20);
                    }
                    if (!process.HasExited)
                    {
                        NativeJob.Close(job);
                        job = IntPtr.Zero;
                        return UpdaterResult.UpdaterFailed;
                    }
                    return updater.GetExitCode() == 0
                        ? UpdaterResult.Success : UpdaterResult.UpdaterFailed;
                }
            }
            finally
            {
                NativeJob.Close(job);
            }
        }

        private static int RunVendor(string vendorPath, string[] arguments)
        {
            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = vendorPath;
            start.Arguments = WindowsArgv.Serialize(arguments);
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            using (Process process = Process.Start(start))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }

        private static long AddSeconds(long startTick, TimeSpan duration)
        {
            return checked(startTick + (long)(duration.TotalSeconds * Stopwatch.Frequency));
        }
    }
}
