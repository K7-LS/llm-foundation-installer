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
    internal static class ClientDetector
    {
        private static readonly Dictionary<string, string> Commands =
            new Dictionary<string, string>(StringComparer.Ordinal)
            {
                { "codex-cli", "codex.exe" },
                { "claude-code", "claude.exe" },
                { "opencode", "opencode.exe" }
            };
        private static readonly System.Text.RegularExpressions.Regex Version =
            new System.Text.RegularExpressions.Regex(
                @"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)",
                System.Text.RegularExpressions.RegexOptions.CultureInvariant
            );

        public static string DetectVersion(string clientId)
        {
            string command;
            if (!Commands.TryGetValue(clientId, out command))
            {
                return null;
            }
            try
            {
                ProcessStartInfo start = new ProcessStartInfo
                {
                    FileName = command,
                    Arguments = "--version",
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
                        return null;
                    }
                    string output = process.StandardOutput.ReadToEnd() + " " +
                        process.StandardError.ReadToEnd();
                    if (!process.WaitForExit(5000))
                    {
                        try
                        {
                            process.Kill();
                        }
                        catch
                        {
                        }
                        return null;
                    }
                    System.Text.RegularExpressions.Match match =
                        Version.Match(output);
                    return match.Success ? match.Groups[1].Value : null;
                }
            }
            catch
            {
                return null;
            }
        }
    }
}
