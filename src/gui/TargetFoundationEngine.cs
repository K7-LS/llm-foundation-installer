using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class TargetEngineBinding
    {
        public string target { get; set; }
        public string engine_version { get; set; }
        public string engine_manifest_sha256 { get; set; }
    }

    // Engines are part of the trusted, immutable embedded target package. An
    // external update may supply base data, but never selects executable code.
    internal static class TargetFoundationEngine
    {
        private const int MaximumResourceBytes = 256 * 1024 * 1024;
        private const int MaximumManifestBytes = 8 * 1024 * 1024;
        private static readonly string[] CommonFiles = {
            "VERSION", "engine-manifest.json", "foundation.ps1",
            "shared-tools.lock.json",
            "shared-tools/officecli/k7-officecli-pdf.exe",
            "shared-tools/officecli/officecli-command-policy.json",
            "shared-tools/officecli/officecli-shim.exe",
            "shared-tools/officecli/officecli.exe",
            "shared-tools/officecli/officecli_csv_batch.py"
        };

        public static TargetEngineBinding ReadContract(TrustedPackage package)
        {
            Dictionary<string, object> release = ReadRelease(package);
            return Binding(release);
        }

        public static TargetEngineBinding Validate(TrustedPackage package)
        {
            TargetEngineBinding binding;
            VerifiedFiles(package, out binding);
            return binding;
        }

        public static TargetEngineBinding Extract(TrustedPackage package, string engineRoot)
        {
            TargetEngineBinding binding;
            // Complete all byte and path checks before writing any executable.
            Dictionary<string, byte[]> files = VerifiedFiles(package, out binding);
            string root = Path.GetFullPath(engineRoot);
            for (DirectoryInfo parent = new DirectoryInfo(root); parent != null; parent = parent.Parent)
            {
                if (parent.Exists && (parent.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw Invalid("Engine extraction root contains a reparse point");
            }
            if (Directory.Exists(root) && Directory.EnumerateFileSystemEntries(root).Any())
                throw Invalid("Engine extraction root must be empty");
            Directory.CreateDirectory(root);
            foreach (KeyValuePair<string, byte[]> item in files)
            {
                string destination = Path.Combine(root, item.Key.Replace('/', Path.DirectorySeparatorChar));
                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                using (FileStream output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    output.Write(item.Value, 0, item.Value.Length);
            }
            return binding;
        }

        private static Dictionary<string, object> ReadRelease(TrustedPackage package)
        {
            if (package == null || package.asset == null || package.release_manifest == null)
                throw Invalid("Target engine has no trusted package metadata");
            Dictionary<string, object> release = Json(ReadResource(package.release_manifest, MaximumManifestBytes));
            TargetEngineBinding binding = Binding(release);
            Dictionary<string, object> asset = Object(release, "asset");
            if (binding.target != package.target || Number(release, "schema_version") != 1 ||
                Text(asset, "name") != Path.GetFileName((package.asset.relative_path ?? "").Replace('/', Path.DirectorySeparatorChar)) ||
                Text(asset, "sha256") != package.asset.sha256 || Number(asset, "bytes") != package.asset.bytes)
                throw Invalid("Target release and trusted package binding differ");
            RequireHash(Text(release, "package_manifest_sha256"));
            return release;
        }

        private static TargetEngineBinding Binding(Dictionary<string, object> release)
        {
            string target = Text(release, "target");
            string version = Text(release, "foundation_engine_version");
            Prefix(target);
            AllowedFiles(version);
            string hash = Text(release, "foundation_engine_manifest_sha256");
            RequireHash(hash);
            return new TargetEngineBinding { target = target, engine_version = version, engine_manifest_sha256 = hash };
        }

        private static Dictionary<string, byte[]> VerifiedFiles(TrustedPackage package, out TargetEngineBinding binding)
        {
            Dictionary<string, object> release = ReadRelease(package);
            binding = Binding(release);
            string engineBase = Prefix(binding.target);
            string prefix = engineBase + binding.engine_version + "/";
            HashSet<string> allowed = AllowedFiles(binding.engine_version);
            byte[] archiveBytes = ReadResource(package.asset, MaximumResourceBytes);
            Dictionary<string, byte[]> files = new Dictionary<string, byte[]>(StringComparer.Ordinal);
            try
            {
                using (MemoryStream memory = new MemoryStream(archiveBytes, false))
                using (ZipArchive archive = new ZipArchive(memory, ZipArchiveMode.Read))
                {
                    Dictionary<string, ZipArchiveEntry> entries = new Dictionary<string, ZipArchiveEntry>(StringComparer.Ordinal);
                    HashSet<string> paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                    Dictionary<string, string> pathSpellings = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                    foreach (ZipArchiveEntry entry in archive.Entries)
                    {
                        string path = entry.FullName;
                        bool directory = path.EndsWith("/", StringComparison.Ordinal);
                        SafePath(directory ? path.Substring(0, path.Length - 1) : path);
                        if (!paths.Add(path.TrimEnd('/')))
                            throw Invalid("Duplicate or case-colliding archive path");
                        string partial = "";
                        foreach (string segment in path.TrimEnd('/').Split('/'))
                        {
                            partial = partial.Length == 0 ? segment : partial + "/" + segment;
                            string previous;
                            if (pathSpellings.TryGetValue(partial, out previous) && previous != partial)
                                throw Invalid("Case-colliding archive directory");
                            pathSpellings[partial] = partial;
                        }
                        int unixType = (entry.ExternalAttributes >> 16) & 0xf000;
                        if ((entry.ExternalAttributes & (int)FileAttributes.ReparsePoint) != 0 ||
                            (unixType != 0 && unixType != 0x8000 && unixType != 0x4000) ||
                            (unixType == 0x4000 && !directory) || (unixType == 0x8000 && directory))
                            throw Invalid("Archive entry is not a regular file or directory");
                        if (!directory) entries.Add(path, entry);
                        if (path.StartsWith(engineBase, StringComparison.OrdinalIgnoreCase) &&
                            !path.StartsWith(prefix, StringComparison.Ordinal))
                            throw Invalid("Archive contains an unexpected target engine path");
                        if (!directory && path.StartsWith(prefix, StringComparison.Ordinal) &&
                            !allowed.Contains(path.Substring(prefix.Length)))
                            throw Invalid("Archive contains an unapproved engine file");
                    }
                    HashSet<string> filePaths = new HashSet<string>(entries.Keys, StringComparer.OrdinalIgnoreCase);
                    foreach (string path in entries.Keys)
                    {
                        int slash = path.IndexOf('/');
                        while (slash >= 0)
                        {
                            if (filePaths.Contains(path.Substring(0, slash)))
                                throw Invalid("Archive file shadows a directory");
                            slash = path.IndexOf('/', slash + 1);
                        }
                    }
                    ZipArchiveEntry manifestEntry;
                    if (!entries.TryGetValue("package-manifest.json", out manifestEntry))
                        throw Invalid("Embedded package manifest is missing");
                    byte[] manifestBytes = ReadEntry(manifestEntry, MaximumManifestBytes);
                    if (Hash(manifestBytes) != Text(release, "package_manifest_sha256"))
                        throw Invalid("Embedded package manifest hash differs from release");
                    Dictionary<string, object> manifest = Json(manifestBytes);
                    if (Number(manifest, "schema_version") != 1 || Text(manifest, "target") != binding.target ||
                        Text(manifest, "foundation_engine_version") != binding.engine_version ||
                        Text(manifest, "version") != Text(release, "version"))
                        throw Invalid("Embedded package engine contract differs from release");
                    object recordsObject;
                    if (!manifest.TryGetValue("files", out recordsObject) || !(recordsObject is IList))
                        throw Invalid("Embedded package file manifest is missing");
                    HashSet<string> manifestPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                    foreach (object value in (IList)recordsObject)
                    {
                        Dictionary<string, object> record = value as Dictionary<string, object>;
                        if (record == null) throw Invalid("Invalid package file record");
                        string path = Text(record, "path");
                        SafePath(path);
                        if (!manifestPaths.Add(path)) throw Invalid("Duplicate or case-colliding manifest path");
                        if (!path.StartsWith(engineBase, StringComparison.OrdinalIgnoreCase)) continue;
                        if (!path.StartsWith(prefix, StringComparison.Ordinal) || !allowed.Contains(path.Substring(prefix.Length)))
                            throw Invalid("Package manifest contains an unapproved engine path");
                        ZipArchiveEntry entry;
                        if (!entries.TryGetValue(path, out entry)) throw Invalid("Engine file is missing from archive");
                        long expectedBytes = Number(record, "bytes");
                        if (expectedBytes < 0 || expectedBytes > MaximumResourceBytes || entry.Length != expectedBytes)
                            throw Invalid("Engine file size differs from package manifest");
                        byte[] payload = ReadEntry(entry, (int)expectedBytes);
                        if (Hash(payload) != Text(record, "sha256")) throw Invalid("Engine file hash differs from package manifest");
                        files.Add(path.Substring(prefix.Length), payload);
                    }
                    if (files.Count != allowed.Count || !allowed.SetEquals(files.Keys))
                        throw Invalid("Embedded target engine is incomplete");
                }
            }
            catch (InvalidDataException error) { throw Invalid("Embedded target archive is invalid", error); }
            if (Hash(files["engine-manifest.json"]) != binding.engine_manifest_sha256 ||
                Encoding.UTF8.GetString(files["VERSION"]).Trim() != binding.engine_version)
                throw Invalid("Embedded engine release binding differs");
            Dictionary<string, object> engine = Json(files["engine-manifest.json"]);
            if (Number(engine, "schema_version") != 1 || Number(engine, "protocol_version") != 1 ||
                Text(engine, "engine_version") != binding.engine_version || Text(engine, "network") != "offline" ||
                Text(engine, "foundation_ps1_sha256") != Hash(files["foundation.ps1"]))
                throw Invalid("Embedded engine executable contract differs");
            return files;
        }

        private static string Prefix(string target)
        {
            switch (target)
            {
                case "codex": return ".codex/base/foundation/";
                case "claude": return ".claude/base/foundation/";
                case "opencode": return ".config/opencode/base/foundation/";
                default: throw Invalid("Unsupported target engine");
            }
        }

        private static HashSet<string> AllowedFiles(string version)
        {
            HashSet<string> files = new HashSet<string>(CommonFiles, StringComparer.Ordinal);
            if (version == "0.5.12")
                files.UnionWith(new[] { "foundation-toml.ps1", "vendor/tomlyn/LICENSE.txt", "vendor/tomlyn/Tomlyn.dll", "vendor/tomlyn/provenance.json" });
            else if (version != "0.5.10") throw Invalid("Unsupported embedded engine version");
            return files;
        }

        private static void SafePath(string path)
        {
            if (String.IsNullOrEmpty(path) || path.IndexOfAny(new[] { '\\', ':', '\0' }) >= 0 || Path.IsPathRooted(path))
                throw Invalid("Unsafe archive or manifest path");
            foreach (string segment in path.Split('/'))
            {
                if (String.IsNullOrEmpty(segment) || segment == "." || segment == ".." ||
                    segment.EndsWith(".", StringComparison.Ordinal) || segment.EndsWith(" ", StringComparison.Ordinal) ||
                    segment.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
                    throw Invalid("Unsafe archive or manifest path segment");
                string stem = segment.Split('.')[0].ToUpperInvariant();
                if (stem == "CON" || stem == "PRN" || stem == "AUX" || stem == "NUL" ||
                    (stem.Length == 4 && (stem.StartsWith("COM", StringComparison.Ordinal) || stem.StartsWith("LPT", StringComparison.Ordinal)) &&
                    stem[3] >= '1' && stem[3] <= '9'))
                    throw Invalid("Reserved Windows archive path");
            }
        }

        private static byte[] ReadResource(TrustedFile file, int maximum)
        {
            if (file == null || String.IsNullOrWhiteSpace(file.resource_name) || file.bytes < 1 || file.bytes > maximum)
                throw Invalid("Invalid trusted resource metadata");
            RequireHash(file.sha256);
            using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(file.resource_name))
            {
                if (stream == null || stream.Length != file.bytes) throw Invalid("Trusted embedded resource is missing or has changed size");
                byte[] payload = ReadBytes(stream, (int)file.bytes);
                if (Hash(payload) != file.sha256) throw Invalid("Trusted embedded resource hash differs");
                return payload;
            }
        }

        private static byte[] ReadEntry(ZipArchiveEntry entry, int maximum)
        {
            if (entry.Length < 0 || entry.Length > maximum) throw Invalid("Embedded entry exceeds its size limit");
            using (Stream stream = entry.Open()) return ReadBytes(stream, (int)entry.Length);
        }

        private static byte[] ReadBytes(Stream stream, int size)
        {
            byte[] payload = new byte[size];
            int offset = 0;
            while (offset < size)
            {
                int read = stream.Read(payload, offset, size - offset);
                if (read == 0) throw Invalid("Embedded payload is truncated");
                offset += read;
            }
            if (stream.ReadByte() != -1) throw Invalid("Embedded payload exceeds declared size");
            return payload;
        }

        private static Dictionary<string, object> Json(byte[] payload)
        {
            try
            {
                JavaScriptSerializer serializer = new JavaScriptSerializer { MaxJsonLength = MaximumManifestBytes };
                Dictionary<string, object> result = serializer.Deserialize<Dictionary<string, object>>(new UTF8Encoding(false, true).GetString(payload));
                if (result == null) throw Invalid("Embedded JSON object is missing");
                return result;
            }
            catch (ArgumentException error) { throw Invalid("Embedded JSON is invalid", error); }
        }

        private static Dictionary<string, object> Object(Dictionary<string, object> value, string key)
        {
            object result;
            if (!value.TryGetValue(key, out result) || !(result is Dictionary<string, object>))
                throw Invalid("Embedded contract object is missing: " + key);
            return (Dictionary<string, object>)result;
        }

        private static string Text(Dictionary<string, object> value, string key)
        {
            object result;
            if (!value.TryGetValue(key, out result) || !(result is string) || String.IsNullOrWhiteSpace((string)result))
                throw Invalid("Embedded contract text is missing: " + key);
            return (string)result;
        }

        private static long Number(Dictionary<string, object> value, string key)
        {
            object result;
            if (!value.TryGetValue(key, out result) || !(result is int || result is long))
                throw Invalid("Embedded contract integer is missing: " + key);
            return Convert.ToInt64(result, CultureInfo.InvariantCulture);
        }

        private static void RequireHash(string hash)
        {
            if (hash == null || hash.Length != 64 || hash.Any(c => !(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f')))
                throw Invalid("Embedded contract SHA-256 is invalid");
        }

        private static string Hash(byte[] payload)
        {
            using (SHA256 algorithm = SHA256.Create())
                return String.Concat(algorithm.ComputeHash(payload).Select(value => value.ToString("x2", CultureInfo.InvariantCulture)));
        }

        private static InvalidOperationException Invalid(string message, Exception inner = null)
        {
            return new InvalidOperationException(message, inner);
        }
    }
}
