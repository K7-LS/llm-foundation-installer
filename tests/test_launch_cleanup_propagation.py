"""Native cleanup propagation in the complete production ClientLauncher.cs.

Only its external dependencies are inert. StartAndWait runs the ordinary appx
branch with a nonexistent, unique executable under the fixture home. No client,
runtime, network, registry or user profile is accessed. The optional source-dir
override supports red/green against an archived pre-change ClientLauncher.cs.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows C# test")

HARNESS = r'''
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class LaunchTargetResolution
    {
        public string status, target_id, role, launch_mode, executable_path;
        public string sha256, activation_id;
    }
    internal sealed class SingBoxSessionResult
    {
        public string status, reason;
        public int listen_port;
        public bool uses_proxy, cleanup_verified, secret_redacted;
        public List<string> lifecycle;
    }
    internal sealed class SingBoxStartException : InvalidOperationException
    {
        public SingBoxSessionResult Result { get; private set; }
        public SingBoxStartException(SingBoxSessionResult result) : base(result.reason)
        { Result = result; }
    }
    internal sealed class RunningSingBoxSession
    {
        public int listen_port;
        public List<string> lifecycle;
        public SingBoxSessionResult stop_result;
    }
    internal sealed class ProxyRecoveryResult
    {
        public string status, reason;
        public bool cleanup_verified;
        public List<string> lifecycle;
    }
    internal static class Fixture
    {
        public static string Scenario, Home;
        public static int RecoverCalls, StartCalls, AcquireCalls, StopCalls, LeaseStopCalls;
        public static RunningSingBoxSession Owned;
        public static void CheckHome(string home)
        {
            if (Path.GetFullPath(home) != Home) throw new Exception("Unexpected home");
        }
        public static SingBoxSessionResult SessionResult(bool cleanup, string reason)
        {
            return new SingBoxSessionResult {
                status = reason == null ? "PASS" : "FAILED", reason = reason,
                cleanup_verified = cleanup, secret_redacted = true, uses_proxy = true,
                listen_port = 19001, lifecycle = new List<string> { "INERT_OWNED_SESSION" }
            };
        }
        public static ProxyRecoveryResult ProxyResult(string status, bool cleanup, string reason)
        {
            return new ProxyRecoveryResult {
                status = status, cleanup_verified = cleanup, reason = reason,
                lifecycle = new List<string>()
            };
        }
    }
    internal static class SingBoxSession
    {
        public static SingBoxSessionResult RecoverOrphanedSessions(string home)
        {
            Fixture.CheckHome(home); Fixture.RecoverCalls++;
            if (Fixture.Scenario == "recover_throw")
                throw new InvalidOperationException("OWNED_SESSION_RECOVERY_FAILED");
            return Fixture.Scenario == "recover_false"
                ? Fixture.SessionResult(false, "OWNED_SESSION_RECOVERY_FAILED")
                : Fixture.SessionResult(true, null);
        }
        public static RunningSingBoxSession Start(string bundle, string home, string target, string route)
        {
            Fixture.CheckHome(home); Fixture.StartCalls++;
            if (Fixture.Scenario.StartsWith("typed_"))
            {
                bool cleanup = Fixture.Scenario.Contains("_true");
                string reason = Fixture.Scenario.EndsWith("_specific")
                    ? "RUNTIME_ARCHIVE_INTEGRITY_FAILED" : "CONFIG_CHECK_FAILED";
                throw new SingBoxStartException(Fixture.SessionResult(cleanup, reason));
            }
            Fixture.Owned = new RunningSingBoxSession {
                listen_port = 19001, lifecycle = new List<string> { "INERT_OWNED_SESSION" }
            };
            return Fixture.Owned;
        }
        public static SingBoxSessionResult StopVerified(RunningSingBoxSession running)
        {
            if (!Object.ReferenceEquals(running, Fixture.Owned))
                throw new Exception("Attempt to stop a session not owned by fixture");
            Fixture.StopCalls++;
            return Fixture.Scenario == "stop_false"
                ? Fixture.SessionResult(false, "SESSION_CLEANUP_FAILED")
                : Fixture.SessionResult(true, null);
        }
        public static SingBoxSessionResult ResetManagedSessions(string home)
        { throw new Exception("Unexpected global reset"); }
    }
    internal static class SystemProxyLease
    {
        public static ProxyRecoveryResult Acquire(string home, int port)
        {
            Fixture.CheckHome(home); Fixture.AcquireCalls++;
            if (Fixture.Scenario == "acquire_throw")
                throw new InvalidOperationException("SYSTEM_PROXY_ACQUIRE_FAILED");
            if (Fixture.Scenario == "acquire_false")
                return Fixture.ProxyResult("FAILED", false, "SYSTEM_PROXY_RECOVERY_FAILED");
            if (Fixture.Scenario != "stop_false")
                throw new Exception("Unexpected lease acquisition");
            return Fixture.ProxyResult("ACQUIRED", true, null);
        }
        public static ProxyRecoveryResult Acquire(string home, int port, string testSubkey)
        { throw new Exception("Test registry overload must not be used"); }
        public static ProxyRecoveryResult StopActiveRoute()
        {
            Fixture.LeaseStopCalls++;
            return Fixture.ProxyResult("RESTORED", true, null);
        }
        public static ProxyRecoveryResult ResetPreservingExternalChanges(string home)
        { throw new Exception("Unexpected global proxy reset"); }
        public static bool IsAllowedTestRegistrySubkey(string key) { return false; }
    }
    internal static class BundleIntegrity
    {
        public static string Sha256(string path)
        { throw new Exception("Nonexistent client must be rejected before hashing or launch"); }
    }
    internal static class ClientCleanupHarness
    {
        private static int Main(string[] args)
        {
            Fixture.Scenario = args[0]; Fixture.Home = Path.GetFullPath(args[1]);
            string client = Path.Combine(Fixture.Home, "never-launch-" + Guid.NewGuid().ToString("N") + ".exe");
            if (File.Exists(client)) throw new Exception("Fixture executable unexpectedly exists");
            LaunchTargetResolution target = new LaunchTargetResolution {
                status = "RESOLVED", target_id = "codex-desktop", role = "desktop",
                launch_mode = "appx", executable_path = client, sha256 = new string('0',64),
                activation_id = "INERT!App"
            };
            LauncherSessionResult result = ClientLauncher.StartAndWait(
                target, "SingBoxHttps", Fixture.Home, Fixture.Home);
            Console.WriteLine(new JavaScriptSerializer().Serialize(new Dictionary<string,object> {
                {"result",result}, {"recover_calls",Fixture.RecoverCalls},
                {"start_calls",Fixture.StartCalls}, {"acquire_calls",Fixture.AcquireCalls},
                {"own_stop_calls",Fixture.StopCalls}, {"lease_stop_calls",Fixture.LeaseStopCalls},
                {"fake_client_exists",File.Exists(client)},
                {"home_entries",Directory.GetFileSystemEntries(Fixture.Home).Length},
                {"fixture_dependencies","inert: no runtime, client, network, Store, registry, DPAPI or profile"}
            }));
            return 0;
        }
    }
}
'''


@pytest.fixture(scope="module")
def client_cleanup_harness(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    source_dir = Path(os.environ.get("K7_CLIENT_CLEANUP_SOURCE_DIR", str(ROOT / "src/gui")))
    source = source_dir / "ClientLauncher.cs"
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    work = tmp_path_factory.mktemp("client-cleanup-compile")
    (work / "ClientLauncher.cs").write_bytes(payload)
    (work / "Harness.cs").write_text(HARNESS, encoding="utf-8")
    framework = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319"
    compiler = framework / "csc.exe"
    if not compiler.is_file():
        pytest.skip(".NET Framework C# compiler is unavailable")
    executable = work / "client-cleanup-harness.exe"
    command = [str(compiler), "/nologo", "/target:exe", "/platform:x64", "/out:" + str(executable),
               "/r:" + str(framework / "System.Web.Extensions.dll"),
               str(work / "ClientLauncher.cs"), str(work / "Harness.cs")]
    run = subprocess.run(command, cwd=work, capture_output=True, timeout=60,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    (work / "compile.stdout.bin").write_bytes(run.stdout)
    (work / "compile.stderr.bin").write_bytes(run.stderr)
    (work / "source-receipt.json").write_text(json.dumps({
        "source": str(source), "sha256": digest, "complete_source_bytes": len(payload),
        "command": command, "compile_exit": run.returncode,
        "harness_sha256": hashlib.sha256(HARNESS.encode()).hexdigest(),
    }, indent=2) + "\n", encoding="utf-8")
    assert run.returncode == 0, (run.stdout + run.stderr).decode("utf-8", "replace")
    assert source.read_bytes() == payload, "Production source changed during compilation"
    return executable, digest


CASES = [
    ("typed_true", True, "CONFIG_CHECK_FAILED", 1, 1, 0, 0, 0),
    ("typed_false", False, "CONFIG_CHECK_FAILED", 1, 1, 0, 0, 0),
    ("typed_true_specific", True, "RUNTIME_ARCHIVE_INTEGRITY_FAILED", 1, 1, 0, 0, 0),
    ("typed_false_specific", False, "RUNTIME_ARCHIVE_INTEGRITY_FAILED", 1, 1, 0, 0, 0),
    ("recover_false", False, "OWNED_SESSION_RECOVERY_FAILED", 1, 0, 0, 0, 0),
    ("recover_throw", False, "OWNED_SESSION_RECOVERY_FAILED", 1, 0, 0, 0, 0),
    ("acquire_false", False, "SYSTEM_PROXY_RECOVERY_FAILED", 1, 1, 1, 1, 0),
    ("acquire_throw", False, "SYSTEM_PROXY_ACQUIRE_FAILED", 1, 1, 1, 1, 0),
    ("stop_false", False, "SESSION_CLEANUP_FAILED", 1, 1, 1, 1, 1),
]


@pytest.mark.parametrize("scenario,cleanup,reason,recover,start,acquire,stop,lease_stop", CASES,
                         ids=[case[0] for case in CASES])
def test_client_cleanup_is_propagated(
    client_cleanup_harness: tuple[Path, str], tmp_path: Path,
    scenario: str, cleanup: bool, reason: str, recover: int, start: int,
    acquire: int, stop: int, lease_stop: int,
) -> None:
    executable, digest = client_cleanup_harness
    home = tmp_path / "empty-home"
    home.mkdir()
    run = subprocess.run([str(executable), scenario, str(home)], cwd=tmp_path,
                         capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    (tmp_path / "run.stdout.bin").write_bytes(run.stdout)
    (tmp_path / "run.stderr.bin").write_bytes(run.stderr)
    (tmp_path / "source-sha256.txt").write_text(digest + "\n", encoding="ascii")
    assert run.returncode == 0, (run.stdout + run.stderr).decode("utf-8", "replace")
    value = json.loads(run.stdout)
    result = value["result"]
    assert result["status"] == "FAILED"
    assert result["cleanup_verified"] is cleanup
    assert result["reason"] == reason
    assert result["transport"] == "SingBoxHttps"
    assert result["target_id"] == "codex-desktop"
    assert result["process_exit_code"] == -1
    assert [value[key] for key in ["recover_calls", "start_calls", "acquire_calls", "own_stop_calls", "lease_stop_calls"]] == [recover, start, acquire, stop, lease_stop]
    assert value["fake_client_exists"] is False
    assert value["home_entries"] == 0
    assert list(home.iterdir()) == []
