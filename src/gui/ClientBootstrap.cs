using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class ClientPlatform
    {
        public string os { get; set; }
        public string architecture { get; set; }
        public int minimum_build { get; set; }
    }

    internal sealed class ClientSource
    {
        public string id { get; set; }
        public string target { get; set; }
        public string display_name { get; set; }
        public string role { get; set; }
        public bool required_for_base { get; set; }
        public bool required_for_employee { get; set; }
        public string version { get; set; }
        public string source_kind { get; set; }
        public string url { get; set; }
        public string sha256 { get; set; }
        public string artifact_kind { get; set; }
        public string archive_entry { get; set; }
        public string publisher { get; set; }
        public bool signature_required { get; set; }
        public string install_mode { get; set; }
        public List<string> detect_commands { get; set; }
        public List<string> version_arguments { get; set; }
        public string store_product_id { get; set; }
        public string store_identity { get; set; }
        public string store_publisher { get; set; }
        public string store_signature_kind { get; set; }
        public string store_application_id { get; set; }
        public string store_executable { get; set; }
        public string store_entry_point { get; set; }
    }

    internal sealed class ClientSourceLock
    {
        public int schema_version { get; set; }
        public bool official_only { get; set; }
        public bool test_only { get; set; }
        public ClientPlatform platform { get; set; }
        public List<ClientSource> clients { get; set; }
    }

    internal sealed class ClientDownloadResult
    {
        public string status { get; set; }
        public string client_id { get; set; }
        public string version { get; set; }
        public string connection_mode { get; set; }
        public bool uses_proxy { get; set; }
        public string sha256 { get; set; }
        public long bytes { get; set; }
        public string relative_path { get; set; }
    }

    internal sealed class ClientPlanResult
    {
        public string status { get; set; }
        public string client_id { get; set; }
        public string supported_version { get; set; }
        public string detected_version { get; set; }
        public string detected_state { get; set; }
        public string action { get; set; }
    }

    internal sealed class TargetClientPlanResult
    {
        public string status { get; set; }
        public string target { get; set; }
        public List<ClientPlanResult> clients { get; set; }
    }

    internal sealed class ClientInstallResult
    {
        public string status { get; set; }
        public string client_id { get; set; }
        public string version { get; set; }
        public string relative_install_path { get; set; }
        public bool path_persisted { get; set; }
        public bool authentication_touched { get; set; }
    }

    internal sealed class ManagedDesktopRecord
    {
        public int schema_version { get; set; }
        public string client_id { get; set; }
        public string version { get; set; }
        public string relative_path { get; set; }
        public string sha256 { get; set; }
    }

    internal sealed class ManagedCommandRecord
    {
        public int schema_version { get; set; }
        public string client_id { get; set; }
        public string version { get; set; }
        public string relative_path { get; set; }
        public string sha256 { get; set; }
        public string source_sha256 { get; set; }
    }

    internal sealed class SignatureProbe
    {
        public string status { get; set; }
        public string subject { get; set; }
        public string simple_name { get; set; }
    }

    internal sealed class StorePackageProbe
    {
        public bool present { get; set; }
        public string name { get; set; }
        public string publisher { get; set; }
        public string signature_kind { get; set; }
        public string architecture { get; set; }
        public string version { get; set; }
        public string package_full_name { get; set; }
        public string package_family_name { get; set; }
        public string install_location { get; set; }
        public string application_id { get; set; }
        public string executable { get; set; }
        public string entry_point { get; set; }
    }

    internal sealed class StoreClientResult
    {
        public string status { get; set; }
        public string client_id { get; set; }
        public string version { get; set; }
        public string package_full_name { get; set; }
        public string package_family_name { get; set; }
        public string install_location { get; set; }
        public string application_id { get; set; }
        public string executable { get; set; }
        public string store_product_id { get; set; }
        public string source_uri { get; set; }
    }

    internal static class ClientBootstrap
    {
        private const string ResourceName = "ClientSources.lock.json";
        private static readonly HashSet<string> OfficialHosts =
            new HashSet<string>(
                new[]
                {
                    "chatgpt.com",
                    "downloads.claude.ai",
                    "github.com",
                    "openai.com",
                    "apps.microsoft.com"
                },
                StringComparer.OrdinalIgnoreCase
            );
        private static readonly Regex VersionPattern = new Regex(
            @"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)",
            RegexOptions.CultureInvariant
        );

        public static ClientSourceLock Load(string bundleRoot)
        {
            byte[] embedded = ReadResource(ResourceName);
            string external = Path.Combine(
                Path.GetFullPath(bundleRoot),
                "client-sources.lock.json"
            );
            if (File.Exists(external))
            {
                byte[] candidate = File.ReadAllBytes(external);
                if (!FixedTimeEquals(
                        ComputeSha256(candidate),
                        ComputeSha256(embedded)))
                {
                    throw new InvalidOperationException(
                        "Client source lock differs from the embedded resource"
                    );
                }
            }
            ClientSourceLock value = new JavaScriptSerializer()
                .Deserialize<ClientSourceLock>(
                    new UTF8Encoding(false, true).GetString(embedded)
                );
            Validate(value);
            return value;
        }

        public static Dictionary<string, object> Describe(string bundleRoot)
        {
            ClientSourceLock value = Load(bundleRoot);
            return new Dictionary<string, object>
            {
                { "status", "READY" },
                { "schema_version", value.schema_version },
                { "platform", value.platform },
                { "official_only", value.official_only },
                { "test_only", value.test_only },
                { "clients", value.clients }
            };
        }

        public static StoreClientResult ValidateStoreRecord(
            string bundleRoot,
            string clientId,
            string recordPath
        )
        {
            ClientSource source = FindSource(bundleRoot, clientId);
            AssertStoreSource(source);
            string full = Path.GetFullPath(recordPath);
            if (!File.Exists(full) ||
                new FileInfo(full).Length < 2 ||
                new FileInfo(full).Length > 65536 ||
                (File.GetAttributes(full) & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException(
                    "Store package record is invalid"
                );
            }
            StorePackageProbe probe;
            try
            {
                probe = new JavaScriptSerializer()
                    .Deserialize<StorePackageProbe>(
                        File.ReadAllText(
                            full,
                            new UTF8Encoding(false, true)
                        )
                    );
            }
            catch (Exception exception)
            {
                throw new InvalidOperationException(
                    "Store package record is invalid",
                    exception
                );
            }
            return ValidateStoreProbe(source, probe);
        }

        public static StoreClientResult ProbeStore(
            string bundleRoot,
            string clientId
        )
        {
            ClientSource source = FindSource(bundleRoot, clientId);
            AssertStoreSource(source);
            const string command =
                "$ErrorActionPreference='Stop';" +
                "Import-Module Appx -ErrorAction Stop;" +
                "$items=@(Get-AppxPackage -Name " +
                "$env:LLM_STORE_IDENTITY -ErrorAction SilentlyContinue|" +
                "Where-Object {$_.Name -ceq " +
                "$env:LLM_STORE_IDENTITY});" +
                "if($items.Count -eq 0){" +
                "[pscustomobject]@{present=$false}|" +
                "ConvertTo-Json -Compress;exit 0};" +
                "if($items.Count -ne 1){exit 44};" +
                "$p=$items[0];" +
                "$m=Get-AppxPackageManifest -Package $p;" +
                "$apps=@($m.Package.Applications.Application);" +
                "if($apps.Count -ne 1){exit 45};" +
                "$a=$apps[0];" +
                "[pscustomobject]@{present=$true;" +
                "name=[string]$p.Name;" +
                "publisher=[string]$p.Publisher;" +
                "signature_kind=[string]$p.SignatureKind;" +
                "architecture=[string]$p.Architecture;" +
                "version=[string]$p.Version;" +
                "package_full_name=[string]$p.PackageFullName;" +
                "package_family_name=[string]$p.PackageFamilyName;" +
                "install_location=[string]$p.InstallLocation;" +
                "application_id=[string]$a.Id;" +
                "executable=[string]$a.Executable;" +
                "entry_point=[string]$a.EntryPoint}|" +
                "ConvertTo-Json -Compress;";
            ProcessStartInfo start = new ProcessStartInfo
            {
                FileName = WindowsPowerShellPath(),
                Arguments =
                    NonInteractiveCommandPrelude +
                    QuoteArgument(command),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            start.EnvironmentVariables["LLM_STORE_IDENTITY"] =
                source.store_identity;
            AddWindowsPowerShellModulePath(start);
            string output;
            using (Process process = Process.Start(start))
            {
                if (process == null)
                {
                    throw new InvalidOperationException(
                        "Microsoft Store package probe could not start"
                    );
                }
                output = process.StandardOutput.ReadToEnd();
                process.StandardError.ReadToEnd();
                if (!process.WaitForExit(30000) ||
                    process.ExitCode != 0)
                {
                    throw new InvalidOperationException(
                        "Microsoft Store package probe failed"
                    );
                }
            }
            StorePackageProbe probe = new JavaScriptSerializer()
                .Deserialize<StorePackageProbe>(output);
            return ValidateStoreProbe(source, probe);
        }

        public static StoreClientResult OpenStoreSource(
            string bundleRoot,
            string clientId
        )
        {
            ClientSource source = FindSource(bundleRoot, clientId);
            AssertStoreSource(source);
            string uri = StoreSourceUri(source);
            Process.Start(new ProcessStartInfo
            {
                FileName = uri,
                UseShellExecute = true
            });
            return new StoreClientResult
            {
                status = "STORE_OPENED",
                client_id = source.id,
                version = null,
                package_full_name = null,
                package_family_name = null,
                install_location = null,
                application_id = null,
                executable = null,
                store_product_id = source.store_product_id,
                source_uri = uri
            };
        }

        public static ClientDownloadResult Download(
            string bundleRoot,
            string home,
            string clientId,
            string stagingRoot
        )
        {
            ClientSourceLock catalog = Load(bundleRoot);
            ClientSource source = catalog.clients.FirstOrDefault(entry =>
                String.Equals(
                    entry.id,
                    clientId,
                    StringComparison.Ordinal
                )
            );
            if (source == null ||
                !String.Equals(
                    source.source_kind,
                    "download",
                    StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "Requested client is not a downloadable source"
                );
            }
            if (!IsSafeSegment(source.id) ||
                !IsSafeSegment(source.version))
            {
                throw new InvalidOperationException(
                    "Client source path segment is unsafe"
                );
            }
            Uri sourceUri = new Uri(source.url, UriKind.Absolute);
            string fileName = Path.GetFileName(sourceUri.AbsolutePath);
            if (!IsSafeSegment(fileName))
            {
                throw new InvalidOperationException(
                    "Client source file name is unsafe"
                );
            }

            string root = Path.GetFullPath(stagingRoot);
            EnsureSafeDirectory(root);
            string clientRoot = Path.Combine(
                root,
                source.id,
                source.version
            );
            EnsureSafeDirectory(clientRoot);
            string finalPath = Path.Combine(clientRoot, fileName);
            string partialPath = Path.Combine(
                clientRoot,
                "." + Path.GetFileNameWithoutExtension(fileName) +
                ".part-" + Guid.NewGuid().ToString("N") +
                Path.GetExtension(fileName)
            );
            string curlOutputPath = ToExtendedLengthPath(partialPath);
            ConnectionProcessState connection = null;
            try
            {
                string curl = Path.Combine(
                    Environment.GetFolderPath(
                        Environment.SpecialFolder.System
                    ),
                    "curl.exe"
                );
                if (!File.Exists(curl))
                {
                    throw new InvalidOperationException(
                        "Windows curl.exe is required for client download"
                    );
                }
                ProcessStartInfo start = new ProcessStartInfo
                {
                    FileName = curl,
                    Arguments =
                        "--fail --location --silent --show-error " +
                        "--max-time 300 --output " +
                        QuoteArgument(curlOutputPath) + " " +
                        QuoteArgument(source.url),
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8
                };
                connection = ConnectionStore.ConfigureProcessEnvironment(
                    home,
                    start
                );
                using (Process process = Process.Start(start))
                {
                    if (process == null)
                    {
                        throw new InvalidOperationException(
                            "Client download process could not start"
                        );
                    }
                    process.StandardOutput.ReadToEnd();
                    process.StandardError.ReadToEnd();
                    if (!process.WaitForExit(310000))
                    {
                        try
                        {
                            process.Kill();
                        }
                        catch
                        {
                        }
                        throw new InvalidOperationException(
                            "Client download timed out"
                        );
                    }
                    if (process.ExitCode != 0)
                    {
                        throw new InvalidOperationException(
                            "Client download failed with curl exit " +
                            process.ExitCode
                        );
                    }
                }
                if (!File.Exists(partialPath) ||
                    new FileInfo(partialPath).Length < 1)
                {
                    throw new InvalidOperationException(
                        "Client download produced an empty file"
                    );
                }
                string actualHash = BundleIntegrity.Sha256(partialPath);
                if (!String.Equals(
                        actualHash,
                        source.sha256,
                        StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException(
                        "Client download hash does not match source lock"
                    );
                }
                if (source.signature_required &&
                    !String.Equals(
                        source.artifact_kind,
                        "zip",
                        StringComparison.Ordinal))
                {
                    VerifyAuthenticode(partialPath, source.publisher);
                }
                if (File.Exists(finalPath))
                {
                    if (!String.Equals(
                            BundleIntegrity.Sha256(finalPath),
                            actualHash,
                            StringComparison.OrdinalIgnoreCase))
                    {
                        throw new InvalidOperationException(
                            "Existing staged client differs from source lock"
                        );
                    }
                    File.Delete(partialPath);
                }
                else
                {
                    File.Move(partialPath, finalPath);
                }
                return new ClientDownloadResult
                {
                    status = "VERIFIED",
                    client_id = source.id,
                    version = source.version,
                    connection_mode = connection.mode,
                    uses_proxy = connection.uses_proxy,
                    sha256 = actualHash,
                    bytes = new FileInfo(finalPath).Length,
                    relative_path = (
                        source.id + "/" + source.version + "/" + fileName
                    )
                };
            }
            finally
            {
                if (File.Exists(partialPath))
                {
                    File.Delete(partialPath);
                }
            }
        }

        public static ClientPlanResult Plan(
            string bundleRoot,
            string home,
            string clientId
        )
        {
            return Plan(bundleRoot, home, clientId, null);
        }

        internal static ClientPlanResult Plan(
            string bundleRoot,
            string home,
            string clientId,
            StoreClientResult storeRecord
        )
        {
            ClientSource source = FindSource(bundleRoot, clientId);
            if (source.source_kind == "store")
            {
                StoreClientResult store = storeRecord ?? ProbeStore(
                    bundleRoot,
                    clientId
                );
                if (store.status == "READY")
                {
                    return new ClientPlanResult
                    {
                        status = "READY",
                        client_id = source.id,
                        supported_version = source.version,
                        detected_version = store.version,
                        detected_state = "exact_identity",
                        action = "none"
                    };
                }
                return new ClientPlanResult
                {
                    status = "GUIDED_STORE",
                    client_id = source.id,
                    supported_version = source.version,
                    detected_version = null,
                    detected_state = "not_checked",
                    action = "open_store"
                };
            }
            string detected = String.Equals(
                    source.install_mode,
                    "managed-desktop",
                    StringComparison.Ordinal)
                ? DetectManagedDesktopVersion(home, source)
                : (String.Equals(
                        source.install_mode,
                        "official-installer",
                        StringComparison.Ordinal)
                    ? DetectOfficialDesktopVersion(
                        home,
                        source,
                        Load(bundleRoot).test_only
                    )
                    : DetectVersion(home, source));
            if (String.IsNullOrWhiteSpace(detected))
            {
                return new ClientPlanResult
                {
                    status = "INSTALL_AVAILABLE",
                    client_id = source.id,
                    supported_version = source.version,
                    detected_version = null,
                    detected_state = "missing",
                    action = "install"
                };
            }
            if (String.Equals(
                    detected,
                    source.version,
                    StringComparison.Ordinal))
            {
                if (UsesManagedCommand(source) &&
                    !HasValidManagedCommandRecord(home, source))
                {
                    return new ClientPlanResult
                    {
                        status = "INSTALL_AVAILABLE",
                        client_id = source.id,
                        supported_version = source.version,
                        detected_version = detected,
                        detected_state = "exact_unrecorded",
                        action = "reinstall"
                    };
                }
                return new ClientPlanResult
                {
                    status = "READY",
                    client_id = source.id,
                    supported_version = source.version,
                    detected_version = detected,
                    detected_state = "exact",
                    action = "none"
                };
            }
            int comparison;
            if (TryCompareCoreVersions(
                    detected,
                    source.version,
                    out comparison) &&
                comparison < 0)
            {
                return new ClientPlanResult
                {
                    status = "INSTALL_AVAILABLE",
                    client_id = source.id,
                    supported_version = source.version,
                    detected_version = detected,
                    detected_state = "older",
                    action = "install"
                };
            }
            if (comparison > 0 && String.Equals(
                    source.target,
                    "codex",
                    StringComparison.Ordinal))
            {
                return new ClientPlanResult
                {
                    status = "READY",
                    client_id = source.id,
                    supported_version = source.version,
                    detected_version = detected,
                    detected_state = "newer",
                    action = "none"
                };
            }
            return new ClientPlanResult
            {
                status = "BLOCKED_NO_DOWNGRADE",
                client_id = source.id,
                supported_version = source.version,
                detected_version = detected,
                detected_state = comparison > 0 ? "newer" : "different",
                action = "none"
            };
        }

        public static List<ClientSource> RequiredSourcesForTarget(
            string bundleRoot,
            string target
        )
        {
            if (target != "codex" &&
                target != "claude" &&
                target != "opencode")
            {
                throw new InvalidOperationException(
                    "Client target is not supported"
                );
            }
            List<ClientSource> sources = Load(bundleRoot).clients
                .Where(source =>
                    source.required_for_employee &&
                    String.Equals(
                        source.target,
                        target,
                        StringComparison.Ordinal))
                .OrderBy(source =>
                    source.role == "cli" ? 0 : 1)
                .ThenBy(source => source.id, StringComparer.Ordinal)
                .ToList();
            if (sources.Count == 0)
            {
                throw new InvalidOperationException(
                    "Required client sources are missing for target"
                );
            }
            return sources;
        }

        public static TargetClientPlanResult PlanTarget(
            string bundleRoot,
            string home,
            string target
        )
        {
            List<ClientPlanResult> clients =
                RequiredSourcesForTarget(bundleRoot, target)
                    .Select(source => Plan(
                        bundleRoot,
                        home,
                        source.id
                    ))
                    .ToList();
            string status = clients.Any(plan =>
                    plan.status == "BLOCKED_NO_DOWNGRADE")
                ? "BLOCKED"
                : (clients.Any(plan =>
                        plan.status == "GUIDED_STORE")
                    ? "GUIDED_STORE"
                    : (clients.Any(plan =>
                            plan.status == "INSTALL_AVAILABLE")
                        ? "INSTALL_AVAILABLE"
                        : "READY"));
            return new TargetClientPlanResult
            {
                status = status,
                target = target,
                clients = clients
            };
        }

        public static object Install(
            string bundleRoot,
            string home,
            string clientId,
            string stagingRoot
        )
        {
            ClientSource source = FindSource(bundleRoot, clientId);
            ClientPlanResult plan = Plan(bundleRoot, home, clientId);
            if (plan.status == "BLOCKED_NO_DOWNGRADE")
            {
                return plan;
            }
            if (plan.status == "READY")
            {
                return new ClientInstallResult
                {
                    status = "ALREADY_READY",
                    client_id = source.id,
                    version = source.version,
                    relative_install_path = ManagedRelativePath(source),
                    path_persisted = IsManagedPathPersisted(home, source),
                    authentication_touched = false
                };
            }
            if (source.source_kind == "store")
            {
                return plan;
            }

            ClientDownloadResult downloaded = Download(
                bundleRoot,
                home,
                clientId,
                stagingRoot
            );
            string stagedPath = Path.Combine(
                Path.GetFullPath(stagingRoot),
                downloaded.relative_path.Replace(
                    '/',
                    Path.DirectorySeparatorChar
                )
            );
            if (String.Equals(
                    source.install_mode,
                    "official-script",
                    StringComparison.Ordinal))
            {
                return InstallOfficialPowerShellScript(
                    home,
                    source,
                    stagedPath
                );
            }
            if (String.Equals(
                    source.install_mode,
                    "managed-desktop",
                    StringComparison.Ordinal))
            {
                return InstallManagedDesktop(
                    home,
                    source,
                    stagedPath
                );
            }
            if (String.Equals(
                    source.install_mode,
                    "official-installer",
                    StringComparison.Ordinal))
            {
                return InstallOfficialDesktop(
                    home,
                    source,
                    stagedPath,
                    Load(bundleRoot).test_only
                );
            }
            if (!String.Equals(
                    source.install_mode,
                    "managed-bin",
                    StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "Client install mode is not implemented"
                );
            }
            if (source.detect_commands == null ||
                source.detect_commands.Count < 1 ||
                !IsSafeSegment(source.detect_commands[0]))
            {
                throw new InvalidOperationException(
                    "Managed client command is invalid"
                );
            }
            string managedPayload = stagedPath;
            string extractedPayload = null;
            if (String.Equals(
                    source.artifact_kind,
                    "zip",
                    StringComparison.Ordinal))
            {
                extractedPayload = ExtractLockedArchivePayload(
                    stagedPath,
                    source
                );
                managedPayload = extractedPayload;
            }
            string binRoot = Path.Combine(
                Path.GetFullPath(home),
                ".llm-foundation",
                "bin"
            );
            EnsureSafeDirectory(binRoot);
            string commandPath = Path.Combine(
                binRoot,
                source.detect_commands[0]
            );
            byte[] prior = File.Exists(commandPath)
                ? File.ReadAllBytes(commandPath)
                : null;
            string temporary = Path.Combine(
                binRoot,
                "." + Path.GetFileNameWithoutExtension(commandPath) +
                ".install-" + Guid.NewGuid().ToString("N") +
                Path.GetExtension(commandPath)
            );
            try
            {
                File.Copy(managedPayload, temporary, false);
                if (File.Exists(commandPath))
                {
                    File.Replace(temporary, commandPath, null, true);
                }
                else
                {
                    File.Move(temporary, commandPath);
                }
                string installedVersion = DetectVersion(home, source);
                if (!String.Equals(
                        installedVersion,
                        source.version,
                        StringComparison.Ordinal))
                {
                    RestoreManagedFile(commandPath, prior);
                    throw new InvalidOperationException(
                        "Installed client version differs from source lock"
                    );
                }
                WriteManagedCommandRecord(
                    home,
                    source,
                    commandPath
                );
            }
            catch
            {
                if (File.Exists(temporary))
                {
                    File.Delete(temporary);
                }
                RestoreManagedFile(commandPath, prior);
                throw;
            }
            finally
            {
                if (!String.IsNullOrWhiteSpace(extractedPayload) &&
                    File.Exists(extractedPayload))
                {
                    File.Delete(extractedPayload);
                }
            }
            bool pathPersisted = PersistManagedPathForCurrentUser(
                home,
                binRoot
            );
            return new ClientInstallResult
            {
                status = "INSTALLED",
                client_id = source.id,
                version = source.version,
                relative_install_path = ManagedRelativePath(source),
                path_persisted = pathPersisted,
                authentication_touched = false
            };
        }

        private static ClientInstallResult InstallOfficialPowerShellScript(
            string home,
            ClientSource source,
            string scriptPath
        )
        {
            if (!String.Equals(
                    source.artifact_kind,
                    "powershell-installer-script",
                    StringComparison.Ordinal) ||
                !String.Equals(
                    Path.GetExtension(scriptPath),
                    ".ps1",
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Official script source contract is invalid"
                );
            }
            VerifyPowerShellInstallerScript(scriptPath);
            string binRoot = ManagedBinRoot(home, source);
            EnsureSafeDirectory(binRoot);
            string clientHome = Path.Combine(
                Path.GetFullPath(home),
                ".llm-foundation",
                "clients",
                source.id,
                "codex-home"
            );
            EnsureSafeDirectory(clientHome);
            string curl = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.System),
                "curl.exe"
            );
            if (!File.Exists(curl))
            {
                throw new InvalidOperationException(
                    "Windows curl.exe is required for official client install"
                );
            }
            Uri scriptUri = new Uri(source.url, UriKind.Absolute);
            bool allowLoopbackHttp =
                scriptUri.IsLoopback &&
                scriptUri.Scheme == Uri.UriSchemeHttp;
            ProcessStartInfo start = new ProcessStartInfo
            {
                FileName = WindowsPowerShellPath(),
                Arguments =
                    NonInteractiveCommandPrelude +
                    QuoteArgument(BuildOfficialScriptWrapper()),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            start.EnvironmentVariables["CODEX_NON_INTERACTIVE"] = "1";
            start.EnvironmentVariables["CODEX_RELEASE"] = source.version;
            start.EnvironmentVariables["LLM_CLIENT_SCRIPT"] =
                Path.GetFullPath(scriptPath);
            start.EnvironmentVariables["LLM_CURL_PATH"] = curl;
            start.EnvironmentVariables["LLM_CURL_PROTOCOLS"] =
                allowLoopbackHttp ? "=http,https" : "=https";
            start.EnvironmentVariables["CODEX_HOME"] = clientHome;
            start.EnvironmentVariables["CODEX_INSTALL_DIR"] = binRoot;
            start.EnvironmentVariables["USERPROFILE"] =
                Path.GetFullPath(home);
            start.EnvironmentVariables["LOCALAPPDATA"] =
                LocalApplicationDataForHome(home);
            ConnectionStore.ConfigureProcessEnvironment(home, start);
            using (Process process = Process.Start(start))
            {
                if (process == null)
                {
                    throw new InvalidOperationException(
                        "Official client installer could not start"
                    );
                }
                process.StandardOutput.ReadToEnd();
                process.StandardError.ReadToEnd();
                if (!process.WaitForExit(600000))
                {
                    try
                    {
                        process.Kill();
                    }
                    catch
                    {
                    }
                    throw new InvalidOperationException(
                        "Official client installer timed out"
                    );
                }
                if (process.ExitCode != 0)
                {
                    throw new InvalidOperationException(
                        "Official client installer failed with exit " +
                        process.ExitCode
                    );
                }
            }
            string installedVersion = DetectVersion(home, source);
            if (!String.Equals(
                    installedVersion,
                    source.version,
                    StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "Installed client version differs from source lock"
                );
            }
            WriteManagedCommandRecord(
                home,
                source,
                Path.Combine(
                    binRoot,
                    source.detect_commands[0]
                )
            );
            bool pathPersisted = PersistManagedPathForCurrentUser(
                home,
                binRoot
            );
            return new ClientInstallResult
            {
                status = "INSTALLED",
                client_id = source.id,
                version = source.version,
                relative_install_path = ManagedRelativePath(source),
                path_persisted = pathPersisted,
                authentication_touched = false
            };
        }

        private static string BuildOfficialScriptWrapper()
        {
            return
                "$ErrorActionPreference='Stop';" +
                "function Invoke-WebRequest {" +
                "[CmdletBinding()]param(" +
                "[Parameter(Mandatory=$true)][string]$Uri," +
                "[string]$OutFile,[switch]$UseBasicParsing);" +
                "$owned=[string]::IsNullOrWhiteSpace($OutFile);" +
                "$target=if($owned){" +
                "[IO.Path]::Combine([IO.Path]::GetTempPath()," +
                "'llm-curl-'+[guid]::NewGuid().ToString('N')+'.part')" +
                "}else{$OutFile+'.curl-'+[guid]::NewGuid().ToString('N')};" +
                "try{" +
                "& $env:LLM_CURL_PATH --fail --location --silent " +
                "--show-error --max-time 300 " +
                "--proto $env:LLM_CURL_PROTOCOLS " +
                "--proto-redir $env:LLM_CURL_PROTOCOLS " +
                "--output $target -- $Uri;" +
                "if($LASTEXITCODE -ne 0 -or " +
                "-not (Test-Path -LiteralPath $target -PathType Leaf)){" +
                "throw 'Verified curl request failed'};" +
                "if(-not $owned){" +
                "if(Test-Path -LiteralPath $OutFile){" +
                "throw 'Network destination already exists'};" +
                "Move-Item -LiteralPath $target -Destination $OutFile;" +
                "return};" +
                "$content=[IO.File]::ReadAllText($target,[Text.Encoding]::UTF8);" +
                "return [pscustomobject]@{Content=$content}" +
                "}finally{" +
                "if(Test-Path -LiteralPath $target){" +
                "Remove-Item -LiteralPath $target -Force}}" +
                "};" +
                "function Invoke-RestMethod {" +
                "[CmdletBinding()]param(" +
                "[Parameter(Mandatory=$true)][string]$Uri);" +
                "$response=Invoke-WebRequest -UseBasicParsing -Uri $Uri;" +
                "return $response.Content|ConvertFrom-Json -ErrorAction Stop" +
                "};" +
                "& $env:LLM_CLIENT_SCRIPT -Release $env:CODEX_RELEASE;";
        }

        private static void VerifyPowerShellInstallerScript(
            string scriptPath
        )
        {
            const string command =
                "$ErrorActionPreference='Stop';" +
                "$errors=@();$tokens=@();" +
                "$ast=[System.Management.Automation.Language.Parser]" +
                "::ParseFile($env:LLM_CLIENT_SCRIPT," +
                "[ref]$tokens,[ref]$errors);" +
                "if($errors.Count -gt 0){exit 41};" +
                "$release=@($ast.ParamBlock.Parameters|" +
                "Where-Object {$_.Name.VariablePath.UserPath -ceq " +
                "'Release'});" +
                "if($release.Count -ne 1){exit 42};" +
                "$bad=@($ast.FindAll({param($node)" +
                "if($node -isnot " +
                "[System.Management.Automation.Language.CommandAst])" +
                "{return $false};" +
                "$name=$node.GetCommandName();" +
                "return $name -ieq 'Invoke-Expression' -or " +
                "$name -ieq 'iex'},$true));" +
                "if($bad.Count -gt 0){exit 43};";
            ProcessStartInfo start = new ProcessStartInfo
            {
                FileName = WindowsPowerShellPath(),
                Arguments =
                    NonInteractiveCommandPrelude +
                    QuoteArgument(command),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            start.EnvironmentVariables["LLM_CLIENT_SCRIPT"] =
                Path.GetFullPath(scriptPath);
            using (Process process = Process.Start(start))
            {
                if (process == null)
                {
                    throw new InvalidOperationException(
                        "PowerShell installer script AST validation failed"
                    );
                }
                process.StandardOutput.ReadToEnd();
                process.StandardError.ReadToEnd();
                if (!process.WaitForExit(30000) ||
                    process.ExitCode != 0)
                {
                    try
                    {
                        process.Kill();
                    }
                    catch
                    {
                    }
                    throw new InvalidOperationException(
                        "PowerShell installer script failed AST validation"
                    );
                }
            }
        }

        private static ClientInstallResult InstallManagedDesktop(
            string home,
            ClientSource source,
            string stagedPath
        )
        {
            if (!String.Equals(
                    source.artifact_kind,
                    "portable-exe",
                    StringComparison.Ordinal) ||
                !String.Equals(
                    Path.GetExtension(stagedPath),
                    ".exe",
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Managed desktop source contract is invalid"
                );
            }
            string appRoot = ManagedDesktopRoot(home, source);
            string versionRoot = Path.Combine(appRoot, source.version);
            EnsureSafeDirectory(versionRoot);
            string fileName = Path.GetFileName(stagedPath);
            if (!IsSafeSegment(fileName))
            {
                throw new InvalidOperationException(
                    "Managed desktop file name is unsafe"
                );
            }
            string destination = Path.Combine(versionRoot, fileName);
            string recordPath = Path.Combine(appRoot, "current.json");
            string destinationIo = ToExtendedLengthPath(destination);
            string recordIo = ToExtendedLengthPath(recordPath);
            byte[] priorDestination = File.Exists(destinationIo)
                ? File.ReadAllBytes(destinationIo)
                : null;
            byte[] priorRecord = File.Exists(recordIo)
                ? File.ReadAllBytes(recordIo)
                : null;
            string temporary = destination + ".install-" +
                Guid.NewGuid().ToString("N");
            string recordTemporary = recordPath + ".install-" +
                Guid.NewGuid().ToString("N");
            string temporaryIo = ToExtendedLengthPath(temporary);
            string recordTemporaryIo = ToExtendedLengthPath(
                recordTemporary
            );
            try
            {
                File.Copy(
                    ToExtendedLengthPath(stagedPath),
                    temporaryIo,
                    false
                );
                if (File.Exists(destinationIo))
                {
                    File.Replace(
                        temporaryIo,
                        destinationIo,
                        null,
                        true
                    );
                }
                else
                {
                    File.Move(temporaryIo, destinationIo);
                }
                string actualHash = BundleIntegrity.Sha256(destinationIo);
                if (!String.Equals(
                        actualHash,
                        source.sha256,
                        StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException(
                        "Managed desktop hash changed during installation"
                    );
                }
                ManagedDesktopRecord record = new ManagedDesktopRecord
                {
                    schema_version = 1,
                    client_id = source.id,
                    version = source.version,
                    relative_path = source.version + "/" + fileName,
                    sha256 = actualHash
                };
                string json = new JavaScriptSerializer()
                    .Serialize(record);
                File.WriteAllText(
                    recordTemporaryIo,
                    json,
                    new UTF8Encoding(false)
                );
                if (File.Exists(recordIo))
                {
                    File.Replace(
                        recordTemporaryIo,
                        recordIo,
                        null,
                        true
                    );
                }
                else
                {
                    File.Move(recordTemporaryIo, recordIo);
                }
                CreateManagedDesktopShortcut(
                    home,
                    source,
                    destination
                );
            }
            catch
            {
                if (File.Exists(temporaryIo))
                {
                    File.Delete(temporaryIo);
                }
                if (File.Exists(recordTemporaryIo))
                {
                    File.Delete(recordTemporaryIo);
                }
                RestoreManagedFile(destination, priorDestination);
                RestoreManagedFile(recordPath, priorRecord);
                throw;
            }
            return new ClientInstallResult
            {
                status = "INSTALLED",
                client_id = source.id,
                version = source.version,
                relative_install_path = ManagedRelativePath(source),
                path_persisted = false,
                authentication_touched = false
            };
        }

        private static ClientInstallResult InstallOfficialDesktop(
            string home,
            ClientSource source,
            string stagedPath,
            bool testOnly
        )
        {
            if (!IsOfficialDesktopInstaller(source) ||
                !String.Equals(
                    source.artifact_kind,
                    "installer-exe",
                    StringComparison.Ordinal) ||
                !String.Equals(
                    Path.GetExtension(stagedPath),
                    ".exe",
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Official desktop installer contract is invalid"
                );
            }
            ProcessStartInfo start = new ProcessStartInfo
            {
                FileName = Path.GetFullPath(stagedPath),
                Arguments = "/S",
                UseShellExecute = false,
                CreateNoWindow = true
            };
            start.EnvironmentVariables["USERPROFILE"] =
                Path.GetFullPath(home);
            start.EnvironmentVariables["LOCALAPPDATA"] =
                LocalApplicationDataForHome(home);
            ConnectionStore.ConfigureProcessEnvironment(home, start);
            using (Process process = Process.Start(start))
            {
                if (process == null)
                {
                    throw new InvalidOperationException(
                        "Official desktop installer could not start"
                    );
                }
                if (!process.WaitForExit(600000))
                {
                    try
                    {
                        process.Kill();
                    }
                    catch
                    {
                    }
                    throw new InvalidOperationException(
                        "Official desktop installer timed out"
                    );
                }
                if (process.ExitCode != 0)
                {
                    throw new InvalidOperationException(
                        "Official desktop installer failed"
                    );
                }
            }
            string detected;
            string executable = ResolveOfficialDesktopPath(
                home,
                source,
                testOnly,
                out detected
            );
            if (!String.Equals(
                    detected,
                    source.version,
                    StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "Official desktop version differs after installation"
                );
            }
            string homeRoot = Path.GetFullPath(home)
                .TrimEnd(Path.DirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            if (!executable.StartsWith(
                    homeRoot,
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Official desktop application is outside the user profile"
                );
            }
            return new ClientInstallResult
            {
                status = "INSTALLED",
                client_id = source.id,
                version = source.version,
                relative_install_path = executable
                    .Substring(homeRoot.Length)
                    .Replace(Path.DirectorySeparatorChar, '/'),
                path_persisted = false,
                authentication_touched = false
            };
        }

        private static bool IsOfficialDesktopInstaller(
            ClientSource source
        )
        {
            return source != null &&
                String.Equals(
                    source.id,
                    "opencode-desktop",
                    StringComparison.Ordinal) &&
                String.Equals(
                    source.install_mode,
                    "official-installer",
                    StringComparison.Ordinal);
        }

        private static string DetectOfficialDesktopVersion(
            string home,
            ClientSource source,
            bool testOnly
        )
        {
            string version;
            try
            {
                ResolveOfficialDesktopPath(
                    home,
                    source,
                    testOnly,
                    out version
                );
                return version;
            }
            catch (FileNotFoundException)
            {
                return null;
            }
        }

        internal static string ResolveOfficialDesktopPath(
            string home,
            ClientSource source,
            bool testOnly,
            out string version
        )
        {
            if (!IsOfficialDesktopInstaller(source))
            {
                throw new InvalidOperationException(
                    "Official desktop source is not supported"
                );
            }
            string local = LocalApplicationDataForHome(home);
            string executable = new[]
            {
                Path.Combine(
                    local,
                    "Programs",
                    "OpenCode",
                    "OpenCode.exe"
                ),
                Path.Combine(local, "OpenCode", "OpenCode.exe")
            }.FirstOrDefault(File.Exists);
            if (executable == null)
            {
                throw new FileNotFoundException(
                    "Official desktop application was not found"
                );
            }
            executable = Path.GetFullPath(executable);
            if ((File.GetAttributes(executable) &
                    FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException(
                    "Official desktop application path is unsafe"
                );
            }
            if (source.signature_required && !testOnly)
            {
                VerifyAuthenticode(executable, source.publisher);
            }
            FileVersionInfo info = FileVersionInfo.GetVersionInfo(
                executable
            );
            version = String.IsNullOrWhiteSpace(info.ProductVersion)
                ? info.FileVersion
                : info.ProductVersion;
            if (String.IsNullOrWhiteSpace(version))
            {
                throw new InvalidOperationException(
                    "Official desktop version is missing"
                );
            }
            version = version.Trim();
            return executable;
        }

        private static string DetectManagedDesktopVersion(
            string home,
            ClientSource source
        )
        {
            string root = ManagedDesktopRoot(home, source);
            string recordPath = Path.Combine(root, "current.json");
            string recordIo = ToExtendedLengthPath(recordPath);
            if (!File.Exists(recordIo))
            {
                return null;
            }
            ManagedDesktopRecord record;
            try
            {
                record = new JavaScriptSerializer()
                    .Deserialize<ManagedDesktopRecord>(
                        File.ReadAllText(
                            recordIo,
                            new UTF8Encoding(false, true)
                        )
                    );
            }
            catch (Exception exception)
            {
                throw new InvalidOperationException(
                    "Managed desktop record is invalid",
                    exception
                );
            }
            if (record == null ||
                record.schema_version != 1 ||
                !String.Equals(
                    record.client_id,
                    source.id,
                    StringComparison.Ordinal) ||
                !IsSafeSegment(record.version) ||
                String.IsNullOrWhiteSpace(record.relative_path) ||
                record.sha256 == null ||
                record.sha256.Length != 64)
            {
                throw new InvalidOperationException(
                    "Managed desktop record is invalid"
                );
            }
            string relative = record.relative_path.Replace(
                '/',
                Path.DirectorySeparatorChar
            );
            if (Path.IsPathRooted(relative) ||
                relative.Split(Path.DirectorySeparatorChar).Any(segment =>
                    segment == "." || segment == ".."))
            {
                throw new InvalidOperationException(
                    "Managed desktop record path is unsafe"
                );
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
                !File.Exists(ToExtendedLengthPath(executable)) ||
                !String.Equals(
                    BundleIntegrity.Sha256(
                        ToExtendedLengthPath(executable)
                    ),
                    record.sha256,
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Managed desktop record integrity check failed"
                );
            }
            return record.version;
        }

        private static string ManagedDesktopRoot(
            string home,
            ClientSource source
        )
        {
            return Path.Combine(
                Path.GetFullPath(home),
                ".llm-foundation",
                "apps",
                source.id
            );
        }

        private static void CreateManagedDesktopShortcut(
            string home,
            ClientSource source,
            string executable
        )
        {
            string shortcutRoot = Path.Combine(
                RoamingApplicationDataForHome(home),
                "Microsoft",
                "Windows",
                "Start Menu",
                "Programs",
                "LLM Foundation"
            );
            EnsureSafeDirectory(shortcutRoot);
            string shortcut = Path.Combine(
                shortcutRoot,
                source.id + ".lnk"
            );
            string shortcutIo = ToExtendedLengthPath(shortcut);
            string adjacentTemporary = shortcut + ".install-" +
                Guid.NewGuid().ToString("N");
            string adjacentTemporaryIo = ToExtendedLengthPath(
                adjacentTemporary
            );
            string shellTemporary = Path.Combine(
                Path.GetTempPath(),
                "llm-foundation-shortcut-" +
                Guid.NewGuid().ToString("N") +
                ".lnk"
            );
            byte[] priorShortcut = File.Exists(shortcutIo)
                ? File.ReadAllBytes(shortcutIo)
                : null;
            const string command =
                "$ErrorActionPreference='Stop';" +
                "$shell=New-Object -ComObject WScript.Shell;" +
                "$link=$shell.CreateShortcut($env:LLM_SHORTCUT_PATH);" +
                "$link.TargetPath=$env:LLM_SHORTCUT_TARGET;" +
                "$link.WorkingDirectory=" +
                "[System.IO.Path]::GetDirectoryName(" +
                "$env:LLM_SHORTCUT_TARGET);" +
                "$link.Description=$env:LLM_SHORTCUT_DESCRIPTION;" +
                "$link.Save();";
            ProcessStartInfo start = new ProcessStartInfo
            {
                FileName = WindowsPowerShellPath(),
                Arguments =
                    NonInteractiveCommandPrelude +
                    QuoteArgument(command),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            start.EnvironmentVariables["LLM_SHORTCUT_PATH"] =
                shellTemporary;
            start.EnvironmentVariables["LLM_SHORTCUT_TARGET"] = executable;
            start.EnvironmentVariables["LLM_SHORTCUT_DESCRIPTION"] =
                source.display_name ?? source.id;
            try
            {
                using (Process process = Process.Start(start))
                {
                    if (process == null)
                    {
                        throw new InvalidOperationException(
                            "Managed desktop shortcut could not be created"
                        );
                    }
                    process.StandardOutput.ReadToEnd();
                    process.StandardError.ReadToEnd();
                    if (!process.WaitForExit(30000) ||
                        process.ExitCode != 0 ||
                        !File.Exists(shellTemporary))
                    {
                        throw new InvalidOperationException(
                            "Managed desktop shortcut could not be created"
                        );
                    }
                }
                File.Copy(
                    ToExtendedLengthPath(shellTemporary),
                    adjacentTemporaryIo,
                    false
                );
                if (File.Exists(shortcutIo))
                {
                    File.Replace(
                        adjacentTemporaryIo,
                        shortcutIo,
                        null,
                        true
                    );
                }
                else
                {
                    File.Move(adjacentTemporaryIo, shortcutIo);
                }
                if (!File.Exists(shortcutIo))
                {
                    throw new InvalidOperationException(
                        "Managed desktop shortcut could not be created"
                    );
                }
            }
            catch
            {
                if (File.Exists(adjacentTemporaryIo))
                {
                    File.Delete(adjacentTemporaryIo);
                }
                RestoreManagedFile(shortcut, priorShortcut);
                throw;
            }
            finally
            {
                if (File.Exists(shellTemporary))
                {
                    File.Delete(shellTemporary);
                }
            }
        }

        private static string ExtractLockedArchivePayload(
            string archivePath,
            ClientSource source
        )
        {
            string lockedEntry = NormalizeZipEntry(source.archive_entry);
            if (String.IsNullOrWhiteSpace(lockedEntry) ||
                lockedEntry.EndsWith("/", StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "Client archive entry is not locked to a file"
                );
            }
            ZipArchiveEntry payload = null;
            using (FileStream stream = new FileStream(
                archivePath,
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read
            ))
            using (ZipArchive archive = new ZipArchive(
                stream,
                ZipArchiveMode.Read,
                false
            ))
            {
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    string normalized = NormalizeZipEntry(entry.FullName);
                    if (!IsSafeZipEntry(normalized))
                    {
                        throw new InvalidOperationException(
                            "Unsafe ZIP entry in client archive"
                        );
                    }
                    if (String.Equals(
                            normalized,
                            lockedEntry,
                            StringComparison.Ordinal))
                    {
                        if (payload != null ||
                            normalized.EndsWith(
                                "/",
                                StringComparison.Ordinal))
                        {
                            throw new InvalidOperationException(
                                "Client archive contains an ambiguous payload"
                            );
                        }
                        payload = entry;
                    }
                }
                if (payload == null)
                {
                    throw new InvalidOperationException(
                        "Locked client archive payload is missing"
                    );
                }
                string suffix = Path.GetExtension(lockedEntry);
                string extractedPath = archivePath + ".payload-" +
                    Guid.NewGuid().ToString("N") + suffix;
                try
                {
                    using (Stream input = payload.Open())
                    using (FileStream output = new FileStream(
                        extractedPath,
                        FileMode.CreateNew,
                        FileAccess.Write,
                        FileShare.None
                    ))
                    {
                        input.CopyTo(output);
                        output.Flush(true);
                    }
                    if (source.signature_required)
                    {
                        VerifyAuthenticode(
                            extractedPath,
                            source.publisher
                        );
                    }
                    return extractedPath;
                }
                catch
                {
                    if (File.Exists(extractedPath))
                    {
                        File.Delete(extractedPath);
                    }
                    throw;
                }
            }
        }

        private static string NormalizeZipEntry(string value)
        {
            return String.IsNullOrWhiteSpace(value)
                ? ""
                : value.Replace('\\', '/');
        }

        private static bool IsSafeZipEntry(string value)
        {
            if (String.IsNullOrWhiteSpace(value) ||
                value.StartsWith("/", StringComparison.Ordinal) ||
                value.Contains(":"))
            {
                return false;
            }
            string[] segments = value.Split('/');
            foreach (string segment in segments)
            {
                if (segment == "." || segment == "..")
                {
                    return false;
                }
                if (segment.Length > 0 &&
                    segment.IndexOfAny(
                        Path.GetInvalidFileNameChars()
                    ) >= 0)
                {
                    return false;
                }
            }
            return true;
        }

        private static void Validate(ClientSourceLock value)
        {
            if (value == null || value.schema_version != 1 ||
                value.platform == null ||
                !String.Equals(
                    value.platform.os,
                    "windows",
                    StringComparison.Ordinal
                ) ||
                !String.Equals(
                    value.platform.architecture,
                    "x64",
                    StringComparison.Ordinal
                ) ||
                value.platform.minimum_build < 19041 ||
                value.clients == null ||
                value.clients.Count == 0)
            {
                throw new InvalidOperationException(
                    "Client source lock schema is invalid"
                );
            }
            HashSet<string> ids = new HashSet<string>(
                StringComparer.Ordinal
            );
            foreach (ClientSource source in value.clients)
            {
                if (source == null ||
                    String.IsNullOrWhiteSpace(source.id) ||
                    !ids.Add(source.id) ||
                    String.IsNullOrWhiteSpace(source.target) ||
                    String.IsNullOrWhiteSpace(source.version) ||
                    String.IsNullOrWhiteSpace(source.source_kind) ||
                    String.IsNullOrWhiteSpace(source.url))
                {
                    throw new InvalidOperationException(
                        "Client source lock contains an invalid entry"
                    );
                }
                if (source.detect_commands == null ||
                    source.version_arguments == null ||
                    source.version_arguments.Any(argument =>
                        String.IsNullOrWhiteSpace(argument) ||
                        !Regex.IsMatch(
                            argument,
                            @"^[-A-Za-z0-9.]+$",
                            RegexOptions.CultureInvariant)))
                {
                    throw new InvalidOperationException(
                        "Client detection command is invalid"
                    );
                }
                Uri uri;
                if (!Uri.TryCreate(source.url, UriKind.Absolute, out uri) ||
                    uri.UserInfo.Length != 0)
                {
                    throw new InvalidOperationException(
                        "Client source URL is not approved"
                    );
                }
                if (value.official_only)
                {
                    if (value.test_only ||
                        uri.Scheme != Uri.UriSchemeHttps ||
                        !OfficialHosts.Contains(uri.Host))
                    {
                        throw new InvalidOperationException(
                            "Client source URL is not approved"
                        );
                    }
                }
                else if (!value.test_only ||
                    !uri.IsLoopback ||
                    (uri.Scheme != Uri.UriSchemeHttp &&
                        uri.Scheme != Uri.UriSchemeHttps))
                {
                    throw new InvalidOperationException(
                        "Local test client source is unsafe"
                    );
                }
                if (source.signature_required &&
                    String.IsNullOrWhiteSpace(source.publisher))
                {
                    throw new InvalidOperationException(
                        "Signed client source publisher is missing"
                    );
                }
                if (source.source_kind == "download")
                {
                    if (String.IsNullOrWhiteSpace(source.sha256) ||
                        source.sha256.Length != 64 ||
                        source.sha256.Any(character =>
                            !Uri.IsHexDigit(character)))
                    {
                        throw new InvalidOperationException(
                            "Client source hash is invalid"
                        );
                    }
                }
                else if (source.source_kind == "store")
                {
                    if (!String.Equals(
                            source.id,
                            "codex-desktop",
                            StringComparison.Ordinal) ||
                        !String.Equals(
                            source.store_identity,
                            "OpenAI.Codex",
                            StringComparison.Ordinal) ||
                        !String.Equals(
                            source.store_product_id,
                            "9PLM9XGG6VKS",
                            StringComparison.Ordinal) ||
                        !String.Equals(
                            source.store_publisher,
                            "CN=50BDFD77-8903-4850-9FFE-6E8522F64D5B",
                            StringComparison.Ordinal) ||
                        !String.Equals(
                            source.store_signature_kind,
                            "Store",
                            StringComparison.Ordinal) ||
                        !String.Equals(
                            source.store_application_id,
                            "App",
                            StringComparison.Ordinal) ||
                        !String.Equals(
                            source.store_executable,
                            "app/ChatGPT.exe",
                            StringComparison.Ordinal) ||
                        !String.Equals(
                            source.store_entry_point,
                            "Windows.FullTrustApplication",
                            StringComparison.Ordinal))
                    {
                        throw new InvalidOperationException(
                            "Store client identity is invalid"
                        );
                    }
                }
                else
                {
                    throw new InvalidOperationException(
                        "Client source kind is invalid"
                    );
                }
            }
        }

        private static void AssertStoreSource(ClientSource source)
        {
            if (source == null ||
                !String.Equals(
                    source.source_kind,
                    "store",
                    StringComparison.Ordinal) ||
                !String.Equals(
                    source.store_product_id,
                    "9PLM9XGG6VKS",
                    StringComparison.Ordinal) ||
                !String.Equals(
                    source.store_identity,
                    "OpenAI.Codex",
                    StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "Requested client is not an approved Store source"
                );
            }
        }

        private static StoreClientResult ValidateStoreProbe(
            ClientSource source,
            StorePackageProbe probe
        )
        {
            AssertStoreSource(source);
            if (probe == null)
            {
                throw new InvalidOperationException(
                    "Store package record is invalid"
                );
            }
            string sourceUri = StoreSourceUri(source);
            if (!probe.present)
            {
                return new StoreClientResult
                {
                    status = "MISSING",
                    client_id = source.id,
                    version = null,
                    package_full_name = null,
                    package_family_name = null,
                    install_location = null,
                    application_id = null,
                    executable = null,
                    store_product_id = source.store_product_id,
                    source_uri = sourceUri
                };
            }
            string normalizedExecutable = (
                probe.executable ?? ""
            ).Replace('\\', '/');
            if (!String.Equals(
                    probe.name,
                    source.store_identity,
                    StringComparison.Ordinal) ||
                !String.Equals(
                    probe.publisher,
                    source.store_publisher,
                    StringComparison.Ordinal) ||
                !String.Equals(
                    probe.signature_kind,
                    source.store_signature_kind,
                    StringComparison.Ordinal) ||
                !String.Equals(
                    probe.architecture,
                    "X64",
                    StringComparison.OrdinalIgnoreCase) ||
                String.IsNullOrWhiteSpace(probe.version) ||
                String.IsNullOrWhiteSpace(probe.package_full_name) ||
                !probe.package_full_name.StartsWith(
                    source.store_identity + "_",
                    StringComparison.Ordinal) ||
                String.IsNullOrWhiteSpace(probe.package_family_name) ||
                !probe.package_family_name.StartsWith(
                    source.store_identity + "_",
                    StringComparison.Ordinal) ||
                String.IsNullOrWhiteSpace(probe.install_location) ||
                !Path.IsPathRooted(probe.install_location) ||
                !String.Equals(
                    probe.application_id,
                    source.store_application_id,
                    StringComparison.Ordinal) ||
                !String.Equals(
                    normalizedExecutable,
                    source.store_executable,
                    StringComparison.Ordinal) ||
                !String.Equals(
                    probe.entry_point,
                    source.store_entry_point,
                    StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "Store package identity differs from source lock"
                );
            }
            return new StoreClientResult
            {
                status = "READY",
                client_id = source.id,
                version = probe.version,
                package_full_name = probe.package_full_name,
                package_family_name = probe.package_family_name,
                install_location = Path.GetFullPath(
                    probe.install_location
                ),
                application_id = probe.application_id,
                executable = normalizedExecutable,
                store_product_id = source.store_product_id,
                source_uri = sourceUri
            };
        }

        private static string StoreSourceUri(ClientSource source)
        {
            return "ms-windows-store://pdp/?ProductId=" +
                source.store_product_id;
        }

        private static void AddWindowsPowerShellModulePath(
            ProcessStartInfo start
        )
        {
            string moduleRoot = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.System),
                "WindowsPowerShell",
                "v1.0",
                "Modules"
            );
            string inheritedModules = Environment.GetEnvironmentVariable(
                "PSModulePath"
            ) ?? "";
            start.EnvironmentVariables["PSModulePath"] = moduleRoot +
                (String.IsNullOrWhiteSpace(inheritedModules)
                    ? ""
                    : ";" + inheritedModules);
        }

        private static byte[] ReadResource(string name)
        {
            using (Stream stream = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream(name))
            {
                if (stream == null)
                {
                    throw new InvalidOperationException(
                        "Client source lock resource is missing"
                    );
                }
                using (MemoryStream buffer = new MemoryStream())
                {
                    stream.CopyTo(buffer);
                    return buffer.ToArray();
                }
            }
        }

        private static void EnsureSafeDirectory(string path)
        {
            string full = Path.GetFullPath(path);
            string root = Path.GetPathRoot(full);
            if (String.Equals(
                    full.TrimEnd(Path.DirectorySeparatorChar),
                    root.TrimEnd(Path.DirectorySeparatorChar),
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Client staging root cannot be a drive root"
                );
            }
            AssertNoReparseAncestors(full);
            Directory.CreateDirectory(ToExtendedLengthPath(full));
            AssertNoReparseAncestors(full);
            FileAttributes attributes = File.GetAttributes(
                ToExtendedLengthPath(full)
            );
            if ((attributes & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException(
                    "Client staging directory cannot be a reparse point"
                );
            }
        }

        private static void AssertNoReparseAncestors(string path)
        {
            string current = Path.GetFullPath(path);
            while (!String.IsNullOrWhiteSpace(current))
            {
                string currentIo = ToExtendedLengthPath(current);
                if (Directory.Exists(currentIo) ||
                    File.Exists(currentIo))
                {
                    FileAttributes attributes = File.GetAttributes(
                        currentIo
                    );
                    if ((attributes & FileAttributes.ReparsePoint) != 0)
                    {
                        throw new InvalidOperationException(
                            "Client staging path contains a reparse point"
                        );
                    }
                }
                string parent = Path.GetDirectoryName(current);
                if (String.IsNullOrWhiteSpace(parent) ||
                    String.Equals(
                        parent,
                        current,
                        StringComparison.OrdinalIgnoreCase))
                {
                    break;
                }
                current = parent;
            }
        }

        private static ClientSource FindSource(
            string bundleRoot,
            string clientId
        )
        {
            ClientSourceLock catalog = Load(bundleRoot);
            ClientSource source = catalog.clients.FirstOrDefault(entry =>
                String.Equals(
                    entry.id,
                    clientId,
                    StringComparison.Ordinal
                )
            );
            if (source == null)
            {
                throw new InvalidOperationException(
                    "Requested client is not in the source lock"
                );
            }
            return source;
        }

        private static string DetectVersion(
            string home,
            ClientSource source
        )
        {
            if (source.detect_commands == null ||
                source.detect_commands.Count == 0)
            {
                return null;
            }
            string command = source.detect_commands[0];
            string managed = ManagedCommandPath(home, source);
            string executable = File.Exists(managed)
                ? managed
                : FindOnPath(command);
            if (String.IsNullOrWhiteSpace(executable))
            {
                return null;
            }
            try
            {
                ProcessStartInfo start;
                string arguments = String.Join(
                    " ",
                    source.version_arguments.ToArray()
                );
                string extension = Path.GetExtension(executable);
                if (String.Equals(
                        extension,
                        ".cmd",
                        StringComparison.OrdinalIgnoreCase) ||
                    String.Equals(
                        extension,
                        ".bat",
                        StringComparison.OrdinalIgnoreCase))
                {
                    start = new ProcessStartInfo
                    {
                        FileName = Environment.GetEnvironmentVariable(
                            "COMSPEC"
                        ) ?? "cmd.exe",
                        Arguments = "/d /s /c \"\"" + executable +
                            "\" " + arguments + "\""
                    };
                }
                else
                {
                    start = new ProcessStartInfo
                    {
                        FileName = executable,
                        Arguments = arguments
                    };
                }
                start.UseShellExecute = false;
                start.CreateNoWindow = true;
                start.RedirectStandardOutput = true;
                start.RedirectStandardError = true;
                start.StandardOutputEncoding = Encoding.UTF8;
                start.StandardErrorEncoding = Encoding.UTF8;
                using (Process process = Process.Start(start))
                {
                    if (process == null)
                    {
                        return null;
                    }
                    string output = process.StandardOutput.ReadToEnd() + " " +
                        process.StandardError.ReadToEnd();
                    if (!process.WaitForExit(10000) ||
                        process.ExitCode != 0)
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
                    Match match = VersionPattern.Match(output);
                    return match.Success ? match.Groups[1].Value : null;
                }
            }
            catch
            {
                return null;
            }
        }

        private static string FindOnPath(string command)
        {
            string path = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string directory in path.Split(
                new[] { Path.PathSeparator },
                StringSplitOptions.RemoveEmptyEntries))
            {
                try
                {
                    string candidate = Path.Combine(
                        directory.Trim().Trim('"'),
                        command
                    );
                    if (File.Exists(candidate))
                    {
                        return candidate;
                    }
                }
                catch
                {
                }
            }
            return null;
        }

        private static bool TryCompareCoreVersions(
            string detected,
            string supported,
            out int comparison
        )
        {
            comparison = 0;
            Version detectedCore;
            Version supportedCore;
            if (!TryParseCoreVersion(detected, out detectedCore) ||
                !TryParseCoreVersion(supported, out supportedCore))
            {
                return false;
            }
            comparison = detectedCore.CompareTo(supportedCore);
            return true;
        }

        private static bool TryParseCoreVersion(
            string value,
            out Version result
        )
        {
            result = null;
            Match match = Regex.Match(
                value ?? "",
                @"^([0-9]+)\.([0-9]+)\.([0-9]+)",
                RegexOptions.CultureInvariant
            );
            if (!match.Success)
            {
                return false;
            }
            int major;
            int minor;
            int patch;
            if (!Int32.TryParse(match.Groups[1].Value, out major) ||
                !Int32.TryParse(match.Groups[2].Value, out minor) ||
                !Int32.TryParse(match.Groups[3].Value, out patch))
            {
                return false;
            }
            result = new Version(major, minor, patch);
            return true;
        }

        private static string ManagedRelativePath(ClientSource source)
        {
            if (String.Equals(
                    source.install_mode,
                    "official-installer",
                    StringComparison.Ordinal) &&
                String.Equals(
                    source.id,
                    "opencode-desktop",
                    StringComparison.Ordinal))
            {
                return "AppData/Local/Programs/OpenCode/OpenCode.exe";
            }
            if (String.Equals(
                    source.install_mode,
                    "managed-desktop",
                    StringComparison.Ordinal))
            {
                string sourceFile = Path.GetFileName(
                    new Uri(
                        source.url,
                        UriKind.Absolute
                    ).AbsolutePath
                );
                return ".llm-foundation/apps/" + source.id + "/" +
                    source.version + "/" + sourceFile;
            }
            if (source.detect_commands == null ||
                source.detect_commands.Count == 0)
            {
                return null;
            }
            if (String.Equals(
                    source.install_mode,
                    "official-script",
                    StringComparison.Ordinal))
            {
                return ".llm-foundation/clients/" + source.id +
                    "/bin/" + source.detect_commands[0];
            }
            return ".llm-foundation/bin/" + source.detect_commands[0];
        }

        internal static bool UsesManagedCommand(ClientSource source)
        {
            return source != null &&
                (String.Equals(
                    source.install_mode,
                    "official-script",
                    StringComparison.Ordinal) ||
                 String.Equals(
                    source.install_mode,
                    "managed-bin",
                    StringComparison.Ordinal));
        }

        internal static string ManagedCommandRecordPath(
            string home,
            ClientSource source
        )
        {
            if (!UsesManagedCommand(source) ||
                !IsSafeSegment(source.id))
            {
                throw new InvalidOperationException(
                    "Managed command source is invalid"
                );
            }
            return Path.Combine(
                Path.GetFullPath(home),
                ".llm-foundation",
                "clients",
                source.id,
                "current.json"
            );
        }

        internal static string ResolveManagedCommandPath(
            string home,
            ClientSource source,
            out ManagedCommandRecord record
        )
        {
            record = null;
            string recordPath = ManagedCommandRecordPath(home, source);
            if (!File.Exists(recordPath))
            {
                throw new FileNotFoundException(
                    "Managed command record is missing",
                    recordPath
                );
            }
            FileInfo recordInfo = new FileInfo(recordPath);
            if (recordInfo.Length < 2 ||
                recordInfo.Length > 65536 ||
                (recordInfo.Attributes &
                    FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException(
                    "Managed command record is invalid"
                );
            }
            record = new JavaScriptSerializer()
                .Deserialize<ManagedCommandRecord>(
                    File.ReadAllText(
                        recordPath,
                        new UTF8Encoding(false, true)
                    )
                );
            string expectedRelative = ManagedRelativePath(source);
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
                    record.relative_path,
                    expectedRelative,
                    StringComparison.Ordinal) ||
                !String.Equals(
                    record.source_sha256,
                    source.sha256,
                    StringComparison.OrdinalIgnoreCase) ||
                String.IsNullOrWhiteSpace(record.sha256) ||
                record.sha256.Length != 64)
            {
                throw new InvalidOperationException(
                    "Managed command record is invalid"
                );
            }
            string relative = expectedRelative.Replace(
                '/',
                Path.DirectorySeparatorChar
            );
            string root = Path.GetFullPath(home)
                .TrimEnd(Path.DirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            string executable = Path.GetFullPath(
                Path.Combine(root, relative)
            );
            if (!executable.StartsWith(
                    root,
                    StringComparison.OrdinalIgnoreCase) ||
                !File.Exists(executable) ||
                (File.GetAttributes(executable) &
                    FileAttributes.ReparsePoint) != 0 ||
                !String.Equals(
                    BundleIntegrity.Sha256(executable),
                    record.sha256,
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Managed command integrity check failed"
                );
            }
            return executable;
        }

        private static bool HasValidManagedCommandRecord(
            string home,
            ClientSource source
        )
        {
            try
            {
                ManagedCommandRecord record;
                ResolveManagedCommandPath(home, source, out record);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static void WriteManagedCommandRecord(
            string home,
            ClientSource source,
            string commandPath
        )
        {
            if (!UsesManagedCommand(source) ||
                String.IsNullOrWhiteSpace(source.sha256) ||
                !File.Exists(commandPath))
            {
                throw new InvalidOperationException(
                    "Managed command cannot be recorded"
                );
            }
            string expected = Path.GetFullPath(
                Path.Combine(
                    Path.GetFullPath(home),
                    ManagedRelativePath(source).Replace(
                        '/',
                        Path.DirectorySeparatorChar
                    )
                )
            );
            if (!String.Equals(
                    expected,
                    Path.GetFullPath(commandPath),
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Managed command path differs from source contract"
                );
            }
            string recordPath = ManagedCommandRecordPath(home, source);
            EnsureSafeDirectory(Path.GetDirectoryName(recordPath));
            string temporary = recordPath + ".install-" +
                Guid.NewGuid().ToString("N");
            ManagedCommandRecord record = new ManagedCommandRecord
            {
                schema_version = 1,
                client_id = source.id,
                version = source.version,
                relative_path = ManagedRelativePath(source),
                sha256 = BundleIntegrity.Sha256(commandPath),
                source_sha256 = source.sha256
            };
            try
            {
                File.WriteAllText(
                    temporary,
                    new JavaScriptSerializer().Serialize(record),
                    new UTF8Encoding(false)
                );
                if (File.Exists(recordPath))
                {
                    File.Replace(temporary, recordPath, null, true);
                }
                else
                {
                    File.Move(temporary, recordPath);
                }
            }
            finally
            {
                if (File.Exists(temporary))
                {
                    File.Delete(temporary);
                }
            }
        }

        private static string ManagedCommandPath(
            string home,
            ClientSource source
        )
        {
            if (source.detect_commands == null ||
                source.detect_commands.Count == 0)
            {
                return null;
            }
            return Path.Combine(
                ManagedBinRoot(home, source),
                source.detect_commands[0]
            );
        }

        private static string ManagedBinRoot(
            string home,
            ClientSource source
        )
        {
            string root = Path.GetFullPath(home);
            if (String.Equals(
                    source.install_mode,
                    "official-script",
                    StringComparison.Ordinal))
            {
                return Path.Combine(
                    root,
                    ".llm-foundation",
                    "clients",
                    source.id,
                    "bin"
                );
            }
            return Path.Combine(root, ".llm-foundation", "bin");
        }

        private static string LocalApplicationDataForHome(string home)
        {
            string actualHome = Path.GetFullPath(
                Environment.GetFolderPath(
                    Environment.SpecialFolder.UserProfile
                )
            ).TrimEnd(Path.DirectorySeparatorChar);
            string requestedHome = Path.GetFullPath(home)
                .TrimEnd(Path.DirectorySeparatorChar);
            if (String.Equals(
                    actualHome,
                    requestedHome,
                    StringComparison.OrdinalIgnoreCase))
            {
                return Environment.GetFolderPath(
                    Environment.SpecialFolder.LocalApplicationData
                );
            }
            return Path.Combine(requestedHome, "AppData", "Local");
        }

        private static string RoamingApplicationDataForHome(string home)
        {
            string actualHome = Path.GetFullPath(
                Environment.GetFolderPath(
                    Environment.SpecialFolder.UserProfile
                )
            ).TrimEnd(Path.DirectorySeparatorChar);
            string requestedHome = Path.GetFullPath(home)
                .TrimEnd(Path.DirectorySeparatorChar);
            if (String.Equals(
                    actualHome,
                    requestedHome,
                    StringComparison.OrdinalIgnoreCase))
            {
                return Environment.GetFolderPath(
                    Environment.SpecialFolder.ApplicationData
                );
            }
            return Path.Combine(requestedHome, "AppData", "Roaming");
        }

        private static bool PersistManagedPathForCurrentUser(
            string home,
            string binRoot
        )
        {
            string actualHome = Path.GetFullPath(
                Environment.GetFolderPath(
                    Environment.SpecialFolder.UserProfile
                )
            ).TrimEnd(Path.DirectorySeparatorChar);
            string requestedHome = Path.GetFullPath(home)
                .TrimEnd(Path.DirectorySeparatorChar);
            if (!String.Equals(
                    actualHome,
                    requestedHome,
                    StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            string userPath = Environment.GetEnvironmentVariable(
                "Path",
                EnvironmentVariableTarget.User
            ) ?? "";
            bool present = userPath.Split(
                new[] { Path.PathSeparator },
                StringSplitOptions.RemoveEmptyEntries
            ).Any(entry => String.Equals(
                entry.Trim().Trim('"')
                    .TrimEnd(Path.DirectorySeparatorChar),
                binRoot.TrimEnd(Path.DirectorySeparatorChar),
                StringComparison.OrdinalIgnoreCase
            ));
            if (!present)
            {
                string updated = String.IsNullOrWhiteSpace(userPath)
                    ? binRoot
                    : userPath.TrimEnd(Path.PathSeparator) +
                        Path.PathSeparator + binRoot;
                Environment.SetEnvironmentVariable(
                    "Path",
                    updated,
                    EnvironmentVariableTarget.User
                );
            }
            string processPath = Environment.GetEnvironmentVariable(
                "Path",
                EnvironmentVariableTarget.Process
            ) ?? "";
            if (!processPath.Split(
                    new[] { Path.PathSeparator },
                    StringSplitOptions.RemoveEmptyEntries
                ).Any(entry => String.Equals(
                    entry.Trim().Trim('"')
                        .TrimEnd(Path.DirectorySeparatorChar),
                    binRoot.TrimEnd(Path.DirectorySeparatorChar),
                    StringComparison.OrdinalIgnoreCase)))
            {
                Environment.SetEnvironmentVariable(
                    "Path",
                    binRoot + Path.PathSeparator + processPath,
                    EnvironmentVariableTarget.Process
                );
            }
            return true;
        }

        private static bool IsManagedPathPersisted(
            string home,
            ClientSource source
        )
        {
            if (String.Equals(
                    source.install_mode,
                    "managed-desktop",
                    StringComparison.Ordinal) ||
                String.Equals(
                    source.install_mode,
                    "official-installer",
                    StringComparison.Ordinal))
            {
                return false;
            }
            string binRoot = ManagedBinRoot(home, source);
            string userPath = Environment.GetEnvironmentVariable(
                "Path",
                EnvironmentVariableTarget.User
            ) ?? "";
            return userPath.Split(
                new[] { Path.PathSeparator },
                StringSplitOptions.RemoveEmptyEntries
            ).Any(entry => String.Equals(
                entry.Trim().Trim('"')
                    .TrimEnd(Path.DirectorySeparatorChar),
                binRoot.TrimEnd(Path.DirectorySeparatorChar),
                StringComparison.OrdinalIgnoreCase
            ));
        }

        private static void RestoreManagedFile(
            string path,
            byte[] prior
        )
        {
            string pathIo = ToExtendedLengthPath(path);
            if (prior == null)
            {
                if (File.Exists(pathIo))
                {
                    File.Delete(pathIo);
                }
                return;
            }
            string temporary = pathIo + ".restore-" +
                Guid.NewGuid().ToString("N");
            File.WriteAllBytes(temporary, prior);
            if (File.Exists(pathIo))
            {
                File.Replace(temporary, pathIo, null, true);
            }
            else
            {
                File.Move(temporary, pathIo);
            }
        }

        // Прелюдия аргументов Windows PowerShell — одна на все пять вызовов
        // (раньше склеивалась вручную в каждом ProcessStartInfo).
        private const string NonInteractiveCommandPrelude =
            "-NoLogo -NoProfile -NonInteractive " +
            "-ExecutionPolicy Bypass -Command ";

        private static string WindowsPowerShellPath()
        {
            string powershell = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.System),
                "WindowsPowerShell",
                "v1.0",
                "powershell.exe"
            );
            if (!File.Exists(powershell))
            {
                throw new InvalidOperationException(
                    "Windows PowerShell is required for client verification"
                );
            }
            return powershell;
        }

        private static void VerifyAuthenticode(
            string path,
            string expectedPublisher
        )
        {
            string powershell = WindowsPowerShellPath();
            const string command =
                "$ErrorActionPreference='Stop';" +
                "Import-Module Microsoft.PowerShell.Security " +
                "-ErrorAction Stop;" +
                "$s=Get-AuthenticodeSignature -LiteralPath " +
                "$env:LLM_CLIENT_ARTIFACT;" +
                "$subject=if($null -eq $s.SignerCertificate)" +
                "{$null}else{[string]$s.SignerCertificate.Subject};" +
                "$simpleName=if($null -eq $s.SignerCertificate)" +
                "{$null}else{$s.SignerCertificate.GetNameInfo(" +
                "[Security.Cryptography.X509Certificates.X509NameType]" +
                "::SimpleName,$false)};" +
                "[pscustomobject]@{status=[string]$s.Status;" +
                "subject=$subject;simple_name=[string]$simpleName}" +
                "|ConvertTo-Json -Compress";
            ProcessStartInfo start = new ProcessStartInfo
            {
                FileName = powershell,
                Arguments =
                    NonInteractiveCommandPrelude +
                    QuoteArgument(command),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            start.EnvironmentVariables["LLM_CLIENT_ARTIFACT"] = path;
            string moduleRoot = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.System),
                "WindowsPowerShell",
                "v1.0",
                "Modules"
            );
            string inheritedModules = Environment.GetEnvironmentVariable(
                "PSModulePath"
            ) ?? "";
            start.EnvironmentVariables["PSModulePath"] = moduleRoot +
                (String.IsNullOrWhiteSpace(inheritedModules)
                    ? ""
                    : ";" + inheritedModules);
            string output;
            using (Process process = Process.Start(start))
            {
                if (process == null)
                {
                    throw new InvalidOperationException(
                        "Authenticode verification could not start"
                    );
                }
                output = process.StandardOutput.ReadToEnd();
                process.StandardError.ReadToEnd();
                if (!process.WaitForExit(30000) || process.ExitCode != 0)
                {
                    throw new InvalidOperationException(
                        "Authenticode verification failed"
                    );
                }
            }
            SignatureProbe result = new JavaScriptSerializer()
                .Deserialize<SignatureProbe>(output);
            if (result == null ||
                !String.Equals(
                    result.status,
                    "Valid",
                    StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "Client artifact Authenticode signature is not valid: " +
                    (result == null ? "no-result" : result.status)
                );
            }
            if (String.IsNullOrWhiteSpace(expectedPublisher) ||
                String.IsNullOrWhiteSpace(result.simple_name) ||
                !String.Equals(
                    result.simple_name,
                    expectedPublisher,
                    StringComparison.OrdinalIgnoreCase
                ))
            {
                throw new InvalidOperationException(
                    "Client artifact Authenticode publisher differs"
                );
            }
        }

        internal static void VerifyInstalledPublisher(
            string path,
            string expectedPublisher
        )
        {
            VerifyAuthenticode(path, expectedPublisher);
        }

        private static bool IsSafeSegment(string value)
        {
            if (String.IsNullOrWhiteSpace(value) ||
                value == "." || value == ".." ||
                value.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
                value.Contains("/") || value.Contains("\\"))
            {
                return false;
            }
            return true;
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

        private static string ToExtendedLengthPath(string path)
        {
            string fullPath = Path.GetFullPath(path);
            if (fullPath.StartsWith(
                    @"\\?\",
                    StringComparison.Ordinal))
            {
                return fullPath;
            }
            if (fullPath.StartsWith(
                    @"\\",
                    StringComparison.Ordinal))
            {
                return @"\\?\UNC\" + fullPath.Substring(2);
            }
            return @"\\?\" + fullPath;
        }

        private static byte[] ComputeSha256(byte[] value)
        {
            using (SHA256 algorithm = SHA256.Create())
            {
                return algorithm.ComputeHash(value);
            }
        }

        private static bool FixedTimeEquals(byte[] left, byte[] right)
        {
            if (left.Length != right.Length)
            {
                return false;
            }
            int difference = 0;
            for (int index = 0; index < left.Length; index++)
            {
                difference |= left[index] ^ right[index];
            }
            return difference == 0;
        }
    }
}
