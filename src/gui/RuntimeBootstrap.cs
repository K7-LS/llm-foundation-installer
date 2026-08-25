using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class RuntimeSource
    {
        public string id { get; set; }
        public string version { get; set; }
        public string url { get; set; }
        public string sha256 { get; set; }
        public string archive_kind { get; set; }
        public string archive_entry { get; set; }
        public string executable_name { get; set; }
    }

    internal sealed class RuntimeSourceLock
    {
        public int schema_version { get; set; }
        public bool test_only { get; set; }
        public RuntimeSource runtime { get; set; }
    }

    internal sealed class RuntimeBootstrapResult
    {
        public string status { get; set; }
        public string runtime_id { get; set; }
        public string version { get; set; }
        public string executable_path { get; set; }
        public string archive_sha256 { get; set; }
        public string executable_sha256 { get; set; }
        public string reason { get; set; }
    }

    internal static class RuntimeBootstrap
    {
        private const string ResourceName = "RuntimeSources.lock.json";

        public static RuntimeSourceLock Load(string bundleRoot)
        {
            byte[] embedded = ReadResource();
            string external = Path.Combine(
                Path.GetFullPath(bundleRoot),
                "runtime-sources.lock.json"
            );
            if (File.Exists(external) &&
                !FixedTimeEquals(
                    BundleIntegrity.Sha256(external),
                    Sha256(embedded)))
            {
                throw new InvalidOperationException(
                    "Runtime source lock differs from embedded resource"
                );
            }
            RuntimeSourceLock value = new JavaScriptSerializer()
                .Deserialize<RuntimeSourceLock>(
                    new UTF8Encoding(false, true).GetString(embedded)
                );
            Validate(value);
            return value;
        }

        public static RuntimeBootstrapResult InstallFromArchive(
            string bundleRoot,
            string home,
            string archivePath
        )
        {
            RuntimeSource source;
            try
            {
                source = Load(bundleRoot).runtime;
            }
            catch
            {
                return Blocked(null, "RUNTIME_SOURCE_LOCK_INVALID");
            }
            string archive;
            try
            {
                archive = Path.GetFullPath(archivePath);
            }
            catch
            {
                return Blocked(source, "RUNTIME_ARCHIVE_INVALID");
            }
            if (!File.Exists(archive) ||
                IsReparse(archive) ||
                !String.Equals(
                    BundleIntegrity.Sha256(archive),
                    source.sha256,
                    StringComparison.OrdinalIgnoreCase))
            {
                return Blocked(source, "RUNTIME_ARCHIVE_INTEGRITY_FAILED");
            }
            string payloadHash;
            try
            {
                payloadHash = ValidateArchiveAndHashPayload(
                    archive,
                    source
                );
            }
            catch (UnsafeArchiveEntryException)
            {
                return Blocked(
                    source,
                    "RUNTIME_ARCHIVE_ENTRY_UNSAFE"
                );
            }
            catch
            {
                return Blocked(source, "RUNTIME_ARCHIVE_INVALID");
            }

            string parent = Path.Combine(
                Path.GetFullPath(home),
                ".llm-foundation",
                "runtimes",
                source.id
            );
            string final = Path.Combine(parent, source.version);
            if (Directory.Exists(final))
            {
                RuntimeBootstrapResult existing = Verify(
                    bundleRoot,
                    home
                );
                return existing.status == "VERIFIED"
                    ? existing
                    : Blocked(
                        source,
                        "RUNTIME_ALREADY_PRESENT_INVALID"
                    );
            }
            Directory.CreateDirectory(parent);
            string staging = Path.Combine(
                parent,
                "." + source.version + ".install-" +
                    Guid.NewGuid().ToString("N")
            );
            try
            {
                Directory.CreateDirectory(staging);
                string cachedArchive = Path.Combine(
                    staging,
                    "source.zip"
                );
                File.Copy(archive, cachedArchive, false);
                string executable = Path.Combine(
                    staging,
                    source.executable_name
                );
                using (ZipArchive package = ZipFile.OpenRead(archive))
                {
                    ZipArchiveEntry entry = package.Entries.Single(
                        candidate => String.Equals(
                            Normalize(candidate.FullName),
                            source.archive_entry,
                            StringComparison.Ordinal
                        )
                    );
                    using (Stream input = entry.Open())
                    using (FileStream output = new FileStream(
                        executable,
                        FileMode.CreateNew,
                        FileAccess.Write,
                        FileShare.None
                    ))
                    {
                        input.CopyTo(output);
                        output.Flush(true);
                    }
                }
                if (!String.Equals(
                        BundleIntegrity.Sha256(executable),
                        payloadHash,
                        StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException();
                }
                Directory.Move(staging, final);
                return new RuntimeBootstrapResult
                {
                    status = "INSTALLED",
                    runtime_id = source.id,
                    version = source.version,
                    executable_path = Path.Combine(
                        final,
                        source.executable_name
                    ),
                    archive_sha256 = source.sha256,
                    executable_sha256 = payloadHash,
                    reason = null
                };
            }
            catch
            {
                try
                {
                    if (Directory.Exists(staging))
                    {
                        Directory.Delete(staging, true);
                    }
                }
                catch
                {
                }
                return Blocked(source, "RUNTIME_INSTALL_FAILED");
            }
        }

        public static RuntimeBootstrapResult EnsureInstalled(
            string bundleRoot,
            string home
        )
        {
            RuntimeBootstrapResult existing = Verify(
                bundleRoot,
                home
            );
            if (existing.status == "VERIFIED")
            {
                return existing;
            }
            if (existing.reason != "RUNTIME_NOT_INSTALLED")
            {
                return existing;
            }
            RuntimeSource source;
            try
            {
                source = Load(bundleRoot).runtime;
            }
            catch
            {
                return Blocked(null, "RUNTIME_SOURCE_LOCK_INVALID");
            }
            string archiveName = Path.GetFileName(
                new Uri(
                    source.url,
                    UriKind.Absolute
                ).AbsolutePath
            );
            if (String.IsNullOrWhiteSpace(archiveName))
            {
                return Blocked(
                    source,
                    "RUNTIME_BUNDLE_ARCHIVE_MISSING"
                );
            }
            string archive = Path.Combine(
                Path.GetFullPath(bundleRoot),
                archiveName
            );
            if (!File.Exists(archive) || IsReparse(archive))
            {
                return Blocked(
                    source,
                    "RUNTIME_BUNDLE_ARCHIVE_MISSING"
                );
            }
            RuntimeBootstrapResult installed = InstallFromArchive(
                bundleRoot,
                home,
                archive
            );
            if (installed.status != "INSTALLED" &&
                installed.status != "VERIFIED")
            {
                return installed;
            }
            return Verify(bundleRoot, home);
        }

        public static string FailureReason(
            RuntimeBootstrapResult result
        )
        {
            return result != null &&
                !String.IsNullOrWhiteSpace(result.reason)
                ? result.reason
                : "RUNTIME_NOT_VERIFIED";
        }

        public static RuntimeBootstrapResult Verify(
            string bundleRoot,
            string home
        )
        {
            RuntimeSource source;
            try
            {
                source = Load(bundleRoot).runtime;
            }
            catch
            {
                return Blocked(null, "RUNTIME_SOURCE_LOCK_INVALID");
            }
            string root = Path.Combine(
                Path.GetFullPath(home),
                ".llm-foundation",
                "runtimes",
                source.id,
                source.version
            );
            string archive = Path.Combine(root, "source.zip");
            string executable = Path.Combine(
                root,
                source.executable_name
            );
            if (!Directory.Exists(root) ||
                !File.Exists(archive) ||
                !File.Exists(executable))
            {
                return Blocked(source, "RUNTIME_NOT_INSTALLED");
            }
            try
            {
                if (IsReparse(root) ||
                    IsReparse(archive) ||
                    IsReparse(executable) ||
                    Directory.GetDirectories(root).Length != 0 ||
                    !new HashSet<string>(
                        Directory.GetFiles(root)
                            .Select(Path.GetFileName),
                        StringComparer.Ordinal
                    ).SetEquals(new[]
                    {
                        "source.zip",
                        source.executable_name
                    }))
                {
                    return Blocked(
                        source,
                        "RUNTIME_LAYOUT_INVALID"
                    );
                }
                if (!String.Equals(
                        BundleIntegrity.Sha256(archive),
                        source.sha256,
                        StringComparison.OrdinalIgnoreCase))
                {
                    return Blocked(
                        source,
                        "RUNTIME_ARCHIVE_INTEGRITY_FAILED"
                    );
                }
                string payloadHash = ValidateArchiveAndHashPayload(
                    archive,
                    source
                );
                string executableHash = BundleIntegrity.Sha256(
                    executable
                );
                if (!String.Equals(
                        payloadHash,
                        executableHash,
                        StringComparison.OrdinalIgnoreCase))
                {
                    return Blocked(
                        source,
                        "RUNTIME_EXECUTABLE_INTEGRITY_FAILED"
                    );
                }
                return new RuntimeBootstrapResult
                {
                    status = "VERIFIED",
                    runtime_id = source.id,
                    version = source.version,
                    executable_path = executable,
                    archive_sha256 = source.sha256,
                    executable_sha256 = executableHash,
                    reason = null
                };
            }
            catch
            {
                return Blocked(source, "RUNTIME_VERIFY_FAILED");
            }
        }

        private static string ValidateArchiveAndHashPayload(
            string archive,
            RuntimeSource source
        )
        {
            if (!IsSafeEntry(source.archive_entry))
            {
                throw new UnsafeArchiveEntryException();
            }
            using (ZipArchive package = ZipFile.OpenRead(archive))
            {
                ZipArchiveEntry payload = null;
                foreach (ZipArchiveEntry entry in package.Entries)
                {
                    string normalized = Normalize(entry.FullName);
                    if (!IsSafeEntry(normalized))
                    {
                        throw new UnsafeArchiveEntryException();
                    }
                    if (String.Equals(
                            normalized,
                            source.archive_entry,
                            StringComparison.Ordinal))
                    {
                        if (payload != null ||
                            normalized.EndsWith(
                                "/",
                                StringComparison.Ordinal))
                        {
                            throw new InvalidOperationException();
                        }
                        payload = entry;
                    }
                }
                if (payload == null)
                {
                    throw new InvalidOperationException();
                }
                using (Stream input = payload.Open())
                {
                    return Sha256(input);
                }
            }
        }

        private static void Validate(RuntimeSourceLock value)
        {
            string pinnedVersion =
                ProductConfig.LoadEmbedded().singbox_version;
            if (value == null ||
                value.schema_version != 1 ||
                value.runtime == null ||
                value.runtime.id != "sing-box" ||
                value.runtime.version != pinnedVersion ||
                value.runtime.archive_kind != "zip" ||
                value.runtime.executable_name != "sing-box.exe" ||
                String.IsNullOrWhiteSpace(
                    value.runtime.archive_entry
                ) ||
                value.runtime.sha256 == null ||
                value.runtime.sha256.Length != 64)
            {
                throw new InvalidOperationException();
            }
            Uri uri;
            if (!Uri.TryCreate(
                    value.runtime.url,
                    UriKind.Absolute,
                    out uri) ||
                uri.UserInfo.Length != 0)
            {
                throw new InvalidOperationException();
            }
            if (value.test_only)
            {
                if (!uri.IsLoopback ||
                    (uri.Scheme != Uri.UriSchemeHttp &&
                        uri.Scheme != Uri.UriSchemeHttps))
                {
                    throw new InvalidOperationException();
                }
            }
            else if (uri.Scheme != Uri.UriSchemeHttps ||
                uri.Host != "github.com" ||
                uri.AbsolutePath !=
                    "/SagerNet/sing-box/releases/download/v" +
                    pinnedVersion + "/sing-box-" + pinnedVersion +
                    "-windows-amd64.zip")
            {
                throw new InvalidOperationException();
            }
        }

        private static bool IsSafeEntry(string value)
        {
            if (String.IsNullOrWhiteSpace(value) ||
                value.StartsWith("/", StringComparison.Ordinal) ||
                value.Contains(":") ||
                value.Contains("\\"))
            {
                return false;
            }
            string normalized = value.EndsWith(
                "/",
                StringComparison.Ordinal)
                ? value.Substring(0, value.Length - 1)
                : value;
            return normalized.Length > 0 &&
                !normalized.Contains("//") &&
                normalized.Split('/').All(segment =>
                segment.Length > 0 &&
                segment != "." &&
                segment != ".." &&
                segment.IndexOfAny(
                    Path.GetInvalidFileNameChars()
                ) < 0
            );
        }

        private static string Normalize(string value)
        {
            return value == null ? "" : value.Replace('\\', '/');
        }

        private static bool IsReparse(string path)
        {
            return (File.GetAttributes(path) &
                FileAttributes.ReparsePoint) != 0;
        }

        private static byte[] ReadResource()
        {
            using (Stream stream = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream(ResourceName))
            {
                if (stream == null)
                {
                    throw new InvalidOperationException();
                }
                using (MemoryStream buffer = new MemoryStream())
                {
                    stream.CopyTo(buffer);
                    return buffer.ToArray();
                }
            }
        }

        private static string Sha256(byte[] value)
        {
            using (SHA256 algorithm = SHA256.Create())
            {
                return String.Concat(
                    algorithm.ComputeHash(value).Select(
                        item => item.ToString("x2")
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
                        item => item.ToString("x2")
                    )
                );
            }
        }

        private static bool FixedTimeEquals(
            string left,
            string right
        )
        {
            if (left == null || right == null ||
                left.Length != right.Length)
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

        private static RuntimeBootstrapResult Blocked(
            RuntimeSource source,
            string reason
        )
        {
            return new RuntimeBootstrapResult
            {
                status = "BLOCKED",
                runtime_id = source == null ? null : source.id,
                version = source == null ? null : source.version,
                executable_path = null,
                archive_sha256 = source == null
                    ? null
                    : source.sha256,
                executable_sha256 = null,
                reason = reason
            };
        }

        private sealed class UnsafeArchiveEntryException : Exception
        {
        }
    }
}
