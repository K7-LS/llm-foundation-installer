"""Native WPF event/binding regressions with inert launch and route stores.

Compile the complete production LaunchCenterActions, the actual pure route UI
methods and ConnectionUiContract. Load the unchanged Employee/Owner XAML in an
STA process without displaying a window. No installer, client, network, proxy,
DPAPI or real user state is accessed by the fakes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "src" / "gui"
EDITIONS = ("Employee", "Owner")
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows WPF test")


HARNESS = r'''
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Threading;

namespace LlmFoundationInstaller
{
    internal sealed class EditionProfile
    {
        public static EditionProfile LoadEmbedded() { return new EditionProfile(); }
    }
    internal sealed class LaunchTargetResolution
    {
        public string target_id;
        public string status;
        public string action;
        public string official_url;
        public string reason;
    }
    internal sealed class LauncherSessionResult
    {
        public string status;
        public string transport;
        public string reason;
        public bool cleanup_verified;
        public int process_exit_code;
        public List<string> lifecycle;
    }
    internal static class VsCodeIntegration
    {
        public const string OfficialMarketplaceUrl =
            "https://marketplace.visualstudio.com/items?itemName=OpenAI.chatgpt";
    }
    internal static class LaunchRouteStore
    {
        public static string SavedRoute = "Direct";
        public static bool FailSave;
        public static int SaveCalls;
        public static string Resolve(string home, string target) { return SavedRoute; }
        public static void Save(string home, string target, string route)
        {
            SaveCalls++;
            if (FailSave) throw new InvalidOperationException("TEST_ROUTE_SAVE_FAILED");
            SavedRoute = route;
        }
    }
    internal static class LaunchTargetResolver
    {
        public static bool FailResolve;
        public static LaunchTargetResolution Resolve(EditionProfile edition,
            string bundle, string home, string target)
        {
            if (FailResolve) throw new InvalidOperationException("TEST_RESOLVE_EXCEPTION");
            return new LaunchTargetResolution { target_id = target, status = "RESOLVED" };
        }
    }
    internal static class ClientLauncher
    {
        public static readonly ManualResetEventSlim Started = new ManualResetEventSlim(false);
        public static readonly ManualResetEventSlim Release = new ManualResetEventSlim(false);
        public static bool ThrowOnFinish;
        public static bool ReturnRunning;
        public static bool ActiveWhileRunning;
        public static int Calls;
        public static LauncherSessionResult StartAndWait(LaunchTargetResolution resolution,
            string route, string bundle, string home)
        {
            Interlocked.Increment(ref Calls);
            Started.Set();
            if (!Release.Wait(TimeSpan.FromSeconds(10)))
                throw new InvalidOperationException("Test did not release the inert launcher");
            if (ThrowOnFinish) throw new InvalidOperationException("TEST_LAUNCH_EXCEPTION");
            if (ReturnRunning) return new LauncherSessionResult
            {
                status = "PASS", transport = route, cleanup_verified = true,
                process_exit_code = -1,
                lifecycle = new List<string> { "EXACT_CLIENT_STARTED", "CLIENT_RUNNING" }
            };
            return new LauncherSessionResult
            {
                status = "FAILED", transport = route,
                reason = "TEST_LAUNCH_FAILED", cleanup_verified = false
            };
        }
        public static bool HasActiveRoute()
        {
            return ActiveWhileRunning && Started.IsSet && !Release.IsSet;
        }
    }

    internal static class UiStateHarness
    {
        private static readonly string[] BusyControls = {
            "LaunchSelected", "LaunchTargetList", "RouteDirect", "RouteHttp", "RouteHttps",
            "ProxyMode", "ProxyType", "ProxyHost", "ProxyPort", "ProxyUsername",
            "ProxyPassword", "SaveConnection", "TestConnection", "ResetRoute", "OpenChromeProxy"
        };
        private static readonly List<string> Unhandled = new List<string>();

        [STAThread]
        public static int Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            Application app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
            SynchronizationContext.SetSynchronizationContext(
                new DispatcherSynchronizationContext(Dispatcher.CurrentDispatcher));
            app.DispatcherUnhandledException += delegate(object sender, DispatcherUnhandledExceptionEventArgs e)
            {
                Unhandled.Add(e.Exception.GetType().Name + ": " + e.Exception.Message);
                e.Handled = true;
            };
            try
            {
                string scenario = args[1];
                if (scenario == "same-route") LaunchRouteStore.SavedRoute = args[2];
                UserControl view;
                using (FileStream input = File.OpenRead(args[0]))
                    view = (UserControl)XamlReader.Load(input);
                LaunchCenterActions.Bind(view, Path.GetDirectoryName(args[0]), true);
                Layout(view);
                Dictionary<string, object> result = new Dictionary<string, object>();
                result["initial"] = Snapshot(view);
                if (scenario == "same-route")
                {
                    result["selected"] = LaunchCenterActions.SelectTarget(view, "codex-desktop");
                    Drain();
                    result["after"] = Snapshot(view);
                    result["save_calls"] = LaunchRouteStore.SaveCalls;
                }
                else if (scenario == "save-failure")
                {
                    LaunchCenterActions.SelectTarget(view, "codex-desktop");
                    LaunchRouteStore.FailSave = true;
                    ConnectionUi.ApplyRoute(view, "SingBoxHttps");
                    Drain();
                    result["after"] = Snapshot(view);
                    result["save_calls"] = LaunchRouteStore.SaveCalls;
                }
                else if (scenario == "async-failure" || scenario == "async-exception" ||
                         scenario == "inherited-disabled" || scenario == "async-running" ||
                         scenario == "active-route")
                {
                    LaunchCenterActions.SelectTarget(view, "codex-desktop");
                    ClientLauncher.ThrowOnFinish = scenario == "async-exception";
                    ClientLauncher.ReturnRunning = scenario == "async-running";
                    ClientLauncher.ActiveWhileRunning = scenario == "active-route";
                    if (scenario == "active-route") ConnectionUi.ApplyRoute(view, "SingBoxHttps");
                    UIElement settings = (UIElement)view.FindName("ProxySettings");
                    TextBox host = (TextBox)view.FindName("ProxyHost");
                    if (scenario == "inherited-disabled")
                    {
                        settings.Visibility = Visibility.Visible;
                        Layout(view);
                        settings.IsEnabled = false;
                        result["host_initial_effective_enabled"] = host.IsEnabled;
                        result["host_initial_has_local_enabled"] =
                            host.ReadLocalValue(UIElement.IsEnabledProperty) != DependencyProperty.UnsetValue;
                    }
                    Button launch = (Button)view.FindName("LaunchSelected");
                    launch.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                    result["started"] = PumpUntil(delegate { return ClientLauncher.Started.IsSet; }, 3000);
                    if (scenario == "active-route")
                    {
                        Button stop = (Button)view.FindName("StopRoute");
                        result["stop_enabled_while_running"] = PumpUntil(delegate { return stop.IsEnabled; }, 1500);
                    }
                    result["busy"] = Snapshot(view);
                    result["controls_during_launch"] = Controls(view);
                    ClientLauncher.Release.Set();
                    result["reenabled"] = PumpUntil(delegate { return launch.IsEnabled; }, 1500);
                    Drain();
                    result["after"] = Snapshot(view);
                    result["controls_after_launch"] = Controls(view);
                    result["launcher_calls"] = ClientLauncher.Calls;
                    if (scenario == "inherited-disabled")
                    {
                        result["settings_after_launch_enabled"] = settings.IsEnabled;
                        result["host_after_launch_effective_enabled"] = host.IsEnabled;
                        result["host_after_launch_has_local_enabled"] =
                            host.ReadLocalValue(UIElement.IsEnabledProperty) != DependencyProperty.UnsetValue;
                        settings.IsEnabled = true;
                        result["host_after_parent_enabled"] = host.IsEnabled;
                    }
                }
                else if (scenario == "resolve-exception")
                {
                    LaunchCenterActions.SelectTarget(view, "codex-desktop");
                    LaunchTargetResolver.FailResolve = true;
                    Button launch = (Button)view.FindName("LaunchSelected");
                    launch.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                    Drain();
                    result["after"] = Snapshot(view);
                    result["controls_after_launch"] = Controls(view);
                    result["launcher_calls"] = ClientLauncher.Calls;
                }
                else if (scenario == "scroll")
                {
                    ListBox list = (ListBox)view.FindName("LaunchTargetList");
                    list.ApplyTemplate();
                    Layout(view);
                    ScrollViewer scroll = VisualChild<ScrollViewer>(list);
                    if (scroll == null) throw new InvalidOperationException("ListBox ScrollViewer is missing");
                    ListBoxItem last = list.Items.Cast<ListBoxItem>().First(item =>
                        String.Equals(item.Tag as string, "opencode-cli", StringComparison.Ordinal));
                    result["extent_height"] = scroll.ExtentHeight;
                    result["viewport_height"] = scroll.ViewportHeight;
                    result["list_height"] = list.ActualHeight;
                    result["initial_offset"] = scroll.VerticalOffset;
                    list.ScrollIntoView(last);
                    Drain();
                    Layout(view);
                    result["bring_into_view_offset"] = scroll.VerticalOffset;
                    // Exercise the actual ScrollViewer even without a PresentationSource.
                    scroll.ScrollToBottom();
                    Drain();
                    Layout(view);
                    Rect bounds = last.TransformToAncestor(list).TransformBounds(new Rect(last.RenderSize));
                    result["final_offset"] = scroll.VerticalOffset;
                    result["last_item_top"] = bounds.Top;
                    result["last_item_bottom"] = bounds.Bottom;
                    result["last_item_height"] = bounds.Height;

                    Layout(view, 1100);
                    scroll.ScrollToVerticalOffset(4);
                    Drain();
                    Layout(view, 1100);
                    ListBoxItem claude = list.Items.Cast<ListBoxItem>().First(item =>
                        String.Equals(item.Tag as string, "claude-code", StringComparison.Ordinal));
                    Grid card = (Grid)((Border)claude.Content).Child;
                    StackPanel copy = card.Children.OfType<StackPanel>().Single();
                    TextBlock description = copy.Children.OfType<TextBlock>().Last();
                    TextBlock badge = card.Children.OfType<TextBlock>().Single();
                    Rect descriptionBounds = description.TransformToAncestor(card)
                        .TransformBounds(new Rect(description.RenderSize));
                    Rect badgeBounds = badge.TransformToAncestor(card)
                        .TransformBounds(new Rect(badge.RenderSize));
                    double textRight = 0, textBottom = 0, textLeft = 0;
                    for (TextPointer cursor = description.ContentStart.GetNextInsertionPosition(LogicalDirection.Forward);
                         cursor != null && cursor.CompareTo(description.ContentEnd) < 0;
                         cursor = cursor.GetNextInsertionPosition(LogicalDirection.Forward))
                    {
                        Rect character = cursor.GetCharacterRect(LogicalDirection.Forward);
                        if (!character.IsEmpty)
                        {
                            textRight = Math.Max(textRight, character.Right);
                            textBottom = Math.Max(textBottom, character.Bottom);
                            textLeft = Math.Min(textLeft, character.Left);
                        }
                    }
                    result["claude_description_1100"] = new Dictionary<string, object> {
                        { "width", description.ActualWidth }, { "height", description.ActualHeight },
                        { "glyph_left", textLeft }, { "glyph_right", textRight },
                        { "glyph_bottom", textBottom },
                        { "card_text_right", descriptionBounds.Right }, { "badge_left", badgeBounds.Left },
                        { "card_text_bottom", descriptionBounds.Bottom }, { "card_height", card.ActualHeight }
                    };
                }
                else if (scenario != "initial") return 2;
                result["unhandled_dispatcher_exceptions"] = Unhandled.ToArray();
                Console.WriteLine(new JavaScriptSerializer().Serialize(result));
                return 0;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error.ToString());
                return 3;
            }
            finally
            {
                ClientLauncher.Release.Set();
                app.Shutdown();
            }
        }

        private static Dictionary<string, object> Snapshot(UserControl view)
        {
            Button launch = (Button)view.FindName("LaunchSelected");
            return new Dictionary<string, object>
            {
                { "client", Text(view, "SelectedClientName") },
                { "route_name", Text(view, "SelectedRouteName") },
                { "route_scope", Text(view, "RouteScopeStatus") },
                { "route_status", Text(view, "RouteStatus") },
                { "evidence", Text(view, "EvidenceStatus") },
                { "health", Text(view, "LaunchHealthStatus") },
                { "launch_enabled", launch.IsEnabled }
            };
        }
        private static string Text(UserControl view, string name)
        {
            TextBlock block = view.FindName(name) as TextBlock;
            return block == null ? null : block.Text;
        }
        private static Dictionary<string, bool> Controls(UserControl view)
        {
            Dictionary<string, bool> state = new Dictionary<string, bool>();
            foreach (string name in BusyControls)
            {
                UIElement control = view.FindName(name) as UIElement;
                if (control != null) state[name] = control.IsEnabled;
            }
            return state;
        }
        private static void Layout(UserControl view, double width = 1280)
        {
            view.Measure(new Size(width, 760));
            view.Arrange(new Rect(0, 0, width, 760));
            view.UpdateLayout();
        }
        private static T VisualChild<T>(DependencyObject parent) where T : DependencyObject
        {
            for (int i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
            {
                DependencyObject child = VisualTreeHelper.GetChild(parent, i);
                T match = child as T ?? VisualChild<T>(child);
                if (match != null) return match;
            }
            return null;
        }
        private static void Drain()
        {
            DispatcherFrame frame = new DispatcherFrame();
            Dispatcher.CurrentDispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle,
                new Action(delegate { frame.Continue = false; }));
            Dispatcher.PushFrame(frame);
        }
        private static bool PumpUntil(Func<bool> condition, int milliseconds)
        {
            Stopwatch watch = Stopwatch.StartNew();
            while (!condition() && watch.ElapsedMilliseconds < milliseconds)
            {
                Drain();
                Thread.Sleep(1);
            }
            return condition();
        }
    }
}
'''


def _pure_route_source() -> str:
    source = (GUI / "ConnectionUi.cs").read_text(encoding="utf-8-sig")
    methods = []
    for signature in (
        "internal static bool ApplyRoute(UserControl view, string route)",
        "internal static string SelectedTag(ComboBox combo)",
        "internal static void SelectTag(ComboBox combo, string value)",
    ):
        start = source.index("        " + signature)
        following = re.search(r"^        (?:public|internal|private) ", source[start + len(signature) + 8:], re.M)
        assert following is not None, signature
        methods.append(source[start:start + len(signature) + 8 + following.start()])
    contract = source[source.index("    internal sealed class ConnectionUiContract"):].rsplit("}", 1)[0]
    return (
        "using System; using System.Collections.Generic; using System.Globalization; "
        "using System.Linq; using System.Windows.Controls;\n"
        "namespace LlmFoundationInstaller { internal static class ConnectionUi {\n"
        + "\n".join(methods) + "\n}\n" + contract + "\n}\n"
    )


def _compiler_and_references() -> tuple[Path, list[Path]]:
    windows = Path(os.environ.get("WINDIR", os.environ.get("SystemRoot", "C:/Windows")))
    for framework in ("Framework64", "Framework"):
        directory = windows / "Microsoft.NET" / framework / "v4.0.30319"
        compiler = directory / "csc.exe"
        references = [directory / "WPF" / name for name in (
            "PresentationFramework.dll", "PresentationCore.dll", "WindowsBase.dll",
        )] + [directory / "System.Xaml.dll", directory / "System.Web.Extensions.dll"]
        if compiler.is_file() and all(path.is_file() for path in references):
            return compiler, references
    pytest.skip("A local .NET Framework C# compiler and WPF runtime are required")


@pytest.fixture(scope="module")
def ui_harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("launch-center-ui-state")
    harness = work / "UiStateHarness.cs"
    harness.write_text(HARNESS, encoding="utf-8")
    route = work / "PureRouteUi.cs"
    route.write_text(_pure_route_source(), encoding="utf-8")
    executable = work / "UiStateHarness.exe"
    compiler, references = _compiler_and_references()
    command = [str(compiler), "/nologo", "/target:exe", "/nowarn:0649", f"/out:{executable}"]
    command.extend("/r:" + str(path) for path in references)
    command.extend([str(GUI / "LaunchCenterActions.cs"), str(route), str(harness)])
    result = subprocess.run(command, cwd=work, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60, check=False,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    return executable


def _run(ui_harness: Path, tmp_path: Path, edition: str, scenario: str, *arguments: str) -> dict:
    environment = os.environ.copy()
    for key in list(environment):
        if key.upper().startswith(("FOUNDATION_", "LLM_FOUNDATION_", "K7_BASE_RELEASE_TEST_API_URL_")):
            del environment[key]
    for key, relative in {
        "USERPROFILE": "profile", "APPDATA": "profile/AppData/Roaming",
        "LOCALAPPDATA": "profile/AppData/Local", "TEMP": "temp", "TMP": "temp",
    }.items():
        directory = tmp_path / relative
        directory.mkdir(parents=True, exist_ok=True)
        environment[key] = str(directory)
    command = [str(ui_harness), str(GUI / f"LaunchCenter{edition}View.xaml"), scenario, *arguments]
    result = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True,
                            timeout=20,
                            check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    (tmp_path / "harness.stdout.txt").write_bytes(result.stdout)
    (tmp_path / "harness.stderr.txt").write_bytes(result.stderr)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8-sig", errors="replace")
    return json.loads(result.stdout.decode("utf-8-sig"))


@pytest.mark.parametrize("edition", EDITIONS)
def test_initial_ui_does_not_claim_a_client_was_checked(ui_harness: Path, tmp_path: Path, edition: str) -> None:
    result = _run(ui_harness, tmp_path, edition, "initial")
    assert result["initial"]["client"] == "GOOGLE CHROME"
    assert "проверен" not in result["initial"]["evidence"].lower()
    if edition == "Employee":
        assert result["initial"]["health"] == "ОЖИДАНИЕ"
    assert result["unhandled_dispatcher_exceptions"] == []


@pytest.mark.parametrize("edition", EDITIONS)
@pytest.mark.parametrize("route", ("Direct", "SingBoxHttps"))
def test_same_saved_route_updates_its_client_label(ui_harness: Path, tmp_path: Path, edition: str, route: str) -> None:
    result = _run(ui_harness, tmp_path, edition, "same-route", route)
    assert "GOOGLE CHROME" in result["initial"]["route_scope"]
    assert result["selected"] is True
    assert result["after"]["client"] == "CODEX"
    assert "CODEX" in result["after"]["route_scope"]
    assert "GOOGLE CHROME" not in result["after"]["route_scope"]
    assert result["after"]["route_name"] == result["initial"]["route_name"]
    assert result["save_calls"] == 0, "Applying a stored route must not save it again"
    assert result["unhandled_dispatcher_exceptions"] == []


@pytest.mark.parametrize("edition", EDITIONS)
def test_failed_route_save_is_not_reported_as_saved(ui_harness: Path, tmp_path: Path, edition: str) -> None:
    result = _run(ui_harness, tmp_path, edition, "save-failure")
    assert result["save_calls"] > 0
    assert "TEST_ROUTE_SAVE_FAILED" in result["after"]["route_scope"]
    assert "не сохран" in result["after"]["route_scope"].lower()
    assert "Сохранено для" not in result["after"]["route_scope"]
    assert result["unhandled_dispatcher_exceptions"] == []


@pytest.mark.parametrize("edition", EDITIONS)
@pytest.mark.parametrize("scenario,reason", (
    ("async-failure", "TEST_LAUNCH_FAILED"),
    ("async-exception", "TEST_LAUNCH_EXCEPTION"),
))
def test_async_failure_keeps_reason_and_restores_controls(
    ui_harness: Path, tmp_path: Path, edition: str, scenario: str, reason: str,
) -> None:
    result = _run(ui_harness, tmp_path, edition, scenario)
    assert result["started"] is True
    assert result["controls_during_launch"]
    assert not any(result["controls_during_launch"].values()), result["controls_during_launch"]
    assert result["reenabled"] is True
    assert all(result["controls_after_launch"].values()), result["controls_after_launch"]
    assert reason in result["after"]["evidence"]
    assert result["after"]["evidence"] != "Пакет проверен"
    assert "ошиб" in result["after"]["route_status"].lower()
    assert result["launcher_calls"] == 1
    if edition == "Employee":
        assert result["busy"]["health"] == "ЗАПУСК"
        assert result["after"]["health"] == "ОШИБКА"
    assert result["unhandled_dispatcher_exceptions"] == []


@pytest.mark.parametrize("edition", EDITIONS)
def test_resolver_exception_restores_ui_without_starting_a_client(ui_harness: Path, tmp_path: Path, edition: str) -> None:
    result = _run(ui_harness, tmp_path, edition, "resolve-exception")
    assert "TEST_RESOLVE_EXCEPTION" in result["after"]["evidence"]
    assert result["after"]["launch_enabled"] is True
    assert all(result["controls_after_launch"].values()), result["controls_after_launch"]
    assert result["launcher_calls"] == 0
    assert result["unhandled_dispatcher_exceptions"] == []


@pytest.mark.parametrize("edition", EDITIONS)
def test_launch_restore_preserves_inherited_disabled_state(ui_harness: Path, tmp_path: Path, edition: str) -> None:
    result = _run(ui_harness, tmp_path, edition, "inherited-disabled")
    assert result["host_initial_effective_enabled"] is False
    assert result["host_initial_has_local_enabled"] is False
    assert result["started"] is True
    assert result["reenabled"] is True
    assert result["launcher_calls"] == 1
    assert result["settings_after_launch_enabled"] is False
    assert result["host_after_launch_effective_enabled"] is False
    assert result["host_after_launch_has_local_enabled"] is False
    assert result["host_after_parent_enabled"] is True
    assert "TEST_LAUNCH_FAILED" in result["after"]["evidence"]
    assert result["unhandled_dispatcher_exceptions"] == []


@pytest.mark.parametrize("edition", EDITIONS)
def test_direct_client_still_running_is_not_reported_as_finished(ui_harness: Path, tmp_path: Path, edition: str) -> None:
    result = _run(ui_harness, tmp_path, edition, "async-running")
    assert result["started"] is True
    assert result["reenabled"] is True
    assert result["launcher_calls"] == 1
    assert "заверш" not in result["after"]["evidence"].lower()
    assert "заверш" not in result["after"]["route_status"].lower()
    assert result["after"]["evidence"] != result["initial"]["evidence"]
    if edition == "Employee":
        assert result["after"]["health"] == "ЗАПУСК ВЫПОЛНЕН"
    assert result["unhandled_dispatcher_exceptions"] == []


@pytest.mark.parametrize("edition", EDITIONS)
def test_active_route_can_be_stopped_while_other_launch_controls_are_disabled(ui_harness: Path, tmp_path: Path, edition: str) -> None:
    result = _run(ui_harness, tmp_path, edition, "active-route")
    assert result["started"] is True
    assert result["stop_enabled_while_running"] is True
    assert not any(result["controls_during_launch"].values()), result["controls_during_launch"]
    assert result["reenabled"] is True
    assert all(result["controls_after_launch"].values()), result["controls_after_launch"]
    assert result["launcher_calls"] == 1
    assert result["unhandled_dispatcher_exceptions"] == []


def test_employee_target_list_scrolls_last_client_into_view_at_1280_by_760(ui_harness: Path, tmp_path: Path) -> None:
    result = _run(ui_harness, tmp_path, "Employee", "scroll")
    assert result["viewport_height"] > 0
    assert result["extent_height"] > result["viewport_height"]
    assert result["final_offset"] > result["initial_offset"], result
    assert result["last_item_height"] > 0
    assert result["last_item_top"] >= -1
    assert result["last_item_bottom"] <= result["list_height"] + 1
    description = result["claude_description_1100"]
    assert description["width"] > 0 and description["height"] > 0
    assert description["glyph_left"] >= -1
    assert description["glyph_right"] <= description["width"] + 1, description
    assert description["glyph_bottom"] <= description["height"] + 1, description
    assert description["card_text_right"] <= description["badge_left"] + 1, description
    assert description["card_text_bottom"] <= description["card_height"] + 1, description
    assert result["unhandled_dispatcher_exceptions"] == []
