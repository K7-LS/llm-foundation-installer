using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace Foundation.ManagedLauncher
{
    internal static class SessionRecovery
    {
        private static readonly string[] OperationNames = new[]
        {
            "move_destination_to_previous",
            "move_staging_to_destination",
            "write_state"
        };

        private static readonly string[] PhaseNames = new[]
        {
            "created", "staged", "move_destination_intent", "move_destination_applied",
            "move_staging_intent", "move_staging_applied", "state_write_intent",
            "state_write_applied", "committed"
        };

        internal static bool HasActiveJournal(string userProfile, string target)
        {
            return File.Exists(GetJournalPath(userProfile, target));
        }

        internal static bool TryRecover(string userProfile, LaunchReceipt receipt, long hardDeadlineTick)
        {
            string journalPath = GetJournalPath(userProfile, receipt.Target);
            if (!File.Exists(journalPath)) { return true; }
            if (DeadlineReached(hardDeadlineTick)) { return false; }

            Dictionary<string, object> journal;
            string stateRoot = Path.GetDirectoryName(journalPath);
            try
            {
                string content = File.ReadAllText(journalPath, new UTF8Encoding(false, true));
                if (ContainsUnicodeEscapeInPropertyName(content)) { return false; }
                JavaScriptSerializer serializer = new JavaScriptSerializer();
                journal = serializer.DeserializeObject(content) as Dictionary<string, object>;
                if (!IsValidJournal(journal, receipt, content, stateRoot, userProfile,
                    hardDeadlineTick)) { return false; }
            }
            catch (Exception) { return false; }

            bool recovered = false;
            Thread worker = new Thread(delegate()
            {
                try { recovered = RecoverCore(journal, journalPath, hardDeadlineTick); }
                catch (Exception) { recovered = false; }
            });
            worker.IsBackground = true;
            worker.Start();
            long remainingTicks = hardDeadlineTick - Stopwatch.GetTimestamp();
            if (remainingTicks <= 0) { return false; }
            long remainingMilliseconds = Math.Max(1L,
                remainingTicks * 1000L / Stopwatch.Frequency);
            if (!worker.Join((int)Math.Min(Int32.MaxValue, remainingMilliseconds)))
            {
                return false;
            }
            return recovered && !DeadlineReached(hardDeadlineTick);
        }

        private static bool RecoverCore(Dictionary<string, object> journal, string journalPath,
            long hardDeadlineTick)
        {
            string phase = (string)journal["phase"];
            string stagingPath = Path.GetFullPath((string)journal["staging_path"]);
            string previousPath = Path.GetFullPath((string)journal["previous_path"]);
            string destinationPath = Path.GetFullPath((string)journal["destination_path"]);
            string statePath = Path.GetFullPath((string)journal["state_path"]);
            string previousDestinationHash = (string)journal["previous_destination_sha256"];
            string expectedDestinationHash = (string)journal["expected_destination_sha256"];
            string previousStateHash = (string)journal["previous_state_sha256"];
            string expectedStateHash = (string)journal["expected_state_sha256"];
            string expectedStagingHash = (string)journal["expected_staging_sha256"];
            Dictionary<string, object> operations = (Dictionary<string, object>)journal["operations"];
            bool movedOld = Applied(operations, "move_destination_to_previous");
            bool movedNew = Applied(operations, "move_staging_to_destination");
            bool wroteState = Applied(operations, "write_state");

            CheckDeadline(hardDeadlineTick);
            RequireFingerprint(statePath, wroteState ? expectedStateHash : previousStateHash);
            RequireFingerprint(destinationPath,
                movedNew ? expectedDestinationHash : movedOld ? "absent" : previousDestinationHash);
            RequireFingerprint(previousPath, movedOld ? previousDestinationHash : "absent");
            RequireFingerprint(stagingPath, movedNew ? "absent" : expectedStagingHash);

            if (String.Equals(phase, "committed", StringComparison.Ordinal) || wroteState)
            {
                if (!movedNew || !wroteState) { return false; }
                DeleteEntry(previousPath, hardDeadlineTick);
                DeleteEntry(stagingPath, hardDeadlineTick);
                RequireFingerprint(destinationPath, expectedDestinationHash);
                RequireFingerprint(statePath, expectedStateHash);
            }
            else
            {
                if (movedNew) { DeleteEntry(destinationPath, hardDeadlineTick); }
                if (movedOld)
                {
                    MoveEntry(previousPath, destinationPath, hardDeadlineTick);
                }
                DeleteEntry(stagingPath, hardDeadlineTick);
                RequireFingerprint(destinationPath, previousDestinationHash);
                RequireFingerprint(statePath, previousStateHash);
            }

            CheckDeadline(hardDeadlineTick);
            File.Delete(journalPath);
            return true;
        }

        private static bool IsValidJournal(Dictionary<string, object> journal, LaunchReceipt receipt,
            string content, string stateRoot, string userProfile, long launcherHardDeadlineTick)
        {
            string[] required = new[]
            {
                "schema_version", "target", "transaction_id", "phase", "receipt_sha256",
                "start_tick", "mutation_cutoff_tick", "kill_tick", "hard_deadline_tick",
                "stopwatch_frequency", "previous_destination_sha256", "previous_state_sha256",
                "expected_staging_sha256", "expected_destination_sha256", "expected_state_sha256",
                "staging_path", "previous_path", "destination_path", "state_path", "operations"
            };
            if (journal == null || journal.Count != required.Length ||
                required.Any(key => !journal.ContainsKey(key)) ||
                required.Any(key => CountJsonKey(content, key) != 1) ||
                !(journal["schema_version"] is int) || (int)journal["schema_version"] != 1 ||
                !(journal["target"] is string) ||
                !String.Equals((string)journal["target"], receipt.Target, StringComparison.Ordinal) ||
                !(journal["transaction_id"] is string) ||
                !Guid.TryParseExact((string)journal["transaction_id"], "D", out Guid transactionId) ||
                !(journal["phase"] is string) ||
                !PhaseNames.Contains((string)journal["phase"], StringComparer.Ordinal) ||
                !(journal["receipt_sha256"] is string) ||
                !String.Equals((string)journal["receipt_sha256"], receipt.ReceiptSha256,
                    StringComparison.Ordinal) ||
                !HasTickContract(journal, launcherHardDeadlineTick))
            {
                return false;
            }
            foreach (string key in new[] { "previous_destination_sha256", "previous_state_sha256",
                "expected_staging_sha256", "expected_destination_sha256", "expected_state_sha256" })
            {
                if (!IsSha256OrAbsent(journal[key])) { return false; }
            }
            if (OperationNames.Any(name => CountJsonKey(content, name) != 1) ||
                CountJsonKey(content, "intent") != OperationNames.Length ||
                CountJsonKey(content, "applied") != OperationNames.Length)
            {
                return false;
            }

            string transactionRoot = Path.Combine(stateRoot, "transactions", transactionId.ToString("D"));
            if (!PathsEqual(journal["staging_path"] as string, Path.Combine(transactionRoot, "staging")) ||
                !PathsEqual(journal["previous_path"] as string, Path.Combine(transactionRoot, "previous")) ||
                !PathsEqual(journal["state_path"] as string, Path.Combine(stateRoot, "state.json")))
            {
                return false;
            }
            string skillsRoot = GetSkillsRoot(userProfile, receipt.Target);
            string destination = journal["destination_path"] as string;
            if (String.IsNullOrWhiteSpace(destination) || !Path.IsPathRooted(destination) ||
                !String.Equals(Path.GetDirectoryName(Path.GetFullPath(destination)),
                    Path.GetFullPath(skillsRoot), StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            foreach (string path in new[] { transactionRoot, (string)journal["staging_path"],
                (string)journal["previous_path"], destination, (string)journal["state_path"] })
            {
                if (HasReparseAtOrAbove(path)) { return false; }
            }
            return HasExactOperationMap(journal);
        }

        private static bool HasExactOperationMap(Dictionary<string, object> journal)
        {
            Dictionary<string, object> operations = journal["operations"] as Dictionary<string, object>;
            if (operations == null || operations.Count != OperationNames.Length ||
                OperationNames.Any(name => !operations.ContainsKey(name))) { return false; }
            foreach (string name in OperationNames)
            {
                Dictionary<string, object> record = operations[name] as Dictionary<string, object>;
                if (record == null || record.Count != 2 || !record.ContainsKey("intent") ||
                    !record.ContainsKey("applied") || !(record["intent"] is bool) ||
                    !(record["applied"] is bool) || ((bool)record["applied"] && !(bool)record["intent"]))
                {
                    return false;
                }
            }
            bool[] actual = OperationNames.SelectMany(name =>
            {
                Dictionary<string, object> record = (Dictionary<string, object>)operations[name];
                return new[] { (bool)record["intent"], (bool)record["applied"] };
            }).ToArray();
            string phase = (string)journal["phase"];
            int enabled = Array.IndexOf(PhaseNames, phase);
            bool[] expected = new bool[6];
            if (enabled >= 2)
            {
                int transition = enabled - 2;
                for (int index = 0; index <= transition && index < expected.Length; index++)
                    expected[index] = true;
            }
            if (String.Equals(phase, "committed", StringComparison.Ordinal))
                for (int index = 0; index < expected.Length; index++) expected[index] = true;
            return actual.SequenceEqual(expected);
        }

        private static bool HasTickContract(Dictionary<string, object> journal,
            long launcherHardDeadlineTick)
        {
            long start, mutation, kill, deadline, frequency;
            if (!TryExactInteger(journal["start_tick"], out start) ||
                !TryExactInteger(journal["mutation_cutoff_tick"], out mutation) ||
                !TryExactInteger(journal["kill_tick"], out kill) ||
                !TryExactInteger(journal["hard_deadline_tick"], out deadline) ||
                !TryExactInteger(journal["stopwatch_frequency"], out frequency) ||
                frequency != Stopwatch.Frequency || deadline > launcherHardDeadlineTick) return false;
            try
            {
                return mutation == checked(start + 22L * frequency) &&
                    kill == checked(start + 25L * frequency) &&
                    deadline == checked(start + 30L * frequency);
            }
            catch (OverflowException) { return false; }
        }

        private static bool TryExactInteger(object value, out long result)
        {
            if (value is int) { result = (int)value; return result > 0; }
            if (value is long) { result = (long)value; return result > 0; }
            result = 0;
            return false;
        }

        private static bool Applied(Dictionary<string, object> operations, string name)
        {
            return (bool)((Dictionary<string, object>)operations[name])["applied"];
        }

        private static string Fingerprint(string path)
        {
            if (!File.Exists(path) && !Directory.Exists(path)) { return "absent"; }
            if (HasReparseAtOrAbove(path)) { throw new InvalidOperationException("reparse path"); }
            if (File.Exists(path)) { return LaunchReceipt.Sha256(path); }
            StringBuilder canonical = new StringBuilder();
            foreach (string file in Directory.GetFiles(path, "*", SearchOption.AllDirectories)
                .OrderBy(value => value, StringComparer.Ordinal))
            {
                if (HasReparseAtOrAbove(file)) { throw new InvalidOperationException("reparse file"); }
                string relative = file.Substring(path.TrimEnd(Path.DirectorySeparatorChar).Length + 1)
                    .Replace('\\', '/');
                canonical.Append(relative).Append('\0').Append(LaunchReceipt.Sha256(file)).Append('\n');
            }
            using (SHA256 sha = SHA256.Create())
            {
                return BitConverter.ToString(sha.ComputeHash(new UTF8Encoding(false).GetBytes(
                    canonical.ToString()))).Replace("-", String.Empty).ToLowerInvariant();
            }
        }

        private static void RequireFingerprint(string path, string expected)
        {
            if (!String.Equals(Fingerprint(path), expected, StringComparison.Ordinal))
                throw new InvalidOperationException("fingerprint mismatch");
        }

        private static void MoveEntry(string source, string destination, long deadline)
        {
            CheckDeadline(deadline);
            if (Directory.Exists(source)) Directory.Move(source, destination);
            else if (File.Exists(source)) File.Move(source, destination);
            else throw new InvalidOperationException("recovery source missing");
            CheckDeadline(deadline);
        }

        private static void DeleteEntry(string path, long deadline)
        {
            CheckDeadline(deadline);
            if (Directory.Exists(path)) Directory.Delete(path, true);
            else if (File.Exists(path)) File.Delete(path);
            CheckDeadline(deadline);
        }

        private static void CheckDeadline(long deadline)
        {
            if (DeadlineReached(deadline)) { throw new TimeoutException(); }
        }

        private static bool DeadlineReached(long deadline)
        {
            return deadline <= 0 || Stopwatch.GetTimestamp() >= deadline;
        }

        private static bool IsSha256OrAbsent(object value)
        {
            string text = value as string;
            return String.Equals(text, "absent", StringComparison.Ordinal) ||
                (text != null && text.Length == 64 && text.All(character =>
                    (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f')));
        }

        private static string GetJournalPath(string userProfile, string target)
        {
            return Path.Combine(userProfile, ".llm-foundation", "state", "session-tools", target,
                "active-transaction.json");
        }

        private static string GetSkillsRoot(string userProfile, string target)
        {
            if (String.Equals(target, "claude", StringComparison.Ordinal))
                return Path.Combine(userProfile, ".claude", "skills");
            if (String.Equals(target, "codex", StringComparison.Ordinal))
                return Path.Combine(userProfile, ".agents", "skills");
            if (String.Equals(target, "opencode", StringComparison.Ordinal))
                return Path.Combine(userProfile, ".config", "opencode", "skills");
            throw new InvalidOperationException("unknown target");
        }

        private static bool PathsEqual(string left, string right)
        {
            return !String.IsNullOrWhiteSpace(left) && Path.IsPathRooted(left) &&
                String.Equals(Path.GetFullPath(left), Path.GetFullPath(right),
                    StringComparison.OrdinalIgnoreCase);
        }

        private static bool HasReparseAtOrAbove(string candidate)
        {
            string full = Path.GetFullPath(candidate);
            FileSystemInfo current = File.Exists(full) ? (FileSystemInfo)new FileInfo(full) :
                Directory.Exists(full) ? (FileSystemInfo)new DirectoryInfo(full) :
                new DirectoryInfo(Path.GetDirectoryName(full));
            while (current != null)
            {
                if (current.Exists && (current.Attributes & FileAttributes.ReparsePoint) != 0) return true;
                DirectoryInfo directory = current as DirectoryInfo;
                current = directory != null ? directory.Parent : ((FileInfo)current).Directory;
            }
            return false;
        }

        internal static bool ContainsUnicodeEscapeInPropertyName(string content)
        {
            for (int index = 0; index < content.Length; index++)
            {
                if (content[index] != '"') continue;
                int start = ++index;
                bool escaped = false;
                while (index < content.Length)
                {
                    if (content[index] == '\\') { escaped = true; index += 2; continue; }
                    if (content[index] == '"') break;
                    index++;
                }
                int after = index + 1;
                while (after < content.Length && Char.IsWhiteSpace(content[after])) after++;
                if (after < content.Length && content[after] == ':' && escaped &&
                    content.Substring(start, index - start).IndexOf("\\u",
                        StringComparison.OrdinalIgnoreCase) >= 0) return true;
            }
            return false;
        }

        private static int CountJsonKey(string content, string key)
        {
            int count = 0;
            int index = 0;
            string token = "\"" + key + "\"";
            while ((index = content.IndexOf(token, index, StringComparison.Ordinal)) >= 0)
            {
                count++;
                index += token.Length;
            }
            return count;
        }
    }
}
