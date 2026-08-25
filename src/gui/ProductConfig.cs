using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class ProductConfig
    {
        private static readonly string[] ExactProperties = new[]
        {
            "schema_version",
            "chrome_path",
            "chrome_proxy_url",
            "connection_probe_url",
            "singbox_version"
        };

        public int schema_version { get; set; }
        public string chrome_path { get; set; }
        public string chrome_proxy_url { get; set; }
        public string connection_probe_url { get; set; }
        public string singbox_version { get; set; }

        public static ProductConfig LoadEmbedded()
        {
            Stream resource = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream("ProductConfig.json");
            if (resource == null)
            {
                throw new InvalidOperationException(
                    "Embedded product config is missing"
                );
            }

            string json;
            using (resource)
            using (StreamReader reader = new StreamReader(
                resource,
                new UTF8Encoding(false),
                true
            ))
            {
                json = reader.ReadToEnd();
            }

            JavaScriptSerializer serializer = new JavaScriptSerializer();
            Dictionary<string, object> document;
            ProductConfig config;
            try
            {
                document = serializer.Deserialize<
                    Dictionary<string, object>
                >(json);
                config = serializer.Deserialize<ProductConfig>(json);
            }
            catch (Exception exception)
            {
                throw new InvalidOperationException(
                    "Embedded product config is invalid",
                    exception
                );
            }

            if (document == null ||
                !document.Keys.OrderBy(value => value, StringComparer.Ordinal)
                    .SequenceEqual(
                        ExactProperties.OrderBy(
                            value => value,
                            StringComparer.Ordinal
                        ),
                        StringComparer.Ordinal
                    ))
            {
                throw new InvalidOperationException(
                    "Embedded product config properties differ"
                );
            }

            config.Validate();
            return config;
        }

        public void Validate()
        {
            if (schema_version != 1)
            {
                throw new InvalidOperationException(
                    "Product config schema version is invalid"
                );
            }
            if (String.IsNullOrWhiteSpace(chrome_path) ||
                !Path.IsPathRooted(chrome_path) ||
                !chrome_path.EndsWith(
                    "\\chrome.exe",
                    StringComparison.OrdinalIgnoreCase
                ))
            {
                throw new InvalidOperationException(
                    "Product config chrome path is invalid"
                );
            }
            ValidateUrl(
                chrome_proxy_url,
                false,
                "Product config chrome proxy URL is invalid"
            );
            ValidateUrl(
                connection_probe_url,
                true,
                "Product config connection probe URL is invalid"
            );
            if (String.IsNullOrWhiteSpace(singbox_version) ||
                !Regex.IsMatch(singbox_version, "^[0-9]+\\.[0-9]+\\.[0-9]+$"))
            {
                throw new InvalidOperationException(
                    "Product config sing-box version is invalid"
                );
            }
        }

        private static void ValidateUrl(
            string value,
            bool requireHttps,
            string message
        )
        {
            Uri uri;
            if (!Uri.TryCreate(value, UriKind.Absolute, out uri) ||
                uri.UserInfo.Length != 0 ||
                (requireHttps
                    ? uri.Scheme != Uri.UriSchemeHttps
                    : uri.Scheme != Uri.UriSchemeHttp &&
                        uri.Scheme != Uri.UriSchemeHttps))
            {
                throw new InvalidOperationException(message);
            }
        }
    }
}
