using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace LlmFoundationInstaller
{
    internal static class FoundationWorkflow
    {
        // Таймаут операции движка (как и прежде — 120 с) и время на
        // дочитывание потоков после завершения или Kill.
        internal const int TimeoutMilliseconds = 120000;
        internal const int DrainMilliseconds = 10000;

        private static readonly HashSet<string> Commands =
            new HashSet<string>(
                new[] { "plan", "install", "doctor", "inventory", "rollback" },
                StringComparer.Ordinal
            );

        public static int Run(
            string bundleRoot,
            string command,
            string target,
            string home,
            string clientVersion,
            out string standardOutput,
            out string standardError
        )
        {
            return Run(
                bundleRoot,
                command,
                target,
                home,
                clientVersion,
                null,
                null,
                null,
                null,
                false,
                out standardOutput,
                out standardError
            );
        }

        public static int Run(
            string bundleRoot,
            string command,
            string target,
            string home,
            string clientVersion,
            string externalPackagePath,
            string releaseManifestPath,
            string releaseManifestSha256,
            IEnumerable<string> localExceptionPaths,
            bool confirmRemoveUnknown,
            out string standardOutput,
            out string standardError
        )
        {
            standardOutput = "";
            standardError = "";
            if (!Commands.Contains(command))
            {
                standardError = "Unsupported Foundation command";
                return 2;
            }
            TrustedPackage package;
            if (!ProductCatalog.TryGetAcceptedPackage(
                    bundleRoot,
                    target,
                    out package
                ))
            {
                standardError = "Target package is not accepted";
                return 30;
            }
            string powershell = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.Windows),
                "System32",
                "WindowsPowerShell",
                "v1.0",
                "powershell.exe"
            );
            if (!File.Exists(powershell))
            {
                standardError = "Windows PowerShell is unavailable";
                return 30;
            }
            string fullHome;
            try
            {
                fullHome = Path.GetFullPath(home);
            }
            catch
            {
                standardError = "Target home path is invalid";
                return 2;
            }
            using (RuntimePayload runtime = RuntimePayload.Prepare(
                package,
                externalPackagePath
            ))
            {
                string effectiveReleaseManifestPath =
                    String.IsNullOrWhiteSpace(releaseManifestPath)
                        ? runtime.ReleaseManifestPath
                        : Path.GetFullPath(releaseManifestPath);
                string effectiveReleaseManifestSha256 =
                    String.IsNullOrWhiteSpace(releaseManifestPath)
                        ? runtime.ReleaseManifestSha256
                        : releaseManifestSha256;
                List<string> arguments = new List<string>
                {
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    runtime.EnginePath,
                    command,
                    "-TargetHome",
                    fullHome,
                    "-Target",
                    target,
                    "-Json"
                };
                if (command == "plan" || command == "install")
                {
                    arguments.Add("-Package");
                    arguments.Add(runtime.PackagePath);
                    if (!String.IsNullOrWhiteSpace(
                            effectiveReleaseManifestPath))
                    {
                        arguments.Add("-ReleaseManifest");
                        arguments.Add(effectiveReleaseManifestPath);
                        arguments.Add("-ReleaseManifestSha256");
                        arguments.Add(effectiveReleaseManifestSha256);
                    }
                    List<string> exceptions = localExceptionPaths == null
                        ? new List<string>()
                        : localExceptionPaths.Where(path =>
                            !String.IsNullOrWhiteSpace(path)
                        ).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
                    if (exceptions.Count > 0)
                    {
                        arguments.Add("-LocalExceptionPath");
                        arguments.Add(String.Join("|", exceptions.ToArray()));
                    }
                    if (confirmRemoveUnknown)
                    {
                        arguments.Add("-ConfirmRemoveUnknown");
                    }
                }
                if (command == "plan" || command == "install" ||
                    command == "doctor")
                {
                    arguments.Add("-ClientId");
                    arguments.Add(package.client_id);
                    arguments.Add("-ClientVersion");
                    arguments.Add(clientVersion);
                }

                ProcessStartInfo start = new ProcessStartInfo
                {
                    FileName = powershell,
                    Arguments = String.Join(
                        " ",
                        arguments.Select(QuoteArgument).ToArray()
                    ),
                    WorkingDirectory = runtime.Root,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8
                };
                using (Process process = Process.Start(start))
                {
                    if (process == null)
                    {
                        standardError = "Foundation process did not start";
                        return 30;
                    }
                    // Оба потока читаются параллельно и с тем же таймаутом,
                    // что и ожидание процесса. Прежний порядок — сначала
                    // ReadToEnd stdout, потом stderr, потом WaitForExit —
                    // делал таймаут фиктивным: повисший движок держал stdout
                    // открытым, ReadToEnd не возвращался, и до WaitForExit
                    // дело не доходило никогда; а переполненный буфер stderr
                    // во время чтения stdout давал классический pipe-deadlock.
                    // Замечание Codex к плану переработки, 2026-09-02.
                    Task<string> outputTask =
                        process.StandardOutput.ReadToEndAsync();
                    Task<string> errorTask =
                        process.StandardError.ReadToEndAsync();
                    bool exited = process.WaitForExit(TimeoutMilliseconds);
                    if (!exited)
                    {
                        try
                        {
                            process.Kill();
                        }
                        catch
                        {
                        }
                    }
                    // После Kill потоки закрываются, и чтение завершается;
                    // ограничение здесь — страховка от зависшего дочернего
                    // процесса, унаследовавшего дескрипторы.
                    bool drained = Task.WaitAll(
                        new Task[] { outputTask, errorTask },
                        DrainMilliseconds
                    );
                    standardOutput = outputTask.IsCompleted && !outputTask.IsFaulted
                        ? outputTask.Result
                        : "";
                    standardError = errorTask.IsCompleted && !errorTask.IsFaulted
                        ? errorTask.Result
                        : "";
                    if (!exited)
                    {
                        standardError = "Foundation operation timed out";
                        return 30;
                    }
                    if (!drained)
                    {
                        standardError = "Foundation output could not be read";
                        return 30;
                    }
                    return process.ExitCode;
                }
            }
        }

        private static string QuoteArgument(string value)
        {
            if (value.Length > 0 &&
                !value.Any(character =>
                    Char.IsWhiteSpace(character) || character == '"'))
            {
                return value;
            }
            StringBuilder result = new StringBuilder();
            result.Append('"');
            int backslashes = 0;
            foreach (char character in value)
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
                backslashes = 0;
                result.Append(character);
            }
            result.Append('\\', backslashes * 2);
            result.Append('"');
            return result.ToString();
        }
    }
}
