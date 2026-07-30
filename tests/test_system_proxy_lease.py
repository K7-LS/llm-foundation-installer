from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
import winreg
import zipfile
from pathlib import Path

import pytest

from test_launcher_runtime import (
    _compile_fake_singbox,
    _write_runtime_lock,
)


REPOSITORY = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPOSITORY / "tools" / "build-gui.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe")
TEST_REGISTRY_PREFIX = r"Software\K7AITests"
APPLIED_PROXY = "127.0.0.1:43191"


def _write_test_only_client_lock(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "official_only": False,
                "test_only": True,
                "platform": {
                    "os": "windows",
                    "architecture": "x64",
                    "minimum_build": 19041,
                },
                "clients": [
                    {
                        "id": "opencode-desktop",
                        "target": "opencode",
                        "display_name": "OpenCode Desktop",
                        "role": "desktop",
                        "required_for_base": False,
                        "required_for_employee": True,
                        "version": "1.0.0",
                        "source_kind": "download",
                        "url": (
                            "http://127.0.0.1:43117/"
                            "opencode-desktop.exe"
                        ),
                        "sha256": "0" * 64,
                        "artifact_kind": "portable-exe",
                        "archive_entry": None,
                        "publisher": None,
                        "signature_required": False,
                        "install_mode": "managed-desktop",
                        "detect_commands": [],
                        "version_arguments": [],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def lease_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("system-proxy-lease-bundle")
    source_lock = root / "client-sources.lock.json"
    _write_test_only_client_lock(source_lock)
    output = root / "center"
    result = subprocess.run(
        [
            str(POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(BUILD_SCRIPT),
            "-OutputRoot",
            str(output),
            "-Edition",
            "Employee",
            "-ProductRole",
            "LaunchCenter",
            "-ClientSourcesLock",
            str(source_lock),
            "-AllowLocalTestSources",
        ],
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


@pytest.fixture(scope="module")
def appx_lease_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("appx-system-proxy-bundle")
    fake = root / "sing-box.exe"
    _compile_fake_singbox(fake)
    archive = root / "sing-box-fixture.zip"
    entry = "sing-box-1.13.14-windows-amd64/sing-box.exe"
    with zipfile.ZipFile(archive, "w") as package:
        package.write(fake, entry)
    runtime_lock = root / "runtime-sources.lock.json"
    _write_runtime_lock(runtime_lock, archive, entry)
    client_lock = root / "client-sources.lock.json"
    _write_test_only_client_lock(client_lock)
    output = root / "center"
    result = subprocess.run(
        [
            str(POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(BUILD_SCRIPT),
            "-OutputRoot",
            str(output),
            "-Edition",
            "Employee",
            "-ProductRole",
            "LaunchCenter",
            "-ClientSourcesLock",
            str(client_lock),
            "-RuntimeSourcesLock",
            str(runtime_lock),
            "-AllowLocalTestSources",
        ],
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(archive, output / archive.name)
    return output


@pytest.fixture
def registry_key() -> str:
    leaf = "system-proxy-lease-" + uuid.uuid4().hex
    subkey = TEST_REGISTRY_PREFIX + "\\" + leaf
    assert subkey.startswith(TEST_REGISTRY_PREFIX + "\\")
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        subkey,
        0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    ) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(
            key,
            "ProxyServer",
            0,
            winreg.REG_SZ,
            "sentinel.invalid:8899",
        )
    try:
        yield subkey
    finally:
        assert subkey.startswith(TEST_REGISTRY_PREFIX + "\\")
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            subkey,
            0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            while True:
                try:
                    value_name = winreg.EnumValue(key, 0)[0]
                except OSError:
                    break
                winreg.DeleteValue(key, value_name)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)


def _registry_snapshot(subkey: str) -> dict[str, tuple[object, int]]:
    assert subkey.startswith(TEST_REGISTRY_PREFIX + "\\")
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        subkey,
        0,
        winreg.KEY_READ,
    ) as key:
        return {
            name: winreg.QueryValueEx(key, name)
            for name in ("ProxyEnable", "ProxyServer")
        }


def _run_json(
    bundle: Path,
    action: str,
    home: Path,
    subkey: str,
    *extra: str,
) -> tuple[int, dict[str, object]]:
    assert subkey.startswith(TEST_REGISTRY_PREFIX + "\\")
    result = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--system-proxy-test-json",
            action,
            str(home),
            subkey,
            "43191",
            *extra,
        ],
        cwd=bundle,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.stdout.strip(), result.stderr
    return result.returncode, json.loads(result.stdout)


def _run_bundle_json(
    bundle: Path,
    *arguments: str,
) -> tuple[int, dict[str, object]]:
    result = subprocess.run(
        [str(bundle / "LLMFoundationInstaller.exe"), *arguments],
        cwd=bundle,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.stdout.strip(), result.stderr
    return result.returncode, json.loads(result.stdout)


def _wait_for_applied(subkey: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = _registry_snapshot(subkey)
        if snapshot["ProxyEnable"] == (1, winreg.REG_DWORD) and snapshot[
            "ProxyServer"
        ] == (APPLIED_PROXY, winreg.REG_SZ):
            return
        time.sleep(0.05)
    raise AssertionError("test proxy was not applied")


def _wait_for_any_local_proxy(
    subkey: str,
    timeout: float = 15.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = _registry_snapshot(subkey)
        server = snapshot["ProxyServer"]
        if (
            snapshot["ProxyEnable"] == (1, winreg.REG_DWORD)
            and server[1] == winreg.REG_SZ
            and str(server[0]).startswith("127.0.0.1:")
        ):
            return
        time.sleep(0.05)
    raise AssertionError("AppX local proxy was not applied")


def _wait_for_snapshot(
    subkey: str,
    expected: dict[str, tuple[object, int]],
    timeout: float = 15.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _registry_snapshot(subkey) == expected:
            return
        time.sleep(0.05)
    raise AssertionError("test proxy was not restored")


def _wait_for_file(path: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.05)
    raise AssertionError(f"expected file was not created: {path}")


def _save_proxy_profile(bundle: Path, home: Path, tmp_path: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    profile = tmp_path / "connection.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "Proxy",
                "proxy": {
                    "type": "HTTP",
                    "host": "proxy.example.test",
                    "port": 8080,
                    "auth": {"mode": "None", "username": None},
                },
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--save-connection-json",
            str(home),
            str(profile),
        ],
        cwd=bundle,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _appx_command(
    bundle: Path,
    home: Path,
    registry_key: str,
    mode: str,
    *extra: Path,
) -> list[str]:
    fixture = Path(os.environ["WINDIR"]) / "System32" / "cmd.exe"
    assert fixture.is_file()
    return [
        str(bundle / "LLMFoundationInstaller.exe"),
        "--test-appx-singbox-json",
        str(home),
        "SingBoxHttp",
        registry_key,
        str(fixture),
        mode,
        *(str(value) for value in extra),
    ]


def _current_user_sid() -> str:
    result = subprocess.run(
        [
            str(POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "[Security.Principal.WindowsIdentity]::"
                "GetCurrent().User.Value"
            ),
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def test_normal_cycle_restores_exact_registry_values_and_kinds(
    lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    before = _registry_snapshot(registry_key)

    returncode, value = _run_json(
        lease_bundle,
        "normal-cycle",
        tmp_path / "home",
        registry_key,
    )

    assert returncode == 0
    assert value["status"] == "RESTORED"
    assert value["cleanup_verified"] is True
    assert value["lifecycle"] == ["PREPARED", "APPLIED", "RESTORED"]
    assert _registry_snapshot(registry_key) == before


def test_normal_process_exit_restores_active_lease(
    lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    before = _registry_snapshot(registry_key)

    returncode, value = _run_json(
        lease_bundle,
        "acquire",
        tmp_path / "home",
        registry_key,
    )

    assert returncode == 0
    assert value["status"] == "ACQUIRED"
    _wait_for_snapshot(registry_key, before)
    assert not (
        tmp_path
        / "home"
        / ".llm-foundation"
        / "system-proxy-lease.json"
    ).exists()


def test_owner_crash_is_restored_by_internal_watchdog(
    lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    before = _registry_snapshot(registry_key)
    stop_file = tmp_path / "never-created"
    home = tmp_path / "home"
    owner = subprocess.Popen(
        [
            str(lease_bundle / "LLMFoundationInstaller.exe"),
            "--system-proxy-test-json",
            "hold",
            str(home),
            registry_key,
            "43191",
            str(stop_file),
        ],
        cwd=lease_bundle,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    _wait_for_applied(registry_key)

    owner.kill()
    owner.communicate(timeout=10)

    _wait_for_snapshot(registry_key, before)
    assert not (
        home / ".llm-foundation" / "system-proxy-lease.json"
    ).exists()


def test_external_change_is_not_overwritten_and_blocks_next_acquire(
    lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    stop_file = tmp_path / "stop"
    owner = subprocess.Popen(
        [
            str(lease_bundle / "LLMFoundationInstaller.exe"),
            "--system-proxy-test-json",
            "hold",
            str(tmp_path / "home"),
            registry_key,
            "43191",
            str(stop_file),
        ],
        cwd=lease_bundle,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    _wait_for_applied(registry_key)
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        registry_key,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(
            key,
            "ProxyServer",
            0,
            winreg.REG_SZ,
            "external.invalid:7777",
        )
    stop_file.write_text("stop", encoding="utf-8")
    stdout, stderr = owner.communicate(timeout=20)
    assert stdout.strip(), stderr
    stopped = json.loads(stdout)

    assert owner.returncode == 20
    assert stopped["reason"] == "SYSTEM_PROXY_CHANGED_EXTERNALLY"
    assert _registry_snapshot(registry_key)["ProxyServer"] == (
        "external.invalid:7777",
        winreg.REG_SZ,
    )
    state_path = (
        tmp_path / "home" / ".llm-foundation" / "system-proxy-lease.json"
    )
    assert state_path.is_file()

    returncode, blocked = _run_json(
        lease_bundle,
        "acquire",
        tmp_path / "home",
        registry_key,
    )
    assert returncode == 20
    assert blocked["reason"] == "SYSTEM_PROXY_CHANGED_EXTERNALLY"


def test_external_change_during_cas_restore_is_not_marked_restored(
    lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    home = tmp_path / "home"
    ready = tmp_path / "cas-ready"
    resume = tmp_path / "cas-continue"
    environment = dict(os.environ)
    environment["K7_PROXY_CAS_READY"] = str(ready)
    environment["K7_PROXY_CAS_CONTINUE"] = str(resume)
    owner = subprocess.Popen(
        [
            str(lease_bundle / "LLMFoundationInstaller.exe"),
            "--system-proxy-test-json",
            "normal-cycle",
            str(home),
            registry_key,
            "43191",
        ],
        cwd=lease_bundle,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    _wait_for_file(ready)
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        registry_key,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(
            key,
            "ProxyServer",
            0,
            winreg.REG_SZ,
            "external-race.invalid:7777",
        )
    resume.write_text("continue", encoding="utf-8")
    stdout, stderr = owner.communicate(timeout=20)
    assert stdout.strip(), stderr
    value = json.loads(stdout)

    assert owner.returncode == 20
    assert value["cleanup_verified"] is False
    assert value["reason"] == "SYSTEM_PROXY_CHANGED_EXTERNALLY"
    assert _registry_snapshot(registry_key)["ProxyServer"] == (
        "external-race.invalid:7777",
        winreg.REG_SZ,
    )
    assert (
        home / ".llm-foundation" / "system-proxy-lease.json"
    ).is_file()


def test_two_concurrent_acquires_allow_only_one_owner(
    lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    stop_file = tmp_path / "stop"
    first = subprocess.Popen(
        [
            str(lease_bundle / "LLMFoundationInstaller.exe"),
            "--system-proxy-test-json",
            "hold",
            str(tmp_path / "home"),
            registry_key,
            "43191",
            str(stop_file),
        ],
        cwd=lease_bundle,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    _wait_for_applied(registry_key)

    returncode, second = _run_json(
        lease_bundle,
        "acquire",
        tmp_path / "home",
        registry_key,
    )

    assert returncode == 20
    assert second["reason"] == "SYSTEM_PROXY_LEASE_BUSY"
    stop_file.write_text("stop", encoding="utf-8")
    stdout, stderr = first.communicate(timeout=20)
    assert stdout.strip(), stderr
    assert first.returncode == 0


def test_state_write_failure_leaves_registry_byte_and_kind_equivalent(
    lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    before = _registry_snapshot(registry_key)
    invalid_home = tmp_path / "home-is-a-file"
    invalid_home.write_text("not a directory", encoding="utf-8")

    returncode, value = _run_json(
        lease_bundle,
        "acquire",
        invalid_home,
        registry_key,
    )

    assert returncode == 20
    assert value["reason"] == "SYSTEM_PROXY_STATE_WRITE_FAILED"
    assert _registry_snapshot(registry_key) == before


def test_appx_acquire_recovers_stale_owned_state_before_new_lease(
    appx_lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    before = _registry_snapshot(registry_key)
    home = tmp_path / "home"
    _save_proxy_profile(appx_lease_bundle, home, tmp_path)
    state_path = home / ".llm-foundation" / "system-proxy-lease.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    applied = {
        "ProxyEnable": (1, winreg.REG_DWORD),
        "ProxyServer": (APPLIED_PROXY, winreg.REG_SZ),
    }
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        registry_key,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        for name, (value, kind) in applied.items():
            winreg.SetValueEx(key, name, 0, kind, value)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sid": _current_user_sid(),
                "owner_pid": 1,
                "phase": "APPLIED",
                "registry_subkey": registry_key,
                "original": [
                    {
                        "name": name,
                        "exists": True,
                        "value": value,
                        "kind": kind,
                    }
                    for name, (value, kind) in before.items()
                ],
                "applied": [
                    {
                        "name": name,
                        "exists": True,
                        "value": value,
                        "kind": kind,
                    }
                    for name, (value, kind) in applied.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["K7_APPX_FIXTURE_ARGS"] = "/d /c exit 0"

    result = subprocess.run(
        _appx_command(
            appx_lease_bundle,
            home,
            registry_key,
            "success",
        ),
        cwd=appx_lease_bundle,
        env=environment,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )

    assert result.stdout.strip(), result.stderr
    value = json.loads(result.stdout)
    assert result.returncode == 0
    assert value["status"] == "PASS"
    assert value["cleanup_verified"] is True
    assert _registry_snapshot(registry_key) == before
    assert not state_path.exists()


def test_appx_route_conflict_rolls_back_acquired_system_proxy(
    appx_lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    before = _registry_snapshot(registry_key)
    home = tmp_path / "home"
    _save_proxy_profile(appx_lease_bundle, home, tmp_path)
    environment = dict(os.environ)
    environment["K7_APPX_FIXTURE_ARGS"] = "/d /c exit 0"

    result = subprocess.run(
        _appx_command(
            appx_lease_bundle,
            home,
            registry_key,
            "route-conflict",
        ),
        cwd=appx_lease_bundle,
        env=environment,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )

    assert result.stdout.strip(), result.stderr
    value = json.loads(result.stdout)
    assert result.returncode == 20
    assert value["status"] == "FAILED"
    assert value["reason"] == "ROUTE_ALREADY_ACTIVE"
    assert value["cleanup_verified"] is True
    assert _registry_snapshot(registry_key) == before
    assert not list(
        (
            home
            / ".llm-foundation"
            / "launcher-state"
            / "sessions"
        ).glob("*")
    )


@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_reason"),
    [
        ("success", "PASS", None),
        ("activation-failure", "FAILED", "APPX_ACTIVATION_FAILED"),
    ],
)
def test_appx_singbox_restores_proxy_after_success_or_activation_failure(
    appx_lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
    mode: str,
    expected_status: str,
    expected_reason: str | None,
) -> None:
    before = _registry_snapshot(registry_key)
    home = tmp_path / "home"
    _save_proxy_profile(appx_lease_bundle, home, tmp_path)
    environment = dict(os.environ)
    environment["K7_APPX_FIXTURE_ARGS"] = "/d /c exit 0"

    result = subprocess.run(
        _appx_command(
            appx_lease_bundle,
            home,
            registry_key,
            mode,
        ),
        cwd=appx_lease_bundle,
        env=environment,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )

    assert result.stdout.strip(), result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == expected_status
    assert value["reason"] == expected_reason
    assert value["uses_proxy"] is True
    assert value["cleanup_verified"] is True
    assert _registry_snapshot(registry_key) == before
    assert not list(
        (
            home
            / ".llm-foundation"
            / "launcher-state"
            / "sessions"
        ).glob("*")
    )


def test_stop_route_restores_proxy_without_killing_appx_client(
    appx_lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    before = _registry_snapshot(registry_key)
    home = tmp_path / "home"
    _save_proxy_profile(appx_lease_bundle, home, tmp_path)
    started = tmp_path / "started.txt"
    heartbeat = tmp_path / "heartbeat.txt"
    client_stop = tmp_path / "client-stop"
    route_stop = tmp_path / "route-stop"
    batch = tmp_path / "fixture.cmd"
    batch.write_text(
        "@echo off\r\n"
        "echo started>\"%K7_APPX_STARTED%\"\r\n"
        ":loop\r\n"
        "echo heartbeat>\"%K7_APPX_HEARTBEAT%\"\r\n"
        "if exist \"%K7_APPX_CLIENT_STOP%\" exit /b 0\r\n"
        "ping 127.0.0.1 -n 2 >nul\r\n"
        "goto loop\r\n",
        encoding="ascii",
    )
    environment = dict(os.environ)
    environment["K7_APPX_FIXTURE_ARGS"] = f'/d /c "{batch}"'
    environment["K7_APPX_STARTED"] = str(started)
    environment["K7_APPX_HEARTBEAT"] = str(heartbeat)
    environment["K7_APPX_CLIENT_STOP"] = str(client_stop)
    owner = subprocess.Popen(
        _appx_command(
            appx_lease_bundle,
            home,
            registry_key,
            "success",
            route_stop,
        ),
        cwd=appx_lease_bundle,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    try:
        _wait_for_any_local_proxy(registry_key)
        _wait_for_file(started)
        _wait_for_file(heartbeat)
        route_stop.write_text("stop", encoding="utf-8")
        _wait_for_snapshot(registry_key, before)
        reacquire_code, reacquired = _run_json(
            appx_lease_bundle,
            "normal-cycle",
            home,
            registry_key,
        )
        assert reacquire_code == 0
        assert reacquired["status"] == "RESTORED"
        assert reacquired["cleanup_verified"] is True
        first_mtime = heartbeat.stat().st_mtime_ns
        time.sleep(1.5)
        assert heartbeat.stat().st_mtime_ns > first_mtime
        assert owner.poll() is None
        client_stop.write_text("stop", encoding="utf-8")
        stdout, stderr = owner.communicate(timeout=20)
        assert stdout.strip(), stderr
        value = json.loads(stdout)
        assert value["cleanup_verified"] is True
    finally:
        client_stop.write_text("stop", encoding="utf-8")
        if owner.poll() is None:
            owner.kill()
            owner.communicate(timeout=10)


def test_stop_after_route_registration_prevents_client_activation(
    appx_lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    before = _registry_snapshot(registry_key)
    home = tmp_path / "home"
    _save_proxy_profile(appx_lease_bundle, home, tmp_path)
    registered = tmp_path / "route-registered"
    resume = tmp_path / "route-continue"
    route_stop = tmp_path / "route-stop"
    client_started = tmp_path / "client-started"
    batch = tmp_path / "should-not-start.cmd"
    batch.write_text(
        "@echo off\r\n"
        "echo started>\"%K7_APPX_STARTED%\"\r\n"
        "exit /b 0\r\n",
        encoding="ascii",
    )
    environment = dict(os.environ)
    environment["K7_APPX_FIXTURE_ARGS"] = f'/d /c "{batch}"'
    environment["K7_APPX_STARTED"] = str(client_started)
    environment["K7_ROUTE_REGISTERED_READY"] = str(registered)
    environment["K7_ROUTE_REGISTERED_CONTINUE"] = str(resume)
    owner = subprocess.Popen(
        _appx_command(
            appx_lease_bundle,
            home,
            registry_key,
            "success",
            route_stop,
        ),
        cwd=appx_lease_bundle,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    _wait_for_file(registered)
    route_stop.write_text("stop", encoding="utf-8")
    _wait_for_snapshot(registry_key, before)
    resume.write_text("continue", encoding="utf-8")
    stdout, stderr = owner.communicate(timeout=20)
    assert stdout.strip(), stderr
    value = json.loads(stdout)

    assert owner.returncode == 20
    assert value["status"] == "FAILED"
    assert value["reason"] == "ROUTE_STOPPED_BEFORE_CLIENT_START"
    assert value["cleanup_verified"] is True
    assert not client_started.exists()


def test_stop_cleanup_failure_is_returned_to_waiting_launch(
    appx_lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    home = tmp_path / "home"
    _save_proxy_profile(appx_lease_bundle, home, tmp_path)
    registered = tmp_path / "route-registered"
    resume = tmp_path / "route-continue"
    route_stop = tmp_path / "route-stop"
    client_started = tmp_path / "client-started"
    batch = tmp_path / "should-not-start.cmd"
    batch.write_text(
        "@echo off\r\n"
        "echo started>\"%K7_APPX_STARTED%\"\r\n"
        "exit /b 0\r\n",
        encoding="ascii",
    )
    environment = dict(os.environ)
    environment["K7_APPX_FIXTURE_ARGS"] = f'/d /c "{batch}"'
    environment["K7_APPX_STARTED"] = str(client_started)
    environment["K7_ROUTE_REGISTERED_READY"] = str(registered)
    environment["K7_ROUTE_REGISTERED_CONTINUE"] = str(resume)
    owner = subprocess.Popen(
        _appx_command(
            appx_lease_bundle,
            home,
            registry_key,
            "success",
            route_stop,
        ),
        cwd=appx_lease_bundle,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    _wait_for_file(registered)
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        registry_key,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(
            key,
            "ProxyServer",
            0,
            winreg.REG_SZ,
            "external-stop.invalid:7777",
        )
    route_stop.write_text("stop", encoding="utf-8")
    sessions = (
        home
        / ".llm-foundation"
        / "launcher-state"
        / "sessions"
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and list(sessions.glob("*")):
        time.sleep(0.05)
    assert not list(sessions.glob("*"))
    resume.write_text("continue", encoding="utf-8")
    stdout, stderr = owner.communicate(timeout=20)
    assert stdout.strip(), stderr
    value = json.loads(stdout)

    assert owner.returncode == 20
    assert value["status"] == "FAILED"
    assert value["reason"] == "SYSTEM_PROXY_CHANGED_EXTERNALLY"
    assert value["cleanup_verified"] is False
    assert _registry_snapshot(registry_key)["ProxyServer"] == (
        "external-stop.invalid:7777",
        winreg.REG_SZ,
    )
    assert (
        home / ".llm-foundation" / "system-proxy-lease.json"
    ).is_file()
    assert not client_started.exists()


@pytest.mark.parametrize("external_proxy_change", [False, True])
def test_appx_owner_crash_restores_proxy_and_owned_singbox_only(
    appx_lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
    external_proxy_change: bool,
) -> None:
    before = _registry_snapshot(registry_key)
    home = tmp_path / "home"
    _save_proxy_profile(appx_lease_bundle, home, tmp_path)
    started = tmp_path / "started.txt"
    heartbeat = tmp_path / "heartbeat.txt"
    client_stop = tmp_path / "client-stop"
    batch = tmp_path / "fixture.cmd"
    batch.write_text(
        "@echo off\r\n"
        "echo started>\"%K7_APPX_STARTED%\"\r\n"
        ":loop\r\n"
        "echo heartbeat>\"%K7_APPX_HEARTBEAT%\"\r\n"
        "if exist \"%K7_APPX_CLIENT_STOP%\" exit /b 0\r\n"
        "ping 127.0.0.1 -n 2 >nul\r\n"
        "goto loop\r\n",
        encoding="ascii",
    )
    environment = dict(os.environ)
    environment["K7_APPX_FIXTURE_ARGS"] = f'/d /c "{batch}"'
    environment["K7_APPX_STARTED"] = str(started)
    environment["K7_APPX_HEARTBEAT"] = str(heartbeat)
    environment["K7_APPX_CLIENT_STOP"] = str(client_stop)
    owner = subprocess.Popen(
        _appx_command(
            appx_lease_bundle,
            home,
            registry_key,
            "success",
        ),
        cwd=appx_lease_bundle,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    try:
        _wait_for_any_local_proxy(registry_key)
        _wait_for_file(started)
        if external_proxy_change:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                registry_key,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(
                    key,
                    "ProxyServer",
                    0,
                    winreg.REG_SZ,
                    "external-crash.invalid:7777",
                )
        owner.kill()
        owner.wait(timeout=10)
        if not external_proxy_change:
            _wait_for_snapshot(registry_key, before)
        sessions = (
            home
            / ".llm-foundation"
            / "launcher-state"
            / "sessions"
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and list(sessions.glob("*")):
            time.sleep(0.05)
        assert not list(sessions.glob("*"))
        state_path = (
            home / ".llm-foundation" / "system-proxy-lease.json"
        )
        if external_proxy_change:
            assert _registry_snapshot(registry_key)["ProxyServer"] == (
                "external-crash.invalid:7777",
                winreg.REG_SZ,
            )
            assert state_path.is_file()
        else:
            assert not state_path.exists()
        first_mtime = heartbeat.stat().st_mtime_ns
        time.sleep(1.5)
        assert heartbeat.stat().st_mtime_ns > first_mtime
    finally:
        client_stop.write_text("stop", encoding="utf-8")


def test_watchdog_does_not_stop_singbox_owned_by_another_live_process(
    appx_lease_bundle: Path,
    tmp_path: Path,
    registry_key: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True)
    returncode, runtime = _run_bundle_json(
        appx_lease_bundle,
        "--ensure-runtime-json",
        str(home),
    )
    assert returncode == 0
    executable = Path(str(runtime["executable_path"]))
    assert executable.is_file()
    config = tmp_path / "live-owner-config.json"
    config.write_text('{"listen_port":18120}', encoding="utf-8")
    process = subprocess.Popen(
        [str(executable), "run", "-c", str(config)],
        cwd=appx_lease_bundle,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    session_root = (
        home
        / ".llm-foundation"
        / "launcher-state"
        / "sessions"
        / uuid.uuid4().hex
    )
    session_root.mkdir(parents=True)
    (session_root / "owned-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "nonce": uuid.uuid4().hex,
                "owner_pid": os.getpid(),
                "process_id": process.pid,
                "listen_port": 18120,
                "executable_path": str(executable),
                "executable_sha256": hashlib.sha256(
                    executable.read_bytes()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    try:
        watchdog = subprocess.run(
            [
                str(appx_lease_bundle / "LLMFoundationInstaller.exe"),
                "--system-proxy-watchdog",
                "999999",
                str(home),
                registry_key,
            ],
            cwd=appx_lease_bundle,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert watchdog.stdout.strip(), watchdog.stderr
        value = json.loads(watchdog.stdout)
        assert watchdog.returncode == 0
        assert value["cleanup_verified"] is True
        assert process.poll() is None
        assert session_root.is_dir()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
