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
    internal static class BundleIntegrity
    {
        private sealed class FoundationExtraFile
        {
            public string relative_path { get; set; }
            public string resource_name { get; set; }
            public string sha256 { get; set; }
            public long bytes { get; set; }
        }

        public static void WriteFoundationExtras(string engineRoot)
        {
            byte[] indexBytes = ReadResourceBytes(
                "FoundationEngine.extra-files.json"
            );
            FoundationExtraFile[] records = new JavaScriptSerializer()
                .Deserialize<FoundationExtraFile[]>(
                    Encoding.UTF8.GetString(indexBytes)
                );
            foreach (FoundationExtraFile record in records ?? new FoundationExtraFile[0])
            {
                if (record == null || String.IsNullOrWhiteSpace(record.relative_path) ||
                    Path.IsPathRooted(record.relative_path) ||
                    record.relative_path.Contains("..") ||
                    String.IsNullOrWhiteSpace(record.resource_name))
                {
                    throw new InvalidOperationException(
                        "Embedded Foundation extra path is invalid"
                    );
                }
                byte[] payload = ReadResourceBytes(record.resource_name);
                if (payload.LongLength != record.bytes || !String.Equals(
                        Sha256(payload), record.sha256,
                        StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException(
                        "Embedded Foundation extra bytes differ"
                    );
                }
                string destination = Path.GetFullPath(Path.Combine(
                    engineRoot,
                    record.relative_path.Replace('/', Path.DirectorySeparatorChar)
                ));
                string prefix = Path.GetFullPath(engineRoot) + Path.DirectorySeparatorChar;
                if (!destination.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException(
                        "Embedded Foundation extra escapes engine root"
                    );
                }
                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                File.WriteAllBytes(destination, payload);
            }
        }

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
}
