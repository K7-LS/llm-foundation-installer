using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows;

[assembly: AssemblyTitle("LLM Foundation Installer")]
[assembly: AssemblyDescription("Verified local installer for native LLM workspaces")]
[assembly: AssemblyCompany("LLM Foundation")]
[assembly: AssemblyProduct("LLM Foundation Installer")]
[assembly: AssemblyCopyright("Copyright 2026")]
[assembly: AssemblyVersion("0.4.3.0")]
[assembly: AssemblyFileVersion("0.4.3.0")]
[assembly: ComVisible(false)]

namespace LlmFoundationInstaller
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            AppContext.SetSwitch(
                "Switch.System.IO.UseLegacyPathHandling",
                false
            );
            AppContext.SetSwitch(
                "Switch.System.IO.BlockLongPaths",
                false
            );
            try
            {
                string bundleRoot = AppDomain.CurrentDomain.BaseDirectory;
                EditionProfile edition = EditionProfile.LoadEmbedded();
                if (args.Length != 0)
                {
                    // Таблица команд: продукт и инструменты — InstallerCommands,
                    // test-only точки — InstallerTestHost (только K7_TEST_HOOKS).
                    int exitCode;
                    if (!InstallerCommands.TryRun(
                            edition,
                            bundleRoot,
                            args,
                            out exitCode))
                    {
                        WriteError("Неподдерживаемая команда");
                        return 2;
                    }
                    if (exitCode != InstallerCommands.ContinueToUi)
                    {
                        return exitCode;
                    }
                }

                PlatformCompatibilityResult currentPlatform =
                    PlatformCompatibility.Inspect();
                if (currentPlatform.status != "READY")
                {
                    MessageBox.Show(
                        currentPlatform.reason,
                        "LLM Foundation — неподдерживаемая система",
                        MessageBoxButton.OK,
                        MessageBoxImage.Error
                    );
                    return 20;
                }
                Application application = new Application();
                application.ShutdownMode = ShutdownMode.OnMainWindowClose;
                Window window = new Window
                {
                    Title = EditionTheme.WindowTitle(edition) + " · v" +
                        BundleIntegrity.ReadBundleVersion(bundleRoot),
                    Width = 1280,
                    Height = 800,
                    MinWidth = 1100,
                    MinHeight = 720,
                    WindowStartupLocation = WindowStartupLocation.CenterScreen,
                    Background = EditionTheme.WindowBackground(edition),
                    Content = InstallerView.Create(bundleRoot)
                };
                application.Run(window);
                return 0;
            }
            catch (Exception exception)
            {
                WriteError(exception.GetType().Name + ": " + exception.Message);
                return 30;
            }
        }

        internal static void WriteOutput(string value)
        {
            WriteStream(Console.OpenStandardOutput(), value);
        }

        internal static void WriteError(string value)
        {
            WriteStream(Console.OpenStandardError(), value);
        }

        private static void WriteStream(Stream stream, string value)
        {
            if (stream == Stream.Null)
            {
                return;
            }
            using (StreamWriter writer = new StreamWriter(
                stream,
                new UTF8Encoding(false),
                4096,
                true
            ))
            {
                writer.WriteLine(value);
                writer.Flush();
            }
        }
    }
}
