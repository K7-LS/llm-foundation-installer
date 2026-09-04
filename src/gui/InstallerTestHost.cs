#if K7_TEST_HOOKS
// Тестовый хост: test-only CLI-точки инсталлятора (этап 4 плана
// переработки). Файл целиком компилируется только с K7_TEST_HOOKS
// (tools/build-gui.ps1 -TestHooks); релизный EXE этих команд не содержит.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using static LlmFoundationInstaller.Program;

namespace LlmFoundationInstaller
{
    internal static partial class InstallerCommands
    {
        static partial void RegisterTestHost()
        {
            Register("--chrome-proxy-json", "test", 0, 0, ChromeProxyJson);
            Register("--client-plan-json", "test", 2, 2, ClientPlanJson);
            Register("--client-plan-store-record-json", "test", 3, 3, ClientPlanStoreRecordJson);
            Register("--client-sources-json", "test", 0, 0, ClientSourcesJson);
            Register("--connection-environment-json", "test", 1, 1, ConnectionEnvironmentJson);
            Register("--connection-json", "test", 1, 1, ConnectionJson);
            Register("--connection-status-texts-json", "test", 0, 0, ConnectionStatusTextsJson);
            Register("--describe-edition", "test", 0, 0, DescribeEdition);
            Register("--download-client-json", "test", 3, 3, DownloadClientJson);
            Register("--evaluate-platform-json", "test", 3, 3, EvaluatePlatformJson);
            Register("--install-client-json", "test", 3, 3, InstallClientJson);
            Register("--install-launch-center-json", "test", 1, 1, InstallLaunchCenterJson);
            Register("--install-runtime-json", "test", 2, 2, InstallRuntimeJson);
            Register("--latest-base-json", "test", 2, 2, LatestBaseJson);
            Register("--launch-routes-json", "test", 1, 1, LaunchRoutesJson);
            Register("--launch-center-view-json", "test", 0, 0, LaunchCenterViewJson);
            Register("--launch-target-json", "test", 3, 3, LaunchTargetJson);
            Register("--preflight-json", "test", 0, 0, PreflightJson);
            Register("--preflight-store-record-json", "test", 1, 1, PreflightStoreRecordJson);
            Register("--probe-connection-json", "test", 2, 2, ProbeConnectionJson);
            Register("--render-guide-preview", "test", 1, 1, RenderGuidePreview);
            Register("--render-preview", "test", 1, 1, RenderPreview);
            Register("--reset-managed-route-json", "test", 1, 1, ResetManagedRouteJson);
            Register("--resolve-store-launch-target-record-json", "test", 2, 2, ResolveStoreLaunchTargetRecordJson);
            Register("--resolve-vscode-mutating-record-json", "test", 3, 3, ResolveVsCodeMutatingRecordJson);
            Register("--resolve-vscode-record-json", "test", 2, 2, ResolveVsCodeRecordJson);
            Register("--save-connection-json", "test", 2, 2, SaveConnectionJson);
            Register("--save-launch-route-json", "test", 3, 3, SaveLaunchRouteJson);
            Register("--system-proxy-test-json", "test", 4, 5, SystemProxyTestJson);
            Register("--target-client-plan-json", "test", 2, 2, TargetClientPlanJson);
            Register("--test-appx-singbox-json", "test", 5, 6, TestAppxSingBoxJson);
            Register("--test-connection-route-json", "test", 3, 3, TestConnectionRouteJson);
            Register("--test-singbox-route-json", "test", 3, 3, TestSingBoxRouteJson);
            Register("--test-singbox-session-json", "test", 3, 3, TestSingBoxSessionJson);
            Register("--ui-connection-state-json", "test", 1, 1, UiConnectionStateJson);
            Register("--ui-guide-selection-json", "test", 1, 1, UiSelectionJson);
            Register("--ui-launch-selection-json", "test", 1, 1, UiSelectionJson);
            Register("--ui-selection-json", "test", 1, 1, UiSelectionJson);
            Register("--ui-stored-launch-route-json", "test", 2, 2, UiStoredLaunchRouteJson);
            Register("--ui-vscode-resolution-json", "test", 2, 2, UiVsCodeResolutionJson);
            Register("--validate-store-record-json", "test", 2, 2, ValidateStoreRecordJson);
            Register("--verify-runtime-json", "test", 1, 1, VerifyRuntimeJson);
            Register("--write-install-report-json", "test", 1, 1, WriteInstallReportJson);
            Register("--write-singbox-config-test-json", "test", 5, 5, WriteSingBoxConfigTestJson);
        }

        private static int ChromeProxyJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                ChromeProxyLauncher.Describe()
            ));
            return 0;
        }

        private static int ClientPlanJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            ClientPlanResult plan = ClientBootstrap.Plan(
                bundleRoot,
                args[1],
                args[2]
            );
            WriteOutput(new JavaScriptSerializer().Serialize(plan));
            return plan.status == "BLOCKED_NO_DOWNGRADE"
                ? 20
                : 0;
        }

        private static int ClientPlanStoreRecordJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            try
            {
                StoreClientResult store =
                    ClientBootstrap.ValidateStoreRecord(
                        bundleRoot,
                        args[2],
                        args[3]
                    );
                ClientPlanResult plan = ClientBootstrap.Plan(
                    bundleRoot,
                    args[1],
                    args[2],
                    store
                );
                WriteOutput(new JavaScriptSerializer().Serialize(plan));
                return plan.status == "BLOCKED_NO_DOWNGRADE"
                    ? 20
                    : 0;
            }
            catch (InvalidOperationException exception)
            {
                WriteError(exception.Message);
                return 2;
            }
        }

        private static int ClientSourcesJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                ClientBootstrap.Describe(bundleRoot)
            ));
            return 0;
        }

        private static int ConnectionEnvironmentJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            ProcessStartInfo child = new ProcessStartInfo
            {
                FileName = "cmd.exe",
                UseShellExecute = false
            };
            WriteOutput(new JavaScriptSerializer().Serialize(
                ConnectionStore.ConfigureProcessEnvironment(
                    args[1],
                    child
                )
            ));
            return 0;
        }

        private static int ConnectionJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                ConnectionStore.Load(args[1])
            ));
            return 0;
        }

        private static int ConnectionStatusTextsJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                ConnectionStatusModel.CanonicalCatalog()
            ));
            return 0;
        }

        private static int DescribeEdition(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                edition
            ));
            return 0;
        }

        private static int DownloadClientJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                ClientBootstrap.Download(
                    bundleRoot,
                    args[1],
                    args[2],
                    args[3]
                )
            ));
            return 0;
        }

        private static int EvaluatePlatformJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            int build;
            if (!Int32.TryParse(
                    args[3],
                    NumberStyles.None,
                    CultureInfo.InvariantCulture,
                    out build) ||
                build < 0)
            {
                WriteError("Windows build is invalid");
                return 2;
            }
            PlatformCompatibilityResult platform =
                PlatformCompatibility.Evaluate(
                    args[1],
                    args[2],
                    build
                );
            WriteOutput(new JavaScriptSerializer().Serialize(
                platform
            ));
            return platform.status == "READY" ? 0 : 20;
        }

        private static int InstallClientJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            object installed = ClientBootstrap.Install(
                bundleRoot,
                args[1],
                args[2],
                args[3]
            );
            WriteOutput(new JavaScriptSerializer().Serialize(
                installed
            ));
            ClientPlanResult blocked = installed as ClientPlanResult;
            return blocked != null &&
                blocked.status == "BLOCKED_NO_DOWNGRADE"
                ? 20
                : 0;
        }

        // Тестовая точка фичи «центр запуска в профиле»: тот же модуль, что зовёт
        // продуктовый поток установки после успешного doctor.
        private static int InstallLaunchCenterJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            LaunchCenterInstallResult installed = LaunchCenterInstall.Install(
                bundleRoot,
                args[1]
            );
            WriteOutput(new JavaScriptSerializer().Serialize(installed));
            return installed.status == "FAILED" ? 20 : 0;
        }

        private static int InstallRuntimeJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            RuntimeBootstrapResult installed =
                RuntimeBootstrap.InstallFromArchive(
                    bundleRoot,
                    args[1],
                    args[2]
                );
            WriteOutput(new JavaScriptSerializer().Serialize(
                installed
            ));
            return installed.status == "INSTALLED" ||
                installed.status == "VERIFIED"
                ? 0
                : 20;
        }

        private static int LatestBaseJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            BaseReleaseResolution latest =
                BaseReleaseUpdater.ResolveLatestOrFallback(
                    bundleRoot,
                    args[2],
                    args[1]
                );
            WriteOutput(new JavaScriptSerializer().Serialize(latest));
            return latest.status == "LATEST" ? 0 : 20;
        }

        private static int LaunchRoutesJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                LaunchRouteStore.Load(args[1])
            ));
            return 0;
        }

        private static int LaunchTargetJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            LaunchTargetResolution resolution =
                LaunchTargetResolver.Resolve(
                    edition,
                    bundleRoot,
                    args[1],
                    args[2]
                );
            string testRegistrySubkey =
                Environment.GetEnvironmentVariable(
                    "K7_SYSTEM_PROXY_TEST_SUBKEY"
                );
            LauncherSessionResult launched;
            if (!String.IsNullOrWhiteSpace(
                testRegistrySubkey))
            {
                if (!ClientBootstrap.Load(bundleRoot).test_only ||
                    !SystemProxyLease
                        .IsAllowedTestRegistrySubkey(
                            testRegistrySubkey))
                {
                    WriteError(
                        "Тестовый реестр запуска запрещён для production"
                    );
                    return 2;
                }
                launched = ClientLauncher.StartAndWaitForTest(
                    resolution,
                    args[3],
                    bundleRoot,
                    args[1],
                    testRegistrySubkey
                );
            }
            else
            {
                launched = ClientLauncher.StartAndWait(
                    resolution,
                    args[3],
                    bundleRoot,
                    args[1]
                );
            }
            WriteOutput(new JavaScriptSerializer().Serialize(
                launched
            ));
            return launched.status == "PASS" ? 0 : 20;
        }

        private static int PreflightJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                ProductCatalog.Inspect(bundleRoot, true)
            ));
            return 0;
        }

        private static int PreflightStoreRecordJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            try
            {
                StoreClientResult store =
                    ClientBootstrap.ValidateStoreRecord(
                        bundleRoot,
                        "codex-desktop",
                        args[1]
                    );
                WriteOutput(new JavaScriptSerializer().Serialize(
                    ProductCatalog.Inspect(bundleRoot, true, store)
                ));
                return 0;
            }
            catch (InvalidOperationException exception)
            {
                WriteError(exception.Message);
                return 2;
            }
        }

        private static int ProbeConnectionJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            ConnectionProbeResult result = ConnectionProbe.Run(
                args[1],
                args[2]
            );
            WriteOutput(new JavaScriptSerializer().Serialize(result));
            return result.status == "READY" ? 0 : 20;
        }

        private static int RenderGuidePreview(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            Application previewApp = new Application();
            UserControl preview =
                OperatorGuideDashboard.Create(bundleRoot);
            InstallerView.RenderPreview(
                preview,
                args[1],
                1440,
                900
            );
            previewApp.Shutdown();
            return 0;
        }

        // Путь ярлыка «K7 Launch Center»: единый EXE (роль Installer) с
        // --launch-center-ui. Команда переключает роль, затем окно строится
        // тем же вызовом, что и в InstallerApp.Main; отчёт — какой вид загружен.
        private static int LaunchCenterViewJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            int exitCode;
            if (!InstallerCommands.TryRun(
                    edition,
                    bundleRoot,
                    new[] { "--launch-center-ui" },
                    out exitCode) ||
                exitCode != InstallerCommands.ContinueToUi)
            {
                WriteError("--launch-center-ui did not continue to UI");
                return 20;
            }
            Application previewApp = new Application();
            UserControl view = InstallerView.Create(
                bundleRoot,
                edition,
                false
            );
            WriteOutput(new JavaScriptSerializer().Serialize(
                new Dictionary<string, object>
                {
                    { "product_role", edition.product_role },
                    { "view", view.Tag as string },
                    { "window_title", EditionTheme.WindowTitle(edition) }
                }
            ));
            previewApp.Shutdown();
            return 0;
        }

        private static int RenderPreview(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            Application previewApp = new Application();
            UserControl preview = InstallerView.Create(
                bundleRoot,
                false
            );
            InstallerView.RenderPreview(preview, args[1], 1440, 900);
            previewApp.Shutdown();
            return 0;
        }

        private static int ResetManagedRouteJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            SingBoxSessionResult reset =
                ClientLauncher.ResetManagedRoute(args[1]);
            WriteOutput(new JavaScriptSerializer().Serialize(
                reset
            ));
            return reset.status == "PASS" ? 0 : 20;
        }

        private static int ResolveStoreLaunchTargetRecordJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            LaunchTargetResolution resolution =
                LaunchTargetResolver.ResolveStoreRecord(
                    edition,
                    bundleRoot,
                    args[1],
                    args[2]
                );
            WriteOutput(new JavaScriptSerializer().Serialize(
                resolution
            ));
            return resolution.status == "RESOLVED" ? 0 : 20;
        }

        private static int ResolveVsCodeMutatingRecordJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            LaunchTargetResolution resolution =
                VsCodeIntegration.ResolveMutatingTestRecord(
                    bundleRoot,
                    args[1],
                    args[2],
                    args[3]
                );
            WriteOutput(new JavaScriptSerializer().Serialize(
                resolution
            ));
            return resolution.status == "RESOLVED" ? 0 : 20;
        }

        private static int ResolveVsCodeRecordJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            LaunchTargetResolution resolution =
                VsCodeIntegration.ResolveTestRecord(
                    bundleRoot,
                    args[1],
                    args[2]
                );
            WriteOutput(new JavaScriptSerializer().Serialize(
                resolution
            ));
            return resolution.status == "RESOLVED" ? 0 : 20;
        }

        private static int SaveConnectionJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            ConnectionProfile profile = ConnectionStore
                .ParseAndValidate(args[2]);
            string password = profile.mode == "Proxy" &&
                profile.proxy.auth.mode == "UsernamePassword"
                ? Console.In.ReadLine()
                : null;
            ConnectionStateResult saved = ConnectionStore.Save(
                args[1],
                profile,
                password
            );
            WriteOutput(
                new JavaScriptSerializer().Serialize(saved)
            );
            return 0;
        }

        private static int SaveLaunchRouteJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                LaunchRouteStore.Save(
                    args[1],
                    args[2],
                    args[3]
                )
            ));
            return 0;
        }

        private static int SystemProxyTestJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            if (!ClientBootstrap.Load(bundleRoot).test_only ||
                !SystemProxyLease.IsAllowedTestRegistrySubkey(
                    args[3]))
            {
                WriteError(
                    "Тест системного proxy запрещён для production"
                );
                return 2;
            }
            int port;
            if (!Int32.TryParse(args[4], out port))
            {
                WriteError("Некорректный локальный порт");
                return 2;
            }
            ProxyRecoveryResult proxyResult;
            if (args[1] == "normal-cycle" &&
                args.Length == 5)
            {
                proxyResult = SystemProxyLease.Acquire(
                    args[2],
                    port,
                    args[3]
                );
                if (proxyResult.status == "ACQUIRED")
                {
                    proxyResult =
                        SystemProxyLease.StopActiveRoute();
                }
            }
            else if (args[1] == "hold" &&
                args.Length == 6)
            {
                proxyResult = SystemProxyLease.Acquire(
                    args[2],
                    port,
                    args[3]
                );
                if (proxyResult.status == "ACQUIRED")
                {
                    DateTime deadline = DateTime.UtcNow
                        .AddSeconds(60);
                    while (!File.Exists(args[5]) &&
                        DateTime.UtcNow < deadline)
                    {
                        Thread.Sleep(50);
                    }
                    proxyResult =
                        SystemProxyLease.StopActiveRoute();
                }
            }
            else if (args[1] == "acquire" &&
                args.Length == 5)
            {
                proxyResult = SystemProxyLease.Acquire(
                    args[2],
                    port,
                    args[3]
                );
            }
            else if (args[1] == "reset" &&
                args.Length == 5)
            {
                proxyResult = SystemProxyLease
                    .ResetPreservingExternalChanges(
                        args[2],
                        args[3]
                    );
            }
            else
            {
                WriteError(
                    "Неподдерживаемый тест системного proxy"
                );
                return 2;
            }
            WriteOutput(new JavaScriptSerializer().Serialize(
                proxyResult
            ));
            return proxyResult.cleanup_verified &&
                (proxyResult.status == "ACQUIRED" ||
                    proxyResult.status == "RESTORED")
                ? 0
                : 20;
        }

        private static int TargetClientPlanJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            TargetClientPlanResult plan =
                ClientBootstrap.PlanTarget(
                    bundleRoot,
                    args[1],
                    args[2]
                );
            WriteOutput(new JavaScriptSerializer().Serialize(plan));
            return plan.status == "BLOCKED" ? 20 : 0;
        }

        private static int TestAppxSingBoxJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            if (!ClientBootstrap.Load(bundleRoot).test_only ||
                !SystemProxyLease.IsAllowedTestRegistrySubkey(
                    args[3]))
            {
                WriteError(
                    "Тест AppX-маршрута запрещён для production"
                );
                return 2;
            }
            string expectedFixture = Path.GetFullPath(
                Path.Combine(
                    Environment.GetFolderPath(
                        Environment.SpecialFolder.Windows
                    ),
                    "System32",
                    "cmd.exe"
                )
            );
            string fixture = Path.GetFullPath(args[4]);
            if (!String.Equals(
                    fixture,
                    expectedFixture,
                    StringComparison.OrdinalIgnoreCase) ||
                !File.Exists(fixture))
            {
                WriteError(
                    "Разрешён только системный подписанный fixture"
                );
                return 2;
            }
            bool activationFailure =
                args[5] == "activation-failure";
            bool routeConflict =
                args[5] == "route-conflict";
            if (!activationFailure &&
                !routeConflict &&
                args[5] != "success")
            {
                WriteError("Некорректный режим AppX-теста");
                return 2;
            }
            Thread stopThread = null;
            if (args.Length == 7)
            {
                string stopSignal = Path.GetFullPath(args[6]);
                stopThread = new Thread(
                    new ThreadStart(delegate
                    {
                        DateTime deadline = DateTime.UtcNow
                            .AddSeconds(60);
                        while (!File.Exists(stopSignal) &&
                            DateTime.UtcNow < deadline)
                        {
                            Thread.Sleep(50);
                        }
                        if (File.Exists(stopSignal))
                        {
                            ClientLauncher.StopActiveRoute();
                        }
                    })
                );
                stopThread.IsBackground = true;
                stopThread.Start();
            }
            LaunchTargetResolution testTarget =
                new LaunchTargetResolution
                {
                    status = "RESOLVED",
                    target_id = "codex-desktop",
                    client_id = "codex-desktop",
                    role = "desktop",
                    launch_mode = "appx",
                    executable_path = fixture,
                    sha256 = BundleIntegrity.Sha256(fixture),
                    activation_id = "K7AITest!App",
                    package_full_name = "K7AITest"
                };
            LauncherSessionResult appx = routeConflict
                ? ClientLauncher.StartAppxWithRouteConflictForTest(
                    testTarget,
                    args[2],
                    bundleRoot,
                    args[1],
                    args[3],
                    Environment.GetEnvironmentVariable(
                        "K7_APPX_FIXTURE_ARGS"
                    )
                )
                : ClientLauncher.StartAppxThroughSingBoxForTest(
                    testTarget,
                    args[2],
                    bundleRoot,
                    args[1],
                    args[3],
                    Environment.GetEnvironmentVariable(
                        "K7_APPX_FIXTURE_ARGS"
                    ),
                    activationFailure
                );
            if (stopThread != null)
            {
                stopThread.Join(5000);
            }
            WriteOutput(new JavaScriptSerializer().Serialize(
                appx
            ));
            return appx.status == "PASS" ? 0 : 20;
        }

        private static int TestConnectionRouteJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            object route = ConnectionUi.TestConnection(
                bundleRoot,
                args[1],
                args[2],
                args[3]
            );
            WriteOutput(new JavaScriptSerializer().Serialize(
                route
            ));
            SingBoxSessionResult singBox =
                route as SingBoxSessionResult;
            ConnectionProbeResult connection =
                route as ConnectionProbeResult;
            return (singBox != null &&
                    singBox.status == "PASS") ||
                (connection != null &&
                    connection.status == "READY")
                ? 0
                : 20;
        }

        private static int TestSingBoxRouteJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            SingBoxSessionResult route =
                SingBoxSession.TestRoute(
                    bundleRoot,
                    args[1],
                    args[2],
                    args[3]
                );
            WriteOutput(new JavaScriptSerializer().Serialize(
                route
            ));
            return route.status == "PASS" ? 0 : 20;
        }

        private static int TestSingBoxSessionJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            SingBoxSessionResult session =
                SingBoxSession.TestCycle(
                    bundleRoot,
                    args[1],
                    args[2],
                    args[3]
                );
            WriteOutput(new JavaScriptSerializer().Serialize(
                session
            ));
            return session.status == "PASS" ? 0 : 20;
        }

        private static int UiConnectionStateJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            Application stateApp = new Application();
            UserControl stateView = InstallerView.Create(
                bundleRoot,
                false
            );
            if (!ConnectionUi.ApplyRoute(stateView, args[1]))
            {
                WriteError("Неподдерживаемый маршрут подключения");
                stateApp.Shutdown();
                return 2;
            }
            WriteOutput(new JavaScriptSerializer().Serialize(
                ConnectionUi.DescribeState(stateView)
            ));
            stateApp.Shutdown();
            return 0;
        }

        private static int UiSelectionJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            if (edition.product_role != "LaunchCenter")
            {
                WriteError(
                    "Команда выбора доступна только центру запуска"
                );
                return 2;
            }
            Application selectionApp = new Application();
            UserControl selectionView = InstallerView.Create(
                bundleRoot,
                false
            );
            bool selected =
                args[0] == "--ui-guide-selection-json"
                    ? OperatorGuideDashboard.ApplyHostSelection(
                        selectionView,
                        args[1]
                    )
                    : LaunchCenterActions.SelectTarget(
                        selectionView,
                        args[1]
                    );
            WriteOutput(new JavaScriptSerializer().Serialize(
                LaunchCenterActions.DescribeSelection(
                    selectionView
                )
            ));
            selectionApp.Shutdown();
            return selected ? 0 : 20;
        }

        private static int UiStoredLaunchRouteJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            if (edition.product_role != "LaunchCenter")
            {
                WriteError(
                    "Команда маршрута доступна только центру запуска"
                );
                return 2;
            }
            Application routeApp = new Application();
            UserControl routeView = InstallerView.Create(
                bundleRoot,
                false
            );
            bool selected = LaunchCenterActions.SelectTarget(
                routeView,
                args[2]
            );
            bool applied = selected && ConnectionUi.ApplyRoute(
                routeView,
                LaunchRouteStore.Resolve(args[1], args[2])
            );
            WriteOutput(new JavaScriptSerializer().Serialize(
                LaunchCenterActions.DescribeSelection(routeView)
            ));
            routeApp.Shutdown();
            return applied ? 0 : 20;
        }

        private static int UiVsCodeResolutionJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            if (edition.product_role != "LaunchCenter")
            {
                WriteError(
                    "Команда состояния доступна только центру запуска"
                );
                return 2;
            }
            Application stateApp = new Application();
            UserControl stateView = InstallerView.Create(
                bundleRoot,
                false
            );
            LaunchTargetResolution resolution =
                VsCodeIntegration.ResolveTestRecord(
                    bundleRoot,
                    args[1],
                    args[2]
                );
            LaunchCenterActions.ApplyResolutionFeedback(
                stateView,
                resolution
            );
            WriteOutput(new JavaScriptSerializer().Serialize(
                LaunchCenterActions.DescribeResolutionFeedback(
                    stateView,
                    resolution
                )
            ));
            stateApp.Shutdown();
            return 0;
        }

        private static int ValidateStoreRecordJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                ClientBootstrap.ValidateStoreRecord(
                    bundleRoot,
                    args[1],
                    args[2]
                )
            ));
            return 0;
        }

        private static int VerifyRuntimeJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            RuntimeBootstrapResult runtime =
                RuntimeBootstrap.Verify(
                    bundleRoot,
                    args[1]
                );
            WriteOutput(new JavaScriptSerializer().Serialize(
                runtime
            ));
            return runtime.status == "VERIFIED" ? 0 : 20;
        }

        private static int WriteInstallReportJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                InstallerActions.TryWriteSuccessReport(
                    args[1],
                    new List<TargetRow>()
                )
            ));
            return 0;
        }

        private static int WriteSingBoxConfigTestJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            int listenPort;
            if (!Int32.TryParse(args[4], out listenPort))
            {
                WriteError("Listen port is invalid");
                return 2;
            }
            SingBoxConfigSummary config =
                SingBoxConfig.WriteTestConfig(
                    bundleRoot,
                    args[1],
                    args[2],
                    args[3],
                    listenPort,
                    args[5]
                );
            WriteOutput(new JavaScriptSerializer().Serialize(
                config
            ));
            return 0;
        }
    }
}
#endif
