using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class GitHubReleaseAsset
    {
        public string name { get; set; }
        public long size { get; set; }
        public string digest { get; set; }
        public string browser_download_url { get; set; }
    }

    internal sealed class GitHubLatestRelease
    {
        public string tag_name { get; set; }
        public bool draft { get; set; }
        public bool prerelease { get; set; }
        public bool immutable { get; set; }
        public List<GitHubReleaseAsset> assets { get; set; }
    }

    internal sealed class StableBaseAsset
    {
        public string name { get; set; }
        public string sha256 { get; set; }
        public long bytes { get; set; }
    }

    internal sealed class StableBaseRequirements
    {
        public bool immutable_release { get; set; }
        public bool release_attestation { get; set; }
    }

    internal sealed class StableBaseSource
    {
        public string repository { get; set; }
    }

    internal sealed class StableBaseManifest
    {
        public int schema_version { get; set; }
        public string target { get; set; }
        public string version { get; set; }
        public string tag { get; set; }
        public string channel { get; set; }
        public string foundation_engine_version { get; set; }
        public string foundation_engine_manifest_sha256 { get; set; }
        public StableBaseSource source { get; set; }
        public StableBaseAsset asset { get; set; }
        public StableBaseRequirements requires { get; set; }
    }

    internal sealed class BaseReleaseResolution
    {
        public string status { get; set; }
        public string target { get; set; }
        public string version { get; set; }
        public string tag { get; set; }
        public string package_path { get; set; }
        public string release_manifest_path { get; set; }
        public string release_manifest_sha256 { get; set; }
        public bool used_embedded_fallback { get; set; }
        public string reason { get; set; }
    }

    internal static class BaseReleaseUpdater
    {
        private sealed class SourceContract
        {
            public string target;
            public string repository;
            public string tagPrefix;
            public string assetPrefix;
        }

        private static readonly Regex Version = new Regex(
            @"\A(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\z",
            RegexOptions.CultureInvariant
        );

        private static readonly Dictionary<string, SourceContract> Sources =
            new Dictionary<string, SourceContract>(StringComparer.Ordinal)
            {
                {
                    "codex",
                    new SourceContract
                    {
                        target = "codex",
                        repository = "daniileliseev1337/codex-base",
                        tagPrefix = "codex-v",
                        assetPrefix = "codex-base-"
                    }
                },
                {
                    "claude",
                    new SourceContract
                    {
                        target = "claude",
                        repository = "daniileliseev1337/claude-base-v2",
                        tagPrefix = "claude-v",
                        assetPrefix = "claude-base-"
                    }
                },
                {
                    "opencode",
                    new SourceContract
                    {
                        target = "opencode",
                        repository = "daniileliseev1337/opencode-base",
                        tagPrefix = "opencode-v",
                        assetPrefix = "opencode-base-"
                    }
                }
            };

        public static BaseReleaseResolution ResolveLatestOrFallback(
            string bundleRoot,
            string home,
            string target
        )
        {
            try
            {
                return ResolveLatest(bundleRoot, home, target);
            }
            catch (Exception exception)
            {
                return new BaseReleaseResolution
                {
                    status = "EMBEDDED_FALLBACK",
                    target = target,
                    version = null,
                    tag = null,
                    package_path = null,
                    release_manifest_path = null,
                    release_manifest_sha256 = null,
                    used_embedded_fallback = true,
                    reason = FirstLine(exception.Message)
                };
            }
        }

        public static BaseReleaseResolution ResolveLatest(
            string bundleRoot,
            string home,
            string target
        )
        {
            SourceContract source = GetSource(target);
            string endpoint = "https://api.github.com/repos/" +
                source.repository + "/releases/latest";
            ClientSourceLock clientLock = ClientBootstrap.Load(bundleRoot);
            string testEndpoint = Environment.GetEnvironmentVariable(
                "K7_BASE_RELEASE_TEST_API_URL_" + target.ToUpperInvariant()
            );
            if (!String.IsNullOrWhiteSpace(testEndpoint))
            {
                if (!clientLock.test_only || !IsLoopbackHttp(testEndpoint))
                {
                    throw new InvalidOperationException(
                        "Latest-base test endpoint is forbidden"
                    );
                }
                endpoint = testEndpoint;
            }

            string cacheRoot = Path.Combine(
                Path.GetFullPath(home),
                ".llm-foundation",
                "cache",
                "bases",
                target
            );
            EnsureSafeDirectory(cacheRoot);
            byte[] releaseBytes = DownloadBytes(
                home,
                endpoint,
                2 * 1024 * 1024
            );
            GitHubLatestRelease release = Deserialize<GitHubLatestRelease>(
                releaseBytes,
                "Latest release metadata is invalid"
            );
            if (release == null || release.draft || release.prerelease ||
                !release.immutable || release.assets == null ||
                String.IsNullOrWhiteSpace(release.tag_name) ||
                !release.tag_name.StartsWith(
                    source.tagPrefix,
                    StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "Latest release is not immutable stable"
                );
            }
            string version = release.tag_name.Substring(
                source.tagPrefix.Length
            );
            if (!Version.IsMatch(version))
            {
                throw new InvalidOperationException(
                    "Latest base version is invalid"
                );
            }
            string packageName = source.assetPrefix + version + ".zip";
            GitHubReleaseAsset packageAsset = FindAsset(
                release,
                packageName
            );
            GitHubReleaseAsset manifestAsset = FindAsset(
                release,
                "release-manifest.json"
            );
            ValidateApiAsset(packageAsset, false, clientLock.test_only);
            ValidateApiAsset(manifestAsset, true, clientLock.test_only);

            string versionRoot = Path.Combine(cacheRoot, version);
            EnsureSafeDirectory(versionRoot);
            string manifestPath = Path.Combine(
                versionRoot,
                "release-manifest.json"
            );
            byte[] manifestBytes = DownloadAndCache(
                home,
                manifestAsset,
                manifestPath,
                1024 * 1024
            );
            StableBaseManifest manifest = Deserialize<StableBaseManifest>(
                manifestBytes,
                "Stable base manifest is invalid"
            );
            ValidateManifest(source, release, manifest, packageAsset);

            string packagePath = Path.Combine(versionRoot, packageName);
            DownloadAndCache(
                home,
                packageAsset,
                packagePath,
                1024L * 1024L * 1024L
            );
            return new BaseReleaseResolution
            {
                status = "LATEST",
                target = target,
                version = version,
                tag = release.tag_name,
                package_path = packagePath,
                release_manifest_path = manifestPath,
                release_manifest_sha256 = BundleIntegrity.Sha256(
                    manifestPath
                ),
                used_embedded_fallback = false,
                reason = null
            };
        }

        private static void ValidateManifest(
            SourceContract source,
            GitHubLatestRelease release,
            StableBaseManifest manifest,
            GitHubReleaseAsset packageAsset
        )
        {
            string expectedVersion = release.tag_name.Substring(
                source.tagPrefix.Length
            );
            string engineVersion = new UTF8Encoding(false, true).GetString(
                ReadResourceBytes("FoundationEngine.VERSION")
            ).Trim();
            string engineManifestHash = Sha256(
                ReadResourceBytes("FoundationEngine.engine-manifest.json")
            );
            string expectedRepository = "https://github.com/" +
                source.repository;
            if (manifest == null || manifest.schema_version != 1 ||
                !String.Equals(manifest.target, source.target,
                    StringComparison.Ordinal) ||
                !String.Equals(manifest.version, expectedVersion,
                    StringComparison.Ordinal) ||
                !String.Equals(manifest.tag, release.tag_name,
                    StringComparison.Ordinal) ||
                !String.Equals(manifest.channel, "stable",
                    StringComparison.Ordinal) ||
                !String.Equals(manifest.foundation_engine_version,
                    engineVersion, StringComparison.Ordinal) ||
                !String.Equals(manifest.foundation_engine_manifest_sha256,
                    engineManifestHash, StringComparison.OrdinalIgnoreCase) ||
                manifest.source == null ||
                !String.Equals(manifest.source.repository,
                    expectedRepository, StringComparison.Ordinal) ||
                manifest.asset == null ||
                !String.Equals(manifest.asset.name, packageAsset.name,
                    StringComparison.Ordinal) ||
                !String.Equals(manifest.asset.sha256,
                    DigestValue(packageAsset.digest),
                    StringComparison.OrdinalIgnoreCase) ||
                manifest.asset.bytes != packageAsset.size ||
                manifest.requires == null ||
                !manifest.requires.immutable_release ||
                !manifest.requires.release_attestation)
            {
                throw new InvalidOperationException(
                    "Latest base release contract is incompatible"
                );
            }
        }

        private static GitHubReleaseAsset FindAsset(
            GitHubLatestRelease release,
            string name
        )
        {
            List<GitHubReleaseAsset> matches = release.assets.Where(asset =>
                asset != null && String.Equals(
                    asset.name,
                    name,
                    StringComparison.Ordinal
                )
            ).ToList();
            if (matches.Count != 1)
            {
                throw new InvalidOperationException(
                    "Latest release asset inventory differs: " + name
                );
            }
            return matches[0];
        }

        private static void ValidateApiAsset(
            GitHubReleaseAsset asset,
            bool manifest,
            bool allowLoopback
        )
        {
            Uri uri;
            long maximum = manifest ? 1024L * 1024L : 1024L * 1024L * 1024L;
            if (asset == null || String.IsNullOrWhiteSpace(asset.name) ||
                asset.size < 1 || asset.size > maximum ||
                !Regex.IsMatch(DigestValue(asset.digest),
                    "\\A[0-9a-f]{64}\\z", RegexOptions.CultureInvariant) ||
                !Uri.TryCreate(asset.browser_download_url,
                    UriKind.Absolute, out uri) ||
                ((!String.Equals(uri.Scheme, "https",
                      StringComparison.OrdinalIgnoreCase) ||
                  !String.Equals(uri.Host, "github.com",
                      StringComparison.OrdinalIgnoreCase)) &&
                 (!allowLoopback ||
                  !IsLoopbackHttp(asset.browser_download_url))))
            {
                throw new InvalidOperationException(
                    "Latest release asset metadata is invalid"
                );
            }
        }

        private static byte[] DownloadAndCache(
            string home,
            GitHubReleaseAsset asset,
            string destination,
            long maximumBytes
        )
        {
            string expected = DigestValue(asset.digest);
            if (File.Exists(destination) &&
                new FileInfo(destination).Length == asset.size &&
                String.Equals(BundleIntegrity.Sha256(destination), expected,
                    StringComparison.OrdinalIgnoreCase))
            {
                return File.ReadAllBytes(destination);
            }
            byte[] payload = DownloadBytes(
                home,
                asset.browser_download_url,
                maximumBytes
            );
            if (payload.LongLength != asset.size ||
                !String.Equals(Sha256(payload), expected,
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Latest release asset hash or size differs"
                );
            }
            WriteAtomic(destination, payload);
            return payload;
        }

        private static byte[] DownloadBytes(
            string home,
            string url,
            long maximumBytes
        )
        {
            string temporary = Path.Combine(
                Path.GetTempPath(),
                "k7-base-release-" + Guid.NewGuid().ToString("N") + ".tmp"
            );
            try
            {
                string curl = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.System),
                    "curl.exe"
                );
                ProcessStartInfo start = new ProcessStartInfo
                {
                    FileName = curl,
                    Arguments = "--fail --location --silent --show-error " +
                        "--max-time 300 --header \"Accept: application/vnd.github+json\" " +
                        "--header \"User-Agent: K7-Foundation-Installer\" " +
                        "--output " + Quote(temporary) + " " + Quote(url),
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8
                };
                ConnectionProcessState route =
                    ConnectionStore.ConfigureProcessEnvironment(home, start);
                using (Process process = Process.Start(start))
                {
                    if (process == null)
                    {
                        throw new InvalidOperationException(
                            "Latest release download did not start"
                        );
                    }
                    process.StandardOutput.ReadToEnd();
                    string error = process.StandardError.ReadToEnd();
                    if (!process.WaitForExit(310000) || process.ExitCode != 0)
                    {
                        throw new InvalidOperationException(
                            "Latest release download failed: " + FirstLine(error)
                        );
                    }
                }
                FileInfo file = new FileInfo(temporary);
                if (!file.Exists || file.Length < 1 || file.Length > maximumBytes)
                {
                    throw new InvalidOperationException(
                        "Latest release response is outside limits"
                    );
                }
                return File.ReadAllBytes(temporary);
            }
            finally
            {
                if (File.Exists(temporary))
                {
                    File.Delete(temporary);
                }
            }
        }

        private static void WriteAtomic(string destination, byte[] payload)
        {
            string parent = Path.GetDirectoryName(destination);
            EnsureSafeDirectory(parent);
            string temporary = destination + ".part-" +
                Guid.NewGuid().ToString("N");
            try
            {
                File.WriteAllBytes(temporary, payload);
                if (File.Exists(destination))
                {
                    File.Replace(temporary, destination, null, true);
                }
                else
                {
                    File.Move(temporary, destination);
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

        private static void EnsureSafeDirectory(string path)
        {
            Directory.CreateDirectory(path);
            DirectoryInfo current = new DirectoryInfo(Path.GetFullPath(path));
            while (current != null)
            {
                if ((current.Attributes & FileAttributes.ReparsePoint) != 0)
                {
                    throw new InvalidOperationException(
                        "Latest base cache cannot use a reparse point"
                    );
                }
                current = current.Parent;
            }
        }

        private static SourceContract GetSource(string target)
        {
            SourceContract source;
            if (!Sources.TryGetValue(target ?? "", out source))
            {
                throw new InvalidOperationException(
                    "Unknown latest-base target"
                );
            }
            return source;
        }

        private static T Deserialize<T>(byte[] payload, string error)
        {
            try
            {
                return new JavaScriptSerializer
                {
                    MaxJsonLength = 2 * 1024 * 1024
                }.Deserialize<T>(new UTF8Encoding(false, true).GetString(payload));
            }
            catch (Exception exception)
            {
                throw new InvalidOperationException(error, exception);
            }
        }

        private static string DigestValue(string digest)
        {
            return !String.IsNullOrWhiteSpace(digest) &&
                digest.StartsWith("sha256:", StringComparison.Ordinal)
                ? digest.Substring(7)
                : "";
        }

        private static bool IsLoopbackHttp(string value)
        {
            Uri uri;
            return Uri.TryCreate(value, UriKind.Absolute, out uri) &&
                String.Equals(uri.Scheme, "http",
                    StringComparison.OrdinalIgnoreCase) &&
                (String.Equals(uri.Host, "127.0.0.1",
                    StringComparison.OrdinalIgnoreCase) ||
                 String.Equals(uri.Host, "localhost",
                    StringComparison.OrdinalIgnoreCase));
        }

        private static byte[] ReadResourceBytes(string name)
        {
            Stream resource = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream(name);
            if (resource == null)
            {
                throw new InvalidOperationException(
                    "Foundation engine resource is missing"
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
            using (System.Security.Cryptography.SHA256 algorithm =
                System.Security.Cryptography.SHA256.Create())
            {
                return String.Concat(algorithm.ComputeHash(payload).Select(
                    value => value.ToString("x2", CultureInfo.InvariantCulture)
                ));
            }
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static string FirstLine(string value)
        {
            return String.IsNullOrWhiteSpace(value)
                ? "latest release unavailable"
                : value.Split(new[] { '\r', '\n' },
                    StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ??
                    "latest release unavailable";
        }
    }
}
