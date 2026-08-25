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
    internal static class ChromeProxyLauncher
    {

        public static void Bind(UserControl view)
        {
            Button button = view.FindName("OpenChromeProxy") as Button;
            if (button == null)
            {
                return;
            }
            button.Click += delegate
            {
                try
                {
                    Process.Start(CreateStartInfo());
                }
                catch (Exception exception)
                {
                    MessageBox.Show(
                        "Chrome не запущен: " + exception.Message,
                        "K-7 AI — Chrome с прокси",
                        MessageBoxButton.OK,
                        MessageBoxImage.Warning
                    );
                }
            };
        }

        public static Dictionary<string, object> Describe()
        {
            ProcessStartInfo start = CreateStartInfo();
            return new Dictionary<string, object>
            {
                { "executable", start.FileName },
                { "arguments", start.Arguments },
                { "use_shell_execute", start.UseShellExecute }
            };
        }

        private static ProcessStartInfo CreateStartInfo()
        {
            ProductConfig config = ProductConfig.LoadEmbedded();
            if (!File.Exists(config.chrome_path))
            {
                throw new FileNotFoundException(
                    "Google Chrome не найден в стандартной папке.",
                    config.chrome_path
                );
            }
            string profile = Path.Combine(
                Environment.GetFolderPath(
                    Environment.SpecialFolder.UserProfile
                ),
                "chrome-proxy"
            );
            return new ProcessStartInfo
            {
                FileName = config.chrome_path,
                Arguments =
                    "--proxy-server=\"" + config.chrome_proxy_url + "\" " +
                    "--user-data-dir=\"" +
                    profile.Replace("\"", "\\\"") + "\"",
                UseShellExecute = false
            };
        }
    }
}
