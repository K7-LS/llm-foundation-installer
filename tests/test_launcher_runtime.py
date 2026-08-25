from __future__ import annotations

import hashlib
import http.server
import json
import os
import shutil
import stat
import subprocess
import textwrap
import threading
import zipfile
from pathlib import Path

import pytest

from test_gui import _accepted_foundation


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


def _build(
    output: Path,
    runtime_lock: Path,
    client_lock: Path | None = None,
    product_role: str = "LaunchCenter",
    foundation_root: Path | None = None,
) -> Path:
    command = [
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
            product_role,
            "-RuntimeSourcesLock",
            str(runtime_lock),
            "-AllowLocalTestSources",
    ]
    if client_lock is not None:
        command.extend(["-ClientSourcesLock", str(client_lock)])
    if foundation_root is not None:
        command.extend(["-FoundationPackageRoot", str(foundation_root)])
    result = subprocess.run(
        command,
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
            using System.Text;
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
                    if (args.Length == 0)
                    {
                        string output = Environment.GetEnvironmentVariable(
                            "K7_TEST_OUTPUT"
                        );
                        if (!String.IsNullOrEmpty(output))
                        {
                            File.WriteAllText(
                                output,
                                "HTTP_PROXY=" +
                                    (Environment.GetEnvironmentVariable(
                                        "HTTP_PROXY"
                                    ) ?? "<null>") + "\n" +
                                "HTTPS_PROXY=" +
                                    (Environment.GetEnvironmentVariable(
                                        "HTTPS_PROXY"
                                    ) ?? "<null>") + "\n"
                            );
                        }
                        return 0;
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
                    if (Environment.GetEnvironmentVariable(
                            "K7_FAKE_CLEANUP_BLOCK") == "1")
                    {
                        string marker = Path.Combine(
                            Path.GetDirectoryName(args[2]),
                            "cleanup-blocked.txt"
                        );
                        File.WriteAllText(marker, "blocked");
                        File.SetAttributes(
                            marker,
                            FileAttributes.ReadOnly
                        );
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
                        using (TcpClient client = listener.AcceptTcpClient())
                        {
                            Forward(client);
                        }
                    }
                }

                private static void Forward(TcpClient client)
                {
                    NetworkStream stream = client.GetStream();
                    string headers = ReadHeaders(stream);
                    if (String.IsNullOrWhiteSpace(headers))
                    {
                        return;
                    }
                    if (Environment.GetEnvironmentVariable(
                            "K7_FAKE_PROXY_AUTH_FAILED") == "1")
                    {
                        Console.Error.WriteLine(
                            "authentication required"
                        );
                        return;
                    }
                    if (Environment.GetEnvironmentVariable(
                            "K7_FAKE_PROXY_BROKEN") == "1")
                    {
                        return;
                    }
                    if (Environment.GetEnvironmentVariable(
                            "K7_FAKE_REQUIRE_CURL_PROBE") == "1" &&
                        !Regex.IsMatch(
                            headers,
                            "(?im)^User-Agent:\\s*K7-AI-Launch-Center"))
                    {
                        byte[] denied = Encoding.ASCII.GetBytes(
                            "HTTP/1.1 403 Forbidden\r\n" +
                            "Content-Length: 0\r\n" +
                            "Connection: close\r\n\r\n"
                        );
                        stream.Write(denied, 0, denied.Length);
                        stream.Flush();
                        return;
                    }
                    if (Environment.GetEnvironmentVariable(
                            "K7_FAKE_REQUIRE_USER_AGENT") == "1" &&
                        !Regex.IsMatch(
                            headers,
                            "(?im)^User-Agent:\\s*\\S+"))
                    {
                        byte[] denied = Encoding.ASCII.GetBytes(
                            "HTTP/1.1 403 Forbidden\r\n" +
                            "Content-Length: 0\r\n" +
                            "Connection: close\r\n\r\n"
                        );
                        stream.Write(denied, 0, denied.Length);
                        stream.Flush();
                        return;
                    }
                    if (Environment.GetEnvironmentVariable(
                            "K7_FAKE_REQUIRE_CURL_ACCEPT") == "1" &&
                        !Regex.IsMatch(
                            headers,
                            "(?im)^Accept:\\s*\\*/\\*\\s*$"))
                    {
                        byte[] denied = Encoding.ASCII.GetBytes(
                            "HTTP/1.1 403 Forbidden\r\n" +
                            "Content-Length: 0\r\n" +
                            "Connection: close\r\n\r\n"
                        );
                        stream.Write(denied, 0, denied.Length);
                        stream.Flush();
                        return;
                    }
                    string firstLine = headers.Split(
                        new[] { "\r\n" },
                        StringSplitOptions.None
                    )[0];
                    string[] parts = firstLine.Split(' ');
                    Uri target;
                    if (parts.Length != 3 ||
                        !Uri.TryCreate(
                            parts[1],
                            UriKind.Absolute,
                            out target))
                    {
                        return;
                    }
                    string upstreamPort =
                        Environment.GetEnvironmentVariable(
                            "K7_FAKE_UPSTREAM_PORT");
                    Uri upstreamTarget = String.IsNullOrEmpty(upstreamPort)
                        ? target
                        : new Uri(
                            "http://127.0.0.1:" + upstreamPort +
                            target.PathAndQuery
                        );
                    HttpWebRequest request =
                        (HttpWebRequest)WebRequest.Create(upstreamTarget);
                    request.Method = "GET";
                    request.Proxy = null;
                    request.Timeout = 5000;
                    HttpWebResponse response = null;
                    try
                    {
                        response = (HttpWebResponse)request.GetResponse();
                    }
                    catch (WebException exception)
                    {
                        response = exception.Response as HttpWebResponse;
                        if (response == null)
                        {
                            throw;
                        }
                    }
                    using (response)
                    using (Stream input = response.GetResponseStream())
                    using (MemoryStream body = new MemoryStream())
                    {
                        input.CopyTo(body);
                        string forwardLog =
                            Environment.GetEnvironmentVariable(
                                "K7_FAKE_FORWARD_LOG");
                        if (!String.IsNullOrEmpty(forwardLog))
                        {
                            File.AppendAllText(
                                forwardLog,
                                target.AbsolutePath + "\n"
                            );
                        }
                        byte[] payload = body.ToArray();
                        byte[] prefix = Encoding.ASCII.GetBytes(
                            "HTTP/1.1 " +
                            ((int)response.StatusCode).ToString() +
                            " OK\r\nContent-Length: " +
                            payload.Length.ToString() +
                            "\r\nConnection: close\r\n\r\n"
                        );
                        stream.Write(prefix, 0, prefix.Length);
                        stream.Write(payload, 0, payload.Length);
                        stream.Flush();
                    }
                }

                private static string ReadHeaders(NetworkStream stream)
                {
                    MemoryStream buffer = new MemoryStream();
                    int matched = 0;
                    byte[] marker = new byte[] { 13, 10, 13, 10 };
                    while (buffer.Length < 65536)
                    {
                        int value = stream.ReadByte();
                        if (value < 0)
                        {
                            break;
                        }
                        buffer.WriteByte((byte)value);
                        matched = value == marker[matched]
                            ? matched + 1
                            : (value == marker[0] ? 1 : 0);
                        if (matched == marker.Length)
                        {
                            break;
                        }
                    }
                    return Encoding.ASCII.GetString(buffer.ToArray());
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


def _compile_argument_probe(path: Path) -> None:
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
            public static class ArgumentProbe
            {
                public static int Main(string[] args)
                {
                    File.WriteAllText(
                        Environment.GetEnvironmentVariable("K7_TEST_OUTPUT"),
                        String.Join("\n", args)
                    );
                    return 0;
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


def test_runtime_ensure_installs_locked_bundle_archive(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "sing-box-fixture.zip"
    entry = "sing-box-1.13.14-windows-amd64/sing-box.exe"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(entry, b"fake-sing-box-runtime\n")
    lock = tmp_path / "runtime.lock.json"
    _write_runtime_lock(lock, archive, entry)
    bundle = _build(tmp_path / "center", lock)
    shutil.copy2(archive, bundle / archive.name)
    home = tmp_path / "home"

    returncode, value = _run_json(
        bundle,
        "--ensure-runtime-json",
        str(home),
    )

    assert returncode == 0
    assert value["status"] == "VERIFIED"
    assert value["archive_sha256"] == _sha256(archive)
    assert (
        home
        / ".llm-foundation"
        / "runtimes"
        / "sing-box"
        / "1.13.14"
        / "sing-box.exe"
    ).is_file()

    missing_bundle = _build(tmp_path / "missing-center", lock)
    returncode, value = _run_json(
        missing_bundle,
        "--ensure-runtime-json",
        str(tmp_path / "other-home"),
    )
    assert returncode == 20
    assert value["reason"] == "RUNTIME_BUNDLE_ARCHIVE_MISSING"


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
    client_lock = tmp_path / "client-sources.lock.json"
    shutil.copyfile(REPOSITORY / "client-sources.lock.json", client_lock)
    foundation_root = _accepted_foundation(tmp_path / "foundation-fixture")
    bundle = _build(
        tmp_path / "center",
        lock,
        client_lock,
        foundation_root=foundation_root,
    )
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
    process_rule = next(
        rule
        for rule in config["route"]["rules"]
        if "process_name" in rule
    )
    assert process_rule == {
        "process_name": ["OpenCode.exe", "opencode.exe"],
        "action": "route",
        "outbound": "upstream",
    }
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


def test_singbox_session_surfaces_concrete_runtime_reason(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "sing-box-fixture.zip"
    entry = "sing-box-1.13.14-windows-amd64/sing-box.exe"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(entry, b"fake-sing-box-runtime\n")
    lock = tmp_path / "runtime.lock.json"
    _write_runtime_lock(lock, archive, entry)
    bundle = _build(tmp_path / "center", lock)

    missing_home = tmp_path / "missing-home"
    missing_home.mkdir()
    missing_code, missing = _run_json(
        bundle,
        "--test-singbox-session-json",
        str(missing_home),
        "connection-test",
        "SingBoxHttp",
    )
    assert missing_code == 20
    assert missing["reason"] == "RUNTIME_BUNDLE_ARCHIVE_MISSING"

    shutil.copy2(archive, bundle / archive.name)
    tampered_home = tmp_path / "tampered-home"
    tampered_home.mkdir()
    installed_code, installed = _run_json(
        bundle,
        "--ensure-runtime-json",
        str(tampered_home),
    )
    assert installed_code == 0
    assert installed["status"] == "VERIFIED"
    (
        tampered_home
        / ".llm-foundation"
        / "runtimes"
        / "sing-box"
        / "1.13.14"
        / "source.zip"
    ).write_bytes(b"tampered")

    tampered_code, tampered = _run_json(
        bundle,
        "--test-singbox-session-json",
        str(tampered_home),
        "connection-test",
        "SingBoxHttp",
    )
    assert tampered_code == 20
    assert tampered["reason"] == "RUNTIME_ARCHIVE_INTEGRITY_FAILED"


@pytest.mark.parametrize("route", ["SingBoxHttp", "SingBoxHttps"])
@pytest.mark.parametrize("product_role", ["Installer", "LaunchCenter"])
def test_singbox_route_probe_forwards_real_local_http_request(
    tmp_path: Path,
    route: str,
    product_role: str,
) -> None:
    class Upstream(http.server.BaseHTTPRequestHandler):
        received_paths: list[str] = []
        status_code = 200

        def do_GET(self) -> None:
            type(self).received_paths.append(self.path)
            self.send_response(type(self).status_code)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"route-ok")

        def log_message(self, *args: object) -> None:
            return

    fake = tmp_path / "sing-box.exe"
    _compile_fake_singbox(fake)
    archive = tmp_path / "sing-box-fixture.zip"
    entry = "sing-box-1.13.14-windows-amd64/sing-box.exe"
    with zipfile.ZipFile(archive, "w") as package:
        package.write(fake, entry)
    lock = tmp_path / "runtime.lock.json"
    _write_runtime_lock(lock, archive, entry)
    bundle = _build(
        tmp_path / product_role.lower(),
        lock,
        product_role=product_role,
    )
    shutil.copy2(archive, bundle / archive.name)
    home = tmp_path / "home"
    home.mkdir()
    profile = tmp_path / "connection.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "Proxy",
                "proxy": {
                    "type": "HTTPS" if route == "SingBoxHttps" else "HTTP",
                    "host": "proxy.example.test",
                    "port": 8080,
                    "auth": {
                        "mode": "UsernamePassword",
                        "username": "fixture-user",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    sentinel = "Route-Probe-Password-Must-Stay-Secret!"
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

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    upstream_thread = threading.Thread(
        target=upstream.serve_forever,
        daemon=True,
    )
    upstream_thread.start()
    endpoint = "http://route-check.invalid/route-check"
    forward_log = tmp_path / "forwarded.txt"
    environment = dict(os.environ)
    environment["K7_FAKE_FORWARD_LOG"] = str(forward_log)
    environment["K7_FAKE_UPSTREAM_PORT"] = str(upstream.server_port)
    environment["K7_FAKE_REQUIRE_CURL_PROBE"] = "1"
    try:
        result = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--test-singbox-route-json",
                str(home),
                route,
                endpoint,
            ],
            cwd=bundle,
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
        assert value["uses_proxy"] is True
        assert value["cleanup_verified"] is True
        assert value["lifecycle"][-2:] == [
            "ROUTE_PROBE_PASS",
            "CLEANUP_VERIFIED",
        ]
        assert Upstream.received_paths == ["/route-check"]
        assert forward_log.read_text(encoding="utf-8").splitlines() == [
            "/route-check"
        ]
        assert sentinel not in result.stdout
        assert sentinel not in result.stderr
        assert not list(
            (
                home
                / ".llm-foundation"
                / "launcher-state"
                / "sessions"
            ).glob("*")
        )

        user_agent_environment = dict(environment)
        user_agent_environment["K7_FAKE_REQUIRE_USER_AGENT"] = "1"
        user_agent_environment["K7_FAKE_REQUIRE_CURL_ACCEPT"] = "1"
        user_agent_required = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--test-singbox-route-json",
                str(home),
                route,
                endpoint,
            ],
            cwd=bundle,
            env=user_agent_environment,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert user_agent_required.stdout.strip(), user_agent_required.stderr
        user_agent_value = json.loads(user_agent_required.stdout)
        assert user_agent_required.returncode == 0
        assert user_agent_value["status"] == "PASS"
        assert user_agent_value["cleanup_verified"] is True
        assert Upstream.received_paths == [
            "/route-check",
            "/route-check",
        ]
        assert forward_log.read_text(encoding="utf-8").splitlines() == [
            "/route-check",
            "/route-check",
        ]

        Upstream.status_code = 404
        non_success_status = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--test-singbox-route-json",
                str(home),
                route,
                endpoint,
            ],
            cwd=bundle,
            env=environment,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert non_success_status.stdout.strip(), non_success_status.stderr
        non_success_value = json.loads(non_success_status.stdout)
        assert non_success_status.returncode == 0
        assert non_success_value["status"] == "PASS"
        assert non_success_value["cleanup_verified"] is True
        Upstream.status_code = 200

        broken_environment = dict(environment)
        broken_environment["K7_FAKE_PROXY_BROKEN"] = "1"
        failed = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--test-singbox-route-json",
                str(home),
                route,
                endpoint,
            ],
            cwd=bundle,
            env=broken_environment,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert failed.stdout.strip(), failed.stderr
        failed_value = json.loads(failed.stdout)
        assert failed.returncode == 20
        assert failed_value["status"] == "FAILED"
        assert failed_value["uses_proxy"] is True
        assert failed_value["reason"] == "PROXY_UPSTREAM_FAILED"
        assert failed_value["cleanup_verified"] is True
        assert Upstream.received_paths == [
            "/route-check",
            "/route-check",
            "/route-check",
        ]
        assert forward_log.read_text(encoding="utf-8").splitlines() == [
            "/route-check",
            "/route-check",
            "/route-check",
        ]
        assert sentinel not in failed.stdout
        assert sentinel not in failed.stderr

        auth_environment = dict(environment)
        auth_environment["K7_FAKE_PROXY_AUTH_FAILED"] = "1"
        auth_failed = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--test-singbox-route-json",
                str(home),
                route,
                endpoint,
            ],
            cwd=bundle,
            env=auth_environment,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert auth_failed.stdout.strip(), auth_failed.stderr
        auth_value = json.loads(auth_failed.stdout)
        assert auth_failed.returncode == 20
        assert auth_value["status"] == "FAILED"
        assert auth_value["reason"] == "PROXY_AUTH_FAILED"
        assert auth_value["cleanup_verified"] is True
        assert "authentication required" not in auth_failed.stdout
        assert "authentication required" not in auth_failed.stderr

        cleanup_failed_environment = dict(broken_environment)
        cleanup_failed_environment["K7_FAKE_CLEANUP_BLOCK"] = "1"
        cleanup_failed = subprocess.run(
            [
                str(bundle / "LLMFoundationInstaller.exe"),
                "--test-singbox-route-json",
                str(home),
                route,
                endpoint,
            ],
            cwd=bundle,
            env=cleanup_failed_environment,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert cleanup_failed.stdout.strip(), cleanup_failed.stderr
        cleanup_failed_value = json.loads(cleanup_failed.stdout)
        assert cleanup_failed.returncode == 20
        assert cleanup_failed_value["status"] == "FAILED"
        assert cleanup_failed_value["uses_proxy"] is True
        assert cleanup_failed_value["cleanup_verified"] is False
        assert (
            cleanup_failed_value["reason"]
            == "SESSION_CLEANUP_FAILED"
        )
        sessions = (
            home
            / ".llm-foundation"
            / "launcher-state"
            / "sessions"
        )
        assert len(list(sessions.glob("*"))) == 1
    finally:
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=5)
        sessions = (
            home
            / ".llm-foundation"
            / "launcher-state"
            / "sessions"
        )
        if sessions.exists():
            for child in sessions.rglob("*"):
                if child.is_file():
                    child.chmod(stat.S_IREAD | stat.S_IWRITE)
            shutil.rmtree(sessions)


def test_singbox_route_launches_exact_client_with_local_proxy_only(
    tmp_path: Path,
) -> None:
    fake = tmp_path / "sing-box.exe"
    _compile_fake_singbox(fake)
    fake_hash = _sha256(fake)
    archive = tmp_path / "sing-box-fixture.zip"
    entry = "sing-box-1.13.14-windows-amd64/sing-box.exe"
    with zipfile.ZipFile(archive, "w") as package:
        package.write(fake, entry)
    runtime_lock = tmp_path / "runtime.lock.json"
    _write_runtime_lock(runtime_lock, archive, entry)
    client_lock = tmp_path / "client.lock.json"
    client_lock.write_text(
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
                        "url": "http://127.0.0.1:43119/opencode.exe",
                        "sha256": fake_hash,
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
    bundle = _build(
        tmp_path / "center",
        runtime_lock,
        client_lock,
    )
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
                    "auth": {"mode": "None", "username": None},
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
    client = (
        home
        / ".llm-foundation"
        / "apps"
        / "opencode-desktop"
        / "1.0.0"
        / "opencode.exe"
    )
    client.parent.mkdir(parents=True)
    client.write_bytes(fake.read_bytes())
    (client.parents[1] / "current.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "client_id": "opencode-desktop",
                "version": "1.0.0",
                "relative_path": "1.0.0/opencode.exe",
                "sha256": fake_hash,
            }
        ),
        encoding="utf-8",
    )
    child_environment = tmp_path / "child-environment.txt"
    command_log = tmp_path / "commands.txt"
    environment = dict(os.environ)
    environment["K7_TEST_OUTPUT"] = str(child_environment)
    environment["K7_FAKE_COMMAND_LOG"] = str(command_log)
    result = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--launch-target-json",
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
    assert value["transport"] == "SingBoxHttp"
    assert value["uses_proxy"] is True
    assert value["cleanup_verified"] is True
    assert value["lifecycle"] == [
        "PROFILE_VALIDATED",
        "RUNTIME_VERIFIED",
        "CONFIG_CHECKED",
        "LOCAL_PROXY_READY",
        "EXACT_CLIENT_STARTED",
        "CLIENT_EXITED",
        "RUNTIME_STOPPED",
        "TEMP_REMOVED",
    ]
    child_values = child_environment.read_text(
        encoding="utf-8"
    ).splitlines()
    assert child_values[0].startswith("HTTP_PROXY=http://127.0.0.1:")
    assert child_values[1].startswith("HTTPS_PROXY=http://127.0.0.1:")
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

    chrome = (
        home
        / "AppData"
        / "Local"
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe"
    )
    chrome.parent.mkdir(parents=True)
    _compile_argument_probe(chrome)
    chrome_config = tmp_path / "chrome-singbox.json"
    returncode, summary = _run_json(
        bundle,
        "--write-singbox-config-test-json",
        str(home),
        "chrome-browser",
        "SingBoxHttp",
        "18082",
        str(chrome_config),
    )
    assert returncode == 0
    assert summary["target_id"] == "chrome-browser"
    assert summary["route_final"] == "upstream"
    assert json.loads(chrome_config.read_text(encoding="utf-8"))["route"][
        "final"
    ] == "upstream"

    chrome_arguments = tmp_path / "chrome-arguments.txt"
    chrome_environment = dict(environment)
    chrome_environment["K7_TEST_OUTPUT"] = str(chrome_arguments)
    chrome_launch = subprocess.run(
        [
            str(bundle / "LLMFoundationInstaller.exe"),
            "--launch-target-json",
            str(home),
            "chrome-browser",
            "SingBoxHttp",
        ],
        cwd=bundle,
        text=True,
        capture_output=True,
        encoding="utf-8",
        env=chrome_environment,
        timeout=30,
    )
    assert chrome_launch.returncode == 0, (
        chrome_launch.stdout + chrome_launch.stderr
    )
    chrome_result = json.loads(chrome_launch.stdout)
    assert chrome_result["status"] == "PASS"
    assert chrome_result["target_id"] == "chrome-browser"
    arguments = chrome_arguments.read_text(encoding="utf-8").splitlines()
    assert any(
        value.startswith("--proxy-server=http://127.0.0.1:")
        for value in arguments
    )
    assert any(value.startswith("--user-data-dir=") for value in arguments)

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
