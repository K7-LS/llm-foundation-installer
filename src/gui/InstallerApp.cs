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

[assembly: AssemblyTitle("LLM Foundation Installer")]
[assembly: AssemblyDescription("Verified local installer for native LLM workspaces")]
[assembly: AssemblyCompany("LLM Foundation")]
[assembly: AssemblyProduct("LLM Foundation Installer")]
[assembly: AssemblyCopyright("Copyright 2026")]
[assembly: AssemblyVersion("0.4.0.0")]
[assembly: AssemblyFileVersion("0.4.0.0")]
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
                bool launchCenterUi = args.Length == 1 &&
                    args[0] == "--launch-center-ui";
                bool launchCenterProduct = args.Length == 1 &&
                    args[0] == "--launch-center-product-json";
                if (launchCenterUi || launchCenterProduct)
                {
                    edition.product_role = "LaunchCenter";
                }
                if (launchCenterUi)
                {
                    args = new string[0];
                }
                if (args.Length == 1 &&
                    args[0] == "--describe-edition")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        edition
                    ));
                    return 0;
                }
                if (args.Length == 1 &&
                    args[0] == "--connection-status-texts-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ConnectionStatusModel.CanonicalCatalog()
                    ));
                    return 0;
                }
                if (args.Length == 1 && args[0] == "--product-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        LaunchTargetCatalog.Describe(
                            edition,
                            bundleRoot
                        )
                    ));
                    return 0;
                }
                if (launchCenterProduct)
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        LaunchTargetCatalog.Describe(
                            edition,
                            bundleRoot
                        )
                    ));
                    return 0;
                }
                if (args.Length == 1 &&
                    args[0] == "--chrome-proxy-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ChromeProxyLauncher.Describe()
                    ));
                    return 0;
                }
                if (args.Length == 3 &&
                    args[0] == "--resolve-launch-target-json")
                {
                    LaunchTargetResolution resolution =
                        LaunchTargetResolver.Resolve(
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
                if (args.Length == 3 &&
                    args[0] ==
                        "--resolve-store-launch-target-record-json")
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
                if (args.Length == 3 &&
                    args[0] == "--resolve-vscode-record-json")
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
                if (args.Length == 4 &&
                    args[0] ==
                        "--resolve-vscode-mutating-record-json")
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
                if (args.Length == 3 &&
                    args[0] == "--ui-vscode-resolution-json")
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
                if (args.Length == 4 &&
                    args[0] == "--launch-target-json")
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
                if (args.Length == 2 &&
                    args[0] == "--resolve-sibling-json")
                {
                    SiblingProductResolution sibling =
                        ProductHandoff.Resolve(edition, args[1]);
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        sibling
                    ));
                    return sibling.status == "RESOLVED" ? 0 : 20;
                }
                if (args.Length == 3 &&
                    args[0] == "--install-runtime-json")
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
                if (args.Length == 2 &&
                    args[0] == "--ensure-runtime-json")
                {
                    RuntimeBootstrapResult runtime =
                        RuntimeBootstrap.EnsureInstalled(
                            bundleRoot,
                            args[1]
                        );
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        runtime
                    ));
                    return runtime.status == "VERIFIED" ? 0 : 20;
                }
                if (args.Length == 2 &&
                    args[0] == "--verify-runtime-json")
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
                if (args.Length == 6 &&
                    args[0] == "--write-singbox-config-test-json")
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
                if (args.Length == 4 &&
                    args[0] == "--test-singbox-route-json")
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
                if (args.Length == 4 &&
                    args[0] == "--test-connection-route-json")
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
                if (args.Length == 4 &&
                    args[0] == "--test-singbox-session-json")
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
                if (args.Length == 2 &&
                    args[0] == "--reset-managed-route-json")
                {
                    SingBoxSessionResult reset =
                        ClientLauncher.ResetManagedRoute(args[1]);
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        reset
                    ));
                    return reset.status == "PASS" ? 0 : 20;
                }
                if ((args.Length == 3 || args.Length == 4) &&
                    args[0] == "--system-proxy-watchdog")
                {
                    int ownerPid;
                    if (!Int32.TryParse(args[1], out ownerPid))
                    {
                        WriteError("Некорректный PID владельца");
                        return 2;
                    }
                    ProxyRecoveryResult watchdogResult;
                    if (args.Length == 4)
                    {
                        if (!ClientBootstrap.Load(bundleRoot).test_only ||
                            !SystemProxyLease
                                .IsAllowedTestRegistrySubkey(args[3]))
                        {
                            WriteError(
                                "Тест системного proxy запрещён для production"
                            );
                            return 2;
                        }
                        watchdogResult = SystemProxyLease.Watchdog(
                            ownerPid,
                            args[2],
                            args[3]
                        );
                    }
                    else
                    {
                        watchdogResult = SystemProxyLease.Watchdog(
                            ownerPid,
                            args[2]
                        );
                    }
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        watchdogResult
                    ));
                    return watchdogResult.cleanup_verified ? 0 : 20;
                }
                if ((args.Length == 5 || args.Length == 6) &&
                    args[0] == "--system-proxy-test-json")
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
                if ((args.Length == 6 || args.Length == 7) &&
                    args[0] == "--test-appx-singbox-json")
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
                if (args.Length == 1 && args[0] == "--self-test-json")
                {
                    return RunSelfTest(bundleRoot);
                }
                if (args.Length == 1 && args[0] == "--catalog-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ProductCatalog.Inspect(bundleRoot)
                    ));
                    return 0;
                }
                if (args.Length == 1 && args[0] == "--preflight-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ProductCatalog.Inspect(bundleRoot, true)
                    ));
                    return 0;
                }
                if (args.Length == 2 &&
                    args[0] == "--preflight-store-record-json")
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
                if (args.Length == 1 && args[0] == "--platform-json")
                {
                    PlatformCompatibilityResult platform =
                        PlatformCompatibility.Inspect();
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        platform
                    ));
                    return platform.status == "READY" ? 0 : 20;
                }
                if (args.Length == 4 &&
                    args[0] == "--evaluate-platform-json")
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
                if (args.Length == 1 &&
                    args[0] == "--client-sources-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ClientBootstrap.Describe(bundleRoot)
                    ));
                    return 0;
                }
                if (args.Length == 3 &&
                    args[0] == "--latest-base-json")
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
                if (args.Length == 3 &&
                    args[0] == "--validate-store-record-json")
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
                if (args.Length == 2 &&
                    args[0] == "--store-client-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ClientBootstrap.ProbeStore(
                            bundleRoot,
                            args[1]
                        )
                    ));
                    return 0;
                }
                if (args.Length == 2 &&
                    args[0] == "--open-store-client-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ClientBootstrap.OpenStoreSource(
                            bundleRoot,
                            args[1]
                        )
                    ));
                    return 0;
                }
                if (args.Length == 4 &&
                    args[0] == "--download-client-json")
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
                if (args.Length == 3 &&
                    args[0] == "--client-plan-json")
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
                if (args.Length == 4 &&
                    args[0] == "--client-plan-store-record-json")
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
                if (args.Length == 3 &&
                    args[0] == "--target-client-plan-json")
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
                if (args.Length == 4 &&
                    args[0] == "--install-client-json")
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
                if (args.Length == 2 &&
                    args[0] == "--write-install-report-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        InstallerActions.TryWriteSuccessReport(
                            args[1],
                            new List<TargetRow>()
                        )
                    ));
                    return 0;
                }
                if (args.Length == 5 && args[0] == "--workflow-json")
                {
                    string output;
                    string error;
                    int exitCode = FoundationWorkflow.Run(
                        bundleRoot,
                        args[1],
                        args[2],
                        args[3],
                        args[4],
                        out output,
                        out error
                    );
                    if (!String.IsNullOrWhiteSpace(output))
                    {
                        WriteOutput(output.TrimEnd('\r', '\n'));
                    }
                    if (!String.IsNullOrWhiteSpace(error))
                    {
                        WriteError(error.TrimEnd('\r', '\n'));
                    }
                    return exitCode;
                }
                if (args.Length == 3 &&
                    args[0] == "--save-connection-json")
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
                if (args.Length == 4 &&
                    args[0] == "--save-launch-route-json")
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
                if (args.Length == 2 &&
                    args[0] == "--launch-routes-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        LaunchRouteStore.Load(args[1])
                    ));
                    return 0;
                }
                if (args.Length == 2 && args[0] == "--connection-json")
                {
                    WriteOutput(new JavaScriptSerializer().Serialize(
                        ConnectionStore.Load(args[1])
                    ));
                    return 0;
                }
                if (args.Length == 2 &&
                    args[0] == "--connection-environment-json")
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
                if (args.Length == 3 &&
                    args[0] == "--probe-connection-json")
                {
                    ConnectionProbeResult result = ConnectionProbe.Run(
                        args[1],
                        args[2]
                    );
                    WriteOutput(new JavaScriptSerializer().Serialize(result));
                    return result.status == "READY" ? 0 : 20;
                }
                if (args.Length == 2 && args[0] == "--render-preview")
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
                if (args.Length == 2 &&
                    args[0] == "--render-guide-preview")
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
                if (args.Length == 2 &&
                    args[0] == "--ui-connection-state-json")
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
                if (args.Length == 3 &&
                    args[0] == "--ui-stored-launch-route-json")
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
                if (args.Length == 2 &&
                    (args[0] == "--ui-selection-json" ||
                     args[0] == "--ui-launch-selection-json" ||
                     args[0] == "--ui-guide-selection-json"))
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
                if (args.Length != 0)
                {
                    WriteError("Неподдерживаемая команда");
                    return 2;
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

        private static int RunSelfTest(string bundleRoot)
        {
            int protocol;
            bool validated = BundleIntegrity.ValidateEngine(bundleRoot, out protocol);
            bool platformReady =
                PlatformCompatibility.Inspect().status == "READY";
            Dictionary<string, object> payload = new Dictionary<string, object>();
            payload["app_id"] = "llm-foundation-installer";
            payload["engine_validated"] = validated;
            payload["foundation_protocol"] = protocol;
            payload["network"] = "user-initiated-only";
            payload["automatic_network"] = false;
            payload["reverse_flow"] = false;
            payload["targets"] = ProductCatalog.TargetIds();
            payload["telemetry"] = false;
            payload["version"] = BundleIntegrity.ReadBundleVersion(bundleRoot);
            WriteOutput(new JavaScriptSerializer().Serialize(payload));
            return validated && platformReady ? 0 : 30;
        }

        private static void WriteOutput(string value)
        {
            WriteStream(Console.OpenStandardOutput(), value);
        }

        private static void WriteError(string value)
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
