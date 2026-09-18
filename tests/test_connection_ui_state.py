"""Native WPF profile-warning lifecycle with the actual ConnectionUi implementation.

Only compile-time stores/probes/runtime dependencies are fake. No DPAPI, network,
installer or real profile is accessed. Both unchanged XAML views are loaded in STA.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "src" / "gui"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows WPF test")


HARNESS = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Security;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Threading;

namespace LlmFoundationInstaller
{
    internal sealed class ConnectionAuth { public string mode; public string username; }
    internal sealed class ProxyProfile
    {
        public string type; public string host; public int port; public ConnectionAuth auth;
    }
    internal sealed class ConnectionProfile
    {
        public int schema_version; public string mode; public ProxyProfile proxy;
    }
    internal sealed class ConnectionStateResult { public ConnectionProfile profile; }
    internal static class ConnectionStore
    {
        public static bool FailSave;
        public static int LoadCalls, SaveCalls, ValidateCalls;
        public static ConnectionProfile SavedProfile;
        public static ConnectionStateResult Load(string home)
        {
            LoadCalls++;
            throw new InvalidOperationException("TEST_PROFILE_LOAD_FAILED");
        }
        public static ConnectionProfile Validate(ConnectionProfile profile)
        {
            ValidateCalls++;
            return profile;
        }
        public static ConnectionStateResult Save(string home, ConnectionProfile profile, SecureString password)
        {
            SaveCalls++;
            if (FailSave) throw new InvalidOperationException("TEST_PROFILE_SAVE_FAILED");
            SavedProfile = profile;
            return new ConnectionStateResult { profile = profile };
        }
    }
    internal sealed class ProductConfig
    {
        public string connection_probe_url;
        public static ProductConfig LoadEmbedded() { throw Forbidden.Call("ProductConfig.LoadEmbedded"); }
    }
    internal sealed class ConnectionProbeResult
    {
        public string status, mode, proxy_type, error; public bool uses_proxy; public long elapsed_ms;
    }
    internal sealed class SingBoxSessionResult
    {
        public string status, reason; public bool cleanup_verified; public List<string> lifecycle;
    }
    internal static class Forbidden
    {
        public static int Calls;
        public static Exception Call(string name)
        {
            Calls++;
            return new InvalidOperationException("FORBIDDEN_FAKE_CALL: " + name);
        }
    }
    internal static class ConnectionProbe
    {
        public static ConnectionProbeResult Run(string home, string endpoint)
        { throw Forbidden.Call("ConnectionProbe.Run"); }
    }
    internal static class SingBoxSession
    {
        public static SingBoxSessionResult TestRoute(string bundle, string home, string route, string endpoint)
        { throw Forbidden.Call("SingBoxSession.TestRoute"); }
    }
    internal static class ClientLauncher
    {
        public static SingBoxSessionResult StopActiveRoute()
        { throw Forbidden.Call("ClientLauncher.StopActiveRoute"); }
        public static SingBoxSessionResult ResetManagedRoute(string home)
        { throw Forbidden.Call("ClientLauncher.ResetManagedRoute"); }
    }
    internal static class ConnectionUiStateHarness
    {
        [STAThread]
        public static int Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            Application app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
            List<string> unhandled = new List<string>();
            app.DispatcherUnhandledException += delegate(object sender, DispatcherUnhandledExceptionEventArgs e)
            {
                unhandled.Add(e.Exception.ToString()); e.Handled = true;
            };
            try
            {
                UserControl view;
                using (FileStream input = File.OpenRead(args[0])) view = (UserControl)XamlReader.Load(input);
                ConnectionUi.Bind(view, Path.GetDirectoryName(args[0]), true);
                Dictionary<string, object> result = new Dictionary<string, object>();
                result["after_load"] = Status(view);
                result["applied_proxy"] = ConnectionUi.ApplyRoute(view, "SingBoxHttps");
                Drain();
                result["after_proxy"] = Status(view);
                if (args[1] == "save-success" || args[1] == "save-failure")
                {
                    ((TextBox)view.FindName("ProxyHost")).Text = "fixture.invalid";
                    ((TextBox)view.FindName("ProxyPort")).Text = "443";
                    ConnectionStore.FailSave = args[1] == "save-failure";
                    string error;
                    result["saved"] = ConnectionUi.TrySaveCurrent(view, out error);
                    result["save_error"] = error;
                    result["after_save"] = Status(view);
                }
                else if (args[1] != "load-failure") return 2;
                ConnectionUi.ApplyRoute(view, "Direct");
                Drain();
                result["after_direct"] = Status(view);
                ConnectionUi.ApplyRoute(view, "SingBoxHttps");
                Drain();
                result["after_proxy_again"] = Status(view);
                result["load_calls"] = ConnectionStore.LoadCalls;
                result["save_calls"] = ConnectionStore.SaveCalls;
                result["validate_calls"] = ConnectionStore.ValidateCalls;
                result["saved_profile_mode"] = ConnectionStore.SavedProfile == null ? null : ConnectionStore.SavedProfile.mode;
                result["forbidden_calls"] = Forbidden.Calls;
                result["unhandled_dispatcher_exceptions"] = unhandled;
                Console.WriteLine(new JavaScriptSerializer().Serialize(result));
                return 0;
            }
            catch (Exception error) { Console.Error.WriteLine(error.ToString()); return 3; }
            finally { app.Shutdown(); }
        }
        private static Dictionary<string, object> Status(UserControl view)
        {
            TextBlock status = (TextBlock)view.FindName("ConnectionStatus");
            Grid settings = (Grid)view.FindName("ProxySettings");
            return new Dictionary<string, object> {
                { "text", status.Text },
                { "color", ((SolidColorBrush)status.Foreground).Color.ToString() },
                { "settings_enabled", settings.IsEnabled },
                { "settings_visibility", settings.Visibility.ToString() }
            };
        }
        private static void Drain()
        {
            DispatcherFrame frame = new DispatcherFrame();
            Dispatcher.CurrentDispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle,
                new Action(delegate { frame.Continue = false; }));
            Dispatcher.PushFrame(frame);
        }
    }
}
'''


@pytest.fixture(scope="module")
def connection_ui_harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    windows = Path(os.environ.get("WINDIR", os.environ.get("SystemRoot", "C:/Windows")))
    for framework in ("Framework64", "Framework"):
        directory = windows / "Microsoft.NET" / framework / "v4.0.30319"
        compiler = directory / "csc.exe"
        references = [directory / "WPF" / name for name in (
            "PresentationFramework.dll", "PresentationCore.dll", "WindowsBase.dll",
        )] + [directory / "System.Xaml.dll", directory / "System.Web.Extensions.dll"]
        if compiler.is_file() and all(path.is_file() for path in references):
            break
    else:
        pytest.skip("A local .NET Framework C# compiler and WPF runtime are required")
    work = tmp_path_factory.mktemp("connection-ui-state")
    source = work / "ConnectionUiStateHarness.cs"
    source.write_text(HARNESS, encoding="utf-8")
    executable = work / "ConnectionUiStateHarness.exe"
    command = [str(compiler), "/nologo", "/target:exe", "/nowarn:0649", f"/out:{executable}"]
    command.extend("/r:" + str(path) for path in references)
    command.extend(str(GUI / name) for name in ("ConnectionUi.cs", "ConnectionStatusModel.cs", "StatusPalette.cs"))
    command.append(str(source))
    result = subprocess.run(command, cwd=work, capture_output=True, timeout=60, check=False,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    (work / "compile.stdout.txt").write_bytes(result.stdout)
    (work / "compile.stderr.txt").write_bytes(result.stderr)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")
    return executable


def _run(executable: Path, tmp_path: Path, edition: str, scenario: str) -> dict:
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
    command = [str(executable), str(GUI / f"LaunchCenter{edition}View.xaml"), scenario]
    result = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True,
                            timeout=20, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    (tmp_path / "harness.stdout.txt").write_bytes(result.stdout)
    (tmp_path / "harness.stderr.txt").write_bytes(result.stderr)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8-sig", errors="replace")
    output = json.loads(result.stdout.decode("utf-8-sig"))
    assert output["forbidden_calls"] == 0
    assert output["unhandled_dispatcher_exceptions"] == []
    assert output["load_calls"] == 1
    return output


@pytest.mark.parametrize("edition", ("Employee", "Owner"))
def test_load_error_survives_applying_saved_route_and_mode_changes(
    connection_ui_harness: Path, tmp_path: Path, edition: str,
) -> None:
    result = _run(connection_ui_harness, tmp_path, edition, "load-failure")
    initial = result["after_load"]
    assert "TEST_PROFILE_LOAD_FAILED" in initial["text"]
    assert initial["color"] == "#FFA15C00"
    for key in ("after_proxy", "after_direct", "after_proxy_again"):
        assert result[key]["text"] == initial["text"]
        assert result[key]["color"] == initial["color"]
    assert result["after_proxy"]["settings_visibility"] == "Visible"
    assert result["after_direct"]["settings_visibility"] == "Collapsed"
    assert result["save_calls"] == 0


@pytest.mark.parametrize("edition", ("Employee", "Owner"))
def test_public_try_save_current_clears_old_load_error_after_success(
    connection_ui_harness: Path, tmp_path: Path, edition: str,
) -> None:
    result = _run(connection_ui_harness, tmp_path, edition, "save-success")
    assert "TEST_PROFILE_LOAD_FAILED" in result["after_proxy"]["text"]
    assert result["saved"] is True
    assert result["save_error"] is None
    assert result["save_calls"] == 1 and result["validate_calls"] == 1
    assert result["saved_profile_mode"] == "Proxy"
    assert "сохран" in result["after_save"]["text"].lower()
    for key in ("after_save", "after_direct", "after_proxy_again"):
        assert "TEST_PROFILE_LOAD_FAILED" not in result[key]["text"]
        assert result[key]["color"] != result["after_load"]["color"]
    assert "Напрямую" in result["after_direct"]["text"]
    assert "Сохранённый пароль" in result["after_proxy_again"]["text"]


@pytest.mark.parametrize("edition", ("Employee", "Owner"))
def test_failed_save_does_not_clear_the_unresolved_load_warning(
    connection_ui_harness: Path, tmp_path: Path, edition: str,
) -> None:
    result = _run(connection_ui_harness, tmp_path, edition, "save-failure")
    assert result["saved"] is False
    assert result["save_error"] == "TEST_PROFILE_SAVE_FAILED"
    assert result["save_calls"] == 1 and result["validate_calls"] == 1
    assert result["saved_profile_mode"] is None
    assert "Не сохранено" in result["after_save"]["text"]
    assert "TEST_PROFILE_SAVE_FAILED" in result["after_save"]["text"]
    for key in ("after_save", "after_direct", "after_proxy_again"):
        assert result[key]["color"] == result["after_load"]["color"]
    for key in ("after_direct", "after_proxy_again"):
        assert "TEST_PROFILE_LOAD_FAILED" in result[key]["text"]
