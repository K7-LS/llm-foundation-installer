"""Единый источник продуктовых констант: src/gui/product-config.json.

Ф1: хардкоды (Chrome-путь, прокси-URL, probe-URL, версия sing-box)
живут в одном конфиге; исходники и сборка читают его, а не литералы.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPOSITORY / "src" / "gui" / "product-config.json"
BUILD_SCRIPT = REPOSITORY / "tools" / "build-gui.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe")


def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_product_config_exists_with_exact_schema():
    value = _config()
    assert set(value) == {
        "schema_version",
        "chrome_path",
        "chrome_proxy_url",
        "connection_probe_url",
        "singbox_version",
    }
    assert value["schema_version"] == 1
    assert value["chrome_path"].lower().endswith("\\chrome.exe")
    assert value["chrome_proxy_url"].startswith(("http://", "https://"))
    assert value["connection_probe_url"].startswith("https://")
    assert re.fullmatch(r"\d+\.\d+\.\d+", value["singbox_version"])


def test_installer_sources_do_not_hardcode_extracted_values():
    for path in sorted((REPOSITORY / "src" / "gui").glob("*.cs")):
        source = path.read_text(encoding="utf-8")
        assert "scuf-meta" not in source, path.name
        assert "cdn-cgi/trace" not in source, path.name
        assert "Program Files\\Google" not in source, path.name
    launcher = (
        REPOSITORY / "src" / "gui" / "ChromeProxyLauncher.cs"
    ).read_text(encoding="utf-8")
    assert "ProductConfig.LoadEmbedded()" in launcher


def test_singbox_version_has_single_source_of_truth():
    version = _config()["singbox_version"]
    lock = json.loads(
        (REPOSITORY / "runtime-sources.lock.json").read_text(
            encoding="utf-8"
        )
    )
    assert lock["runtime"]["version"] == version
    bootstrap = (
        REPOSITORY / "src" / "gui" / "RuntimeBootstrap.cs"
    ).read_text(encoding="utf-8")
    assert version not in bootstrap
    build_script = (
        REPOSITORY / "tools" / "build-gui.ps1"
    ).read_text(encoding="utf-8")
    assert version not in build_script


def test_build_script_embeds_and_validates_product_config():
    build_script = (
        REPOSITORY / "tools" / "build-gui.ps1"
    ).read_text(encoding="utf-8")
    assert "ProductConfig.json" in build_script
    assert "product-config.json" in build_script


def test_build_rejects_probe_url_outside_approved_hosts(tmp_path: Path):
    tampered = _config()
    tampered["connection_probe_url"] = "https://evil.example/trace"
    bad_config = tmp_path / "product-config.json"
    bad_config.write_text(
        json.dumps(tampered, indent=2) + "\n", encoding="utf-8"
    )
    result = subprocess.run(
        [
            str(POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(BUILD_SCRIPT),
            "-OutputRoot",
            str(tmp_path / "out"),
            "-Edition",
            "Employee",
            "-ProductRole",
            "LaunchCenter",
            "-ProductConfigPath",
            str(bad_config),
        ],
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "not an approved official endpoint" in (
        result.stdout + result.stderr
    )
