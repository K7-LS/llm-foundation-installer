using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

namespace Foundation.ManagedLauncher
{
    internal static class SessionRecovery
    {
        internal static bool HasActiveJournal(string userProfile, string target)
        {
            return File.Exists(Path.Combine(userProfile, ".llm-foundation", "state", "session-tools",
                target, "active-transaction.json"));
        }

        internal static bool TryRecover(string userProfile, LaunchReceipt receipt, long hardDeadlineTick)
        {
            string stateRoot = Path.Combine(userProfile, ".llm-foundation", "state", "session-tools", receipt.Target);
            string journalPath = Path.Combine(stateRoot, "active-transaction.json");
            if (!File.Exists(journalPath))
            {
                return true;
            }
            if (hardDeadlineTick <= 0 || Stopwatch.GetTimestamp() >= hardDeadlineTick)
            {
                return false;
            }

            try
            {
                string content = File.ReadAllText(journalPath, new UTF8Encoding(false, true));
                if (ContainsUnicodeEscapeInPropertyName(content) || Stopwatch.GetTimestamp() >= hardDeadlineTick)
                {
                    return false;
                }
                JavaScriptSerializer serializer = new JavaScriptSerializer();
                Dictionary<string, object> journal = serializer.DeserializeObject(content)
                    as Dictionary<string, object>;
                if (!IsValidJournal(journal, receipt, content, stateRoot, userProfile) ||
                    Stopwatch.GetTimestamp() >= hardDeadlineTick)
                {
                    return false;
                }

                string phase = Convert.ToString(journal["phase"]);
                bool hasAppliedOperation = HasAppliedOperation(journal);
                if ((String.Equals(phase, "created", StringComparison.Ordinal) ||
                    String.Equals(phase, "staged", StringComparison.Ordinal)) && hasAppliedOperation)
                {
                    return false;
                }

                string stagingPath = Path.GetFullPath(Convert.ToString(journal["staging_path"]));
                if (!hasAppliedOperation)
                {
                    // До mutation безопасно удаляется только staging текущей transaction.
                    DeleteEntry(stagingPath, hardDeadlineTick);
                }
                else
                {
                    RestorePreviousDestination(journal, hardDeadlineTick);
                    DeleteEntry(stagingPath, hardDeadlineTick);
                }
                if (Stopwatch.GetTimestamp() >= hardDeadlineTick)
                {
                    return false;
                }
                File.Delete(journalPath);
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static bool IsValidJournal(Dictionary<string, object> journal, LaunchReceipt receipt,
            string content, string stateRoot, string userProfile)
        {
            string[] required = new[]
            {
                "schema_version", "target", "transaction_id", "phase", "receipt_sha256",
                "start_tick", "mutation_cutoff_tick", "kill_tick", "hard_deadline_tick",
                "stopwatch_frequency", "previous_state_sha256", "expected_destination_sha256",
                "expected_state_sha256", "staging_path", "previous_path", "destination_path",
                "state_path", "operations"
            };
            if (journal == null || journal.Count != required.Length || required.Any(key => !journal.ContainsKey(key)) ||
                required.Any(key => CountJsonKey(content, key) != 1) ||
                !(journal["schema_version"] is int) || (int)journal["schema_version"] != 1 ||
                !String.Equals(Convert.ToString(journal["target"]), receipt.Target, StringComparison.Ordinal) ||
                !Guid.TryParseExact(Convert.ToString(journal["transaction_id"]), "D", out Guid ignored) ||
                !String.Equals(Convert.ToString(journal["receipt_sha256"]), receipt.ReceiptSha256,
                    StringComparison.OrdinalIgnoreCase) || !HasTickContract(journal) ||
                !IsSha256OrAbsent(journal["previous_state_sha256"]) ||
                !IsSha256OrAbsent(journal["expected_destination_sha256"]) ||
                !IsSha256OrAbsent(journal["expected_state_sha256"]))
            {
                return false;
            }

            foreach (string key in new[] { "staging_path", "previous_path", "destination_path" })
            {
                if (!IsPathWithinAny(Convert.ToString(journal[key]), GetAllowedRoots(userProfile, receipt.Target)))
                {
                    return false;
                }
            }
            if (!IsPathWithin(Convert.ToString(journal["state_path"]), stateRoot)) { return false; }
            object operations = journal["operations"];
            Dictionary<string, object> operationMap = operations as Dictionary<string, object>;
            if (operationMap == null || operationMap.Count == 0 || operationMap.Values.Any(value =>
                !(value is Dictionary<string, object>) ||
                !((Dictionary<string, object>)value).ContainsKey("intent") ||
                !((Dictionary<string, object>)value).ContainsKey("applied") ||
                !(((Dictionary<string, object>)value)["intent"] is bool) ||
                !(((Dictionary<string, object>)value)["applied"] is bool) ||
                ((bool)((Dictionary<string, object>)value)["applied"] &&
                    !(bool)((Dictionary<string, object>)value)["intent"])))
            {
                return false;
            }
            return true;
        }

        private static bool HasAppliedOperation(Dictionary<string, object> journal)
        {
            Dictionary<string, object> operations = journal["operations"] as Dictionary<string, object>;
            return operations.Values.Any(value => Convert.ToBoolean(
                ((Dictionary<string, object>)value)["applied"]));
        }

        private static void RestorePreviousDestination(Dictionary<string, object> journal, long hardDeadlineTick)
        {
            string previousPath = Path.GetFullPath(Convert.ToString(journal["previous_path"]));
            string destinationPath = Path.GetFullPath(Convert.ToString(journal["destination_path"]));
            if (Stopwatch.GetTimestamp() >= hardDeadlineTick) { throw new TimeoutException(); }
            DeleteEntry(destinationPath, hardDeadlineTick);
            if (Directory.Exists(previousPath))
            {
                Directory.Move(previousPath, destinationPath);
                return;
            }
            if (File.Exists(previousPath))
            {
                File.Move(previousPath, destinationPath);
                return;
            }
            throw new InvalidOperationException("previous destination is missing");
        }

        private static void DeleteEntry(string path, long hardDeadlineTick)
        {
            if (Stopwatch.GetTimestamp() >= hardDeadlineTick) { throw new TimeoutException(); }
            if (Directory.Exists(path))
            {
                Directory.Delete(path, true);
            }
            else if (File.Exists(path))
            {
                File.Delete(path);
            }
        }

        private static bool HasTickContract(Dictionary<string, object> journal)
        {
            long start, mutation, kill, deadline, frequency;
            return TryPositiveInt64(journal["start_tick"], out start) &&
                TryPositiveInt64(journal["mutation_cutoff_tick"], out mutation) &&
                TryPositiveInt64(journal["kill_tick"], out kill) &&
                TryPositiveInt64(journal["hard_deadline_tick"], out deadline) &&
                TryPositiveInt64(journal["stopwatch_frequency"], out frequency) &&
                frequency == Stopwatch.Frequency && start < mutation && mutation < kill && kill < deadline;
        }

        private static bool TryPositiveInt64(object value, out long result)
        {
            if (value is string || value == null)
            {
                result = 0;
                return false;
            }
            try
            {
                result = Convert.ToInt64(value);
                return result > 0;
            }
            catch (Exception)
            {
                result = 0;
                return false;
            }
        }

        private static bool IsSha256OrAbsent(object value)
        {
            string text = value as string;
            return String.Equals(text, "absent", StringComparison.Ordinal) ||
                (text != null && text.Length == 64 && text.All(character =>
                    (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f')));
        }

        private static string[] GetAllowedRoots(string userProfile, string target)
        {
            string skillsRoot;
            if (String.Equals(target, "claude", StringComparison.Ordinal))
                skillsRoot = Path.Combine(userProfile, ".claude", "skills");
            else if (String.Equals(target, "codex", StringComparison.Ordinal))
                skillsRoot = Path.Combine(userProfile, ".agents", "skills");
            else if (String.Equals(target, "opencode", StringComparison.Ordinal))
                skillsRoot = Path.Combine(userProfile, ".config", "opencode", "skills");
            else throw new InvalidOperationException("unknown target");
            return new[] { Path.Combine(userProfile, ".llm-foundation", "state", "session-tools", target), skillsRoot };
        }

        private static bool IsPathWithinAny(string candidate, IEnumerable<string> roots)
        {
            return roots.Any(root => IsPathWithin(candidate, root));
        }

        internal static bool ContainsUnicodeEscapeInPropertyName(string content)
        {
            for (int index = 0; index < content.Length; index++)
            {
                if (content[index] != '"') { continue; }
                int start = ++index;
                bool escaped = false;
                while (index < content.Length)
                {
                    if (content[index] == '\\') { escaped = true; index += 2; continue; }
                    if (content[index] == '"') { break; }
                    index++;
                }
                int after = index + 1;
                while (after < content.Length && Char.IsWhiteSpace(content[after])) { after++; }
                if (after < content.Length && content[after] == ':' && escaped &&
                    content.Substring(start, index - start).IndexOf("\\u", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    return true;
                }
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

        private static bool IsPathWithin(string candidate, string root)
        {
            if (String.IsNullOrWhiteSpace(candidate) || !Path.IsPathRooted(candidate))
            {
                return false;
            }
            string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            string fullCandidate = Path.GetFullPath(candidate);
            return fullCandidate.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase) &&
                !IsReparsePoint(fullCandidate) &&
                !HasReparseAncestor(fullCandidate, fullRoot);
        }

        private static bool IsReparsePoint(string path)
        {
            if (!File.Exists(path) && !Directory.Exists(path))
            {
                return false;
            }
            FileAttributes attributes = File.GetAttributes(path);
            return (attributes & FileAttributes.ReparsePoint) == FileAttributes.ReparsePoint;
        }

        private static bool HasReparseAncestor(string candidate, string root)
        {
            DirectoryInfo current = new DirectoryInfo(Path.GetDirectoryName(candidate));
            while (current != null && current.FullName.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            {
                if ((current.Attributes & FileAttributes.ReparsePoint) == FileAttributes.ReparsePoint)
                {
                    return true;
                }
                current = current.Parent;
            }
            return false;
        }
    }
}
