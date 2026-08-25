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
    internal sealed class TargetRow
    {
        public string id { get; set; }
        public string display_name { get; set; }
        public string client_id { get; set; }
        public string package_state { get; set; }
        public string supported_version { get; set; }
        public string detected_version { get; set; }
        public string client_state { get; set; }
        public string latest_base_version { get; set; }
        public string latest_base_package_path { get; set; }
        public string latest_base_manifest_path { get; set; }
        public string latest_base_manifest_sha256 { get; set; }
        public List<string> local_exception_paths { get; set; }
        public bool confirm_remove_unknown { get; set; }
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
        public TrustedFile internal_acceptance { get; set; }
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
}
