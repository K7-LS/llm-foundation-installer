"""Full native SingBoxSession acceptance against an owned loopback runtime.

The real session code creates/ACLs its task-local directory, starts/checks/stops
the compiled fixture process and invokes Windows curl. Stores/config/bootstrap
are compile-time fakes. BoundedProcess only injects malformed/exit fixtures;
ordinary HTTP scenarios execute the actual curl command against loopback.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("K7_SINGBOX_TEST_SOURCE", str(ROOT / "src/gui/SingBoxSession.cs")))
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows process/ACL test")

HARNESS = r'''
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class RuntimeSourceLock { public bool test_only = true; }
    internal sealed class RuntimeBootstrapResult { public string status, executable_path, reason; }
    internal static class RuntimeBootstrap
    {
        public static RuntimeSourceLock Load(string bundle) { return new RuntimeSourceLock(); }
        public static RuntimeBootstrapResult EnsureInstalled(string bundle, string home)
        {
            return new RuntimeBootstrapResult { status = "VERIFIED", executable_path = Assembly.GetExecutingAssembly().Location };
        }
        public static string FailureReason(RuntimeBootstrapResult value) { return value.reason ?? "RUNTIME_NOT_VERIFIED"; }
    }
    internal sealed class ConnectionProfile { }
    internal static class ConnectionStore
    {
        public static int Calls;
        public static FileStream HeldLock;
        public static T WithProxyCredential<T>(string home, Func<ConnectionProfile, string, T> action)
        {
            Calls++;
            if (Fixture.Scenario == "check-fail-locked" || Fixture.Scenario == "http-locked")
            {
                string root = Directory.GetDirectories(Fixture.SessionParent(home)).Single();
                HeldLock = new FileStream(Path.Combine(root, "cleanup.lock"), FileMode.Create, FileAccess.ReadWrite, FileShare.None);
            }
            return action(new ConnectionProfile(), null);
        }
    }
    internal static class SingBoxConfig
    {
        public static Dictionary<string, object> Create(ConnectionProfile profile, string password,
            string target, string route, int port)
        {
            return new Dictionary<string, object> { { "listen_port", port } };
        }
    }
    internal static class BundleIntegrity
    {
        public static string Sha256(string path)
        {
            using (SHA256 hash = SHA256.Create())
            using (FileStream input = File.OpenRead(path))
                return String.Concat(hash.ComputeHash(input).Select(value => value.ToString("x2")));
        }
    }
    internal sealed class BoundedProcessResult
    {
        public bool started, timed_out, drained; public int exit_code;
        public string standard_output, standard_error;
    }
    internal static class BoundedProcess
    {
        public static int Calls, RealCurlCalls;
        public static string[] ChildProxyKeys;
        public static BoundedProcessResult Run(ProcessStartInfo start, int timeout)
        {
            Calls++;
            ChildProxyKeys = Fixture.ProxyKeys.Where(key =>
                !String.IsNullOrEmpty(start.EnvironmentVariables[key])).ToArray();
            if (Fixture.Scenario == "malformed-output")
                return new BoundedProcessResult { started = true, drained = true, exit_code = 0,
                    standard_output = Fixture.Value, standard_error = "" };
            if (Fixture.Scenario == "curl-exit")
                return new BoundedProcessResult { started = true, drained = true, exit_code = Int32.Parse(Fixture.Value),
                    standard_output = "000", standard_error = "fixture curl failure" };
            RealCurlCalls++;
            using (Process process = Process.Start(start))
            {
                Task<string> output = process.StandardOutput.ReadToEndAsync();
                Task<string> error = process.StandardError.ReadToEndAsync();
                bool exited = process.WaitForExit(timeout);
                if (!exited) { process.Kill(); process.WaitForExit(5000); }
                bool drained = Task.WaitAll(new Task[] { output, error }, 5000);
                return new BoundedProcessResult { started = true, timed_out = !exited, drained = drained,
                    exit_code = exited ? process.ExitCode : -1,
                    standard_output = output.IsCompleted ? output.Result : "",
                    standard_error = error.IsCompleted ? error.Result : "" };
            }
        }
    }
    internal static class Fixture
    {
        public static readonly string[] ProxyKeys = {
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"
        };
        public static string Scenario { get { return Environment.GetEnvironmentVariable("K7_PROBE_FIXTURE_SCENARIO"); } }
        public static string Value { get { return Environment.GetEnvironmentVariable("K7_PROBE_FIXTURE_VALUE") ?? ""; } }
        private static string Control { get { return Environment.GetEnvironmentVariable("K7_PROBE_FIXTURE_CONTROL"); } }
        public static string SessionParent(string home)
        { return Path.Combine(home, ".llm-foundation", "launcher-state", "sessions"); }

        public static int Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            if (args.Length > 0 && args[0] == "check")
                return Scenario.StartsWith("check-fail", StringComparison.Ordinal) ? 19 : 0;
            if (args.Length > 0 && args[0] == "run") return Runtime(args[2]);
            string home = Path.GetFullPath(args[0]);
            string sessionParent = SessionParent(home);
            Directory.CreateDirectory(home);
            Directory.CreateDirectory(Control);
            byte[] originalAcl = null;
            try
            {
                if (Scenario == "acl-failure")
                {
                    Directory.CreateDirectory(sessionParent);
                    DirectorySecurity security = Directory.GetAccessControl(sessionParent);
                    originalAcl = security.GetSecurityDescriptorBinaryForm();
                    security.AddAccessRule(new FileSystemAccessRule(
                        new SecurityIdentifier("S-1-3-4"), FileSystemRights.ChangePermissions,
                        InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
                        PropagationFlags.InheritOnly, AccessControlType.Deny));
                    Directory.SetAccessControl(sessionParent, security);
                }
                SingBoxSessionResult result;
                string diagnosticOnStopReturn = null;
                bool diagnosticProbeFailed = false;
                long stopMilliseconds = 0;
                if (Scenario == "late-diagnostic")
                {
                    RunningSingBoxSession running = SingBoxSession.Start(
                        Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location), home, "connection-test", "SingBoxHttps");
                    ManualResetEvent gateHeld = new ManualResetEvent(false);
                    Thread delay = new Thread(delegate() {
                        lock (running.diagnostic_gate) { gateHeld.Set(); Thread.Sleep(750); }
                    });
                    delay.IsBackground = true;
                    delay.Start();
                    gateHeld.WaitOne();
                    try
                    {
                        typeof(SingBoxSession).GetMethod("RunCurlProbe", BindingFlags.NonPublic | BindingFlags.Static)
                            .Invoke(null, new object[] { running.listen_port, "http://127.0.0.1:1/trace" });
                    }
                    catch (TargetInvocationException) { diagnosticProbeFailed = true; }
                    Stopwatch stopwatch = Stopwatch.StartNew();
                    result = SingBoxSession.StopVerified(running);
                    stopMilliseconds = stopwatch.ElapsedMilliseconds;
                    diagnosticOnStopReturn = running.runtime_reason;
                    delay.Join(2000);
                    gateHeld.Dispose();
                }
                else if (Scenario == "stop-process-failure")
                {
                    string root = Path.Combine(sessionParent, "owned-fixture-stop");
                    Directory.CreateDirectory(root);
                    File.WriteAllText(Path.Combine(root, "must-retain.txt"), "fixture");
                    result = SingBoxSession.StopVerified(new RunningSingBoxSession {
                        process = new Process(), session_root = root, lifecycle = new List<string>(), diagnostic_gate = new object()
                    });
                }
                else if (args[1] == "cycle")
                    result = SingBoxSession.TestCycle(Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location),
                        home, "connection-test", "SingBoxHttps");
                else
                    result = SingBoxSession.TestRoute(Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location),
                        home, "SingBoxHttps", "http://127.0.0.1:1/trace");
                string requests = Path.Combine(Control, "requests.txt");
                var report = new Dictionary<string, object> {
                    { "result", result }, { "credential_calls", ConnectionStore.Calls },
                    { "probe_calls", BoundedProcess.Calls }, { "real_curl_calls", BoundedProcess.RealCurlCalls },
                    { "child_proxy_keys", BoundedProcess.ChildProxyKeys },
                    { "remaining_session_roots", Directory.Exists(sessionParent) ? Directory.GetDirectories(sessionParent).Length : 0 },
                    { "requests", File.Exists(requests) ? File.ReadAllLines(requests) : new string[0] },
                    { "owned_runtime_alive", OwnedRuntimeAlive() },
                    { "acl_fault_configured", originalAcl != null },
                    { "diagnostic_on_stop_return", diagnosticOnStopReturn },
                    { "diagnostic_probe_failed", diagnosticProbeFailed },
                    { "stop_milliseconds", stopMilliseconds }
                };
                Console.WriteLine(new JavaScriptSerializer().Serialize(report));
                return 0;
            }
            catch (Exception error) { Console.Error.WriteLine(error.ToString()); return 3; }
            finally
            {
                if (ConnectionStore.HeldLock != null) ConnectionStore.HeldLock.Dispose();
                if (originalAcl != null)
                {
                    DirectorySecurity restore = new DirectorySecurity();
                    restore.SetSecurityDescriptorBinaryForm(originalAcl, AccessControlSections.Access);
                    Directory.SetAccessControl(sessionParent, restore);
                }
                StopOwnedRuntimeIfPresent();
                string full = Path.GetFullPath(sessionParent);
                if (!full.StartsWith(home.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar,
                    StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("Fixture cleanup escaped its home");
                if (Directory.Exists(full)) Directory.Delete(full, true);
            }
        }
        private static int Runtime(string config)
        {
            Process current = Process.GetCurrentProcess();
            File.WriteAllText(Path.Combine(Control, "runtime-pid.txt"), current.Id + "\n" + current.StartTime.ToUniversalTime().Ticks);
            if (Scenario == "run-exits") return 23;
            int port = Convert.ToInt32(new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(
                File.ReadAllText(config))["listen_port"]);
            TcpListener listener = new TcpListener(IPAddress.Loopback, port);
            listener.Start();
            int requests = 0;
            while (true)
            {
                using (TcpClient client = listener.AcceptTcpClient())
                {
                    client.ReceiveTimeout = 3000;
                    using (NetworkStream stream = client.GetStream())
                    {
                        StreamReader reader = new StreamReader(stream, Encoding.ASCII, false, 1024, true);
                        string line;
                        try { line = reader.ReadLine(); } catch (IOException) { continue; }
                        if (String.IsNullOrEmpty(line)) continue;
                        requests++;
                        File.AppendAllText(Path.Combine(Control, "requests.txt"), line + "\n");
                        if (Scenario == "late-diagnostic")
                        {
                            Console.Error.WriteLine("authentication required");
                            Console.Error.Flush();
                            return 0;
                        }
                        if (Scenario == "malformed-http")
                        {
                            byte[] malformed = Encoding.ASCII.GetBytes("NOT AN HTTP RESPONSE\r\n\r\n");
                            stream.Write(malformed, 0, malformed.Length);
                            continue;
                        }
                        while (!String.IsNullOrEmpty(reader.ReadLine())) { }
                        int status = Scenario == "http" || Scenario == "http-locked" || Scenario == "hostile-curlrc"
                            ? Int32.Parse(Value) : 200;
                        if (requests > 1) status = 200;
                        string body = status == 204 ? "" : "fixture-response\n";
                        string response = "HTTP/1.1 " + status + " Fixture\r\nContent-Type: text/plain\r\n" +
                            "Connection: close\r\nContent-Length: " + Encoding.ASCII.GetByteCount(body) + "\r\n" +
                            (status >= 300 && status < 400 ? "Location: http://127.0.0.1:1/redirect\r\n" : "") + "\r\n" + body;
                        byte[] bytes = Encoding.ASCII.GetBytes(response);
                        stream.Write(bytes, 0, bytes.Length);
                    }
                }
            }
        }
        private static Process ReadOwnedRuntime()
        {
            string path = Path.Combine(Control, "runtime-pid.txt");
            if (!File.Exists(path)) return null;
            string[] lines = File.ReadAllLines(path);
            Process process;
            try { process = Process.GetProcessById(Int32.Parse(lines[0])); }
            catch (ArgumentException) { return null; }
            if (process.StartTime.ToUniversalTime().Ticks != Int64.Parse(lines[1]) ||
                !String.Equals(process.MainModule.FileName, Assembly.GetExecutingAssembly().Location, StringComparison.OrdinalIgnoreCase))
            { process.Dispose(); return null; }
            return process;
        }
        private static bool OwnedRuntimeAlive()
        { using (Process process = ReadOwnedRuntime()) return process != null && !process.HasExited; }
        private static void StopOwnedRuntimeIfPresent()
        {
            using (Process process = ReadOwnedRuntime())
                if (process != null && !process.HasExited) { process.Kill(); process.WaitForExit(5000); }
        }
    }
}
'''


@pytest.fixture(scope="module")
def native_harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    windows = Path(os.environ.get("WINDIR", "C:/Windows"))
    compiler = next((windows / "Microsoft.NET" / framework / "v4.0.30319/csc.exe"
                     for framework in ("Framework64", "Framework")
                     if (windows / "Microsoft.NET" / framework / "v4.0.30319/csc.exe").is_file()), None)
    if compiler is None:
        pytest.fail("Native .NET Framework compiler unavailable; acceptance is not verified")
    work = tmp_path_factory.mktemp("singbox-native")
    source = work / "SingBoxSession.cs"
    source.write_bytes(SOURCE.read_bytes())
    harness = work / "ProbeHarness.cs"
    harness.write_text(HARNESS, encoding="utf-8")
    executable = work / "ProbeHarness.exe"
    command = [str(compiler), "/nologo", "/target:exe", "/nowarn:0649", f"/out:{executable}",
               "/r:" + str(compiler.parent / "System.Web.Extensions.dll"), str(source), str(harness)]
    result = subprocess.run(command, cwd=work, capture_output=True, timeout=60, check=False,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    (work / "compile.stdout.txt").write_bytes(result.stdout)
    (work / "compile.stderr.txt").write_bytes(result.stderr)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")
    return executable


def _run(harness: Path, tmp_path: Path, scenario: str, value: str = "", entry: str = "route") -> dict:
    environment = os.environ.copy()
    for key in list(environment):
        if key.upper().startswith(("FOUNDATION_", "LLM_FOUNDATION_", "K7_BASE_RELEASE_TEST_API_URL_")):
            del environment[key]
    for key, relative in {"USERPROFILE": "profile", "APPDATA": "profile/AppData/Roaming",
                          "LOCALAPPDATA": "profile/AppData/Local", "TEMP": "temp", "TMP": "temp"}.items():
        directory = tmp_path / relative
        directory.mkdir(parents=True, exist_ok=True)
        environment[key] = str(directory)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        environment.pop(key, None)
    curl_home = tmp_path / "curl-config"
    curl_home.mkdir()
    environment["CURL_HOME"] = str(curl_home)
    environment["XDG_CONFIG_HOME"] = str(curl_home)
    if scenario == "hostile-curlrc":
        config = 'location\nnoproxy = "*"\ninsecure\n'
        (curl_home / ".curlrc").write_text(config, encoding="ascii")
        (curl_home / "_curlrc").write_text(config, encoding="ascii")
    if scenario == "inherited-proxy":
        environment.update({"NO_PROXY": "*", "HTTP_PROXY": "http://127.0.0.1:1",
                            "HTTPS_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1"})
    environment.update({"K7_PROBE_FIXTURE_SCENARIO": scenario, "K7_PROBE_FIXTURE_VALUE": value,
                        "K7_PROBE_FIXTURE_CONTROL": str(tmp_path / "control")})
    command = [str(harness), str(tmp_path / "home"), entry]
    result = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, timeout=40,
                            check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    (tmp_path / "harness.stdout.txt").write_bytes(result.stdout)
    (tmp_path / "harness.stderr.txt").write_bytes(result.stderr)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8-sig", errors="replace")
    report = json.loads(result.stdout.decode("utf-8-sig"))
    assert report["owned_runtime_alive"] is False, report
    return report


@pytest.mark.parametrize("status", (200, 204, 301, 302, 307, 403, 404, 407, 429, 500))
def test_full_test_route_accepts_only_2xx_and_does_not_follow_redirects(native_harness: Path, tmp_path: Path, status: int) -> None:
    report = _run(native_harness, tmp_path, "http", str(status))
    result = report["result"]
    assert result["status"] == ("PASS" if 200 <= status < 300 else "FAILED"), report
    assert result.get("http_status") == status, report
    assert result["reason"] == (None if 200 <= status < 300 else f"ROUTE_HTTP_STATUS_{status}")
    assert ("ROUTE_PROBE_PASS" in result["lifecycle"]) == (200 <= status < 300)
    assert result["cleanup_verified"] is True and report["remaining_session_roots"] == 0
    assert report["real_curl_calls"] == 1 and len(report["requests"]) == 1


@pytest.mark.parametrize("output", ("", "malformed", "099", "600"))
def test_malformed_curl_status_output_is_not_accepted(native_harness: Path, tmp_path: Path, output: str) -> None:
    report = _run(native_harness, tmp_path, "malformed-output", output)
    assert report["result"]["status"] == "FAILED"
    assert report["result"]["reason"] == "ROUTE_PROBE_FAILED"
    assert report["result"]["cleanup_verified"] is True
    assert "ROUTE_PROBE_PASS" not in report["result"]["lifecycle"]
    assert report["remaining_session_roots"] == 0 and report["real_curl_calls"] == 0


@pytest.mark.parametrize("exit_code,reason", ((6, "PROXY_DNS_FAILED"), (7, "PROXY_CONNECT_FAILED"),
    (28, "PROXY_TIMEOUT"), (35, "PROXY_TLS_FAILED"), (56, "PROXY_UPSTREAM_FAILED")))
def test_curl_transport_failure_reasons_survive_full_session_cleanup(native_harness: Path, tmp_path: Path, exit_code: int, reason: str) -> None:
    report = _run(native_harness, tmp_path, "curl-exit", str(exit_code))
    assert report["result"]["status"] == "FAILED"
    assert report["result"]["reason"] == reason
    assert report["result"]["cleanup_verified"] is True and report["remaining_session_roots"] == 0
    assert "ROUTE_PROBE_PASS" not in report["result"]["lifecycle"]


def test_real_curl_rejects_malformed_http_response(native_harness: Path, tmp_path: Path) -> None:
    report = _run(native_harness, tmp_path, "malformed-http")
    assert report["result"]["status"] == "FAILED"
    assert report["result"]["reason"] == "PROXY_UPSTREAM_FAILED"
    assert report["result"]["cleanup_verified"] is True and report["remaining_session_roots"] == 0
    assert report["real_curl_calls"] == 1 and len(report["requests"]) == 1


def test_inherited_no_proxy_cannot_bypass_the_owned_loopback_proxy(native_harness: Path, tmp_path: Path) -> None:
    report = _run(native_harness, tmp_path, "inherited-proxy")
    assert report["result"]["status"] == "PASS", report
    assert report["result"].get("http_status") == 200
    assert report["child_proxy_keys"] == []
    assert report["real_curl_calls"] == 1 and len(report["requests"]) == 1


@pytest.mark.parametrize("entry", ("route", "cycle"))
@pytest.mark.parametrize("scenario,cleanup,reason", (
    ("check-fail-clean", True, "CONFIG_CHECK_FAILED"),
    ("check-fail-locked", False, "CONFIG_CHECK_FAILED"),
    ("run-exits", True, "RUNTIME_EXITED_BEFORE_READY"),
))
def test_failed_start_preserves_observed_cleanup_result(native_harness: Path, tmp_path: Path,
    entry: str, scenario: str, cleanup: bool, reason: str) -> None:
    report = _run(native_harness, tmp_path, scenario, entry=entry)
    assert report["result"]["status"] == "FAILED"
    assert report["result"]["cleanup_verified"] is cleanup, report
    assert report["result"]["reason"] == reason
    assert report["remaining_session_roots"] == (0 if cleanup else 1)
    assert report["probe_calls"] == 0


@pytest.mark.parametrize("entry", ("route", "cycle"))
def test_early_directory_acl_failure_is_inside_session_ownership(native_harness: Path, tmp_path: Path, entry: str) -> None:
    report = _run(native_harness, tmp_path, "acl-failure", entry=entry)
    assert report["acl_fault_configured"] is True
    assert report["credential_calls"] == 0, "The OS ACL fault was not reproduced; this case is not verified"
    assert report["result"]["status"] == "FAILED"
    assert report["result"]["cleanup_verified"] is True
    assert report["remaining_session_roots"] == 0, report
    assert report["probe_calls"] == 0


def test_stop_with_failed_process_state_retains_unproven_session_directory(native_harness: Path, tmp_path: Path) -> None:
    report = _run(native_harness, tmp_path, "stop-process-failure")
    assert report["result"]["status"] == "FAILED"
    assert report["result"]["reason"] == "SESSION_CLEANUP_FAILED"
    assert report["result"]["cleanup_verified"] is False
    assert report["remaining_session_roots"] == 1


@pytest.mark.parametrize("status", (200, 403))
def test_http_result_and_failed_cleanup_are_both_reported(native_harness: Path, tmp_path: Path, status: int) -> None:
    report = _run(native_harness, tmp_path, "http-locked", str(status))
    result = report["result"]
    assert result["status"] == "FAILED"
    assert result.get("http_status") == status
    assert result["cleanup_verified"] is False
    assert result["reason"] == ("SESSION_CLEANUP_FAILED" if status == 200 else "ROUTE_HTTP_STATUS_403")
    assert "CLEANUP_UNVERIFIED" in result["lifecycle"]
    assert ("ROUTE_PROBE_PASS" in result["lifecycle"]) == (status == 200)
    assert report["remaining_session_roots"] == 1
    assert report["real_curl_calls"] == 1 and len(report["requests"]) == 1


def test_task_curlrc_cannot_enable_bypass_or_follow_redirects(native_harness: Path, tmp_path: Path) -> None:
    report = _run(native_harness, tmp_path, "hostile-curlrc", "302")
    assert report["result"]["status"] == "FAILED"
    assert report["result"].get("http_status") == 302, report
    assert report["result"]["reason"] == "ROUTE_HTTP_STATUS_302"
    assert report["real_curl_calls"] == 1 and len(report["requests"]) == 1
    assert report["result"]["cleanup_verified"] is True


def test_final_runtime_auth_diagnostic_is_drained_before_stop_returns(native_harness: Path, tmp_path: Path) -> None:
    report = _run(native_harness, tmp_path, "late-diagnostic")
    assert report["diagnostic_probe_failed"] is True
    assert report["diagnostic_on_stop_return"] == "PROXY_AUTH_FAILED", report
    assert report["result"]["cleanup_verified"] is True and report["remaining_session_roots"] == 0
    assert report["real_curl_calls"] == 1 and len(report["requests"]) == 1
