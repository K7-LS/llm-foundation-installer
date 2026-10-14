"""Real Windows processes, production session host and original proxy watchdog.

Only client/runtime and UI are fixtures. Proxy acquisition/restoration and owned
session recovery execute the production code on isolated HKCU test keys.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows lifecycle")

def _wait(fn, seconds=25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if fn():
            return
        time.sleep(.08)
    raise AssertionError("Lifecycle condition timed out")

def _alive(pid):
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == 259

def _registry(key):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
        result = {}
        for i in range(winreg.QueryInfoKey(handle)[1]):
            name, value, kind = winreg.EnumValue(handle, i)
            result[name] = (value, kind)
        return result

def _reachable(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as stream:
            return b"200 OK" in stream.recv(256)
    except OSError:
        return False

@pytest.fixture(scope="module")
def lifecycle_exe(tmp_path_factory, release_bundle):
    folder = tmp_path_factory.mktemp("launcher-lifecycle")
    executable = folder / "LifecycleFixture.exe"
    compiler = os.environ.get("K7_ROSLYN_COMPILER_PATH")
    if not compiler:
        compiler = "C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    result = subprocess.run([
        compiler, "/nologo", "/target:exe", "/r:System.Web.Extensions.dll",
        "/out:" + str(executable),
        str(ROOT / "src/gui/LaunchSessionHost.cs"),
        str(ROOT / "tests/fixtures/launch_session_lifecycle.cs"),
    ], capture_output=True, timeout=45)
    assert result.returncode == 0, result.stdout.decode(errors="replace")
    return executable, release_bundle / "LLMFoundationInstaller.exe"

@pytest.mark.parametrize("case", [
    "close", "crash-ui", "natural", "stop", "reset", "repeat", "crash-worker", "failure",
    "stop-false", "stop-throw"
])
def test_desktop_route_lifetime(case, lifecycle_exe, tmp_path):
    import winreg
    executable, original = lifecycle_exe
    system_key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    live_proxy = _registry(system_key)
    key_path = "Software\\K7AITests\\LauncherLifetime" + uuid.uuid4().hex
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, "fixture-original:4567")
    initial = _registry(key_path)
    env = os.environ.copy()
    env.update(K7_FIXTURE_ROOT=str(tmp_path), K7_FIXTURE_REGISTRY=key_path,
               K7_FIXTURE_ORIGINAL=str(original))
    if case == "failure":
        env["K7_FIXTURE_FAIL"] = "1"
    if case.startswith("stop-"):
        env["K7_FIXTURE_STOP_FAIL"] = case.split("-", 1)[1]
    mode = case if case in ["close", "stop", "reset", "repeat"] else "wait"
    if case.startswith("stop-"):
        mode = "stop"
    with (tmp_path / "process.log").open("wb") as log:
        ui = subprocess.Popen([str(executable), "--fixture-ui", mode],
                              env=env, stdout=log, stderr=log)
        try:
            if case == "failure":
                assert ui.wait(20) == 20
                result = json.loads((tmp_path / "result.json").read_text())
                assert result["cleanup_verified"] and result["reason"] == "CLIENT_START_FAILED"
            else:
                _wait(lambda: (tmp_path / "ui-ready").exists() or ui.poll() is not None)
                assert (tmp_path / "ui-ready").exists(), (tmp_path / "process.log").read_text(errors="replace")
                worker, runtime, client = [int((tmp_path / (name + ".pid")).read_text())
                                           for name in ["worker", "runtime", "client"]]
                port = int((tmp_path / "runtime.port").read_text())
                if case in ["close", "crash-ui"]:
                    if case == "crash-ui":
                        ui.kill()
                    ui.wait(5)
                    assert _alive(worker) and _alive(client) and _alive(runtime)
                    assert _reachable(port)
                    assert _registry(key_path) != initial
                    (tmp_path / "client-exit").touch()
                elif case == "natural":
                    assert _reachable(port)
                    (tmp_path / "client-exit").touch()
                    assert ui.wait(20) == 0
                    assert json.loads((tmp_path / "result.json").read_text())["cleanup_verified"]
                elif case in ["stop", "reset", "repeat", "stop-false", "stop-throw"]:
                    assert ui.wait(20) == 0
                    stopped = json.loads((tmp_path / "stop.json").read_text())
                    if case.startswith("stop-"):
                        assert not stopped["cleanup_verified"]
                        assert stopped["reason"] == "SESSION_HOST_STOP_NOT_VERIFIED"
                    else:
                        assert stopped["cleanup_verified"]
                    if case == "repeat":
                        assert json.loads((tmp_path / "repeat.json").read_text())["reason"] == "ROUTE_ALREADY_ACTIVE"
                else:
                    os.kill(worker, signal.SIGTERM)
                    assert ui.wait(15) == 20
                    result = json.loads((tmp_path / "result.json").read_text())
                    assert result["status"] == "FAILED" and not result["cleanup_verified"]
                    (tmp_path / "client-exit").touch()
                _wait(lambda: not any(_alive(p) for p in [worker, runtime, client]))
                _wait(lambda: _registry(key_path) == initial)
                _wait(lambda: not list((tmp_path / ".llm-foundation/launcher-state/sessions").glob("*")))
                assert not _reachable(port)
            assert _registry(key_path) == initial
            assert _registry(system_key) == live_proxy, "Live system proxy changed"
        finally:
            (tmp_path / "client-exit").touch()
            if ui.poll() is None:
                ui.kill()
                ui.wait(5)
            for name in ["worker", "runtime", "client"]:
                marker = tmp_path / (name + ".pid")
                if marker.exists():
                    pid = int(marker.read_text())
                    if _alive(pid):
                        os.kill(pid, signal.SIGTERM)
            # The unique, leaf-only registry key was created by this test.
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)

def test_worker_command_rejects_untrusted_arguments(release_bundle):
    exe = release_bundle / "LLMFoundationInstaller.exe"
    for args in [[], ["codex-desktop"], ["codex-desktop", "Direct", "bad-token"],
                 ["codex-desktop", "SingBoxHttp", "bad-token"]]:
        result = subprocess.run([str(exe), "--launch-session", *args], capture_output=True, timeout=15)
        assert result.returncode == 2
