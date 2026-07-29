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
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;

[assembly: AssemblyTitle("LLM Foundation Installer")]
[assembly: AssemblyDescription("Verified local installer for native LLM workspaces")]
[assembly: AssemblyCompany("LLM Foundation")]
[assembly: AssemblyProduct("LLM Foundation Installer")]
[assembly: AssemblyCopyright("Copyright 2026")]
[assembly: AssemblyVersion("0.3.0.0")]
[assembly: AssemblyFileVersion("0.3.0.0")]
[assembly: ComVisible(false)]

namespace LlmFoundationInstaller
{
    internal sealed class TargetRow
    {
        public string id { get; set; }
        public string display_name { get; set; }
        public string client_id { get; set; }
        public string package_state { get; set; }
        public string supported_version { get; set; }
        public string detected_version { get; set; }
        public string client_state { get; set; }
    }

    internal sealed class ClientDetectionResult
    {
        public string version { get; set; }
        public string source { get; set; }
    }

    internal sealed class TrustedFile
    {
        public string relative_path { get; set; }
        public string resource_name { get; set; }
        public string sha256 { get; set; }
        public long bytes { get; set; }
    }

    internal sealed class TrustedPackage
    {
        public string trust_level { get; set; }
        public string target { get; set; }
        public string client_id { get; set; }
        public string supported_version { get; set; }
        public TrustedFile asset { get; set; }
        public TrustedFile release_manifest { get; set; }
        public TrustedFile acceptance_evidence { get; set; }
        public TrustedFile release_verification { get; set; }
        public TrustedFile package_acceptance { get; set; }
        public TrustedFile candidate_acceptance { get; set; }
        public TrustedFile live_canary { get; set; }
        public TrustedFile components_lock { get; set; }
        public TrustedFile non_releasable { get; set; }
    }

    internal sealed class ProviderEligibilityRecord
    {
        public string status { get; set; }
        public string reviewed_at_utc { get; set; }
        public string expires_at_utc { get; set; }
        public TrustedFile evidence { get; set; }
    }

    internal sealed class TrustedPackageIndex
    {
        public int schema_version { get; set; }
        public ProviderEligibilityRecord provider_eligibility { get; set; }
        public List<TrustedPackage> packages { get; set; }
    }

    internal sealed class CatalogResult
    {
        public List<TargetRow> targets { get; set; }
        public bool install_enabled { get; set; }
        public string reason { get; set; }
        public string provider_eligibility { get; set; }
    }

    internal sealed class ConnectionProbeResult
    {
        public string status { get; set; }
        public string mode { get; set; }
        public bool uses_proxy { get; set; }
        public string proxy_type { get; set; }
        public string endpoint_host { get; set; }
        public int elapsed_ms { get; set; }
        public string error { get; set; }
    }

    internal sealed class SuccessReportResult
    {
        public bool written { get; set; }
        public string path { get; set; }
        public string error { get; set; }
    }

    internal sealed class PlatformCompatibilityResult
    {
        public string status { get; set; }
        public string os { get; set; }
        public string architecture { get; set; }
        public int windows_build { get; set; }
        public int minimum_build { get; set; }
        public bool admin_required { get; set; }
        public string reason { get; set; }
    }

    internal static class PlatformCompatibility
    {
        private const int MinimumWindowsBuild = 19041;

        public static PlatformCompatibilityResult Evaluate(
            string os,
            string architecture,
            int windowsBuild
        )
        {
            string reason = null;
            if (!String.Equals(
                    os,
                    "windows",
                    StringComparison.OrdinalIgnoreCase))
            {
                reason = "Windows is required.";
            }
            else if (!String.Equals(
                    architecture,
                    "x64",
                    StringComparison.OrdinalIgnoreCase))
            {
                reason = "Windows x64 is required.";
            }
            else if (windowsBuild < MinimumWindowsBuild)
            {
                reason = "Windows build 19041 or newer is required.";
            }
            return new PlatformCompatibilityResult
            {
                status = reason == null ? "READY" : "BLOCKED",
                os = os.ToLowerInvariant(),
                architecture = architecture.ToLowerInvariant(),
                windows_build = windowsBuild,
                minimum_build = MinimumWindowsBuild,
                admin_required = false,
                reason = reason
            };
        }

        public static PlatformCompatibilityResult Inspect()
        {
            string os = Environment.OSVersion.Platform == PlatformID.Win32NT
                ? "windows"
                : Environment.OSVersion.Platform.ToString().ToLowerInvariant();
            string architecture = Environment.Is64BitOperatingSystem
                ? "x64"
                : "x86";
            return Evaluate(
                os,
                architecture,
                Environment.OSVersion.Version.Build
            );
        }

        public static void RequireSupported()
        {
            PlatformCompatibilityResult result = Inspect();
            if (result.status != "READY")
            {
                throw new InvalidOperationException(result.reason);
            }
        }
    }

    internal static class ProductCatalog
    {
        private static readonly string[][] Definitions = new[]
        {
            new[] { "codex", "Codex", "codex-cli" },
            new[] { "claude", "Claude", "claude-code" },
            new[] { "opencode", "OpenCode", "opencode" }
        };

        public static CatalogResult Inspect(
            string bundleRoot,
            bool detectClients = false
        )
        {
            return Inspect(bundleRoot, detectClients, null);
        }

        internal static CatalogResult Inspect(
            string bundleRoot,
            bool detectClients,
            StoreClientResult storeRecord
        )
        {
            EditionProfile edition = EditionProfile.LoadEmbedded();
            ProviderEligibilityRecord eligibility;
            Dictionary<string, TrustedPackage> trusted =
                LoadTrustedPackages(out eligibility);
            string eligibilityState = ProviderEligibilityState(
                bundleRoot,
                eligibility
            );
            List<TargetRow> targets = new List<TargetRow>();
            foreach (string[] definition in Definitions.Where(
                value => edition.Includes(value[0])
            ))
            {
                TrustedPackage package;
                string state = !trusted.TryGetValue(definition[0], out package)
                    ? "missing"
                    : (ValidateTrustedPackage(
                        bundleRoot,
                        package,
                        definition[0],
                        definition[2]
                    ) ? (String.Equals(
                        package.trust_level,
                        "owner_candidate",
                        StringComparison.Ordinal
                    ) ? "owner_candidate" : "accepted") : "tampered");
                if (state == "accepted" &&
                    definition[0] == "claude" &&
                    eligibilityState != "PASS")
                {
                    state = edition.owner_controlled
                        ? "owner_candidate"
                        : "policy_blocked";
                }
                string detected = null;
                string clientState = "not_checked";
                string supported = package == null
                    ? null
                    : package.supported_version;
                if (detectClients)
                {
                    ClientDetectionResult detection = definition[0] == "codex"
                        ? DetectCodex(bundleRoot, storeRecord)
                        : DetectCli(definition[2]);
                    detected = detection.version;
                    clientState = detected == null
                        ? "missing"
                        : (state != "accepted"
                            ? "present_unbound"
                            : (definition[0] == "codex"
                                ? "ready"
                                : (String.Equals(
                                    detected,
                                    supported,
                                    StringComparison.Ordinal
                                ) ? "ready" : "unsupported")));
                }
                targets.Add(new TargetRow
                {
                    id = definition[0],
                    display_name = definition[1],
                    client_id = definition[2],
                    package_state = state,
                    supported_version = supported,
                    detected_version = detected,
                    client_state = clientState
                });
            }

            bool enabled = edition.required_target_ids.All(
                required => targets.Any(
                    row => row.id == required &&
                        row.package_state == "accepted"
                )
            );
            return new CatalogResult
            {
                targets = targets,
                install_enabled = enabled,
                reason = enabled
                    ? "Accepted target package is available"
                    : "Required edition packages are missing or changed",
                provider_eligibility = eligibilityState
            };
        }

        private static ClientDetectionResult DetectCli(string clientId)
        {
            return new ClientDetectionResult
            {
                version = ClientDetector.DetectVersion(clientId),
                source = "cli"
            };
        }

        private static ClientDetectionResult DetectCodex(
            string bundleRoot,
            StoreClientResult storeRecord
        )
        {
            try
            {
                StoreClientResult store = storeRecord ??
                    ClientBootstrap.ProbeStore(bundleRoot, "codex-desktop");
                if (store != null && store.status == "READY" &&
                    !String.IsNullOrWhiteSpace(store.version))
                {
                    return new ClientDetectionResult
                    {
                        version = store.version,
                        source = "store"
                    };
                }
            }
            catch
            {
                // A Store probe failure is treated as a missing Store client.
            }
            return DetectCli("codex-cli");
        }

        public static string[] TargetIds()
        {
            EditionProfile edition = EditionProfile.LoadEmbedded();
            return Definitions.Where(
                row => edition.Includes(row[0])
            ).Select(row => row[0]).ToArray();
        }

        public static bool TryGetAcceptedPackage(
            string bundleRoot,
            string target,
            out TrustedPackage package
        )
        {
            package = null;
            EditionProfile edition = EditionProfile.LoadEmbedded();
            if (!edition.Includes(target))
            {
                return false;
            }
            string[] definition = Definitions.FirstOrDefault(
                row => String.Equals(
                    row[0],
                    target,
                    StringComparison.Ordinal
                )
            );
            if (definition == null)
            {
                return false;
            }
            ProviderEligibilityRecord eligibility;
            Dictionary<string, TrustedPackage> trusted =
                LoadTrustedPackages(out eligibility);
            TrustedPackage candidate;
            if (!trusted.TryGetValue(target, out candidate) ||
                !ValidateTrustedPackage(
                    bundleRoot,
                    candidate,
                    definition[0],
                    definition[2]
                ))
            {
                return false;
            }
            package = candidate;
            return true;
        }

        private static Dictionary<string, TrustedPackage> LoadTrustedPackages(
            out ProviderEligibilityRecord eligibility
        )
        {
            Stream resource = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream("TrustedPackages.json");
            if (resource == null)
            {
                throw new InvalidOperationException(
                    "Trusted package index is missing"
                );
            }
            string json;
            using (resource)
            using (StreamReader reader = new StreamReader(
                resource,
                Encoding.UTF8,
                true
            ))
            {
                json = reader.ReadToEnd();
            }
            TrustedPackageIndex index = new JavaScriptSerializer()
                .Deserialize<TrustedPackageIndex>(json);
            if (index == null || index.schema_version != 1 ||
                index.packages == null)
            {
                throw new InvalidOperationException(
                    "Trusted package index is invalid"
                );
            }
            eligibility = index.provider_eligibility;
            Dictionary<string, TrustedPackage> packages =
                new Dictionary<string, TrustedPackage>(
                    StringComparer.OrdinalIgnoreCase
                );
            foreach (TrustedPackage package in index.packages)
            {
                if (package == null || String.IsNullOrWhiteSpace(package.target) ||
                    packages.ContainsKey(package.target))
                {
                    throw new InvalidOperationException(
                        "Trusted package index contains duplicate targets"
                    );
                }
                packages.Add(package.target, package);
            }
            return packages;
        }

        private static string ProviderEligibilityState(
            string bundleRoot,
            ProviderEligibilityRecord record
        )
        {
            if (record == null ||
                String.Equals(
                    record.status,
                    "NOT_PROVIDED",
                    StringComparison.Ordinal
                ))
            {
                return "NOT_PROVIDED";
            }
            DateTimeOffset reviewed = DateTimeOffset.MinValue;
            DateTimeOffset expires = DateTimeOffset.MinValue;
            const string format = "yyyy-MM-dd'T'HH:mm:ss'Z'";
            DateTimeStyles styles = DateTimeStyles.AssumeUniversal |
                DateTimeStyles.AdjustToUniversal;
            bool valid = String.Equals(
                    record.status,
                    "PASS",
                    StringComparison.Ordinal
                ) &&
                ValidateFile(bundleRoot, record.evidence) &&
                DateTimeOffset.TryParseExact(
                    record.reviewed_at_utc,
                    format,
                    CultureInfo.InvariantCulture,
                    styles,
                    out reviewed
                ) &&
                DateTimeOffset.TryParseExact(
                    record.expires_at_utc,
                    format,
                    CultureInfo.InvariantCulture,
                    styles,
                    out expires
                );
            if (!valid)
            {
                return "INVALID_OR_EXPIRED";
            }
            DateTimeOffset now = DateTimeOffset.UtcNow;
            if (reviewed > now.AddMinutes(5) ||
                expires <= now ||
                expires <= reviewed ||
                expires - reviewed > TimeSpan.FromDays(7))
            {
                return "INVALID_OR_EXPIRED";
            }
            return "PASS";
        }

        private static bool ValidateTrustedPackage(
            string bundleRoot,
            TrustedPackage package,
            string target,
            string clientId
        )
        {
            if (!String.Equals(
                    package.target,
                    target,
                    StringComparison.Ordinal
                ) ||
                !String.Equals(
                    package.client_id,
                    clientId,
                    StringComparison.Ordinal
                ) ||
                String.IsNullOrWhiteSpace(package.supported_version))
            {
                return false;
            }
            if (!ValidateFile(bundleRoot, package.asset) ||
                !ValidateFile(bundleRoot, package.release_manifest))
            {
                return false;
            }
            if (String.Equals(
                    package.trust_level,
                    "accepted",
                    StringComparison.Ordinal))
            {
                return ValidateFile(
                        bundleRoot,
                        package.acceptance_evidence
                    ) &&
                    ValidateFile(
                        bundleRoot,
                        package.release_verification
                    ) &&
                    ValidateFile(
                        bundleRoot,
                        package.package_acceptance
                    );
            }
            if (String.Equals(
                    package.trust_level,
                    "owner_candidate",
                    StringComparison.Ordinal))
            {
                EditionProfile edition =
                    EditionProfile.LoadEmbedded();
                return edition.owner_controlled &&
                    String.Equals(
                        target,
                        "claude",
                        StringComparison.Ordinal
                    ) &&
                    ValidateFile(
                        bundleRoot,
                        package.candidate_acceptance
                    ) &&
                    ValidateFile(
                        bundleRoot,
                        package.live_canary
                    ) &&
                    ValidateFile(
                        bundleRoot,
                        package.components_lock
                    ) &&
                    ValidateFile(
                        bundleRoot,
                        package.non_releasable
                    );
            }
            return false;
        }

        private static bool ValidateFile(string bundleRoot, TrustedFile record)
        {
            if (record == null || record.bytes < 0 ||
                String.IsNullOrWhiteSpace(record.relative_path) ||
                String.IsNullOrWhiteSpace(record.sha256) ||
                record.sha256.Length != 64)
            {
                return false;
            }
            string root = Path.GetFullPath(bundleRoot)
                .TrimEnd(Path.DirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            string relative = record.relative_path.Replace(
                '/',
                Path.DirectorySeparatorChar
            );
            string path = Path.GetFullPath(Path.Combine(root, relative));
            if (!path.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            if (File.Exists(path))
            {
                FileInfo info = new FileInfo(path);
                return info.Length == record.bytes &&
                    String.Equals(
                        BundleIntegrity.Sha256(path),
                        record.sha256,
                        StringComparison.Ordinal
                    );
            }
            return BundleIntegrity.ValidateResource(
                record.resource_name,
                record.sha256,
                record.bytes
            );
        }

        public static string ResolveTrustedPath(
            string bundleRoot,
            TrustedFile record
        )
        {
            if (!ValidateFile(bundleRoot, record))
            {
                throw new InvalidOperationException(
                    "Trusted package file is missing or changed"
                );
            }
            return Path.GetFullPath(Path.Combine(
                bundleRoot,
                record.relative_path.Replace('/', Path.DirectorySeparatorChar)
            ));
        }
    }

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

    internal sealed class RuntimePayload : IDisposable
    {
        public string Root { get; private set; }
        public string EnginePath { get; private set; }
        public string PackagePath { get; private set; }

        public static RuntimePayload Prepare(TrustedPackage package)
        {
            int protocol;
            if (!BundleIntegrity.ValidateEngine("", out protocol))
            {
                throw new InvalidOperationException(
                    "Embedded Foundation engine is invalid"
                );
            }
            if (!BundleIntegrity.ValidateResource(
                    package.asset.resource_name,
                    package.asset.sha256,
                    package.asset.bytes
                ))
            {
                throw new InvalidOperationException(
                    "Embedded target package is invalid"
                );
            }
            string root = Path.Combine(
                Path.GetTempPath(),
                "llm-foundation-runtime-" + Guid.NewGuid().ToString("N")
            );
            Directory.CreateDirectory(root);
            try
            {
                string engineRoot = Path.Combine(root, "engine");
                Directory.CreateDirectory(engineRoot);
                string engine = Path.Combine(engineRoot, "foundation.ps1");
                BundleIntegrity.WriteResource(
                    "FoundationEngine.foundation.ps1",
                    engine
                );
                BundleIntegrity.WriteResource(
                    "FoundationEngine.engine-manifest.json",
                    Path.Combine(engineRoot, "engine-manifest.json")
                );
                BundleIntegrity.WriteResource(
                    "FoundationEngine.VERSION",
                    Path.Combine(engineRoot, "VERSION")
                );
                string packagePath = Path.Combine(root, "target-package.zip");
                BundleIntegrity.WriteResource(
                    package.asset.resource_name,
                    packagePath
                );
                return new RuntimePayload
                {
                    Root = root,
                    EnginePath = engine,
                    PackagePath = packagePath
                };
            }
            catch
            {
                if (Directory.Exists(root))
                {
                    Directory.Delete(root, true);
                }
                throw;
            }
        }

        public void Dispose()
        {
            if (!String.IsNullOrWhiteSpace(Root) && Directory.Exists(Root))
            {
                Directory.Delete(Root, true);
            }
        }
    }

    internal static class FoundationWorkflow
    {
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
            using (RuntimePayload runtime = RuntimePayload.Prepare(package))
            {
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
                    standardOutput = process.StandardOutput.ReadToEnd();
                    standardError = process.StandardError.ReadToEnd();
                    if (!process.WaitForExit(120000))
                    {
                        try
                        {
                            process.Kill();
                        }
                        catch
                        {
                        }
                        standardError = "Foundation operation timed out";
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

    internal static class BundleIntegrity
    {
        public static bool ValidateEngine(string bundleRoot, out int protocol)
        {
            protocol = 0;
            byte[] manifestBytes;
            byte[] scriptBytes;
            try
            {
                manifestBytes = ReadResourceBytes(
                    "FoundationEngine.engine-manifest.json"
                );
                scriptBytes = ReadResourceBytes(
                    "FoundationEngine.foundation.ps1"
                );
            }
            catch
            {
                return false;
            }
            Dictionary<string, object> manifest =
                new JavaScriptSerializer().Deserialize<
                    Dictionary<string, object>
                >(Encoding.UTF8.GetString(manifestBytes));
            object expectedObject;
            object protocolObject;
            if (!manifest.TryGetValue("foundation_ps1_sha256", out expectedObject) ||
                !manifest.TryGetValue("protocol_version", out protocolObject))
            {
                return false;
            }
            protocol = Convert.ToInt32(protocolObject, CultureInfo.InvariantCulture);
            string expected = Convert.ToString(
                expectedObject,
                CultureInfo.InvariantCulture
            );
            bool embeddedValid = String.Equals(
                expected,
                Sha256(scriptBytes),
                StringComparison.OrdinalIgnoreCase
            );
            if (!embeddedValid || String.IsNullOrWhiteSpace(bundleRoot))
            {
                return embeddedValid;
            }
            string engineRoot = Path.Combine(bundleRoot, "engine");
            string scriptPath = Path.Combine(engineRoot, "foundation.ps1");
            string manifestPath = Path.Combine(engineRoot, "engine-manifest.json");
            if (!File.Exists(scriptPath) && !File.Exists(manifestPath))
            {
                return true;
            }
            return File.Exists(scriptPath) &&
                File.Exists(manifestPath) &&
                String.Equals(
                    Sha256(scriptPath),
                    Sha256(scriptBytes),
                    StringComparison.Ordinal
                ) &&
                String.Equals(
                    Sha256(manifestPath),
                    Sha256(manifestBytes),
                    StringComparison.Ordinal
                );
        }

        public static string ReadBundleVersion(string bundleRoot)
        {
            string path = Path.Combine(bundleRoot, "bundle-manifest.json");
            if (!File.Exists(path))
            {
                try
                {
                    return Encoding.UTF8.GetString(
                        ReadResourceBytes("FoundationInstaller.VERSION")
                    ).Trim();
                }
                catch
                {
                    return "unknown";
                }
            }
            JavaScriptSerializer serializer = new JavaScriptSerializer();
            Dictionary<string, object> manifest = serializer.Deserialize<
                Dictionary<string, object>
            >(File.ReadAllText(path, Encoding.UTF8));
            object value;
            return manifest.TryGetValue("version", out value)
                ? Convert.ToString(value, CultureInfo.InvariantCulture)
                : "unknown";
        }

        internal static string Sha256(string path)
        {
            using (FileStream stream = File.OpenRead(path))
            using (SHA256 algorithm = SHA256.Create())
            {
                return String.Concat(
                    algorithm.ComputeHash(stream).Select(
                        value => value.ToString("x2", CultureInfo.InvariantCulture)
                    )
                );
            }
        }

        internal static bool ValidateResource(
            string resourceName,
            string expectedSha256,
            long expectedBytes
        )
        {
            if (String.IsNullOrWhiteSpace(resourceName) ||
                String.IsNullOrWhiteSpace(expectedSha256) ||
                expectedBytes < 0)
            {
                return false;
            }
            Stream stream = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream(resourceName);
            if (stream == null)
            {
                return false;
            }
            using (stream)
            {
                return stream.Length == expectedBytes &&
                    String.Equals(
                        Sha256(stream),
                        expectedSha256,
                        StringComparison.Ordinal
                    );
            }
        }

        internal static void WriteResource(
            string resourceName,
            string destination
        )
        {
            Stream source = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream(resourceName);
            if (source == null)
            {
                throw new InvalidOperationException(
                    "Embedded resource is missing: " + resourceName
                );
            }
            string parent = Path.GetDirectoryName(destination);
            if (!String.IsNullOrWhiteSpace(parent))
            {
                Directory.CreateDirectory(parent);
            }
            using (source)
            using (FileStream output = File.Create(destination))
            {
                source.CopyTo(output);
            }
        }

        private static byte[] ReadResourceBytes(string resourceName)
        {
            Stream resource = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream(resourceName);
            if (resource == null)
            {
                throw new InvalidOperationException(
                    "Embedded resource is missing"
                );
            }
            using (resource)
            using (MemoryStream memory = new MemoryStream())
            {
                resource.CopyTo(memory);
                return memory.ToArray();
            }
        }

        private static string Sha256(byte[] payload)
        {
            using (SHA256 algorithm = SHA256.Create())
            {
                return String.Concat(
                    algorithm.ComputeHash(payload).Select(
                        value => value.ToString("x2", CultureInfo.InvariantCulture)
                    )
                );
            }
        }

        private static string Sha256(Stream stream)
        {
            using (SHA256 algorithm = SHA256.Create())
            {
                return String.Concat(
                    algorithm.ComputeHash(stream).Select(
                        value => value.ToString("x2", CultureInfo.InvariantCulture)
                    )
                );
            }
        }
    }

    internal static class InstallerView
    {
        public static UserControl Create(
            string bundleRoot,
            bool loadConnectionState = true
        )
        {
            EditionProfile edition = EditionProfile.LoadEmbedded();
            string viewResource = EditionTheme.ViewResource(edition);
            Stream resource = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream(viewResource);
            if (resource == null)
            {
                throw new InvalidOperationException(
                    "Product UI resource is missing: " + viewResource
                );
            }
            using (resource)
            {
                UserControl view = (UserControl)XamlReader.Load(resource);
                if (edition.product_role == "Installer")
                {
                    ApplyCatalog(
                        view,
                        ProductCatalog.Inspect(
                            bundleRoot,
                            loadConnectionState
                        )
                    );
                }
                ConnectionUi.Bind(
                    view,
                    bundleRoot,
                    loadConnectionState
                );
                if (edition.product_role == "Installer")
                {
                    InstallerActions.Bind(
                        view,
                        bundleRoot,
                        loadConnectionState
                    );
                }
                else
                {
                    LaunchCenterActions.Bind(
                        view,
                        bundleRoot,
                        loadConnectionState
                    );
                }
                OperatorGuideDashboard.Bind(
                    view,
                    bundleRoot,
                    loadConnectionState
                );
                return view;
            }
        }

        internal static void ApplyCatalog(
            UserControl view,
            CatalogResult catalog
        )
        {
            foreach (TargetRow row in catalog.targets)
            {
                string prefix = row.id == "codex"
                    ? "Codex"
                    : (row.id == "claude" ? "Claude" : "OpenCode");
                TextBlock status = view.FindName(prefix + "Status") as TextBlock;
                Border badge = view.FindName(
                    prefix + "StatusBadge"
                ) as Border;
                CheckBox selected = view.FindName(prefix + "Selected") as CheckBox;
                if (status != null)
                {
                    status.Text = row.package_state == "accepted"
                        ? (row.client_state == "missing"
                            ? "Клиент не найден · требуется " +
                                row.supported_version
                            : (row.client_state == "unsupported"
                                ? "Версия " + row.detected_version +
                                    " · требуется " + row.supported_version
                                : (row.client_state == "ready"
                                    ? "Готово · клиент " +
                                        row.detected_version
                                    : "Пакет проверен · " +
                                        row.supported_version)))
                        : (row.package_state == "tampered"
                            ? "Пакет повреждён · установка запрещена"
                            : (row.package_state == "owner_candidate"
                                ? "Только владельцу · маркер провайдера не проверен"
                                : (row.package_state == "policy_blocked"
                                ? "Допуск провайдера истёк или недействителен"
                                : (row.client_state == "present_unbound"
                                    ? "Клиент " + row.detected_version +
                                        " найден · пакет не включён"
                                    : (row.client_state == "missing"
                                        ? "Пакет не включён · клиент не найден"
                                        : "Пакет не включён в эту сборку")))));
                    bool ready = row.package_state == "accepted" &&
                        (row.client_state == "ready" ||
                            row.client_state == "not_checked");
                    status.Foreground = new SolidColorBrush(
                        ready
                            ? Color.FromRgb(22, 122, 88)
                            : Color.FromRgb(161, 92, 0)
                    );
                    status.ToolTip = row.package_state == "accepted"
                        ? (row.id == "codex" &&
                            !String.IsNullOrWhiteSpace(row.detected_version)
                            ? "Пакет проверен. Обнаруженная версия клиента: " +
                                row.detected_version
                            : "Пакет проверен. Поддерживаемая версия клиента: " +
                                row.supported_version)
                        : (row.package_state == "owner_candidate"
                            ? "Пакет доступен только в версии владельца. Установщик не подтверждает допуск провайдера."
                            : (row.package_state == "policy_blocked"
                            ? "Повторите проверку политики поддерживаемых регионов и соберите новый подписанный установщик."
                            : null));
                    if (badge != null)
                    {
                        badge.Background = new SolidColorBrush(
                            ready
                                ? Color.FromRgb(231, 246, 240)
                                : Color.FromRgb(255, 244, 222)
                        );
                    }
                }
                if (selected != null)
                {
                    bool eligible = row.package_state == "accepted" ||
                        row.package_state == "owner_candidate";
                    selected.IsEnabled = eligible;
                    selected.IsChecked = eligible;
                }
            }

            Button action = view.FindName("PrimaryAction") as Button;
            TextBlock statusText = view.FindName("StatusText") as TextBlock;
            Border statusBanner = view.FindName(
                "StatusBanner"
            ) as Border;
            if (action != null)
            {
                action.IsEnabled = catalog.install_enabled;
            }
            if (statusText != null)
            {
                statusText.Text = catalog.install_enabled
                    ? "Компоненты готовы. Следующий шаг — проверяемый план изменений."
                    : (catalog.provider_eligibility == "INVALID_OR_EXPIRED"
                        ? "Установка Claude заблокирована: допуск провайдера истёк или недействителен."
                        : "Установка заблокирована: нет принятых пакетов клиентов.");
                statusText.Foreground = new SolidColorBrush(
                    catalog.install_enabled
                        ? Color.FromRgb(22, 122, 88)
                        : Color.FromRgb(161, 92, 0)
                );
            }
            if (statusBanner != null)
            {
                statusBanner.Background = new SolidColorBrush(
                    catalog.install_enabled
                        ? Color.FromRgb(231, 246, 240)
                        : Color.FromRgb(255, 244, 222)
                );
            }
            SetWorkflowStep(view, 1, false);
        }

        internal static void SetWorkflowStep(
            UserControl view,
            int activeStep,
            bool completedAll
        )
        {
            for (int index = 1; index <= 7; index++)
            {
                Border badge = view.FindName(
                    "Step" + index + "Badge"
                ) as Border;
                TextBlock number = view.FindName(
                    "Step" + index + "Number"
                ) as TextBlock;
                TextBlock title = view.FindName(
                    "Step" + index + "Title"
                ) as TextBlock;
                bool complete = completedAll || index < activeStep;
                bool active = !completedAll && index == activeStep;
                if (badge != null)
                {
                    badge.Background = new SolidColorBrush(
                        complete
                            ? Color.FromRgb(22, 122, 88)
                            : (active
                                ? Color.FromRgb(65, 105, 225)
                                : Colors.Transparent)
                    );
                    badge.BorderBrush = new SolidColorBrush(
                        complete
                            ? Color.FromRgb(22, 122, 88)
                            : (active
                                ? Color.FromRgb(65, 105, 225)
                                : Color.FromRgb(82, 96, 120))
                    );
                    badge.BorderThickness = complete || active
                        ? new Thickness(0)
                        : new Thickness(1.5);
                }
                if (number != null)
                {
                    number.Text = complete ? "\u2713" : index.ToString(
                        CultureInfo.InvariantCulture
                    );
                    number.Foreground = new SolidColorBrush(
                        complete || active
                            ? Colors.White
                            : Color.FromRgb(170, 182, 200)
                    );
                }
                if (title != null)
                {
                    title.Foreground = new SolidColorBrush(
                        complete || active
                            ? Colors.White
                            : Color.FromRgb(201, 210, 223)
                    );
                }
            }
        }

        public static void RenderPreview(
            UserControl view,
            string outputPath,
            int width,
            int height
        )
        {
            view.Width = width;
            view.Height = height;
            view.Measure(new Size(width, height));
            view.Arrange(new Rect(0, 0, width, height));
            view.UpdateLayout();

            RenderTargetBitmap bitmap = new RenderTargetBitmap(
                width,
                height,
                96,
                96,
                PixelFormats.Pbgra32
            );
            bitmap.Render(view);
            PngBitmapEncoder encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(bitmap));

            string fullPath = Path.GetFullPath(outputPath);
            string parent = Path.GetDirectoryName(fullPath);
            if (!String.IsNullOrEmpty(parent))
            {
                Directory.CreateDirectory(parent);
            }
            using (FileStream stream = File.Create(fullPath))
            {
                encoder.Save(stream);
            }
        }
    }

    internal static class LaunchCenterActions
    {
        public static void Bind(
            UserControl view,
            string bundleRoot,
            bool interactive
        )
        {
            ListBox targetList = view.FindName(
                "LaunchTargetList"
            ) as ListBox;
            Button launch = view.FindName("LaunchSelected") as Button;
            TextBlock routeStatus = view.FindName(
                "RouteStatus"
            ) as TextBlock;
            TextBlock evidenceStatus = view.FindName(
                "EvidenceStatus"
            ) as TextBlock;
            TextBlock rollbackStatus = view.FindName(
                "RollbackStatus"
            ) as TextBlock;
            RadioButton direct = view.FindName(
                "RouteDirect"
            ) as RadioButton;
            RadioButton vpn = view.FindName("RouteVpn") as RadioButton;
            RadioButton http = view.FindName(
                "RouteHttp"
            ) as RadioButton;
            RadioButton https = view.FindName(
                "RouteHttps"
            ) as RadioButton;
            TextBlock selectedClientName = view.FindName(
                "SelectedClientName"
            ) as TextBlock;
            TextBlock selectedRouteName = view.FindName(
                "SelectedRouteName"
            ) as TextBlock;
            TextBlock selectedProviderName = view.FindName(
                "SelectedProviderName"
            ) as TextBlock;
            if (targetList == null || launch == null)
            {
                return;
            }
            Action refreshLabel = delegate
            {
                ListBoxItem selected =
                    targetList.SelectedItem as ListBoxItem;
                string targetId = selected == null
                    ? null
                    : selected.Tag as string;
                launch.IsEnabled = !String.IsNullOrWhiteSpace(targetId);
                launch.Content = SelectionLabel(targetId);
                if (selectedClientName != null)
                {
                    selectedClientName.Text =
                        TargetDisplayName(targetId);
                }
                if (selectedProviderName != null)
                {
                    selectedProviderName.Text =
                        TargetProviderName(targetId);
                }
            };
            Action refreshRoute = delegate
            {
                string route = direct != null &&
                    direct.IsChecked == true
                    ? "Direct"
                    : (vpn != null && vpn.IsChecked == true
                        ? "VPN"
                        : (http != null && http.IsChecked == true
                            ? "SingBoxHttp"
                            : "SingBoxHttps"));
                string routeLabel = RouteLabel(route);
                if (selectedRouteName != null)
                {
                    selectedRouteName.Text =
                        routeLabel.ToUpperInvariant();
                }
                if (routeStatus != null)
                {
                    routeStatus.Text = routeLabel + " · готово";
                }
            };
            targetList.SelectionChanged += delegate
            {
                refreshLabel();
            };
            RoutedEventHandler routeChanged = delegate
            {
                refreshRoute();
            };
            if (direct != null)
            {
                direct.Checked += routeChanged;
            }
            if (vpn != null)
            {
                vpn.Checked += routeChanged;
            }
            if (http != null)
            {
                http.Checked += routeChanged;
            }
            if (https != null)
            {
                https.Checked += routeChanged;
            }
            refreshLabel();
            refreshRoute();
            if (!interactive)
            {
                return;
            }
            launch.Click += async delegate
            {
                ListBoxItem selected =
                    targetList.SelectedItem as ListBoxItem;
                string targetId = selected == null
                    ? null
                    : selected.Tag as string;
                if (String.IsNullOrWhiteSpace(targetId))
                {
                    return;
                }
                string route = direct != null &&
                    direct.IsChecked == true
                    ? "Direct"
                    : (vpn != null && vpn.IsChecked == true
                        ? "VPN"
                        : (http != null && http.IsChecked == true
                            ? "SingBoxHttp"
                            : (https != null &&
                                https.IsChecked == true
                                ? "SingBoxHttps"
                                : "Direct")));
                EditionProfile edition = EditionProfile.LoadEmbedded();
                string home = Environment.GetFolderPath(
                    Environment.SpecialFolder.UserProfile
                );
                LaunchTargetResolution resolution =
                    LaunchTargetResolver.Resolve(
                        edition,
                        bundleRoot,
                        home,
                        targetId
                    );
                if (resolution.status != "RESOLVED")
                {
                    if (evidenceStatus != null)
                    {
                        evidenceStatus.Text = resolution.reason;
                        evidenceStatus.Foreground = new SolidColorBrush(
                            Color.FromRgb(252, 122, 77)
                        );
                    }
                    return;
                }
                launch.IsEnabled = false;
                if (routeStatus != null)
                {
                    routeStatus.Text = RouteLabel(route) +
                        " · запуск";
                }
                LauncherSessionResult result = await Task.Run(
                    () => ClientLauncher.StartAndWait(
                        resolution,
                        route,
                        bundleRoot,
                        home
                    )
                );
                if (routeStatus != null)
                {
                    routeStatus.Text = RouteLabel(result.transport) +
                        " · " + ResultLabel(result.status);
                }
                if (evidenceStatus != null)
                {
                    evidenceStatus.Text = result.reason ??
                        "Точный клиент проверен";
                    evidenceStatus.Foreground = new SolidColorBrush(
                        result.status == "PASS"
                            ? Color.FromRgb(119, 203, 185)
                            : Color.FromRgb(252, 122, 77)
                    );
                }
                if (rollbackStatus != null)
                {
                    rollbackStatus.Text = result.cleanup_verified
                        ? "Очистка подтверждена"
                        : "Очистка не подтверждена";
                }
                refreshLabel();
            };
        }

        internal static bool SelectTarget(
            UserControl view,
            string targetId
        )
        {
            ListBox targetList = view.FindName(
                "LaunchTargetList"
            ) as ListBox;
            if (targetList == null)
            {
                return false;
            }
            foreach (object candidate in targetList.Items)
            {
                ListBoxItem item = candidate as ListBoxItem;
                if (item != null && String.Equals(
                        item.Tag as string,
                        targetId,
                        StringComparison.Ordinal
                    ))
                {
                    targetList.SelectedItem = item;
                    item.ApplyTemplate();
                    return true;
                }
            }
            return false;
        }

        internal static Dictionary<string, object> DescribeSelection(
            UserControl view
        )
        {
            ListBox targetList = view.FindName(
                "LaunchTargetList"
            ) as ListBox;
            Button launch = view.FindName("LaunchSelected") as Button;
            TextBlock client = view.FindName(
                "SelectedClientName"
            ) as TextBlock;
            TextBlock provider = view.FindName(
                "SelectedProviderName"
            ) as TextBlock;
            TextBlock route = view.FindName(
                "SelectedRouteName"
            ) as TextBlock;
            ListBoxItem selected = targetList == null
                ? null
                : targetList.SelectedItem as ListBoxItem;
            Border frame = null;
            if (selected != null)
            {
                selected.ApplyTemplate();
                frame = selected.Template == null
                    ? null
                    : selected.Template.FindName(
                        "SelectionFrame",
                        selected
                    ) as Border;
            }
            bool visible = frame != null &&
                frame.BorderThickness.Left >= 2 &&
                frame.BorderBrush != null &&
                frame.BorderBrush != Brushes.Transparent;
            Dictionary<string, object> state =
                new Dictionary<string, object>();
            state["selected_target"] = selected == null
                ? null
                : selected.Tag as string;
            state["button_content"] = launch == null
                ? null
                : Convert.ToString(
                    launch.Content,
                    CultureInfo.InvariantCulture
                );
            state["button_enabled"] =
                launch != null && launch.IsEnabled;
            state["selection_visual"] =
                visible ? "VISIBLE" : "MISSING";
            state["client_display"] =
                client == null ? null : client.Text;
            state["provider_display"] =
                provider == null ? null : provider.Text;
            state["route_display"] =
                route == null ? null : route.Text;
            return state;
        }

        private static string SelectionLabel(string targetId)
        {
            if (String.IsNullOrWhiteSpace(targetId))
            {
                return "Выберите клиент";
            }
            if (targetId.StartsWith(
                    "codex",
                    StringComparison.Ordinal
                ))
            {
                return "Запустить Codex →";
            }
            if (targetId.StartsWith(
                    "claude",
                    StringComparison.Ordinal
                ))
            {
                return "Запустить Claude →";
            }
            if (targetId.StartsWith(
                    "opencode",
                    StringComparison.Ordinal
                ))
            {
                return "Запустить OpenCode →";
            }
            return "Запустить выбранный клиент →";
        }

        private static string TargetDisplayName(string targetId)
        {
            if (String.IsNullOrWhiteSpace(targetId))
            {
                return "НЕ ВЫБРАНО";
            }
            if (targetId.StartsWith(
                    "codex",
                    StringComparison.Ordinal
                ))
            {
                return "CODEX";
            }
            if (targetId.StartsWith(
                    "claude",
                    StringComparison.Ordinal
                ))
            {
                return "CLAUDE";
            }
            return "OPENCODE CLI";
        }

        private static string TargetProviderName(string targetId)
        {
            if (String.IsNullOrWhiteSpace(targetId))
            {
                return "НЕ ВЫБРАН";
            }
            if (targetId.StartsWith(
                    "codex",
                    StringComparison.Ordinal
                ))
            {
                return "OPENAI";
            }
            if (targetId.StartsWith(
                    "claude",
                    StringComparison.Ordinal
                ))
            {
                return "ANTHROPIC";
            }
            return "ВЫБИРАЕТ КЛИЕНТ";
        }

        private static string RouteLabel(string route)
        {
            return String.Equals(
                    route,
                    "Direct",
                    StringComparison.OrdinalIgnoreCase
                )
                ? "Напрямую"
                : (String.Equals(
                        route,
                        "VPN",
                        StringComparison.OrdinalIgnoreCase
                    )
                    ? "VPN"
                    : (String.Equals(
                            route,
                            "SingBoxHttp",
                            StringComparison.OrdinalIgnoreCase
                        )
                        ? "SingBox HTTP"
                        : (String.Equals(
                                route,
                                "SingBoxHttps",
                                StringComparison.OrdinalIgnoreCase
                            )
                            ? "SingBox HTTPS"
                            : (route ?? "Маршрут"))));
        }

        private static string ResultLabel(string status)
        {
            return String.Equals(
                    status,
                    "PASS",
                    StringComparison.OrdinalIgnoreCase
                )
                ? "готово"
                : "ошибка";
        }
    }

    internal static class InstallerActions
    {
        private sealed class WorkflowRunResult
        {
            public int code { get; set; }
            public string output { get; set; }
            public string error { get; set; }
        }

        public static void Bind(
            UserControl view,
            string bundleRoot,
            bool interactive
        )
        {
            if (!interactive)
            {
                return;
            }
            Button primary = view.FindName("PrimaryAction") as Button;
            TextBlock status = view.FindName("StatusText") as TextBlock;
            Button refresh = view.FindName(
                "RefreshEnvironment"
            ) as Button;
            if (primary == null || status == null)
            {
                return;
            }
            Action refreshAction = delegate
            {
                InstallerView.ApplyCatalog(
                    view,
                    ProductCatalog.Inspect(bundleRoot, true)
                );
            };
            if (refresh != null)
            {
                refresh.Click += delegate
                {
                    refreshAction();
                };
            }
            primary.Click += async delegate
            {
                await RunPlanAndInstallAsync(
                    view,
                    bundleRoot
                );
            };
        }

        private static async Task RunPlanAndInstallAsync(
            UserControl view,
            string bundleRoot
        )
        {
            PlatformCompatibility.RequireSupported();
            EditionProfile edition = EditionProfile.LoadEmbedded();
            CatalogResult catalog = ProductCatalog.Inspect(bundleRoot, true);
            List<TargetRow> selected = catalog.targets.Where(row =>
            {
                string prefix = row.id == "codex"
                    ? "Codex"
                    : (row.id == "claude" ? "Claude" : "OpenCode");
                CheckBox box = view.FindName(
                    prefix + "Selected"
                ) as CheckBox;
                return box != null && box.IsChecked == true &&
                    IsInstallableTarget(row, edition);
            }).ToList();
            if (selected.Count == 0)
            {
                SetStatus(
                    view,
                    "Нет выбранных принятых баз.",
                    "warning"
                );
                return;
            }
            string home = Environment.GetFolderPath(
                Environment.SpecialFolder.UserProfile
            );
            ProgressBar progress = view.FindName(
                "InstallProgress"
            ) as ProgressBar;
            string connectionError;
            InstallerView.SetWorkflowStep(view, 2, false);
            if (!ConnectionUi.TrySaveCurrent(
                    view,
                    out connectionError))
            {
                SetStatus(
                    view,
                    "Параметры соединения не сохранены: " +
                        connectionError,
                    "warning"
                );
                return;
            }
            SetBusy(view, catalog, true);
            if (progress != null)
            {
                progress.Minimum = 0;
                progress.Maximum = Math.Max(1, selected.Count * 7);
                progress.Value = 0;
                progress.Visibility = Visibility.Visible;
            }
            int completedOperations = 0;
            List<string> notices = new List<string>();
            List<TargetRow> clientReady = new List<TargetRow>();
            List<TargetRow> completed = new List<TargetRow>();
            try
            {
                InstallerView.SetWorkflowStep(view, 3, false);
                SetStatus(
                    view,
                    "Проверяются официальные клиенты и версии...",
                    "info"
                );
                foreach (TargetRow row in selected)
                {
                    TargetClientPlanResult clientPlan =
                        await RunClientPlanAsync(
                            bundleRoot,
                            home,
                            row.id
                        );
                    completedOperations++;
                    SetProgress(progress, completedOperations);
                    if (clientPlan.status == "BLOCKED")
                    {
                        ClientPlanResult blocked =
                            clientPlan.clients.First(plan =>
                                plan.status ==
                                    "BLOCKED_NO_DOWNGRADE");
                        notices.Add(
                            row.display_name +
                            ": база пропущена — обнаружена версия " +
                            blocked.detected_version +
                            ", автоматический downgrade запрещён."
                        );
                        continue;
                    }
                    List<ClientPlanResult> guided =
                        clientPlan.clients.Where(plan =>
                            plan.status == "GUIDED_STORE"
                        ).ToList();
                    if (guided.Count > 0)
                    {
                        MessageBoxResult openStore = MessageBox.Show(
                            "Для " + row.display_name +
                            " требуется официальный Microsoft Store " +
                            "пакет OpenAI.Codex.\n\n" +
                            "Открыть точную карточку Store сейчас? " +
                            "После установки нажмите «Проверить снова». " +
                            "Остальные базы продолжат установку.",
                            "Требуется Codex Desktop",
                            MessageBoxButton.YesNo,
                            MessageBoxImage.Information
                        );
                        if (openStore == MessageBoxResult.Yes)
                        {
                            foreach (ClientPlanResult store in guided)
                            {
                                ClientBootstrap.OpenStoreSource(
                                    bundleRoot,
                                    store.client_id
                                );
                            }
                        }
                        notices.Add(
                            row.display_name +
                            ": ожидается установка Codex Desktop из Store."
                        );
                        continue;
                    }
                    List<ClientPlanResult> toInstall =
                        clientPlan.clients.Where(plan =>
                            plan.status == "INSTALL_AVAILABLE"
                        ).ToList();
                    if (toInstall.Count > 0)
                    {
                        MessageBoxResult clientApproval =
                            MessageBox.Show(
                                "Для " + row.display_name +
                                " будут скачаны и проверены официальные " +
                                "клиенты:\n\n" +
                                String.Join(
                                    "\n",
                                    toInstall.Select(plan =>
                                        "• " + plan.client_id + " " +
                                        plan.supported_version
                                    ).ToArray()
                                ) +
                                "\n\nSHA-256 и издатель проверяются до " +
                                "запуска. Учётные данные не читаются. " +
                                "Продолжить?",
                                "Установка официальных клиентов",
                                MessageBoxButton.YesNo,
                                MessageBoxImage.Question
                            );
                        if (clientApproval != MessageBoxResult.Yes)
                        {
                            notices.Add(
                                row.display_name +
                                ": установка клиентов отменена."
                            );
                            continue;
                        }
                    }
                    bool clientFailed = false;
                    foreach (ClientPlanResult client in toInstall)
                    {
                        SetStatus(
                            view,
                            "Загрузка и проверка " +
                                client.client_id + "...",
                            "info"
                        );
                        try
                        {
                            object installed =
                                await RunClientBootstrapAsync(
                                    bundleRoot,
                                    home,
                                    client.client_id
                                );
                            ClientPlanResult blocked =
                                installed as ClientPlanResult;
                            if (blocked != null)
                            {
                                notices.Add(
                                    row.display_name +
                                    ": клиент заблокирован политикой " +
                                    "downgrade."
                                );
                                clientFailed = true;
                                break;
                            }
                        }
                        catch (Exception exception)
                        {
                            notices.Add(
                                row.display_name +
                                ": клиент не установлен — " +
                                FirstUseful(exception.Message, null)
                            );
                            clientFailed = true;
                            break;
                        }
                        completedOperations++;
                        SetProgress(progress, completedOperations);
                    }
                    if (clientFailed)
                    {
                        continue;
                    }
                    TargetClientPlanResult verified =
                        await RunClientPlanAsync(
                            bundleRoot,
                            home,
                            row.id
                        );
                    if (verified.status != "READY")
                    {
                        notices.Add(
                            row.display_name +
                            ": клиенты не достигли состояния READY."
                        );
                        continue;
                    }
                    row.detected_version = verified.clients.First(plan =>
                        plan.client_id == row.client_id
                    ).detected_version;
                    row.client_state = "ready";
                    clientReady.Add(row);
                }

                if (clientReady.Count == 0)
                {
                    SetStatus(
                        view,
                        "Ни одна база не готова к установке. " +
                            String.Join(" ", notices.ToArray()),
                        "warning"
                    );
                    return;
                }

                InstallerView.SetWorkflowStep(view, 4, false);
                SetStatus(
                    view,
                    "Формируется проверяемый план баз...",
                    "info"
                );
                List<string> planLines = new List<string>();
                List<TargetRow> planned = new List<TargetRow>();
                foreach (TargetRow row in clientReady)
                {
                    WorkflowRunResult result = await RunFoundationAsync(
                        bundleRoot,
                        "plan",
                        row,
                        home
                    );
                    if (result.code != 0)
                    {
                        SetStatus(
                            view,
                            "План заблокирован для " +
                                row.display_name + ": " +
                                FirstUseful(result.error, result.output),
                            "warning"
                        );
                        notices.Add(
                            row.display_name +
                            ": план базы заблокирован."
                        );
                        continue;
                    }
                    Dictionary<string, object> plan =
                        new JavaScriptSerializer().Deserialize<
                            Dictionary<string, object>
                        >(result.output);
                    object actionsValue;
                    int actions = plan.TryGetValue(
                        "actions",
                        out actionsValue
                    ) && actionsValue is object[]
                        ? ((object[])actionsValue).Length
                        : 0;
                    planLines.Add(
                        row.display_name + ": " + actions +
                        " файлов, backup и doctor"
                    );
                    planned.Add(row);
                    completedOperations++;
                    SetProgress(progress, completedOperations);
                }
                if (planned.Count == 0)
                {
                    return;
                }
                MessageBoxResult approval = MessageBox.Show(
                    "Будет установлено:\n\n" +
                    String.Join("\n", planLines.ToArray()) +
                    "\n\nАвторизация, сессии, проекты и состояние клиентов " +
                    "останутся без изменений. Продолжить?",
                    "План установки",
                    MessageBoxButton.YesNo,
                    MessageBoxImage.Question
                );
                if (approval != MessageBoxResult.Yes)
                {
                    SetStatus(
                        view,
                        "План сформирован; установка отменена пользователем.",
                        "info"
                    );
                    return;
                }
                InstallerView.SetWorkflowStep(view, 5, false);
                foreach (TargetRow row in planned)
                {
                    SetStatus(
                        view,
                        "Устанавливается " + row.display_name +
                            ": backup и атомарное применение...",
                        "info"
                    );
                    WorkflowRunResult install = await RunFoundationAsync(
                        bundleRoot,
                        "install",
                        row,
                        home
                    );
                    completedOperations++;
                    SetProgress(progress, completedOperations);
                    if (install.code != 0)
                    {
                        notices.Add(
                            row.display_name + ": установка остановлена — " +
                            FirstUseful(
                                install.error,
                                install.output
                            )
                        );
                        continue;
                    }
                    SetStatus(
                        view,
                        "Проверяется " + row.display_name +
                            " через Foundation doctor...",
                        "info"
                    );
                    WorkflowRunResult doctor = await RunFoundationAsync(
                        bundleRoot,
                        "doctor",
                        row,
                        home
                    );
                    completedOperations++;
                    SetProgress(progress, completedOperations);
                    if (doctor.code == 0)
                    {
                        completed.Add(row);
                        continue;
                    }
                    SetStatus(
                        view,
                        "Doctor не пройден для " + row.display_name +
                            "; выполняется автоматический rollback...",
                        "warning"
                    );
                    WorkflowRunResult rollback = await RunFoundationAsync(
                        bundleRoot,
                        "rollback",
                        row,
                        home
                    );
                    notices.Add(
                        rollback.code == 0
                            ? row.display_name +
                                ": doctor не пройден; предыдущая " +
                                "версия восстановлена."
                            : row.display_name +
                                ": критическая ошибка doctor/rollback; " +
                                "используйте Foundation inventory."
                    );
                }
                if (completed.Count == 0)
                {
                    SetStatus(
                        view,
                        "Установка баз не завершена. " +
                            String.Join(" ", notices.ToArray()),
                        "warning"
                    );
                    return;
                }

                InstallerView.SetWorkflowStep(view, 6, false);
                OpenAuthorizationActions(completed);
                SuccessReportResult report = TryWriteSuccessReport(
                    home,
                    completed
                );
                InstallerView.SetWorkflowStep(view, 7, true);
                string noticeText = notices.Count == 0
                    ? ""
                    : " Ограничения: " +
                        String.Join(" ", notices.ToArray());
                SetStatus(
                    view,
                    report.written
                        ? "Установка завершена. Doctor пройден. Отчёт: " +
                            report.path + noticeText
                        : "Установка завершена. Doctor пройден. " +
                            "Локальный отчёт не сохранён: " + report.error +
                            noticeText,
                    "success"
                );
                MessageBox.Show(
                    "Рабочая среда установлена и проверена.\n\n" +
                    "Авторизация выполняется только в самих клиентах.\n" +
                    "Для обновлений используйте $sync-base.\n" +
                    (report.written
                        ? "Локальный отчёт: " + report.path
                        : "Локальный отчёт не сохранён: " + report.error) +
                    (notices.Count == 0
                        ? ""
                        : "\n\nНе завершено:\n" +
                            String.Join("\n", notices.ToArray())),
                    "LLM Foundation",
                    MessageBoxButton.OK,
                    MessageBoxImage.Information
                );
            }
            catch (Exception exception)
            {
                SetStatus(
                    view,
                    "Операция остановлена: " +
                        FirstUseful(exception.Message, null),
                    "warning"
                );
            }
            finally
            {
                if (progress != null)
                {
                    progress.Visibility = Visibility.Collapsed;
                }
                SetBusy(view, catalog, false);
            }
        }

        private static Task<TargetClientPlanResult> RunClientPlanAsync(
            string bundleRoot,
            string home,
            string target
        )
        {
            return Task.Run(delegate
            {
                return ClientBootstrap.PlanTarget(
                    bundleRoot,
                    home,
                    target
                );
            });
        }

        private static Task<object> RunClientBootstrapAsync(
            string bundleRoot,
            string home,
            string clientId
        )
        {
            return Task.Run(delegate
            {
                string staging = Path.Combine(
                    Path.GetFullPath(home),
                    ".llm-foundation",
                    "staging",
                    "clients"
                );
                return ClientBootstrap.Install(
                    bundleRoot,
                    home,
                    clientId,
                    staging
                );
            });
        }

        private static void OpenAuthorizationActions(
            List<TargetRow> targets
        )
        {
            MessageBoxResult open = MessageBox.Show(
                "Базы установлены. Следующий шаг — интерактивная " +
                "авторизация в выбранных клиентах.\n\n" +
                "Codex: войдите через ChatGPT в приложении.\n" +
                "Claude: выполните вход в окне Claude Code.\n" +
                "OpenCode: запустите /connect → OpenAI → " +
                "ChatGPT Plus/Pro.\n\n" +
                "Установщик не читает и не переносит токены. " +
                "Открыть клиенты сейчас?",
                "Интерактивная авторизация",
                MessageBoxButton.YesNo,
                MessageBoxImage.Information
            );
            if (open != MessageBoxResult.Yes)
            {
                return;
            }
            foreach (TargetRow row in targets)
            {
                if (row.id == "codex")
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = "explorer.exe",
                        Arguments =
                            "shell:AppsFolder\\" +
                            "OpenAI.Codex_2p2nqsd0c76g0!App",
                        UseShellExecute = true
                    });
                }
                else
                {
                    string command = row.id == "claude"
                        ? "claude"
                        : "opencode";
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = Environment.GetEnvironmentVariable(
                            "COMSPEC"
                        ) ?? "cmd.exe",
                        Arguments = "/d /k " + command,
                        UseShellExecute = true
                    });
                }
            }
        }

        private static Task<WorkflowRunResult> RunFoundationAsync(
            string bundleRoot,
            string command,
            TargetRow row,
            string home
        )
        {
            return Task.Run(delegate
            {
                string output;
                string error;
                int code = FoundationWorkflow.Run(
                    bundleRoot,
                    command,
                    row.id,
                    home,
                    row.detected_version,
                    out output,
                    out error
                );
                return new WorkflowRunResult
                {
                    code = code,
                    output = output,
                    error = error
                };
            });
        }

        private static void SetProgress(
            ProgressBar progress,
            int value
        )
        {
            if (progress != null)
            {
                progress.Value = Math.Min(progress.Maximum, value);
            }
        }

        private static void SetBusy(
            UserControl view,
            CatalogResult catalog,
            bool busy
        )
        {
            EditionProfile edition = EditionProfile.LoadEmbedded();
            Button primary = view.FindName("PrimaryAction") as Button;
            Button refresh = view.FindName(
                "RefreshEnvironment"
            ) as Button;
            if (primary != null)
            {
                primary.IsEnabled = !busy && catalog.install_enabled;
                primary.Content = busy
                    ? "Выполняется..."
                    : "Сформировать план";
            }
            if (refresh != null)
            {
                refresh.IsEnabled = !busy;
            }
            foreach (TargetRow row in catalog.targets)
            {
                string prefix = row.id == "codex"
                    ? "Codex"
                    : (row.id == "claude" ? "Claude" : "OpenCode");
                CheckBox box = view.FindName(
                    prefix + "Selected"
                ) as CheckBox;
                if (box != null)
                {
                    box.IsEnabled = !busy &&
                        IsInstallableTarget(row, edition);
                }
            }
        }

        private static bool IsInstallableTarget(
            TargetRow row,
            EditionProfile edition
        )
        {
            return row != null &&
                edition != null &&
                (row.package_state == "accepted" ||
                 (edition.owner_controlled &&
                  row.package_state == "owner_candidate"));
        }

        private static void SetStatus(
            UserControl view,
            string message,
            string state
        )
        {
            TextBlock status = view.FindName("StatusText") as TextBlock;
            Border banner = view.FindName("StatusBanner") as Border;
            Color foreground = state == "success"
                ? Color.FromRgb(22, 122, 88)
                : (state == "info"
                    ? Color.FromRgb(49, 87, 199)
                    : Color.FromRgb(161, 92, 0));
            Color background = state == "success"
                ? Color.FromRgb(231, 246, 240)
                : (state == "info"
                    ? Color.FromRgb(234, 240, 255)
                    : Color.FromRgb(255, 244, 222));
            if (status != null)
            {
                status.Text = message;
                status.Foreground = new SolidColorBrush(foreground);
            }
            if (banner != null)
            {
                banner.Background = new SolidColorBrush(background);
            }
        }

        internal static SuccessReportResult TryWriteSuccessReport(
            string home,
            List<TargetRow> targets
        )
        {
            try
            {
                return new SuccessReportResult
                {
                    written = true,
                    path = WriteSuccessReport(home, targets),
                    error = null
                };
            }
            catch (Exception exception)
            {
                return new SuccessReportResult
                {
                    written = false,
                    path = null,
                    error = FirstUseful(exception.Message, null)
                };
            }
        }

        private static string WriteSuccessReport(
            string home,
            List<TargetRow> targets
        )
        {
            string root = Path.Combine(
                Path.GetFullPath(home),
                ".llm-foundation",
                "reports"
            );
            Directory.CreateDirectory(root);
            string name = "install-" +
                DateTime.UtcNow.ToString(
                    "yyyyMMdd-HHmmss",
                    CultureInfo.InvariantCulture
                ) + "Z-" +
                Guid.NewGuid().ToString("N").Substring(0, 8) +
                ".json";
            string path = Path.Combine(root, name);
            object[] installed = targets.Select(row =>
                (object)new Dictionary<string, object>
                {
                    { "target", row.id },
                    { "client_id", row.client_id },
                    { "client_version", row.detected_version },
                    { "result", "DOCTOR_PASS" }
                }
            ).ToArray();
            Dictionary<string, object> report =
                new Dictionary<string, object>
                {
                    { "schema_version", 1 },
                    { "created_at_utc", DateTime.UtcNow.ToString("o") },
                    {
                        "installer_version",
                        Assembly.GetExecutingAssembly()
                            .GetName().Version.ToString(3)
                    },
                    { "targets", installed },
                    {
                        "network_during_install",
                        "official-client-downloads-only"
                    },
                    { "reverse_flow", false },
                    { "result", "PASS" }
                };
            string temporary = path + ".tmp-" +
                Guid.NewGuid().ToString("N");
            File.WriteAllText(
                temporary,
                new JavaScriptSerializer().Serialize(report) + "\n",
                new UTF8Encoding(false)
            );
            File.Move(temporary, path);
            return path;
        }

        private static string FirstUseful(string error, string output)
        {
            string value = !String.IsNullOrWhiteSpace(error)
                ? error
                : output;
            value = (value ?? "неизвестная ошибка").Trim();
            return value.Length <= 220 ? value : value.Substring(0, 220);
        }
    }

    internal static class ConnectionUi
    {
        public static bool TrySaveCurrent(
            UserControl view,
            out string error
        )
        {
            return SaveCurrent(ConnectionUiContract.Resolve(view), out error);
        }

        public static void Bind(
            UserControl view,
            string bundleRoot,
            bool loadState
        )
        {
            ConnectionUiContract contract = ConnectionUiContract.Resolve(view);

            Action updateMode = delegate
            {
                bool isProxy = contract.IsProxy;
                contract.ProxySettings.IsEnabled = isProxy;
                contract.ProxySettings.Visibility = isProxy
                    ? Visibility.Visible
                    : Visibility.Collapsed;
                if (!isProxy)
                {
                    contract.Status.Text = contract.Vpn.IsChecked == true
                        ? "VPN: прокси не требуется."
                        : "Напрямую: прокси не используется.";
                    contract.Status.Foreground = new SolidColorBrush(
                        Color.FromRgb(22, 122, 88)
                    );
                }
            };
            RoutedEventHandler checkedHandler = delegate
            {
                updateMode();
            };
            foreach (RadioButton route in contract.Routes)
            {
                route.Checked += checkedHandler;
            }

            SelectionChangedEventHandler updateAuth = delegate
            {
                bool enabled = contract.ProxyAuth == null ||
                    SelectedTag(contract.ProxyAuth) == "UsernamePassword";
                contract.ProxyUsername.IsEnabled = enabled;
                contract.ProxyPassword.IsEnabled = enabled;
            };
            if (contract.ProxyAuth != null)
            {
                contract.ProxyAuth.SelectionChanged += updateAuth;
            }

            if (loadState)
            {
                try
                {
                    ApplyProfile(
                        ConnectionStore.Load(UserHome()).profile,
                        contract
                    );
                }
                catch (Exception exception)
                {
                    contract.Status.Text = "Сохранённый профиль требует внимания: " +
                        exception.Message;
                    contract.Status.Foreground = new SolidColorBrush(
                        Color.FromRgb(161, 92, 0)
                    );
                }
            }
            updateMode();
            updateAuth(null, null);

            Func<bool> saveCurrent = delegate
            {
                string error;
                return SaveCurrent(contract, out error);
            };
            contract.Save.Click += delegate
            {
                saveCurrent();
            };
            contract.Test.Click += async delegate
            {
                if (!saveCurrent())
                {
                    return;
                }
                contract.Test.IsEnabled = false;
                contract.Save.IsEnabled = false;
                object originalContent = contract.Test.Content;
                contract.Test.Content = "Проверка…";
                contract.Status.Text = "Проверяем доступ к GitHub через выбранный режим…";
                contract.Status.Foreground = new SolidColorBrush(
                    Color.FromRgb(49, 87, 199)
                );
                try
                {
                    ConnectionProbeResult result = await Task.Run(
                        delegate
                        {
                            return ConnectionProbe.Run(
                                UserHome(),
                                "https://api.github.com/meta"
                            );
                        }
                    );
                    if (result.status == "READY")
                    {
                        contract.Status.Text = "Соединение проверено: " +
                            result.mode +
                            (result.uses_proxy
                                ? " / " + result.proxy_type
                                : "") +
                            " · " + result.elapsed_ms.ToString(
                                CultureInfo.InvariantCulture
                            ) + " мс.";
                        contract.Status.Foreground = new SolidColorBrush(
                            Color.FromRgb(22, 122, 88)
                        );
                    }
                    else
                    {
                        contract.Status.Text =
                            "Соединение не прошло проверку (" +
                            result.error + "). Offline-установка доступна.";
                        contract.Status.Foreground = new SolidColorBrush(
                            Color.FromRgb(161, 92, 0)
                        );
                    }
                }
                catch (Exception exception)
                {
                    contract.Status.Text = "Проверка не выполнена: " +
                        exception.Message;
                    contract.Status.Foreground = new SolidColorBrush(
                        Color.FromRgb(161, 92, 0)
                    );
                }
                finally
                {
                    contract.Test.Content = originalContent;
                    contract.Test.IsEnabled = true;
                    contract.Save.IsEnabled = true;
                }
            };
            if (contract.Stop != null)
            {
                contract.Stop.IsEnabled = false;
            }
        }

        internal static bool ApplyRoute(UserControl view, string route)
        {
            ConnectionUiContract contract = ConnectionUiContract.Resolve(view);
            if (route == "Direct")
            {
                contract.Direct.IsChecked = true;
                return true;
            }
            if (route == "VPN")
            {
                contract.Vpn.IsChecked = true;
                return true;
            }
            if (route == "SingBoxHttp")
            {
                if (contract.Http != null)
                {
                    contract.Http.IsChecked = true;
                }
                else
                {
                    contract.Proxy.IsChecked = true;
                    SelectTag(contract.ProxyType, "HTTP");
                }
                return true;
            }
            if (route == "SingBoxHttps")
            {
                if (contract.Https != null)
                {
                    contract.Https.IsChecked = true;
                }
                else
                {
                    contract.Proxy.IsChecked = true;
                    SelectTag(contract.ProxyType, "HTTPS");
                }
                return true;
            }
            return false;
        }

        internal static Dictionary<string, object> DescribeState(
            UserControl view
        )
        {
            ConnectionUiContract contract = ConnectionUiContract.Resolve(view);
            bool proxy = contract.IsProxy;
            return new Dictionary<string, object>
            {
                { "mode", contract.Mode },
                { "proxy_type", proxy ? contract.SelectedProxyType() : null },
                {
                    "proxy_settings",
                    contract.ProxySettings.Visibility == Visibility.Visible
                        ? "Visible"
                        : "Collapsed"
                },
                {
                    "fields",
                    new List<string>
                    {
                        "server", "port", "login", "password"
                    }
                },
                { "save_enabled", contract.Save.IsEnabled },
                { "test_enabled", contract.Test.IsEnabled },
                {
                    "stop_enabled",
                    contract.Stop != null && contract.Stop.IsEnabled
                }
            };
        }

        private static bool SaveCurrent(
            ConnectionUiContract contract,
            out string error
        )
        {
            error = null;
            try
            {
                ConnectionProfile profile = BuildProfile(contract);
                using (System.Security.SecureString secure =
                    contract.ProxyPassword.SecurePassword.Copy())
                {
                    secure.MakeReadOnly();
                    ConnectionStateResult result = ConnectionStore.Save(
                        UserHome(),
                        profile,
                        secure.Length > 0 ? secure : null
                    );
                    contract.ProxyPassword.Clear();
                    contract.Status.Text = result.profile.mode == "VPN"
                        ? "VPN сохранён: отсутствие прокси не является ошибкой."
                        : (result.profile.mode == "Direct"
                            ? "Прямое подключение сохранено: прокси отключён."
                            : "Прокси сохранён; пароль защищён Windows DPAPI.");
                    contract.Status.Foreground = new SolidColorBrush(
                        Color.FromRgb(22, 122, 88)
                    );
                }
                return true;
            }
            catch (Exception exception)
            {
                error = exception.Message;
                contract.Status.Text = "Не сохранено: " + error;
                contract.Status.Foreground = new SolidColorBrush(
                    Color.FromRgb(161, 92, 0)
                );
                return false;
            }
        }

        private static ConnectionProfile BuildProfile(
            ConnectionUiContract contract
        )
        {
            if (!contract.IsProxy)
            {
                return new ConnectionProfile
                {
                    schema_version = 1,
                    mode = contract.Mode,
                    proxy = null
                };
            }
            int portValue;
            if (!Int32.TryParse(contract.ProxyPort.Text, out portValue))
            {
                throw new ArgumentException("Порт должен быть числом.");
            }
            string authMode = contract.ProxyAuth == null
                ? (String.IsNullOrWhiteSpace(contract.ProxyUsername.Text)
                    ? "None"
                    : "UsernamePassword")
                : SelectedTag(contract.ProxyAuth);
            return ConnectionStore.Validate(new ConnectionProfile
            {
                schema_version = 1,
                mode = "Proxy",
                proxy = new ProxyProfile
                {
                    type = contract.SelectedProxyType(),
                    host = contract.ProxyHost.Text.Trim(),
                    port = portValue,
                    auth = new ConnectionAuth
                    {
                        mode = authMode,
                        username = authMode == "UsernamePassword"
                            ? contract.ProxyUsername.Text.Trim()
                            : null
                    }
                }
            });
        }

        private static void ApplyProfile(
            ConnectionProfile profile,
            ConnectionUiContract contract
        )
        {
            if (profile.mode == "Direct")
            {
                contract.Direct.IsChecked = true;
                return;
            }
            if (profile.mode == "VPN")
            {
                contract.Vpn.IsChecked = true;
                return;
            }
            ApplyRoute(
                contract.View,
                profile.proxy.type == "HTTPS" ? "SingBoxHttps" : "SingBoxHttp"
            );
            contract.ProxyHost.Text = profile.proxy.host;
            contract.ProxyPort.Text = profile.proxy.port.ToString(
                CultureInfo.InvariantCulture
            );
            if (contract.ProxyAuth != null)
            {
                SelectTag(contract.ProxyAuth, profile.proxy.auth.mode);
            }
            contract.ProxyUsername.Text = profile.proxy.auth.username ?? "";
        }

        internal static string SelectedTag(ComboBox combo)
        {
            ComboBoxItem item = combo == null
                ? null
                : combo.SelectedItem as ComboBoxItem;
            return item == null || item.Tag == null
                ? ""
                : Convert.ToString(item.Tag, CultureInfo.InvariantCulture);
        }

        internal static void SelectTag(ComboBox combo, string value)
        {
            if (combo == null)
            {
                return;
            }
            foreach (object candidate in combo.Items)
            {
                ComboBoxItem item = candidate as ComboBoxItem;
                if (item != null && String.Equals(
                        Convert.ToString(item.Tag, CultureInfo.InvariantCulture),
                        value,
                        StringComparison.Ordinal
                    ))
                {
                    combo.SelectedItem = item;
                    return;
                }
            }
        }

        private static string UserHome()
        {
            return Environment.GetFolderPath(
                Environment.SpecialFolder.UserProfile
            );
        }
    }

    internal sealed class ConnectionUiContract
    {
        public UserControl View { get; private set; }
        public RadioButton Direct { get; private set; }
        public RadioButton Vpn { get; private set; }
        public RadioButton Proxy { get; private set; }
        public RadioButton Http { get; private set; }
        public RadioButton Https { get; private set; }
        public Grid ProxySettings { get; private set; }
        public ComboBox ProxyType { get; private set; }
        public TextBox ProxyHost { get; private set; }
        public TextBox ProxyPort { get; private set; }
        public ComboBox ProxyAuth { get; private set; }
        public TextBox ProxyUsername { get; private set; }
        public PasswordBox ProxyPassword { get; private set; }
        public Button Save { get; private set; }
        public Button Test { get; private set; }
        public Button Stop { get; private set; }
        public TextBlock Status { get; private set; }

        public IEnumerable<RadioButton> Routes
        {
            get
            {
                return new[] { Direct, Vpn, Proxy, Http, Https }
                    .Where(route => route != null);
            }
        }

        public bool IsProxy
        {
            get
            {
                return (Proxy != null && Proxy.IsChecked == true) ||
                    (Http != null && Http.IsChecked == true) ||
                    (Https != null && Https.IsChecked == true);
            }
        }

        public string Mode
        {
            get
            {
                return IsProxy
                    ? "Proxy"
                    : (Vpn.IsChecked == true ? "VPN" : "Direct");
            }
        }

        public string SelectedProxyType()
        {
            if (Https != null && Https.IsChecked == true)
            {
                return "HTTPS";
            }
            if (Http != null && Http.IsChecked == true)
            {
                return "HTTP";
            }
            return ConnectionUi.SelectedTag(ProxyType);
        }

        public static ConnectionUiContract Resolve(UserControl view)
        {
            ConnectionUiContract contract = new ConnectionUiContract();
            contract.View = view;
            contract.Direct = Required<RadioButton>(view, "DirectMode", "RouteDirect");
            contract.Vpn = Required<RadioButton>(view, "VpnMode", "RouteVpn");
            contract.Proxy = Optional<RadioButton>(view, "ProxyMode");
            contract.Http = Optional<RadioButton>(view, "RouteHttp");
            contract.Https = Optional<RadioButton>(view, "RouteHttps");
            if (contract.Proxy == null &&
                (contract.Http == null || contract.Https == null))
            {
                throw new InvalidOperationException(
                    "Не найдены элементы маршрута прокси"
                );
            }
            contract.ProxySettings = Required<Grid>(view, "ProxySettings");
            contract.ProxyType = Optional<ComboBox>(view, "ProxyType");
            contract.ProxyHost = Required<TextBox>(view, "ProxyHost");
            contract.ProxyPort = Required<TextBox>(view, "ProxyPort");
            contract.ProxyAuth = Optional<ComboBox>(view, "ProxyAuth");
            contract.ProxyUsername = Required<TextBox>(view, "ProxyUsername");
            contract.ProxyPassword = Required<PasswordBox>(view, "ProxyPassword");
            contract.Save = Required<Button>(view, "SaveConnection");
            contract.Test = Required<Button>(view, "TestConnection");
            contract.Stop = Optional<Button>(view, "StopRoute");
            contract.Status = Required<TextBlock>(view, "ConnectionStatus");
            return contract;
        }

        private static T Required<T>(UserControl view, params string[] names)
            where T : class
        {
            T control = Optional<T>(view, names);
            if (control == null)
            {
                throw new InvalidOperationException(
                    "Не найден элемент подключения: " + String.Join("/", names)
                );
            }
            return control;
        }

        private static T Optional<T>(UserControl view, params string[] names)
            where T : class
        {
            foreach (string name in names)
            {
                T control = view.FindName(name) as T;
                if (control != null)
                {
                    return control;
                }
            }
            return null;
        }
    }

    internal static class ConnectionProbe
    {
        public static ConnectionProbeResult Run(
            string home,
            string endpointValue
        )
        {
            Uri endpoint;
            if (!Uri.TryCreate(
                    endpointValue,
                    UriKind.Absolute,
                    out endpoint
                ) ||
                (endpoint.Scheme != Uri.UriSchemeHttp &&
                 endpoint.Scheme != Uri.UriSchemeHttps) ||
                !IsApprovedEndpoint(endpoint))
            {
                throw new ArgumentException(
                    "Connection probe endpoint is not approved"
                );
            }
            string curl = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.System),
                "curl.exe"
            );
            if (!File.Exists(curl))
            {
                throw new InvalidOperationException(
                    "Windows curl.exe is required for the connection probe"
                );
            }
            ProcessStartInfo start = new ProcessStartInfo
            {
                FileName = curl,
                Arguments =
                    "--fail --location --silent --show-error " +
                    "--max-time 15 --output NUL " +
                    QuoteArgument(endpoint.AbsoluteUri),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            ConnectionProcessState connection =
                ConnectionStore.ConfigureProcessEnvironment(home, start);
            Stopwatch clock = Stopwatch.StartNew();
            using (Process process = Process.Start(start))
            {
                if (process == null)
                {
                    throw new InvalidOperationException(
                        "Connection probe could not start"
                    );
                }
                if (!process.WaitForExit(20000))
                {
                    try
                    {
                        process.Kill();
                    }
                    catch
                    {
                    }
                    clock.Stop();
                    return Result(
                        "BLOCKED",
                        connection,
                        endpoint.Host,
                        clock,
                        "timeout"
                    );
                }
                clock.Stop();
                if (process.ExitCode != 0)
                {
                    return Result(
                        "BLOCKED",
                        connection,
                        endpoint.Host,
                        clock,
                        "curl-exit-" + process.ExitCode.ToString(
                            CultureInfo.InvariantCulture
                        )
                    );
                }
            }
            return Result(
                "READY",
                connection,
                endpoint.Host,
                clock,
                null
            );
        }

        private static ConnectionProbeResult Result(
            string status,
            ConnectionProcessState connection,
            string endpointHost,
            Stopwatch clock,
            string error
        )
        {
            return new ConnectionProbeResult
            {
                status = status,
                mode = connection.mode,
                uses_proxy = connection.uses_proxy,
                proxy_type = connection.proxy_type,
                endpoint_host = endpointHost,
                elapsed_ms = (int)Math.Min(
                    Int32.MaxValue,
                    clock.ElapsedMilliseconds
                ),
                error = error
            };
        }

        private static bool IsApprovedEndpoint(Uri endpoint)
        {
            if (String.Equals(
                    endpoint.Host,
                    "api.github.com",
                    StringComparison.OrdinalIgnoreCase
                ))
            {
                return endpoint.Scheme == Uri.UriSchemeHttps;
            }
            return String.Equals(
                       endpoint.Host,
                       "localhost",
                       StringComparison.OrdinalIgnoreCase
                   ) ||
                   endpoint.Host == "127.0.0.1" ||
                   endpoint.Host == "::1";
        }

        private static string QuoteArgument(string value)
        {
            return "\"" + value.Replace("\\", "\\\\")
                .Replace("\"", "\\\"") + "\"";
        }
    }

    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            AppContext.SetSwitch(
                "Switch.System.IO.UseLegacyPathHandling",
                false
            );
            AppContext.SetSwitch(
                "Switch.System.IO.BlockLongPaths",
                false
            );
            try
            {
                string bundleRoot = AppDomain.CurrentDomain.BaseDirectory;
                EditionProfile edition = EditionProfile.LoadEmbedded();
                if (args.Length == 1 &&
                    args[0] == "--describe-edition")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        edition
                    ));
                    return 0;
                }
                if (args.Length == 1 && args[0] == "--product-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        LaunchTargetCatalog.Describe(
                            edition,
                            bundleRoot
                        )
                    ));
                    return 0;
                }
                if (args.Length == 3 &&
                    args[0] == "--resolve-launch-target-json")
                {
                    LaunchTargetResolution resolution =
                        LaunchTargetResolver.Resolve(
                            edition,
                            bundleRoot,
                            args[1],
                            args[2]
                        );
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        resolution
                    ));
                    return resolution.status == "RESOLVED" ? 0 : 20;
                }
                if (args.Length == 3 &&
                    args[0] ==
                        "--resolve-store-launch-target-record-json")
                {
                    LaunchTargetResolution resolution =
                        LaunchTargetResolver.ResolveStoreRecord(
                            edition,
                            bundleRoot,
                            args[1],
                            args[2]
                        );
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        resolution
                    ));
                    return resolution.status == "RESOLVED" ? 0 : 20;
                }
                if (args.Length == 4 &&
                    args[0] == "--launch-target-json")
                {
                    LaunchTargetResolution resolution =
                        LaunchTargetResolver.Resolve(
                            edition,
                            bundleRoot,
                            args[1],
                            args[2]
                        );
                    LauncherSessionResult launched =
                        ClientLauncher.StartAndWait(
                            resolution,
                            args[3],
                            bundleRoot,
                            args[1]
                        );
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        launched
                    ));
                    return launched.status == "PASS" ? 0 : 20;
                }
                if (args.Length == 2 &&
                    args[0] == "--resolve-sibling-json")
                {
                    SiblingProductResolution sibling =
                        ProductHandoff.Resolve(edition, args[1]);
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        sibling
                    ));
                    return sibling.status == "RESOLVED" ? 0 : 20;
                }
                if (args.Length == 3 &&
                    args[0] == "--install-runtime-json")
                {
                    RuntimeBootstrapResult installed =
                        RuntimeBootstrap.InstallFromArchive(
                            bundleRoot,
                            args[1],
                            args[2]
                        );
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        installed
                    ));
                    return installed.status == "INSTALLED" ||
                        installed.status == "VERIFIED"
                        ? 0
                        : 20;
                }
                if (args.Length == 2 &&
                    args[0] == "--ensure-runtime-json")
                {
                    RuntimeBootstrapResult runtime =
                        RuntimeBootstrap.EnsureInstalled(
                            bundleRoot,
                            args[1]
                        );
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        runtime
                    ));
                    return runtime.status == "VERIFIED" ? 0 : 20;
                }
                if (args.Length == 2 &&
                    args[0] == "--verify-runtime-json")
                {
                    RuntimeBootstrapResult runtime =
                        RuntimeBootstrap.Verify(
                            bundleRoot,
                            args[1]
                        );
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        runtime
                    ));
                    return runtime.status == "VERIFIED" ? 0 : 20;
                }
                if (args.Length == 6 &&
                    args[0] == "--write-singbox-config-test-json")
                {
                    int listenPort;
                    if (!Int32.TryParse(args[4], out listenPort))
                    {
                        WriteError("Listen port is invalid");
                        return 2;
                    }
                    SingBoxConfigSummary config =
                        SingBoxConfig.WriteTestConfig(
                            bundleRoot,
                            args[1],
                            args[2],
                            args[3],
                            listenPort,
                            args[5]
                        );
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        config
                    ));
                    return 0;
                }
                if (args.Length == 4 &&
                    args[0] == "--test-singbox-session-json")
                {
                    SingBoxSessionResult session =
                        SingBoxSession.TestCycle(
                            bundleRoot,
                            args[1],
                            args[2],
                            args[3]
                        );
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        session
                    ));
                    return session.status == "PASS" ? 0 : 20;
                }
                if (args.Length == 1 && args[0] == "--self-test-json")
                {
                    return RunSelfTest(bundleRoot);
                }
                if (args.Length == 1 && args[0] == "--catalog-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ProductCatalog.Inspect(bundleRoot)
                    ));
                    return 0;
                }
                if (args.Length == 1 && args[0] == "--preflight-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ProductCatalog.Inspect(bundleRoot, true)
                    ));
                    return 0;
                }
                if (args.Length == 2 &&
                    args[0] == "--preflight-store-record-json")
                {
                    try
                    {
                        StoreClientResult store =
                            ClientBootstrap.ValidateStoreRecord(
                                bundleRoot,
                                "codex-desktop",
                                args[1]
                            );
                        WriteOutput(new JavaScriptSerializer().Serialize(
                            ProductCatalog.Inspect(bundleRoot, true, store)
                        ));
                        return 0;
                    }
                    catch (InvalidOperationException exception)
                    {
                        WriteError(exception.Message);
                        return 2;
                    }
                }
                if (args.Length == 1 && args[0] == "--platform-json")
                {
                    PlatformCompatibilityResult platform =
                        PlatformCompatibility.Inspect();
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        platform
                    ));
                    return platform.status == "READY" ? 0 : 20;
                }
                if (args.Length == 4 &&
                    args[0] == "--evaluate-platform-json")
                {
                    int build;
                    if (!Int32.TryParse(
                            args[3],
                            NumberStyles.None,
                            CultureInfo.InvariantCulture,
                            out build) ||
                        build < 0)
                    {
                        WriteError("Windows build is invalid");
                        return 2;
                    }
                    PlatformCompatibilityResult platform =
                        PlatformCompatibility.Evaluate(
                            args[1],
                            args[2],
                            build
                        );
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        platform
                    ));
                    return platform.status == "READY" ? 0 : 20;
                }
                if (args.Length == 1 &&
                    args[0] == "--client-sources-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ClientBootstrap.Describe(bundleRoot)
                    ));
                    return 0;
                }
                if (args.Length == 3 &&
                    args[0] == "--validate-store-record-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ClientBootstrap.ValidateStoreRecord(
                            bundleRoot,
                            args[1],
                            args[2]
                        )
                    ));
                    return 0;
                }
                if (args.Length == 2 &&
                    args[0] == "--store-client-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ClientBootstrap.ProbeStore(
                            bundleRoot,
                            args[1]
                        )
                    ));
                    return 0;
                }
                if (args.Length == 2 &&
                    args[0] == "--open-store-client-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ClientBootstrap.OpenStoreSource(
                            bundleRoot,
                            args[1]
                        )
                    ));
                    return 0;
                }
                if (args.Length == 4 &&
                    args[0] == "--download-client-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ClientBootstrap.Download(
                            bundleRoot,
                            args[1],
                            args[2],
                            args[3]
                        )
                    ));
                    return 0;
                }
                if (args.Length == 3 &&
                    args[0] == "--client-plan-json")
                {
                    ClientPlanResult plan = ClientBootstrap.Plan(
                        bundleRoot,
                        args[1],
                        args[2]
                    );
                    WriteOutput(new JavaScriptSerializer().Serialize(plan));
                    return plan.status == "BLOCKED_NO_DOWNGRADE"
                        ? 20
                        : 0;
                }
                if (args.Length == 4 &&
                    args[0] == "--client-plan-store-record-json")
                {
                    try
                    {
                        StoreClientResult store =
                            ClientBootstrap.ValidateStoreRecord(
                                bundleRoot,
                                args[2],
                                args[3]
                            );
                        ClientPlanResult plan = ClientBootstrap.Plan(
                            bundleRoot,
                            args[1],
                            args[2],
                            store
                        );
                        WriteOutput(new JavaScriptSerializer().Serialize(plan));
                        return plan.status == "BLOCKED_NO_DOWNGRADE"
                            ? 20
                            : 0;
                    }
                    catch (InvalidOperationException exception)
                    {
                        WriteError(exception.Message);
                        return 2;
                    }
                }
                if (args.Length == 3 &&
                    args[0] == "--target-client-plan-json")
                {
                    TargetClientPlanResult plan =
                        ClientBootstrap.PlanTarget(
                            bundleRoot,
                            args[1],
                            args[2]
                        );
                    WriteOutput(new JavaScriptSerializer().Serialize(plan));
                    return plan.status == "BLOCKED" ? 20 : 0;
                }
                if (args.Length == 4 &&
                    args[0] == "--install-client-json")
                {
                    object installed = ClientBootstrap.Install(
                        bundleRoot,
                        args[1],
                        args[2],
                        args[3]
                    );
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        installed
                    ));
                    ClientPlanResult blocked = installed as ClientPlanResult;
                    return blocked != null &&
                        blocked.status == "BLOCKED_NO_DOWNGRADE"
                        ? 20
                        : 0;
                }
                if (args.Length == 2 &&
                    args[0] == "--write-install-report-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        InstallerActions.TryWriteSuccessReport(
                            args[1],
                            new List<TargetRow>()
                        )
                    ));
                    return 0;
                }
                if (args.Length == 5 && args[0] == "--workflow-json")
                {
                    string output;
                    string error;
                    int exitCode = FoundationWorkflow.Run(
                        bundleRoot,
                        args[1],
                        args[2],
                        args[3],
                        args[4],
                        out output,
                        out error
                    );
                    if (!String.IsNullOrWhiteSpace(output))
                    {
                        WriteOutput(output.TrimEnd('\r', '\n'));
                    }
                    if (!String.IsNullOrWhiteSpace(error))
                    {
                        WriteError(error.TrimEnd('\r', '\n'));
                    }
                    return exitCode;
                }
                if (args.Length == 3 &&
                    args[0] == "--save-connection-json")
                {
                    ConnectionProfile profile = ConnectionStore
                        .ParseAndValidate(args[2]);
                    string password = profile.mode == "Proxy" &&
                        profile.proxy.auth.mode == "UsernamePassword"
                        ? Console.In.ReadLine()
                        : null;
                    ConnectionStateResult saved = ConnectionStore.Save(
                        args[1],
                        profile,
                        password
                    );
                    WriteOutput(
                        new JavaScriptSerializer().Serialize(saved)
                    );
                    return 0;
                }
                if (args.Length == 2 && args[0] == "--connection-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ConnectionStore.Load(args[1])
                    ));
                    return 0;
                }
                if (args.Length == 2 &&
                    args[0] == "--connection-environment-json")
                {
                    ProcessStartInfo child = new ProcessStartInfo
                    {
                        FileName = "cmd.exe",
                        UseShellExecute = false
                    };
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ConnectionStore.ConfigureProcessEnvironment(
                            args[1],
                            child
                        )
                    ));
                    return 0;
                }
                if (args.Length == 3 &&
                    args[0] == "--probe-connection-json")
                {
                    ConnectionProbeResult result = ConnectionProbe.Run(
                        args[1],
                        args[2]
                    );
                    WriteOutput(new JavaScriptSerializer().Serialize(result));
                    return result.status == "READY" ? 0 : 20;
                }
                if (args.Length == 2 && args[0] == "--render-preview")
                {
                    Application previewApp = new Application();
                    UserControl preview = InstallerView.Create(
                        bundleRoot,
                        false
                    );
                    InstallerView.RenderPreview(preview, args[1], 1440, 900);
                    previewApp.Shutdown();
                    return 0;
                }
                if (args.Length == 2 &&
                    args[0] == "--render-guide-preview")
                {
                    Application previewApp = new Application();
                    UserControl preview =
                        OperatorGuideDashboard.Create(bundleRoot);
                    InstallerView.RenderPreview(
                        preview,
                        args[1],
                        1440,
                        900
                    );
                    previewApp.Shutdown();
                    return 0;
                }
                if (args.Length == 2 &&
                    args[0] == "--ui-connection-state-json")
                {
                    Application stateApp = new Application();
                    UserControl stateView = InstallerView.Create(
                        bundleRoot,
                        false
                    );
                    if (!ConnectionUi.ApplyRoute(stateView, args[1]))
                    {
                        WriteError("Неподдерживаемый маршрут подключения");
                        stateApp.Shutdown();
                        return 2;
                    }
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ConnectionUi.DescribeState(stateView)
                    ));
                    stateApp.Shutdown();
                    return 0;
                }
                if (args.Length == 2 &&
                    (args[0] == "--ui-selection-json" ||
                     args[0] == "--ui-guide-selection-json"))
                {
                    if (edition.product_role != "LaunchCenter")
                    {
                        WriteError(
                            "Команда выбора доступна только центру запуска"
                        );
                        return 2;
                    }
                    Application selectionApp = new Application();
                    UserControl selectionView = InstallerView.Create(
                        bundleRoot,
                        false
                    );
                    bool selected =
                        args[0] == "--ui-guide-selection-json"
                            ? OperatorGuideDashboard.ApplyHostSelection(
                                selectionView,
                                args[1]
                            )
                            : LaunchCenterActions.SelectTarget(
                                selectionView,
                                args[1]
                            );
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        LaunchCenterActions.DescribeSelection(
                            selectionView
                        )
                    ));
                    selectionApp.Shutdown();
                    return selected ? 0 : 20;
                }
                if (args.Length != 0)
                {
                    WriteError("Неподдерживаемая команда");
                    return 2;
                }

                PlatformCompatibilityResult currentPlatform =
                    PlatformCompatibility.Inspect();
                if (currentPlatform.status != "READY")
                {
                    MessageBox.Show(
                        currentPlatform.reason,
                        "LLM Foundation — неподдерживаемая система",
                        MessageBoxButton.OK,
                        MessageBoxImage.Error
                    );
                    return 20;
                }
                Application application = new Application();
                application.ShutdownMode = ShutdownMode.OnMainWindowClose;
                Window window = new Window
                {
                    Title = EditionTheme.WindowTitle(edition),
                    Width = 1280,
                    Height = 800,
                    MinWidth = 1100,
                    MinHeight = 720,
                    WindowStartupLocation = WindowStartupLocation.CenterScreen,
                    Background = EditionTheme.WindowBackground(edition),
                    Content = InstallerView.Create(bundleRoot)
                };
                application.Run(window);
                return 0;
            }
            catch (Exception exception)
            {
                WriteError(exception.GetType().Name + ": " + exception.Message);
                return 30;
            }
        }

        private static int RunSelfTest(string bundleRoot)
        {
            int protocol;
            bool validated = BundleIntegrity.ValidateEngine(bundleRoot, out protocol);
            bool platformReady =
                PlatformCompatibility.Inspect().status == "READY";
            Dictionary<string, object> payload = new Dictionary<string, object>();
            payload["app_id"] = "llm-foundation-installer";
            payload["engine_validated"] = validated;
            payload["foundation_protocol"] = protocol;
            payload["network"] = "user-initiated-only";
            payload["automatic_network"] = false;
            payload["reverse_flow"] = false;
            payload["targets"] = ProductCatalog.TargetIds();
            payload["telemetry"] = false;
            payload["version"] = BundleIntegrity.ReadBundleVersion(bundleRoot);
            WriteOutput(new JavaScriptSerializer().Serialize(payload));
            return validated && platformReady ? 0 : 30;
        }

        private static void WriteOutput(string value)
        {
            WriteStream(Console.OpenStandardOutput(), value);
        }

        private static void WriteError(string value)
        {
            WriteStream(Console.OpenStandardError(), value);
        }

        private static void WriteStream(Stream stream, string value)
        {
            if (stream == Stream.Null)
            {
                return;
            }
            using (StreamWriter writer = new StreamWriter(
                stream,
                new UTF8Encoding(false),
                4096,
                true
            ))
            {
                writer.WriteLine(value);
                writer.Flush();
            }
        }
    }
}
