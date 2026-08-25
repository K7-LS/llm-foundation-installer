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
    internal sealed class RuntimePayload : IDisposable
    {
        public string Root { get; private set; }
        public string EnginePath { get; private set; }
        public string PackagePath { get; private set; }
        public string ReleaseManifestPath { get; private set; }
        public string ReleaseManifestSha256 { get; private set; }

        public static RuntimePayload Prepare(
            TrustedPackage package,
            string externalPackagePath
        )
        {
            int protocol;
            if (!BundleIntegrity.ValidateEngine("", out protocol))
            {
                throw new InvalidOperationException(
                    "Embedded Foundation engine is invalid"
                );
            }
            if (String.IsNullOrWhiteSpace(externalPackagePath) &&
                !BundleIntegrity.ValidateResource(
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
                BundleIntegrity.WriteFoundationExtras(engineRoot);
                string packagePath;
                if (String.IsNullOrWhiteSpace(externalPackagePath))
                {
                    string packageName = Path.GetFileName(
                        package.asset.relative_path.Replace(
                            '/',
                            Path.DirectorySeparatorChar
                        )
                    );
                    if (String.IsNullOrWhiteSpace(packageName))
                    {
                        throw new InvalidOperationException(
                            "Embedded target package name is invalid"
                        );
                    }
                    packagePath = Path.Combine(root, packageName);
                    BundleIntegrity.WriteResource(
                        package.asset.resource_name,
                        packagePath
                    );
                }
                else
                {
                    packagePath = Path.GetFullPath(externalPackagePath);
                    if (!File.Exists(packagePath) ||
                        (File.GetAttributes(packagePath) &
                            FileAttributes.ReparsePoint) != 0)
                    {
                        throw new InvalidOperationException(
                            "Latest base package is missing or unsafe"
                        );
                    }
                }
                string releaseManifestPath = Path.Combine(
                    root,
                    "release-manifest.json"
                );
                if (!BundleIntegrity.ValidateResource(
                        package.release_manifest.resource_name,
                        package.release_manifest.sha256,
                        package.release_manifest.bytes
                    ))
                {
                    throw new InvalidOperationException(
                        "Embedded release manifest is invalid"
                    );
                }
                BundleIntegrity.WriteResource(
                    package.release_manifest.resource_name,
                    releaseManifestPath
                );
                return new RuntimePayload
                {
                    Root = root,
                    EnginePath = engine,
                    PackagePath = packagePath,
                    ReleaseManifestPath = releaseManifestPath,
                    ReleaseManifestSha256 = package.release_manifest.sha256
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
}
