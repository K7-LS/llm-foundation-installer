using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class LaunchTarget
    {
        public string target_id { get; set; }
        public string client_id { get; set; }
        public string role { get; set; }
        public string display_name { get; set; }
    }

    internal sealed class ProductDescription
    {
        public string app_id { get; set; }
        public string edition_id { get; set; }
        public string product_role { get; set; }
        public List<string> targets { get; set; }
    }

    internal sealed class LaunchTargetResolution
    {
        public string status { get; set; }
        public string target_id { get; set; }
        public string client_id { get; set; }
        public string role { get; set; }
        public string launch_mode { get; set; }
        public string executable_path { get; set; }
        public string sha256 { get; set; }
        public string activation_id { get; set; }
        public string package_full_name { get; set; }
        public string official_url { get; set; }
        public string action { get; set; }
        public string extension_path { get; set; }
        public string reason { get; set; }
    }

    internal static class LaunchTargetCatalog
    {
        public static List<LaunchTarget> ForEdition(
            EditionProfile edition,
            string bundleRoot
        )
        {
            HashSet<string> included = new HashSet<string>(
                edition.included_target_ids,
                StringComparer.Ordinal
            );
            List<LaunchTarget> targets = new List<LaunchTarget>();
            targets.Add(new LaunchTarget
            {
                target_id = "chrome-browser",
                client_id = "chrome-browser",
                role = "desktop",
                display_name = "Google Chrome — proxy"
            });
            targets.AddRange(ClientBootstrap.Load(bundleRoot).clients
                .Where(source => included.Contains(source.target))
                .Select(source => new LaunchTarget
                {
                    target_id = source.id,
                    client_id = source.id,
                    role = source.role,
                    display_name = source.display_name
                }));
            if (included.Contains("codex"))
            {
                targets.Add(new LaunchTarget
                {
                    target_id = "vscode-codex",
                    client_id = "codex-desktop",
                    role = "desktop",
                    display_name = "VS Code — Codex"
                });
            }
            return targets;
        }

        public static ProductDescription Describe(
            EditionProfile edition,
            string bundleRoot
        )
        {
            return new ProductDescription
            {
                app_id = edition.product_role == "LaunchCenter"
                    ? "k7-ai-launch-center"
                    : "k7-ai-foundation-installer",
                edition_id = edition.edition_id,
                product_role = edition.product_role,
                targets = ForEdition(edition, bundleRoot)
                    .Select(target => target.target_id)
                    .ToList()
            };
        }
    }

    internal static class LaunchTargetResolver
    {
        public static LaunchTargetResolution Resolve(
            EditionProfile edition,
            string bundleRoot,
            string home,
            string targetId
        )
        {
            LaunchTarget target = LaunchTargetCatalog.ForEdition(
                edition,
                bundleRoot
            ).FirstOrDefault(candidate => String.Equals(
                candidate.target_id,
                targetId,
                StringComparison.Ordinal
            ));
            if (target == null)
            {
                return Blocked(
                    targetId,
                    null,
                    null,
                    "TARGET_NOT_IN_EDITION"
                );
            }
            if (String.Equals(
                    target.target_id,
                    "vscode-codex",
                    StringComparison.Ordinal))
            {
                return VsCodeIntegration.Resolve(home);
            }
            if (String.Equals(
                    target.target_id,
                    "chrome-browser",
                    StringComparison.Ordinal))
            {
                return ChromeBrowserIntegration.Resolve(
                    bundleRoot,
                    home
                );
            }
            ClientSource source = ClientBootstrap.Load(bundleRoot).clients
                .First(entry => String.Equals(
                    entry.id,
                    target.client_id,
                    StringComparison.Ordinal
                ));
            if (String.Equals(
                    source.source_kind,
                    "store",
                    StringComparison.Ordinal))
            {
                StoreClientResult store = ClientBootstrap.ProbeStore(
                    bundleRoot,
                    source.id
                );
                if (store.status != "READY")
                {
                    return Blocked(
                        target.target_id,
                        target.client_id,
                        target.role,
                        "STORE_PACKAGE_NOT_FOUND"
                    );
                }
                return ResolveStore(source, target, store);
            }
            if (ClientBootstrap.UsesManagedCommand(source))
            {
                return ResolveManagedCommand(home, source, target);
            }
            if (String.Equals(
                    source.install_mode,
                    "official-installer",
                    StringComparison.Ordinal))
            {
                return ResolveOfficialDesktop(
                    bundleRoot,
                    home,
                    source,
                    target
                );
            }
            if (!String.Equals(
                    source.install_mode,
                    "managed-desktop",
                    StringComparison.Ordinal))
            {
                return Blocked(
                    target.target_id,
                    target.client_id,
                    target.role,
                    "EXACT_TARGET_NOT_SUPPORTED"
                );
            }
            return ResolveManagedDesktop(home, source, target);
        }

        private static LaunchTargetResolution ResolveOfficialDesktop(
            string bundleRoot,
            string home,
            ClientSource source,
            LaunchTarget target
        )
        {
            try
            {
                string version;
                string executable =
                    ClientBootstrap.ResolveOfficialDesktopPath(
                        home,
                        source,
                        ClientBootstrap.Load(bundleRoot).test_only,
                        out version
                    );
                int comparison;
                if (!String.Equals(
                        version,
                        source.version,
                        StringComparison.Ordinal) &&
                    (!TryCompareVersions(
                        version,
                        source.version,
                        out comparison) || comparison < 0))
                {
                    throw new InvalidOperationException();
                }
                return new LaunchTargetResolution
                {
                    status = "RESOLVED",
                    target_id = target.target_id,
                    client_id = target.client_id,
                    role = target.role,
                    launch_mode = "executable",
                    executable_path = executable,
                    sha256 = BundleIntegrity.Sha256(executable),
                    reason = null
                };
            }
            catch
            {
                return Blocked(
                    target.target_id,
                    target.client_id,
                    target.role,
                    "OFFICIAL_DESKTOP_INTEGRITY_FAILED"
                );
            }
        }

        private static bool TryCompareVersions(
            string left,
            string right,
            out int comparison
        )
        {
            comparison = 0;
            Version leftValue;
            Version rightValue;
            if (!Version.TryParse(left, out leftValue) ||
                !Version.TryParse(right, out rightValue))
            {
                return false;
            }
            comparison = leftValue.CompareTo(rightValue);
            return true;
        }

        public static LaunchTargetResolution ResolveStoreRecord(
            EditionProfile edition,
            string bundleRoot,
            string targetId,
            string recordPath
        )
        {
            LaunchTarget target = LaunchTargetCatalog.ForEdition(
                edition,
                bundleRoot
            ).FirstOrDefault(candidate => String.Equals(
                candidate.target_id,
                targetId,
                StringComparison.Ordinal
            ));
            if (target == null)
            {
                return Blocked(
                    targetId,
                    null,
                    null,
                    "TARGET_NOT_IN_EDITION"
                );
            }
            ClientSource source = ClientBootstrap.Load(bundleRoot).clients
                .First(entry => String.Equals(
                    entry.id,
                    target.client_id,
                    StringComparison.Ordinal
                ));
            try
            {
                StoreClientResult store =
                    ClientBootstrap.ValidateStoreRecord(
                        bundleRoot,
                        source.id,
                        recordPath
                    );
                return ResolveStore(source, target, store);
            }
            catch
            {
                return Blocked(
                    target.target_id,
                    target.client_id,
                    target.role,
                    "STORE_APP_INTEGRITY_FAILED"
                );
            }
        }

        private static LaunchTargetResolution ResolveManagedCommand(
            string home,
            ClientSource source,
            LaunchTarget target
        )
        {
            string recordPath;
            try
            {
                recordPath = ClientBootstrap.ManagedCommandRecordPath(
                    home,
                    source
                );
            }
            catch
            {
                return Blocked(
                    target.target_id,
                    target.client_id,
                    target.role,
                    "MANAGED_COMMAND_INTEGRITY_FAILED"
                );
            }
            if (!File.Exists(recordPath))
            {
                return Blocked(
                    target.target_id,
                    target.client_id,
                    target.role,
                    "MANAGED_COMMAND_NOT_FOUND"
                );
            }
            try
            {
                ManagedCommandRecord record;
                string executable =
                    ClientBootstrap.ResolveManagedCommandPath(
                        home,
                        source,
                        out record
                    );
                return new LaunchTargetResolution
                {
                    status = "RESOLVED",
                    target_id = target.target_id,
                    client_id = target.client_id,
                    role = target.role,
                    launch_mode = "executable",
                    executable_path = executable,
                    sha256 = record.sha256,
                    activation_id = null,
                    package_full_name = null,
                    reason = null
                };
            }
            catch
            {
                return Blocked(
                    target.target_id,
                    target.client_id,
                    target.role,
                    "MANAGED_COMMAND_INTEGRITY_FAILED"
                );
            }
        }

        private static LaunchTargetResolution ResolveManagedDesktop(
            string home,
            ClientSource source,
            LaunchTarget target
        )
        {
            string root = Path.Combine(
                Path.GetFullPath(home),
                ".llm-foundation",
                "apps",
                source.id
            );
            string recordPath = Path.Combine(root, "current.json");
            if (!File.Exists(recordPath))
            {
                return Blocked(
                    target.target_id,
                    target.client_id,
                    target.role,
                    "MANAGED_DESKTOP_NOT_FOUND"
                );
            }
            try
            {
                FileInfo recordInfo = new FileInfo(recordPath);
                if (recordInfo.Length < 2 ||
                    recordInfo.Length > 65536 ||
                    (recordInfo.Attributes &
                        FileAttributes.ReparsePoint) != 0)
                {
                    throw new InvalidOperationException();
                }
                ManagedDesktopRecord record =
                    new JavaScriptSerializer()
                        .Deserialize<ManagedDesktopRecord>(
                            File.ReadAllText(
                                recordPath,
                                new UTF8Encoding(false, true)
                            )
                        );
                if (record == null ||
                    record.schema_version != 1 ||
                    !String.Equals(
                        record.client_id,
                        source.id,
                        StringComparison.Ordinal) ||
                    !String.Equals(
                        record.version,
                        source.version,
                        StringComparison.Ordinal) ||
                    !String.Equals(
                        record.sha256,
                        source.sha256,
                        StringComparison.OrdinalIgnoreCase) ||
                    String.IsNullOrWhiteSpace(record.relative_path))
                {
                    throw new InvalidOperationException();
                }
                string relative = record.relative_path.Replace(
                    '/',
                    Path.DirectorySeparatorChar
                );
                if (Path.IsPathRooted(relative) ||
                    relative.Split(Path.DirectorySeparatorChar).Any(
                        segment => segment.Length == 0 ||
                            segment == "." ||
                            segment == ".."
                    ))
                {
                    throw new InvalidOperationException();
                }
                string executable = Path.GetFullPath(
                    Path.Combine(root, relative)
                );
                string rootPrefix = Path.GetFullPath(root)
                    .TrimEnd(Path.DirectorySeparatorChar) +
                    Path.DirectorySeparatorChar;
                if (!executable.StartsWith(
                        rootPrefix,
                        StringComparison.OrdinalIgnoreCase) ||
                    !File.Exists(executable))
                {
                    throw new InvalidOperationException();
                }
                FileInfo executableInfo = new FileInfo(executable);
                if ((executableInfo.Attributes &
                        FileAttributes.ReparsePoint) != 0)
                {
                    throw new InvalidOperationException();
                }
                string actual = BundleIntegrity.Sha256(executable);
                if (!String.Equals(
                        actual,
                        record.sha256,
                        StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException();
                }
                return new LaunchTargetResolution
                {
                    status = "RESOLVED",
                    target_id = target.target_id,
                    client_id = target.client_id,
                    role = target.role,
                    launch_mode = "executable",
                    executable_path = executable,
                    sha256 = actual,
                    activation_id = null,
                    package_full_name = null,
                    reason = null
                };
            }
            catch
            {
                return Blocked(
                    target.target_id,
                    target.client_id,
                    target.role,
                    "MANAGED_DESKTOP_INTEGRITY_FAILED"
                );
            }
        }

        private static LaunchTargetResolution ResolveStore(
            ClientSource source,
            LaunchTarget target,
            StoreClientResult store
        )
        {
            try
            {
                if (store == null ||
                    store.status != "READY" ||
                    !String.Equals(
                        store.application_id,
                        source.store_application_id,
                        StringComparison.Ordinal) ||
                    !String.Equals(
                        store.executable,
                        source.store_executable,
                        StringComparison.Ordinal) ||
                    String.IsNullOrWhiteSpace(
                        store.package_family_name) ||
                    String.IsNullOrWhiteSpace(
                        store.package_full_name))
                {
                    throw new InvalidOperationException();
                }
                string root = Path.GetFullPath(
                    store.install_location
                );
                if (!String.Equals(
                        Path.GetFileName(root),
                        store.package_full_name,
                        StringComparison.Ordinal))
                {
                    throw new InvalidOperationException();
                }
                string relative = store.executable.Replace(
                    '/',
                    Path.DirectorySeparatorChar
                );
                if (Path.IsPathRooted(relative) ||
                    relative.Split(Path.DirectorySeparatorChar).Any(
                        segment => segment.Length == 0 ||
                            segment == "." ||
                            segment == ".."
                    ))
                {
                    throw new InvalidOperationException();
                }
                string executable = Path.GetFullPath(
                    Path.Combine(root, relative)
                );
                string rootPrefix = root.TrimEnd(
                    Path.DirectorySeparatorChar
                ) + Path.DirectorySeparatorChar;
                if (!executable.StartsWith(
                        rootPrefix,
                        StringComparison.OrdinalIgnoreCase) ||
                    !File.Exists(executable) ||
                    (File.GetAttributes(executable) &
                        FileAttributes.ReparsePoint) != 0)
                {
                    throw new InvalidOperationException();
                }
                string actual = BundleIntegrity.Sha256(executable);
                return new LaunchTargetResolution
                {
                    status = "RESOLVED",
                    target_id = target.target_id,
                    client_id = target.client_id,
                    role = target.role,
                    launch_mode = "appx",
                    executable_path = executable,
                    sha256 = actual,
                    activation_id = store.package_family_name +
                        "!" + store.application_id,
                    package_full_name = store.package_full_name,
                    reason = null
                };
            }
            catch
            {
                return Blocked(
                    target.target_id,
                    target.client_id,
                    target.role,
                    "STORE_APP_INTEGRITY_FAILED"
                );
            }
        }

        private static LaunchTargetResolution Blocked(
            string targetId,
            string clientId,
            string role,
            string reason
        )
        {
            return new LaunchTargetResolution
            {
                status = "BLOCKED",
                target_id = targetId,
                client_id = clientId,
                role = role,
                launch_mode = null,
                executable_path = null,
                sha256 = null,
                activation_id = null,
                package_full_name = null,
                official_url = null,
                action = null,
                extension_path = null,
                reason = reason
            };
        }
    }

    internal static class ChromeBrowserIntegration
    {
        public static LaunchTargetResolution Resolve(
            string bundleRoot,
            string home
        )
        {
            try
            {
                string local = Path.Combine(
                    Path.GetFullPath(home),
                    "AppData",
                    "Local"
                );
                List<string> candidates = new List<string>
                {
                    Path.Combine(
                        local,
                        "Google",
                        "Chrome",
                        "Application",
                        "chrome.exe"
                    ),
                    Path.Combine(
                        Environment.GetFolderPath(
                            Environment.SpecialFolder.ProgramFiles
                        ),
                        "Google",
                        "Chrome",
                        "Application",
                        "chrome.exe"
                    ),
                    Path.Combine(
                        Environment.GetFolderPath(
                            Environment.SpecialFolder.ProgramFilesX86
                        ),
                        "Google",
                        "Chrome",
                        "Application",
                        "chrome.exe"
                    )
                };
                string executable = candidates
                    .Where(path => !String.IsNullOrWhiteSpace(path))
                    .FirstOrDefault(File.Exists);
                if (executable == null)
                {
                    return Blocked("CHROME_NOT_FOUND");
                }
                executable = Path.GetFullPath(executable);
                if ((File.GetAttributes(executable) &
                        FileAttributes.ReparsePoint) != 0)
                {
                    return Blocked("CHROME_INTEGRITY_FAILED");
                }
                if (!ClientBootstrap.Load(bundleRoot).test_only)
                {
                    ClientBootstrap.VerifyInstalledPublisher(
                        executable,
                        "Google LLC"
                    );
                }
                return new LaunchTargetResolution
                {
                    status = "RESOLVED",
                    target_id = "chrome-browser",
                    client_id = "chrome-browser",
                    role = "desktop",
                    launch_mode = "chrome",
                    executable_path = executable,
                    sha256 = BundleIntegrity.Sha256(executable),
                    reason = null
                };
            }
            catch
            {
                return Blocked("CHROME_INTEGRITY_FAILED");
            }
        }

        private static LaunchTargetResolution Blocked(string reason)
        {
            return new LaunchTargetResolution
            {
                status = "BLOCKED",
                target_id = "chrome-browser",
                client_id = "chrome-browser",
                role = "desktop",
                reason = reason
            };
        }
    }
}
