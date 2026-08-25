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
}
