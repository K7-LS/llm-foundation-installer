using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

namespace Foundation.OfficeCliShim
{
    internal sealed class CommandPolicy
    {
        public int schema_version { get; set; }
        public string[] allowed_commands { get; set; }
        public Dictionary<string, string> process_environment { get; set; }

        public string Validate(string[] arguments)
        {
            if (arguments == null || arguments.Length == 0)
            {
                return "BLOCKED_BARE_INVOCATION";
            }

            if (arguments.Length == 1 && (
                String.Equals(arguments[0], "--version", StringComparison.Ordinal) ||
                String.Equals(arguments[0], "--help", StringComparison.Ordinal) ||
                String.Equals(arguments[0], "-h", StringComparison.Ordinal) ||
                String.Equals(arguments[0], "-?", StringComparison.Ordinal)))
            {
                return null;
            }

            int commandIndex = String.Equals(
                arguments[0], "--json", StringComparison.Ordinal) ? 1 : 0;
            if (commandIndex >= arguments.Length)
            {
                return "BLOCKED_UNKNOWN_COMMAND";
            }

            string command = arguments[commandIndex];
            if (String.IsNullOrEmpty(command) || command.StartsWith("-") ||
                command.StartsWith("/") || command.StartsWith("@") || !IsAscii(command))
            {
                return "BLOCKED_UNKNOWN_COMMAND";
            }

            if (Contains(ManagedInstallCommands, command))
            {
                return "BLOCKED_MANAGED_INSTALL";
            }

            return Contains(allowed_commands, command)
                ? null
                : "BLOCKED_UNKNOWN_COMMAND";
        }

        private static readonly string[] ManagedInstallCommands = new[]
        {
            "install", "skills", "skill", "mcp", "mcp-serve", "config", "update",
            "self-update", "__update-check__", "__resident-serve__"
        };

        private static bool Contains(IEnumerable<string> values, string value)
        {
            return values != null && values.Contains(
                value, StringComparer.Ordinal);
        }

        private static bool IsAscii(string value)
        {
            return value.All(character => character <= 0x7f);
        }
    }

    internal static class WindowsArgv
    {
        public static string Serialize(string[] arguments)
        {
            if (arguments == null || arguments.Length == 0)
            {
                return String.Empty;
            }

            return String.Join(" ", arguments.Select(Quote));
        }

        private static string Quote(string argument)
        {
            if (argument == null || argument.Length == 0)
            {
                return "\"\"";
            }

            if (argument.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
            {
                return argument;
            }

            StringBuilder result = new StringBuilder();
            result.Append('"');
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

    internal static class Program
    {
        private const string ExpectedPolicySha256 = "__POLICY_SHA256__";

        private static int Main(string[] args)
        {
            try
            {
                string foundationRoot = Path.GetFullPath(Path.Combine(
                    AppDomain.CurrentDomain.BaseDirectory, ".."));
                string privateDirectory = Path.Combine(
                    foundationRoot, "libexec", "officecli");
                string policyPath = Path.Combine(
                    privateDirectory, "officecli-command-policy.json");
                string executablePath = Path.Combine(
                    privateDirectory, "officecli.exe");
                CommandPolicy policy = ReadPolicy(policyPath);

                string blockCode = policy.Validate(args);
                if (blockCode != null)
                {
                    Console.Error.WriteLine(blockCode);
                    return 64;
                }

                if (!File.Exists(executablePath))
                {
                    Console.Error.WriteLine("OFFICECLI_PRIVATE_EXECUTABLE_MISSING");
                    return 65;
                }

                ProcessStartInfo start = new ProcessStartInfo();
                start.FileName = executablePath;
                start.Arguments = WindowsArgv.Serialize(args);
                start.UseShellExecute = false;
                start.CreateNoWindow = true;
                foreach (KeyValuePair<string, string> variable in policy.process_environment)
                {
                    start.EnvironmentVariables[variable.Key] = variable.Value;
                }
                using (Process process = Process.Start(start))
                {
                    process.WaitForExit();
                    return process.ExitCode;
                }
            }
            catch (Exception error)
            {
                Console.Error.WriteLine("BLOCKED_OFFICECLI_POLICY: " + error.Message);
                return 66;
            }
        }

        private static CommandPolicy ReadPolicy(string policyPath)
        {
            if (!File.Exists(policyPath) || !String.Equals(
                Sha256(policyPath), ExpectedPolicySha256,
                StringComparison.Ordinal))
            {
                throw new InvalidOperationException("policy hash mismatch");
            }

            JavaScriptSerializer serializer = new JavaScriptSerializer();
            CommandPolicy policy = serializer.Deserialize<CommandPolicy>(
                File.ReadAllText(policyPath, new UTF8Encoding(false, true)));
            if (policy == null)
            {
                throw new InvalidOperationException("policy is empty");
            }
            if (policy.schema_version != 1 || policy.allowed_commands == null ||
                policy.process_environment == null ||
                !policy.process_environment.TryGetValue(
                    "OFFICECLI_NO_AUTO_INSTALL", out string noAutoInstall) ||
                !String.Equals(noAutoInstall, "1", StringComparison.Ordinal) ||
                !policy.process_environment.TryGetValue(
                    "OFFICECLI_SKIP_UPDATE", out string skipUpdate) ||
                !String.Equals(skipUpdate, "1", StringComparison.Ordinal))
            {
                throw new InvalidOperationException("policy is invalid");
            }

            return policy;
        }

        private static string Sha256(string path)
        {
            using (SHA256 hash = SHA256.Create())
            using (FileStream input = File.OpenRead(path))
            {
                return BitConverter.ToString(hash.ComputeHash(input)).Replace(
                    "-", String.Empty).ToLowerInvariant();
            }
        }
    }
}
