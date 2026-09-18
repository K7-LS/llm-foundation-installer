"""Регрессия проверки Store-пакета Codex перед запуском.

Codex 26.915 объявляет в манифесте два приложения: `App` и служебное
`CodexCoreCommandRunner`. Проверка требовала ровно одно приложение, выходила
с кодом 45, и Launch Center отказывался запускать Codex. Тест берёт команду
прямо из `ClientBootstrap.ProbeStore` и выполняет её в Windows PowerShell 5.1,
подменив командлеты Appx функциями-заглушками; сборка GUI не нужна.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PS = Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not WINDOWS_PS.is_file(),
    reason="Windows PowerShell 5.1 is required",
)

IDENTITY = "OpenAI.Codex"
PUBLISHER = "CN=50BDFD77-8903-4850-9FFE-6E8522F64D5B"
LOCKED_APP = ("App", "app/ChatGPT.exe", "Windows.FullTrustApplication")
RUNNER_APP = (
    "CodexCoreCommandRunner",
    "app/resources/codex-command-runner.exe",
    "Windows.FullTrustApplication",
)


def _probe_method() -> str:
    source = (ROOT / "src/gui/ClientBootstrap.cs").read_text(encoding="utf-8")
    start = source.index("public static StoreClientResult ProbeStore(")
    end = source.index("public static StoreClientResult OpenStoreSource(", start)
    return source[start:end]


def _probe_command() -> str:
    method = _probe_method()
    match = re.search(
        r'const string command =\s*((?:"[^"\\]*"\s*\+\s*)*"[^"\\]*")\s*;',
        method,
    )
    assert match is not None, "ProbeStore command literal was not found"
    return "".join(re.findall(r'"([^"\\]*)"', match.group(1)))


def _package(version: str) -> str:
    full_name = f"{IDENTITY}_{version}_x64__2p2nqsd0c76g0"
    return (
        "[pscustomobject]@{"
        f"Name='{IDENTITY}';Publisher='{PUBLISHER}';SignatureKind='Store';"
        f"Architecture='X64';Version='{version}';PackageFullName='{full_name}';"
        f"PackageFamilyName='{IDENTITY}_2p2nqsd0c76g0';"
        f"InstallLocation='C:\\Program Files\\WindowsApps\\{full_name}'"
        "}"
    )


def _manifest(applications) -> str:
    rows = "".join(
        f'<Application Id="{app_id}" Executable="{executable}" '
        f'EntryPoint="{entry_point}" />'
        for app_id, executable, entry_point in applications
    )
    return f"<Package><Applications>{rows}</Applications></Package>"


def _run_probe(packages, applications):
    # Функции имеют приоритет над командлетами модуля Appx, поэтому
    # Import-Module Appx внутри команды заглушки не перекрывает.
    stubs = (
        "function Get-AppxPackage { [CmdletBinding()] param([string]$Name) "
        + (", ".join(packages) if packages else "@()")
        + " }\n"
        "function Get-AppxPackageManifest { [CmdletBinding()] param($Package) "
        f"[xml]'{_manifest(applications)}' }}\n"
    )
    script = stubs + _probe_command()
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    environment = dict(os.environ)
    environment.update({
        "LLM_STORE_IDENTITY": IDENTITY,
        "LLM_STORE_APPLICATION_ID": LOCKED_APP[0],
    })
    completed = subprocess.run(
        [
            str(WINDOWS_PS), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded,
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False, env=environment,
    )
    return completed


def _record(completed) -> dict:
    assert completed.returncode == 0, (completed.returncode, completed.stderr)
    return json.loads(completed.stdout.strip())


def test_codex_26_915_with_command_runner_resolves_locked_app():
    completed = _run_probe(
        [_package("26.915.3509.0")], [LOCKED_APP, RUNNER_APP]
    )
    record = _record(completed)
    assert record["present"] is True
    assert record["version"] == "26.915.3509.0"
    assert (
        record["application_id"], record["executable"], record["entry_point"]
    ) == LOCKED_APP


def test_single_locked_app_manifest_still_resolves():
    record = _record(_run_probe([_package("26.911.7940.0")], [LOCKED_APP]))
    assert record["application_id"] == "App"
    assert record["executable"] == "app/ChatGPT.exe"


def test_manifest_without_locked_app_fails_closed():
    completed = _run_probe([_package("26.915.3509.0")], [RUNNER_APP])
    assert completed.returncode == 45, (completed.returncode, completed.stdout)


def test_duplicate_locked_app_fails_closed():
    completed = _run_probe(
        [_package("26.915.3509.0")], [LOCKED_APP, LOCKED_APP]
    )
    assert completed.returncode == 45, (completed.returncode, completed.stdout)


def test_two_codex_packages_fail_closed():
    completed = _run_probe(
        [_package("26.911.7940.0"), _package("26.915.3509.0")], [LOCKED_APP]
    )
    assert completed.returncode == 44, (completed.returncode, completed.stdout)


def test_missing_package_reports_absent():
    record = _record(_run_probe([], [LOCKED_APP]))
    assert record == {"present": False}


def test_probe_process_receives_every_environment_variable_it_reads():
    method = _probe_method()
    used = set(re.findall(r"\$env:([A-Z_]+)", _probe_command()))
    assigned = set(
        re.findall(r'start\.EnvironmentVariables\["([A-Z_]+)"\]', method)
    )
    assert used, "the probe command reads no environment variables"
    assert used <= assigned, sorted(used - assigned)


def test_locked_app_is_found_regardless_of_manifest_order():
    record = _record(
        _run_probe([_package("26.915.3509.0")], [RUNNER_APP, LOCKED_APP])
    )
    assert (
        record["application_id"], record["executable"], record["entry_point"]
    ) == LOCKED_APP


def test_locked_app_id_match_is_case_sensitive():
    lowercase = ("app",) + LOCKED_APP[1:]
    completed = _run_probe(
        [_package("26.915.3509.0")], [lowercase, RUNNER_APP]
    )
    assert completed.returncode == 45, (completed.returncode, completed.stdout)
