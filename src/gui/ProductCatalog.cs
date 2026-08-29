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
    internal static class ProductCatalog
    {
        // Позиционный string[][] молча путал два разных client id (у codex и
        // claude они совпадают, у opencode — нет) и требовал спецкейсов по
        // definition[0] == "codex". Типизированная запись убирает и то, и другое.
        private sealed class TargetDefinition
        {
            public TargetDefinition(
                string id,
                string displayName,
                string packageClientId,
                string sourceClientId,
                bool detectsThroughStore
            )
            {
                Id = id;
                DisplayName = displayName;
                PackageClientId = packageClientId;
                SourceClientId = sourceClientId;
                DetectsThroughStore = detectsThroughStore;
            }

            public string Id { get; private set; }
            public string DisplayName { get; private set; }
            public string PackageClientId { get; private set; }
            public string SourceClientId { get; private set; }
            public bool DetectsThroughStore { get; private set; }
        }

        private static readonly TargetDefinition[] Definitions = new[]
        {
            new TargetDefinition(
                "codex", "Codex", "codex-cli", "codex-cli", true
            ),
            new TargetDefinition(
                "claude", "Claude", "claude-code", "claude-code", false
            ),
            new TargetDefinition(
                "opencode", "OpenCode", "opencode", "opencode-cli", false
            )
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
            // Источники клиентов читаются один раз: раньше ClientBootstrap.Load
            // стоял внутри цикла и парсил лок-файл по разу на каждую цель.
            List<ClientSource> clientSources =
                ClientBootstrap.Load(bundleRoot).clients;
            foreach (TargetDefinition definition in Definitions.Where(
                value => edition.Includes(value.Id)
            ))
            {
                TrustedPackage package;
                string state = !trusted.TryGetValue(definition.Id, out package)
                    ? "missing"
                    : (ValidateTrustedPackage(
                        bundleRoot,
                        package,
                        definition.Id,
                        definition.PackageClientId
                    ) ? "accepted" : "tampered");
                string detected = null;
                string clientState = "not_checked";
                ClientSource primarySource = clientSources
                    .FirstOrDefault(source => String.Equals(
                        source.id,
                        definition.SourceClientId,
                        StringComparison.Ordinal
                    ));
                string supported = primarySource == null
                    ? (package == null ? null : package.supported_version)
                    : primarySource.version;
                if (detectClients)
                {
                    ClientDetectionResult detection =
                        definition.DetectsThroughStore
                            ? DetectCodex(bundleRoot, storeRecord)
                            : DetectCli(definition.SourceClientId);
                    detected = detection.version;
                    clientState = detected == null
                        ? "missing"
                        : (state != "accepted"
                            ? "present_unbound"
                            : (definition.DetectsThroughStore
                                ? "ready"
                                : (String.Equals(
                                    detected,
                                    supported,
                                    StringComparison.Ordinal
                                ) ? "ready" : "unsupported")));
                }
                targets.Add(new TargetRow
                {
                    id = definition.Id,
                    display_name = definition.DisplayName,
                    client_id = definition.SourceClientId,
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
                row => edition.Includes(row.Id)
            ).Select(row => row.Id).ToArray();
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
            TargetDefinition definition = Definitions.FirstOrDefault(
                row => String.Equals(
                    row.Id,
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
                    definition.Id,
                    definition.PackageClientId
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
                    "internal_unsigned",
                    StringComparison.Ordinal))
            {
                return ValidateFile(bundleRoot, package.internal_acceptance);
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
}
