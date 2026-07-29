using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class VsCodeTrustRecord
    {
        public string executable_path { get; set; }
        public string signature_status { get; set; }
        public string signer_subject { get; set; }
        public string extension_publisher { get; set; }
        public string extension_name { get; set; }
        public string extension_path { get; set; }
        public bool code_running { get; set; }
    }

    internal sealed class VsCodeExtensionManifest
    {
        public string publisher { get; set; }
        public string name { get; set; }
    }

    internal static class VsCodeIntegration
    {
        private const string MarketplaceUrl =
            "https://marketplace.visualstudio.com/" +
            "items?itemName=OpenAI.chatgpt";
        private const string CloseAction =
            "Сохраните работу, закройте все окна VS Code " +
            "и повторите запуск.";
        private static readonly Guid GenericVerifyAction =
            new Guid("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct WinTrustFileInfo
        {
            public uint cbStruct;
            [MarshalAs(UnmanagedType.LPWStr)]
            public string pcwszFilePath;
            public IntPtr hFile;
            public IntPtr pgKnownSubject;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct WinTrustData
        {
            public uint cbStruct;
            public IntPtr pPolicyCallbackData;
            public IntPtr pSIPClientData;
            public uint dwUIChoice;
            public uint fdwRevocationChecks;
            public uint dwUnionChoice;
            public IntPtr pFile;
            public uint dwStateAction;
            public IntPtr hWVTStateData;
            [MarshalAs(UnmanagedType.LPWStr)]
            public string pwszURLReference;
            public uint dwProvFlags;
            public uint dwUIContext;
        }

        [DllImport(
            "wintrust.dll",
            CharSet = CharSet.Unicode,
            ExactSpelling = true
        )]
        private static extern uint WinVerifyTrust(
            IntPtr window,
            ref Guid action,
            ref WinTrustData trustData
        );

        public static LaunchTargetResolution Resolve(string home)
        {
            string executable = FindExecutable();
            if (String.IsNullOrWhiteSpace(executable))
            {
                return Blocked("VSCODE_NOT_FOUND", null, null);
            }

            string signatureStatus;
            string signerSubject;
            InspectSignature(
                executable,
                out signatureStatus,
                out signerSubject
            );
            string extensionPublisher;
            string extensionName;
            string extensionPath;
            InspectExtension(
                home,
                out extensionPublisher,
                out extensionName,
                out extensionPath
            );
            return Evaluate(new VsCodeTrustRecord
            {
                executable_path = executable,
                signature_status = signatureStatus,
                signer_subject = signerSubject,
                extension_publisher = extensionPublisher,
                extension_name = extensionName,
                extension_path = extensionPath,
                code_running = IsCodeRunning()
            });
        }

        internal static LaunchTargetResolution ResolveTestRecord(
            string bundleRoot,
            string home,
            string recordPath
        )
        {
            ClientSourceLock sourceLock = ClientBootstrap.Load(bundleRoot);
            if (!sourceLock.test_only)
            {
                return Blocked(
                    "TEST_ONLY_SOURCE_REQUIRED",
                    null,
                    null
                );
            }
            try
            {
                Path.GetFullPath(home);
                VsCodeTrustRecord record = LoadTestRecord(recordPath);
                return Evaluate(record);
            }
            catch
            {
                return Blocked(
                    "VSCODE_TEST_RECORD_INVALID",
                    null,
                    null
                );
            }
        }

        private static VsCodeTrustRecord LoadTestRecord(string recordPath)
        {
            string fullPath = Path.GetFullPath(recordPath);
            FileInfo info = new FileInfo(fullPath);
            if (!info.Exists ||
                info.Length < 2 ||
                info.Length > 65536 ||
                (info.Attributes & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException();
            }
            string json = File.ReadAllText(
                fullPath,
                new UTF8Encoding(false, true)
            );
            JavaScriptSerializer serializer = new JavaScriptSerializer();
            Dictionary<string, object> shape =
                serializer.DeserializeObject(json)
                    as Dictionary<string, object>;
            HashSet<string> required = new HashSet<string>(
                new[]
                {
                    "executable_path",
                    "signature_status",
                    "signer_subject",
                    "extension_publisher",
                    "extension_name",
                    "extension_path",
                    "code_running"
                },
                StringComparer.Ordinal
            );
            if (shape == null ||
                !required.SetEquals(shape.Keys) ||
                !(shape["code_running"] is bool))
            {
                throw new InvalidOperationException();
            }
            foreach (string key in new[]
            {
                "executable_path",
                "signature_status",
                "signer_subject"
            })
            {
                if (!(shape[key] is string))
                {
                    throw new InvalidOperationException();
                }
            }
            bool extensionMissing =
                shape["extension_publisher"] == null &&
                shape["extension_name"] == null &&
                shape["extension_path"] == null;
            bool extensionPresent =
                shape["extension_publisher"] is string &&
                shape["extension_name"] is string &&
                shape["extension_path"] is string;
            if (!extensionMissing && !extensionPresent)
            {
                throw new InvalidOperationException();
            }
            VsCodeTrustRecord record =
                serializer.Deserialize<VsCodeTrustRecord>(json);
            if (record == null ||
                String.IsNullOrWhiteSpace(record.executable_path) ||
                !Path.IsPathRooted(record.executable_path) ||
                !String.Equals(
                    Path.GetFileName(record.executable_path),
                    "Code.exe",
                    StringComparison.OrdinalIgnoreCase) ||
                String.IsNullOrWhiteSpace(record.signature_status) ||
                String.IsNullOrWhiteSpace(record.signer_subject) ||
                (extensionPresent &&
                    (String.IsNullOrWhiteSpace(record.extension_path) ||
                     !Path.IsPathRooted(record.extension_path) ||
                     !Path.GetFileName(record.extension_path).StartsWith(
                         "openai.chatgpt-",
                         StringComparison.OrdinalIgnoreCase))))
            {
                throw new InvalidOperationException();
            }
            return record;
        }

        private static LaunchTargetResolution Evaluate(
            VsCodeTrustRecord record
        )
        {
            if (!String.Equals(
                    record.signature_status,
                    "Valid",
                    StringComparison.Ordinal))
            {
                return Blocked(
                    "VSCODE_SIGNATURE_INVALID",
                    null,
                    null
                );
            }
            if (!IsMicrosoftPublisher(record.signer_subject))
            {
                return Blocked(
                    "VSCODE_PUBLISHER_INVALID",
                    null,
                    null
                );
            }
            if (!String.Equals(
                    record.extension_publisher,
                    "OpenAI",
                    StringComparison.Ordinal) ||
                !String.Equals(
                    record.extension_name,
                    "chatgpt",
                    StringComparison.Ordinal))
            {
                return Blocked(
                    "CODEX_EXTENSION_NOT_VERIFIED",
                    MarketplaceUrl,
                    null
                );
            }
            if (record.code_running)
            {
                return Blocked(
                    "VSCODE_ALREADY_RUNNING",
                    null,
                    CloseAction
                );
            }
            string hash = File.Exists(record.executable_path)
                ? BundleIntegrity.Sha256(record.executable_path)
                : null;
            return new LaunchTargetResolution
            {
                status = "RESOLVED",
                target_id = "vscode-codex",
                client_id = "codex-desktop",
                role = "desktop",
                launch_mode = "executable",
                executable_path = record.executable_path,
                sha256 = hash,
                activation_id = null,
                package_full_name = null,
                official_url = null,
                action = null,
                extension_path = record.extension_path,
                reason = null
            };
        }

        private static LaunchTargetResolution Blocked(
            string reason,
            string officialUrl,
            string action
        )
        {
            return new LaunchTargetResolution
            {
                status = "BLOCKED",
                target_id = "vscode-codex",
                client_id = "codex-desktop",
                role = "desktop",
                launch_mode = null,
                executable_path = null,
                sha256 = null,
                activation_id = null,
                package_full_name = null,
                official_url = officialUrl,
                action = action,
                extension_path = null,
                reason = reason
            };
        }

        private static string FindExecutable()
        {
            List<string> candidates = new List<string>();
            AddInstallCandidate(
                candidates,
                Environment.GetEnvironmentVariable("LOCALAPPDATA")
            );
            AddProgramFilesCandidate(
                candidates,
                Environment.GetEnvironmentVariable("ProgramFiles")
            );
            AddProgramFilesCandidate(
                candidates,
                Environment.GetEnvironmentVariable("ProgramFiles(x86)")
            );
            AddPathCandidates(candidates);

            foreach (string candidate in candidates.Distinct(
                StringComparer.OrdinalIgnoreCase
            ))
            {
                try
                {
                    string fullPath = Path.GetFullPath(candidate);
                    FileInfo info = new FileInfo(fullPath);
                    if (info.Exists &&
                        (info.Attributes & FileAttributes.ReparsePoint) == 0 &&
                        String.Equals(
                            info.Name,
                            "Code.exe",
                            StringComparison.OrdinalIgnoreCase))
                    {
                        return fullPath;
                    }
                }
                catch
                {
                }
            }
            return null;
        }

        private static void AddInstallCandidate(
            List<string> candidates,
            string localAppData
        )
        {
            if (!String.IsNullOrWhiteSpace(localAppData))
            {
                candidates.Add(Path.Combine(
                    localAppData,
                    "Programs",
                    "Microsoft VS Code",
                    "Code.exe"
                ));
            }
        }

        private static void AddProgramFilesCandidate(
            List<string> candidates,
            string programFiles
        )
        {
            if (!String.IsNullOrWhiteSpace(programFiles))
            {
                candidates.Add(Path.Combine(
                    programFiles,
                    "Microsoft VS Code",
                    "Code.exe"
                ));
            }
        }

        private static void AddPathCandidates(List<string> candidates)
        {
            string path = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string rawEntry in path.Split(
                new[] { Path.PathSeparator },
                StringSplitOptions.RemoveEmptyEntries
            ))
            {
                string entry = rawEntry.Trim().Trim('"');
                if (String.IsNullOrWhiteSpace(entry))
                {
                    continue;
                }
                try
                {
                    candidates.Add(Path.Combine(entry, "Code.exe"));
                    string command = Path.Combine(entry, "code.cmd");
                    if (File.Exists(command))
                    {
                        DirectoryInfo directory = new DirectoryInfo(entry);
                        if (directory.Parent != null)
                        {
                            candidates.Add(Path.Combine(
                                directory.Parent.FullName,
                                "Code.exe"
                            ));
                        }
                    }
                }
                catch
                {
                }
            }
        }

        private static void InspectSignature(
            string path,
            out string status,
            out string subject
        )
        {
            status = "NotSigned";
            subject = null;
            WinTrustFileInfo fileInfo = new WinTrustFileInfo
            {
                cbStruct = (uint)Marshal.SizeOf(
                    typeof(WinTrustFileInfo)
                ),
                pcwszFilePath = path,
                hFile = IntPtr.Zero,
                pgKnownSubject = IntPtr.Zero
            };
            IntPtr fileInfoPointer = Marshal.AllocHGlobal(
                Marshal.SizeOf(typeof(WinTrustFileInfo))
            );
            try
            {
                Marshal.StructureToPtr(
                    fileInfo,
                    fileInfoPointer,
                    false
                );
                WinTrustData trustData = new WinTrustData
                {
                    cbStruct = (uint)Marshal.SizeOf(
                        typeof(WinTrustData)
                    ),
                    pPolicyCallbackData = IntPtr.Zero,
                    pSIPClientData = IntPtr.Zero,
                    dwUIChoice = 2,
                    fdwRevocationChecks = 0,
                    dwUnionChoice = 1,
                    pFile = fileInfoPointer,
                    dwStateAction = 0,
                    hWVTStateData = IntPtr.Zero,
                    pwszURLReference = null,
                    dwProvFlags = 0x1000,
                    dwUIContext = 0
                };
                Guid action = GenericVerifyAction;
                if (WinVerifyTrust(
                        IntPtr.Zero,
                        ref action,
                        ref trustData) != 0)
                {
                    return;
                }
                status = "Valid";
                using (X509Certificate2 certificate =
                    new X509Certificate2(
                        X509Certificate.CreateFromSignedFile(path)))
                {
                    subject = certificate.Subject;
                }
            }
            catch
            {
                status = "NotSigned";
                subject = null;
            }
            finally
            {
                Marshal.DestroyStructure(
                    fileInfoPointer,
                    typeof(WinTrustFileInfo)
                );
                Marshal.FreeHGlobal(fileInfoPointer);
            }
        }

        private static bool IsMicrosoftPublisher(string subject)
        {
            if (String.IsNullOrWhiteSpace(subject))
            {
                return false;
            }
            foreach (string component in subject.Split(','))
            {
                string value = component.Trim();
                if (value.StartsWith(
                        "CN=",
                        StringComparison.OrdinalIgnoreCase))
                {
                    return String.Equals(
                        value.Substring(3).Trim(),
                        "Microsoft Corporation",
                        StringComparison.OrdinalIgnoreCase
                    );
                }
            }
            return false;
        }

        private static void InspectExtension(
            string home,
            out string publisher,
            out string name,
            out string extensionPath
        )
        {
            publisher = null;
            name = null;
            extensionPath = null;
            string root;
            try
            {
                root = Path.Combine(
                    Path.GetFullPath(home),
                    ".vscode",
                    "extensions"
                );
            }
            catch
            {
                return;
            }
            if (!Directory.Exists(root))
            {
                return;
            }
            foreach (string directory in Directory.GetDirectories(
                root,
                "openai.chatgpt-*",
                SearchOption.TopDirectoryOnly
            ).OrderByDescending(value => value, StringComparer.OrdinalIgnoreCase))
            {
                try
                {
                    DirectoryInfo directoryInfo =
                        new DirectoryInfo(directory);
                    if ((directoryInfo.Attributes &
                            FileAttributes.ReparsePoint) != 0)
                    {
                        continue;
                    }
                    string manifestPath = Path.Combine(
                        directoryInfo.FullName,
                        "package.json"
                    );
                    FileInfo manifestInfo = new FileInfo(manifestPath);
                    if (!manifestInfo.Exists ||
                        manifestInfo.Length < 2 ||
                        manifestInfo.Length > 1048576 ||
                        (manifestInfo.Attributes &
                            FileAttributes.ReparsePoint) != 0)
                    {
                        continue;
                    }
                    VsCodeExtensionManifest manifest =
                        new JavaScriptSerializer()
                            .Deserialize<VsCodeExtensionManifest>(
                                File.ReadAllText(
                                    manifestPath,
                                    new UTF8Encoding(false, true)
                                )
                            );
                    if (manifest != null &&
                        String.Equals(
                            manifest.publisher,
                            "OpenAI",
                            StringComparison.Ordinal) &&
                        String.Equals(
                            manifest.name,
                            "chatgpt",
                            StringComparison.Ordinal))
                    {
                        publisher = manifest.publisher;
                        name = manifest.name;
                        extensionPath = directoryInfo.FullName;
                        return;
                    }
                }
                catch
                {
                }
            }
        }

        private static bool IsCodeRunning()
        {
            Process[] processes = Process.GetProcessesByName("Code");
            try
            {
                return processes.Length != 0;
            }
            finally
            {
                foreach (Process process in processes)
                {
                    process.Dispose();
                }
            }
        }
    }
}
