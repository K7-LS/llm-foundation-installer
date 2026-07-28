from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import textwrap
import zipfile
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPOSITORY / "tools" / "build-gui.ps1"
RUNTIME_LOCK = REPOSITORY / "runtime-sources.lock.json"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_runtime_lock(path: Path, archive: Path, entry: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "test_only": True,
                "runtime": {
                    "id": "sing-box",
                    "version": "1.13.14",
                    "url": "http://127.0.0.1:43118/" + archive.name,
                    "sha256": _sha256(archive),
                    "archive_kind": "zip",
                    "archive_entry": entry,
                    "executable_name": "sing-box.exe",
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _build(output: Path, runtime_lock: Path) -> Path:
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
            "-RuntimeSourcesLock",
            str(runtime_lock),
            "-AllowLocalTestSources",
        ],
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def _run_json(bundle: Path, *arguments: str) -> tuple[int, dict[str, object]]:
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


def _compile_fake_singbox(path: Path) -> None:
    roots = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")),
        Path(os.environ.get("ProgramFiles", "C:/Program Files")),
    ]
    compilers: list[Path] = []
    for root in roots:
        compilers.extend(
            root.glob(
                "Microsoft Visual Studio/*/*/MSBuild/Current/Bin/Roslyn/csc.exe"
            )
        )
    framework = Path(
        "C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    )
    if framework.is_file():
        compilers.append(framework)
    assert compilers, "C# compiler is unavailable"
    source = path.with_suffix(".cs")
    source.write_text(
        textwrap.dedent(
            r"""
            using System;
            using System.IO;
            using System.Net;
            using System.Net.Sockets;
            using System.Text.RegularExpressions;
            using System.Threading;
            public static class FakeSingBox
            {
                public static int Main(string[] args)
                {
                    string log = Environment.GetEnvironmentVariable(
                        "K7_FAKE_COMMAND_LOG"
                    );
                    if (!String.IsNullOrEmpty(log))
                    {
                        File.AppendAllText(
                            log,
                            String.Join(" ", args) + "\n"
                        );
                    }
                    if (args.Length != 3 || args[1] != "-c" ||
                        !File.Exists(args[2]))
                    {
                        return 11;
                    }
                    if (args[0] == "check")
                    {
                        return Environment.GetEnvironmentVariable(
                            "K7_FAKE_CHECK_FAIL"
                        ) == "1" ? 12 : 0;
                    }
                    if (args[0] != "run")
                    {
                        return 13;
                    }
                    string json = File.ReadAllText(args[2]);
                    Match portMatch = Regex.Match(
                        json,
                        "\"listen_port\"\\s*:\\s*(\\d+)"
                    );
                    if (!portMatch.Success)
                    {
                        return 14;
                    }
                    int port = Int32.Parse(portMatch.Groups[1].Value);
                    TcpListener listener = new TcpListener(
                        IPAddress.Loopback,
                        port
                    );
                    listener.Start();
                    while (true)
                    {
                        Thread.Sleep(250);
                    }
                }
            }
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            str(sorted(compilers)[0]),
            "/nologo",
            "/target:exe",
            f"/out:{path}",
            str(source),
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_official_runtime_lock_is_immutable_and_versioned() -> None:
    value = json.loads(RUNTIME_LOCK.read_text(encoding="utf-8"))
    runtime = value["runtime"]
    assert value == {
        "schema_version": 1,
        "test_only": False,
        "runtime": {
            "id": "sing-box",
            "version": "1.13.14",
            "url": (
                "https://github.com/SagerNet/sing-box/releases/download/"
                "v1.13.14/sing-box-1.13.14-windows-amd64.zip"
            ),
            "sha256": (
                "f580782c6dd10f7691c66cea1d7c421813c5fbf7e305d1ee7ce0c3a40d196341"
            ),
            "archive_kind": "zip",
            "archive_entry": (
                "sing-box-1.13.14-windows-amd64/sing-box.exe"
            ),
            "executable_name": "sing-box.exe",
        },
    }
    assert "latest" not in runtime["url"]
    assert runtime["url"].startswith("https://github.com/SagerNet/")


def test_runtime_install_and_verify_are_archive_hash_bound(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "sing-box-fixture.zip"
    entry = "sing-box-1.13.14-windows-amd64/sing-box.exe"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(entry, b"fake-sing-box-runtime\n")
    lock = tmp_path / "runtime.lock.json"
    _write_runtime_lock(lock, archive, entry)
    bundle = _build(tmp_path / "center", lock)
    home = tmp_path / "home"

    returncode, installed = _run_json(
        bundle,
        "--install-runtime-json",
        str(home),
        str(archive),
    )
    expected_executable = (
        home
        / ".llm-foundation"
        / "runtimes"
        / "sing-box"
        / "1.13.14"
        / "sing-box.exe"
    )
    assert returncode == 0
    assert installed["status"] == "INSTALLED"
    assert installed["executable_path"] == str(expected_executable.resolve())
    assert installed["archive_sha256"] == _sha256(archive)

    returncode, verified = _run_json(
        bundle,
        "--verify-runtime-json",
        str(home),
    )
    assert returncode == 0
    assert verified["status"] == "VERIFIED"
    assert verified["executable_path"] == str(expected_executable.resolve())

    expected_executable.write_bytes(b"tampered")
    returncode, verified = _run_json(
        bundle,
        "--verify-runtime-json",
        str(home),
    )
    assert returncode == 20
    assert verified["status"] == "BLOCKED"
    assert verified["reason"] == "RUNTIME_EXECUTABLE_INTEGRITY_FAILED"


def test_runtime_install_rejects_unsafe_zip_entry(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    entry = "../sing-box.exe"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(entry, b"unsafe")
    lock = tmp_path / "runtime.lock.json"
    _write_runtime_lock(lock, archive, entry)
    bundle = _build(tmp_path / "center", lock)

    returncode, value = _run_json(
        bundle,
        "--install-runtime-json",
        str(tmp_path / "home"),
        str(archive),
    )
    assert returncode == 20
    assert value["status"] == "BLOCKED"
    assert value["reason"] == "RUNTIME_ARCHIVE_ENTRY_UNSAFE"
    assert not (tmp_path / "home" / ".llm-foundation").exists()


def test_singbox_https_config_is_targeted_and_secret_redacted(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "sing-box-fixture.zip"
    entry = "sing-box-1.13.14-windows-amd64/sing-box.exe"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(entry, b"fake-sing-box-runtime\n")
    lock = tmp_path / "runtime.lock.json"
    _write_runtime_lock(lock, archive, entry)
    bundle = _build(tmp_path / "center", lock)
    home = tmp_path / "home"
    home.mkdir()
    profile = tmp_path / "connection.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "Proxy",
                "proxy": {
                    "type": "HTTPS",
                    "host": "proxy.example.test",
                    "port": 8443,
                    "auth": {
                        "mode": "UsernamePassword",
                        "username": "fixture-user",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    sentinel = "S3cret-Not-In-Evidence!"
    saved = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--save-connection-json",
            str(home),
            str(profile),
        ],
        cwd=bundle,
        input=sentinel + "\n",
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert saved.returncode == 0, saved.stdout + saved.stderr
    assert sentinel not in saved.stdout
    assert sentinel not in saved.stderr
    config_path = tmp_path / "session" / "config.json"

    returncode, summary = _run_json(
        bundle,
        "--write-singbox-config-test-json",
        str(home),
        "opencode-desktop",
        "SingBoxHttps",
        "18082",
        str(config_path),
    )

    assert returncode == 0
    assert sentinel not in json.dumps(summary)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["inbounds"][0]["set_system_proxy"] is False
    assert config["outbounds"][0]["tag"] == "upstream"
    assert config["outbounds"][0]["password"] == sentinel
    assert config["outbounds"][0]["tls"] == {
        "enabled": True,
        "server_name": "proxy.example.test",
        "insecure": False,
        "alpn": ["http/1.1"],
    }
    assert config["outbounds"][1] == {"tag": "direct", "type": "direct"}
    assert config["route"]["final"] == "direct"
    serialized_rules = json.dumps(config["route"]["rules"])
    assert "opencode.ai" in serialized_rules
    assert "openai.com" in serialized_rules
    assert "example.com" not in serialized_rules
    assert summary == {
        "status": "CONFIG_WRITTEN",
        "target_id": "opencode-desktop",
        "route": "SingBoxHttps",
        "listen_port": 18082,
        "uses_tls": True,
        "uses_auth": True,
        "route_final": "direct",
        "secret_redacted": True,
    }


def test_singbox_session_owns_runtime_and_removes_secret_config(
    tmp_path: Path,
) -> None:
    fake = tmp_path / "sing-box.exe"
    _compile_fake_singbox(fake)
    archive = tmp_path / "sing-box-fixture.zip"
    entry = "sing-box-1.13.14-windows-amd64/sing-box.exe"
    with zipfile.ZipFile(archive, "w") as package:
        package.write(fake, entry)
    lock = tmp_path / "runtime.lock.json"
    _write_runtime_lock(lock, archive, entry)
    bundle = _build(tmp_path / "center", lock)
    home = tmp_path / "home"
    home.mkdir()
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
                    "auth": {
                        "mode": "None",
                        "username": None,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    saved = subprocess.run(
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
    assert saved.returncode == 0, saved.stdout + saved.stderr
    returncode, _ = _run_json(
        bundle,
        "--install-runtime-json",
        str(home),
        str(archive),
    )
    assert returncode == 0
    command_log = tmp_path / "commands.txt"
    environment = dict(os.environ)
    environment["K7_FAKE_COMMAND_LOG"] = str(command_log)
    result = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--test-singbox-session-json",
            str(home),
            "opencode-desktop",
            "SingBoxHttp",
        ],
        cwd=bundle,
        text=True,
        capture_output=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )
    assert result.stdout.strip(), result.stderr
    value = json.loads(result.stdout)

    assert result.returncode == 0
    assert value["status"] == "PASS"
    assert value["cleanup_verified"] is True
    assert value["secret_redacted"] is True
    assert value["lifecycle"] == [
        "PROFILE_VALIDATED",
        "RUNTIME_VERIFIED",
        "CONFIG_CHECKED",
        "LOCAL_PROXY_READY",
        "RUNTIME_STOPPED",
        "TEMP_REMOVED",
    ]
    assert "password" not in result.stdout.lower()
    assert not list(
        (
            home
            / ".llm-foundation"
            / "launcher-state"
            / "sessions"
        ).glob("*")
    )
    commands = command_log.read_text(encoding="utf-8").splitlines()
    assert commands[0].startswith("check -c ")
    assert commands[1].startswith("run -c ")

    failed_environment = dict(environment)
    failed_environment["K7_FAKE_CHECK_FAIL"] = "1"
    failed = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--test-singbox-session-json",
            str(home),
            "opencode-desktop",
            "SingBoxHttp",
        ],
        cwd=bundle,
        text=True,
        capture_output=True,
        encoding="utf-8",
        env=failed_environment,
        timeout=30,
    )
    failed_value = json.loads(failed.stdout)
    assert failed.returncode == 20
    assert failed_value["status"] == "FAILED"
    assert failed_value["reason"] == "CONFIG_CHECK_FAILED"
    assert failed_value["cleanup_verified"] is True
    assert not list(
        (
            home
            / ".llm-foundation"
            / "launcher-state"
            / "sessions"
        ).glob("*")
    )
