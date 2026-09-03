using System;
using System.Collections.Generic;
using System.IO;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class LaunchCenterInstallResult
    {
        // INSTALLED — копия обновлена; SKIPPED_SAME_ROOT — комплект запущен из
        // самой копии, обновлены только ярлыки; FAILED — причина в reason.
        public string status;
        public string reason = "";
        public string launcher_root;
        public string executable_path;
        public List<string> copied = new List<string>();
        public string desktop_shortcut;
        public string start_menu_shortcut;
    }

    // Копия центра запуска в профиле пользователя и ярлыки к ней (решение
    // владельца 2026-09-03): после установки центр запуска открывается с
    // рабочего стола и из меню «Пуск», папка комплекта больше не нужна.
    // Копируются только файлы из bundle-manifest.json; SHA-256 EXE сверяется.
    internal static class LaunchCenterInstall
    {
        internal const string ShortcutName = "K7 Launch Center";
        internal const string LauncherArguments = "--launch-center-ui";
        private const string ShortcutDescription = "K7 AI Launch Center";

        internal static LaunchCenterInstallResult Install(
            string bundleRoot,
            string home
        )
        {
            LaunchCenterInstallResult result = new LaunchCenterInstallResult();
            string launcherRoot = Path.Combine(home, ".llm-foundation", "launcher");
            result.launcher_root = launcherRoot;
            try
            {
                // Две формы манифеста: комплект издания (build-edition:
                // products.installer, launch_center_fallback, runtime) и одиночная
                // сборка (build-gui: artifacts с относительными путями, включая
                // engine/…). Копируется объединение объявленных файлов.
                Dictionary<string, object> manifest = ReadManifest(bundleRoot);
                List<string> names = new List<string> { "bundle-manifest.json" };
                string exeName = Nested(manifest, "products", "installer", "file");
                string exeSha = Nested(manifest, "products", "installer", "sha256");
                AddIfPresent(names, bundleRoot, exeName);
                AddIfPresent(names, bundleRoot,
                    Nested(manifest, "launch_center_fallback", "file"));
                AddIfPresent(names, bundleRoot, Nested(manifest, "runtime", "file"));
                object artifactsObject;
                Dictionary<string, object> artifacts =
                    manifest.TryGetValue("artifacts", out artifactsObject)
                        ? artifactsObject as Dictionary<string, object>
                        : null;
                if (artifacts != null)
                {
                    foreach (string key in artifacts.Keys)
                    {
                        string relative = key.Replace('/', Path.DirectorySeparatorChar);
                        AddIfPresent(names, bundleRoot, relative);
                        if (exeName == null &&
                            relative.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
                        {
                            exeName = relative;
                            exeSha = Nested(artifacts, key, "sha256");
                        }
                    }
                }
                if (String.IsNullOrWhiteSpace(exeName))
                {
                    throw new InvalidOperationException(
                        "В bundle-manifest.json нет EXE (products.installer или artifacts)"
                    );
                }
                if (!File.Exists(Path.Combine(bundleRoot, exeName)))
                {
                    throw new InvalidOperationException(
                        "В комплекте нет " + exeName
                    );
                }
                if (SamePath(bundleRoot, launcherRoot))
                {
                    result.status = "SKIPPED_SAME_ROOT";
                }
                else
                {
                    ReplaceLauncher(bundleRoot, launcherRoot, names, exeName, exeSha);
                    result.copied.AddRange(names);
                    result.status = "INSTALLED";
                }
                result.executable_path = Path.Combine(launcherRoot, exeName);
                string desktop = DesktopForHome(home);
                string startMenu = Path.Combine(
                    ClientBootstrap.RoamingApplicationDataForHome(home),
                    "Microsoft",
                    "Windows",
                    "Start Menu",
                    "Programs",
                    "LLM Foundation"
                );
                ClientBootstrap.EnsureSafeDirectory(desktop);
                ClientBootstrap.EnsureSafeDirectory(startMenu);
                result.desktop_shortcut = Path.Combine(desktop, ShortcutName + ".lnk");
                result.start_menu_shortcut = Path.Combine(startMenu, ShortcutName + ".lnk");
                foreach (string shortcut in new[]
                {
                    result.desktop_shortcut,
                    result.start_menu_shortcut
                })
                {
                    ClientBootstrap.WriteShortcut(
                        shortcut,
                        result.executable_path,
                        LauncherArguments,
                        launcherRoot,
                        ShortcutDescription,
                        "Не удалось создать ярлык центра запуска: " + shortcut
                    );
                }
                return result;
            }
            catch (Exception exception)
            {
                result.status = "FAILED";
                result.reason = exception.Message;
                return result;
            }
        }

        private static void ReplaceLauncher(
            string bundleRoot,
            string launcherRoot,
            List<string> names,
            string exeName,
            string exeSha
        )
        {
            string parent = Path.GetDirectoryName(launcherRoot);
            ClientBootstrap.EnsureSafeDirectory(parent);
            SweepLeftovers(parent);
            string staging = launcherRoot + ".install-" + Guid.NewGuid().ToString("N");
            string previous = launcherRoot + ".previous-" + Guid.NewGuid().ToString("N");
            Directory.CreateDirectory(staging);
            try
            {
                foreach (string name in names)
                {
                    string destination = Path.Combine(staging, name);
                    Directory.CreateDirectory(Path.GetDirectoryName(destination));
                    File.Copy(
                        ClientBootstrap.ToExtendedLengthPath(
                            Path.Combine(bundleRoot, name)),
                        ClientBootstrap.ToExtendedLengthPath(destination),
                        true
                    );
                }
                string copiedSha = BundleIntegrity.Sha256(
                    Path.Combine(staging, exeName)
                );
                string expectedSha = String.IsNullOrWhiteSpace(exeSha)
                    ? BundleIntegrity.Sha256(Path.Combine(bundleRoot, exeName))
                    : exeSha;
                if (!String.Equals(copiedSha, expectedSha, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException(
                        "SHA-256 скопированного EXE не совпал с bundle-manifest.json"
                    );
                }
                if (Directory.Exists(launcherRoot))
                {
                    Directory.Move(launcherRoot, previous);
                }
                Directory.Move(staging, launcherRoot);
            }
            catch
            {
                TryDeleteDirectory(staging);
                if (Directory.Exists(previous) && !Directory.Exists(launcherRoot))
                {
                    Directory.Move(previous, launcherRoot);
                }
                throw;
            }
            // Прежняя копия не хранится (решение владельца); занятый EXE открытого
            // центра запуска оставит каталог до следующей установки — не ошибка.
            TryDeleteDirectory(previous);
        }

        private static void SweepLeftovers(string parent)
        {
            if (!Directory.Exists(parent))
            {
                return;
            }
            foreach (string pattern in new[] { "launcher.install-*", "launcher.previous-*" })
            {
                foreach (string leftover in Directory.GetDirectories(parent, pattern))
                {
                    TryDeleteDirectory(leftover);
                }
            }
        }

        private static void TryDeleteDirectory(string path)
        {
            try
            {
                if (Directory.Exists(path))
                {
                    Directory.Delete(path, true);
                }
            }
            catch (IOException)
            {
            }
            catch (UnauthorizedAccessException)
            {
            }
        }

        private static void AddIfPresent(List<string> names, string bundleRoot, string name)
        {
            if (!String.IsNullOrWhiteSpace(name) &&
                !names.Contains(name) &&
                File.Exists(Path.Combine(bundleRoot, name)))
            {
                names.Add(name);
            }
        }

        private static bool SamePath(string left, string right)
        {
            return String.Equals(
                Path.GetFullPath(left).TrimEnd(Path.DirectorySeparatorChar),
                Path.GetFullPath(right).TrimEnd(Path.DirectorySeparatorChar),
                StringComparison.OrdinalIgnoreCase
            );
        }

        // Рабочий стол реального профиля — через известную папку (учитывает
        // перенаправление); для тестового home — <home>\Desktop, как AppData у клиентов.
        private static string DesktopForHome(string home)
        {
            string actualHome = Path.GetFullPath(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile)
            ).TrimEnd(Path.DirectorySeparatorChar);
            string requestedHome = Path.GetFullPath(home)
                .TrimEnd(Path.DirectorySeparatorChar);
            if (String.Equals(actualHome, requestedHome, StringComparison.OrdinalIgnoreCase))
            {
                return Environment.GetFolderPath(
                    Environment.SpecialFolder.DesktopDirectory
                );
            }
            return Path.Combine(home, "Desktop");
        }

        private static Dictionary<string, object> ReadManifest(string bundleRoot)
        {
            string path = Path.Combine(bundleRoot, "bundle-manifest.json");
            if (!File.Exists(path))
            {
                throw new InvalidOperationException(
                    "В комплекте нет bundle-manifest.json"
                );
            }
            return new JavaScriptSerializer()
                .Deserialize<Dictionary<string, object>>(
                    File.ReadAllText(path)
                );
        }

        private static string Nested(Dictionary<string, object> root, params string[] keys)
        {
            object current = root;
            foreach (string key in keys)
            {
                Dictionary<string, object> table = current as Dictionary<string, object>;
                if (table == null || !table.TryGetValue(key, out current) || current == null)
                {
                    return null;
                }
            }
            return current as string;
        }
    }
}
