using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

namespace K7.OfficeCliPdfExporter
{
    internal static class Program
    {
        private const string Info = "{\"name\":\"k7-officecli-pdf\",\"version\":\"1.0.0\",\"protocol\":1,\"kinds\":[\"exporter\"],\"extensions\":[\".pdf\"],\"runtime\":\"dotnet\",\"idle_timeout_seconds\":{\"default\":60,\"verbs\":{\"export\":120}},\"supports\":[\"from:docx\",\"from:xlsx\",\"from:pptx\"]}";

        [STAThread]
        private static int Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            if (args.Length == 1 && args[0] == "--info")
            {
                Console.WriteLine(Info);
                return 0;
            }
            string source;
            string target;
            string error;
            if (!TryParse(args, out source, out target, out error))
            {
                Console.Error.WriteLine(error);
                return 2;
            }

            string extension = Path.GetExtension(source).ToLowerInvariant();
            if (extension != ".docx" && extension != ".xlsx" && extension != ".pptx")
            {
                Console.Error.WriteLine("unsupported_source");
                return 2;
            }
            if (!File.Exists(source))
            {
                Console.Error.WriteLine("not_found");
                return 3;
            }
            source = Path.GetFullPath(source);
            target = Path.GetFullPath(target);
            if (String.Equals(source, target, StringComparison.OrdinalIgnoreCase))
            {
                Console.Error.WriteLine("source_target_alias");
                return 2;
            }
            if (!String.Equals(Path.GetExtension(target), ".pdf", StringComparison.OrdinalIgnoreCase))
            {
                Console.Error.WriteLine("invalid_target");
                return 2;
            }

            string sourceHash = Sha256(source);
            long sourceLength = new FileInfo(source).Length;
            DateTime sourceWrite = File.GetLastWriteTimeUtc(source);
            string targetDirectory = Path.GetDirectoryName(target);
            if (String.IsNullOrWhiteSpace(targetDirectory)) return 2;
            Directory.CreateDirectory(targetDirectory);
            string temporary = Path.Combine(
                targetDirectory,
                "." + Path.GetFileNameWithoutExtension(target) + ".k7-" +
                Guid.NewGuid().ToString("N") + ".pdf");
            Dictionary<string, HashSet<int>> officeBefore = SnapshotOffice();
            Timer watchdog = new Timer(delegate(object state)
            {
                try { CleanupOwnedOffice(officeBefore); } catch { }
                try { Process.GetCurrentProcess().Kill(); } catch { Environment.FailFast("office_export_timeout"); }
            }, null, TimeSpan.FromSeconds(120), Timeout.InfiniteTimeSpan);
            try
            {
                Console.Error.WriteLine("heartbeat: office export started");
                if (extension == ".docx") ExportWord(source, temporary);
                else if (extension == ".xlsx") ExportExcel(source, temporary);
                else ExportPowerPoint(source, temporary);

                if (sourceLength != new FileInfo(source).Length ||
                    sourceWrite != File.GetLastWriteTimeUtc(source) ||
                    !String.Equals(sourceHash, Sha256(source), StringComparison.Ordinal))
                    throw new InvalidOperationException("source_modified");
                PdfMetrics metrics = ValidatePdf(temporary);
                AtomicMove(temporary, target);
                Console.WriteLine(
                    "{\"ok\":true,\"pages\":" + metrics.Pages.ToString() +
                    ",\"page_sizes\":" + metrics.PageSizesJson +
                    ",\"sha256\":\"" + Sha256(target) + "\"}"
                );
                Console.Error.WriteLine("heartbeat: office export completed");
                return 0;
            }
            catch (COMException errorValue)
            {
                Console.Error.WriteLine("office_com_error: " + errorValue.ErrorCode.ToString("X8"));
                return 6;
            }
            catch (Exception errorValue)
            {
                Console.Error.WriteLine("internal_error: " + errorValue.Message);
                return 7;
            }
            finally
            {
                watchdog.Dispose();
                if (File.Exists(temporary)) File.Delete(temporary);
                CleanupOwnedOffice(officeBefore);
            }
        }

        private static bool TryParse(string[] args, out string source, out string target, out string error)
        {
            source = null;
            target = null;
            error = "invalid_argument";
            if (args.Length < 4 || args[0] != "export" || args[2] != "--out") return false;
            if (args.Length != 4 && !(args.Length == 6 && args[4] == "--options")) return false;
            source = args[1];
            target = args[3];
            return !String.IsNullOrWhiteSpace(source) && !String.IsNullOrWhiteSpace(target);
        }

        private static void ExportWord(string source, string target)
        {
            object application = null;
            object document = null;
            try
            {
                Type type = Type.GetTypeFromProgID("Word.Application", true);
                dynamic app = application = Activator.CreateInstance(type);
                app.Visible = false;
                app.DisplayAlerts = 0;
                app.AutomationSecurity = 3;
                dynamic doc = document = app.Documents.Open(
                    FileName: source, ReadOnly: true, AddToRecentFiles: false, Visible: false);
                doc.ExportAsFixedFormat(target, 17);
                doc.Close(false);
                document = null;
                app.Quit(false);
                application = null;
            }
            finally
            {
                Release(document);
                Release(application);
            }
        }

        private static void ExportExcel(string source, string target)
        {
            object application = null;
            object workbook = null;
            try
            {
                Type type = Type.GetTypeFromProgID("Excel.Application", true);
                dynamic app = application = Activator.CreateInstance(type);
                app.Visible = false;
                app.DisplayAlerts = false;
                app.AutomationSecurity = 3;
                dynamic book = workbook = app.Workbooks.Open(source, 0, true);
                book.ExportAsFixedFormat(0, target);
                book.Close(false);
                workbook = null;
                app.Quit();
                application = null;
            }
            finally
            {
                Release(workbook);
                Release(application);
            }
        }

        private static void ExportPowerPoint(string source, string target)
        {
            object application = null;
            object presentation = null;
            try
            {
                Type type = Type.GetTypeFromProgID("PowerPoint.Application", true);
                dynamic app = application = Activator.CreateInstance(type);
                app.DisplayAlerts = 1;
                app.AutomationSecurity = 3;
                dynamic deck = presentation = app.Presentations.Open(source, true, true, false);
                deck.SaveAs(target, 32);
                deck.Close();
                presentation = null;
                app.Quit();
                application = null;
            }
            finally
            {
                Release(presentation);
                Release(application);
            }
        }

        private sealed class PdfMetrics
        {
            public int Pages;
            public string PageSizesJson;
        }

        private static PdfMetrics ValidatePdf(string path)
        {
            if (!File.Exists(path) || new FileInfo(path).Length < 8)
                throw new InvalidOperationException("empty_pdf");
            byte[] header = new byte[5];
            using (FileStream input = File.OpenRead(path)) input.Read(header, 0, header.Length);
            if (Encoding.ASCII.GetString(header) != "%PDF-")
                throw new InvalidOperationException("invalid_pdf");
            string text = Encoding.GetEncoding(28591).GetString(File.ReadAllBytes(path));
            int pages = Regex.Matches(text, @"/Type\s*/Page(?!s)\b").Count;
            MatchCollection boxes = Regex.Matches(
                text,
                @"/MediaBox\s*\[\s*([-+0-9.]+)\s+([-+0-9.]+)\s+([-+0-9.]+)\s+([-+0-9.]+)\s*\]"
            );
            if (pages < 1 || boxes.Count < 1)
                throw new InvalidOperationException("pdf_page_geometry_missing");
            StringBuilder sizes = new StringBuilder("[");
            for (int index = 0; index < boxes.Count; index++)
            {
                if (index > 0) sizes.Append(',');
                sizes.Append("[\"").Append(boxes[index].Groups[1].Value)
                    .Append("\",\"").Append(boxes[index].Groups[2].Value)
                    .Append("\",\"").Append(boxes[index].Groups[3].Value)
                    .Append("\",\"").Append(boxes[index].Groups[4].Value)
                    .Append("\"]");
            }
            sizes.Append(']');
            return new PdfMetrics { Pages = pages, PageSizesJson = sizes.ToString() };
        }

        private static void AtomicMove(string source, string target)
        {
            if (!File.Exists(target)) { File.Move(source, target); return; }
            string backup = target + ".k7-backup-" + Guid.NewGuid().ToString("N");
            try { File.Replace(source, target, backup, true); }
            finally { if (File.Exists(backup)) File.Delete(backup); }
        }

        private static string Sha256(string path)
        {
            using (SHA256 algorithm = SHA256.Create())
            using (FileStream input = File.OpenRead(path))
                return BitConverter.ToString(algorithm.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
        }

        private static void Release(object value)
        {
            if (value != null && Marshal.IsComObject(value)) Marshal.FinalReleaseComObject(value);
        }

        private static Dictionary<string, HashSet<int>> SnapshotOffice()
        {
            Dictionary<string, HashSet<int>> result = new Dictionary<string, HashSet<int>>(
                StringComparer.OrdinalIgnoreCase);
            foreach (string name in new[] { "WINWORD", "EXCEL", "POWERPNT" })
            {
                HashSet<int> ids = new HashSet<int>();
                foreach (Process process in Process.GetProcessesByName(name))
                {
                    try { ids.Add(process.Id); }
                    finally { process.Dispose(); }
                }
                result[name] = ids;
            }
            return result;
        }

        private static void CleanupOwnedOffice(Dictionary<string, HashSet<int>> before)
        {
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();
            DateTime deadline = DateTime.UtcNow.AddSeconds(10);
            while (DateTime.UtcNow < deadline)
            {
                bool any = false;
                foreach (string name in before.Keys)
                    foreach (Process process in Process.GetProcessesByName(name))
                    {
                        try { if (!before[name].Contains(process.Id)) any = true; }
                        finally { process.Dispose(); }
                    }
                if (!any) return;
                System.Threading.Thread.Sleep(250);
            }
            foreach (string name in before.Keys)
                foreach (Process process in Process.GetProcessesByName(name))
                {
                    try
                    {
                        if (!before[name].Contains(process.Id) &&
                            String.IsNullOrEmpty(process.MainWindowTitle))
                        {
                            process.Kill();
                            process.WaitForExit(5000);
                        }
                    }
                    finally { process.Dispose(); }
                }
        }
    }
}
