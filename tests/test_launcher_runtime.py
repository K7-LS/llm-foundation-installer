from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
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
