"""No-network regression for the environment of the official Windows PS installer."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")
WINDOWS_PS = Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not COMPILER.is_file() or not WINDOWS_PS.is_file(),
    reason="Windows PowerShell and the .NET Framework compiler are required",
)


def _run(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=30, check=False, **kwargs)


def _member(source, signature):
    start = source.index("        " + signature)
    end = re.search(r"^        (?:private|internal) ", source[start + len(signature) + 8:], re.M)
    assert end is not None
    return source[start:start + len(signature) + 8 + end.start()]


@pytest.fixture(scope="module")
def production_process_setup(tmp_path_factory):
    root = tmp_path_factory.mktemp("official-process-setup")
    source = (ROOT / "src/gui/ClientBootstrap.cs").read_text(encoding="utf-8")
    method = _member(source, "private static ClientInstallResult InstallOfficialPowerShellScript(")
    setup = method[method.index("            ProcessStartInfo start ="):]
    setup = setup[:setup.index("            Dictionary<string, string> bundledAssets =")]
    members = [_member(source, signature) for signature in (
        "private static string BuildOfficialScriptWrapper()",
        "private static void AddWindowsPowerShellModulePath(",
        "private static string WindowsPowerShellPath()",
        "private static string QuoteArgument(string value)",
    )]
    constants = [re.search(pattern, source, re.S).group(0) for pattern in (
        r"private const int OfficialDownloadMaxSeconds = \d+;",
        r"private const string NonInteractiveCommandPrelude =.*?;",
    )]
    fixture = root / "Setup.cs"
    fixture.write_text(
        "using System; using System.IO; using System.Linq; using System.Text; "
        "using System.Diagnostics; public static class Fixture {\n" +
        "\n".join(members + constants) +
        "public static void Main(string[] args) { if(args.Length > 0) {\n" + setup +
        "Console.Write(start.EnvironmentVariables[\"PSModulePath\"] ?? \"\"); return;}\n"
        "Console.Write(BuildOfficialScriptWrapper()); }}", encoding="utf-8",
    )
    executable = root / "Setup.exe"
    result = _run([str(COMPILER), "/nologo", "/target:exe", f"/out:{executable}", str(fixture)])
    assert result.returncode == 0, result.stdout + result.stderr
    return executable


def test_official_process_can_hash_with_incompatible_inherited_modules(production_process_setup, tmp_path):
    # A PS7 host puts its real, incompatible Utility module ahead of PS5's own module.
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is needed to reproduce the cross-host module path")
    modules = Path(pwsh).parent / "Modules"
    env = {key: value for key, value in os.environ.items() if key.lower() != "psmodulepath"}
    env["PSModulePath"] = str(modules)
    setup = _run([str(production_process_setup), "environment"], env=env)
    assert setup.returncode == 0, setup.stderr
    env["PSModulePath"] = setup.stdout
    input_file = tmp_path / "asset.bin"
    input_file.write_bytes(b"verified")
    env["FIXTURE_HASH_INPUT"] = str(input_file)
    probe = (
        "$ErrorActionPreference='Stop';"
        "$hash=Get-FileHash -LiteralPath $env:FIXTURE_HASH_INPUT -Algorithm SHA256;"
        f"if($hash.Hash -ne '{hashlib.sha256(input_file.read_bytes()).hexdigest()}')"
        "{throw 'Digest mismatch'}"
    )
    result = _run([str(WINDOWS_PS), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", probe], env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(setup.stdout.split(";")[0]) == WINDOWS_PS.parent / "Modules"
    assert str(modules) in setup.stdout, "inherited module paths must be preserved in the child"


def test_official_wrapper_retains_existing_destination_guard(production_process_setup, tmp_path):
    # This fix must not bypass checksum failures by weakening the secondary overwrite guard.
    wrapper = _run([str(production_process_setup)])
    assert wrapper.returncode == 0, wrapper.stderr
    source = wrapper.stdout
    assert source.count("throw 'Network destination already exists'") == 2
    assert "Copy-Item -LiteralPath $bundled -Destination $OutFile;" in source
    assert "Move-Item -LiteralPath $target -Destination $OutFile;" in source
