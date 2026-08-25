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
    internal static class ConnectionProbe
    {
        public static ConnectionProbeResult Run(
            string home,
            string endpointValue
        )
        {
            Uri endpoint;
            if (!Uri.TryCreate(
                    endpointValue,
                    UriKind.Absolute,
                    out endpoint
                ) ||
                (endpoint.Scheme != Uri.UriSchemeHttp &&
                 endpoint.Scheme != Uri.UriSchemeHttps) ||
                !IsApprovedEndpoint(endpoint))
            {
                throw new ArgumentException(
                    "Connection probe endpoint is not approved"
                );
            }
            string curl = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.System),
                "curl.exe"
            );
            if (!File.Exists(curl))
            {
                throw new InvalidOperationException(
                    "Windows curl.exe is required for the connection probe"
                );
            }
            ProcessStartInfo start = new ProcessStartInfo
            {
                FileName = curl,
                Arguments =
                    "--fail --location --silent --show-error " +
                    "--max-time 15 --output NUL " +
                    QuoteArgument(endpoint.AbsoluteUri),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            ConnectionProcessState connection =
                ConnectionStore.ConfigureProcessEnvironment(home, start);
            Stopwatch clock = Stopwatch.StartNew();
            using (Process process = Process.Start(start))
            {
                if (process == null)
                {
                    throw new InvalidOperationException(
                        "Connection probe could not start"
                    );
                }
                if (!process.WaitForExit(20000))
                {
                    try
                    {
                        process.Kill();
                    }
                    catch
                    {
                    }
                    clock.Stop();
                    return Result(
                        "BLOCKED",
                        connection,
                        endpoint.Host,
                        clock,
                        "timeout"
                    );
                }
                clock.Stop();
                if (process.ExitCode != 0)
                {
                    return Result(
                        "BLOCKED",
                        connection,
                        endpoint.Host,
                        clock,
                        "curl-exit-" + process.ExitCode.ToString(
                            CultureInfo.InvariantCulture
                        )
                    );
                }
            }
            return Result(
                "READY",
                connection,
                endpoint.Host,
                clock,
                null
            );
        }

        private static ConnectionProbeResult Result(
            string status,
            ConnectionProcessState connection,
            string endpointHost,
            Stopwatch clock,
            string error
        )
        {
            return new ConnectionProbeResult
            {
                status = status,
                mode = connection.mode,
                uses_proxy = connection.uses_proxy,
                proxy_type = connection.proxy_type,
                endpoint_host = endpointHost,
                elapsed_ms = (int)Math.Min(
                    Int32.MaxValue,
                    clock.ElapsedMilliseconds
                ),
                error = error
            };
        }

        private static bool IsApprovedEndpoint(Uri endpoint)
        {
            if (String.Equals(
                    endpoint.Host,
                    "api.github.com",
                    StringComparison.OrdinalIgnoreCase
                ))
            {
                return endpoint.Scheme == Uri.UriSchemeHttps;
            }
            return String.Equals(
                       endpoint.Host,
                       "localhost",
                       StringComparison.OrdinalIgnoreCase
                   ) ||
                   endpoint.Host == "127.0.0.1" ||
                   endpoint.Host == "::1";
        }

        private static string QuoteArgument(string value)
        {
            return "\"" + value.Replace("\\", "\\\\")
                .Replace("\"", "\\\"") + "\"";
        }
    }
}
