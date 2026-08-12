from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "tools" / "build-officecli-pdf-exporter.ps1"


def _powershell() -> str:
    value = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    assert value
    return value


def _build(tmp_path: Path) -> Path:
    output = tmp_path / "officecli-pdf.exe"
    result = subprocess.run(
        [
            _powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(BUILD), "-OutputPath", str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.is_file()
    return output


def test_exporter_builds_deterministically_and_reports_protocol_v1(tmp_path: Path) -> None:
    """A non-protocol binary or non-reproducible build must fail this boundary."""
    first = _build(tmp_path / "first")
    second = _build(tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()

    info = subprocess.run(
        [str(first), "--info"], check=False, capture_output=True,
        text=True, encoding="utf-8",
    )
    assert info.returncode == 0, info.stderr
    assert json.loads(info.stdout) == {
        "name": "k7-officecli-pdf",
        "version": "1.0.0",
        "protocol": 1,
        "kinds": ["exporter"],
        "extensions": [".pdf"],
        "runtime": "dotnet",
        "idle_timeout_seconds": {
            "default": 60,
            "verbs": {"export": 120},
        },
        "supports": ["from:docx", "from:xlsx", "from:pptx"],
    }


def test_exporter_rejects_unsupported_or_aliasing_paths_without_writes(tmp_path: Path) -> None:
    """Accepting a foreign extension or source=target can corrupt user input."""
    exporter = _build(tmp_path / "build")
    source = tmp_path / "source.txt"
    source.write_text("preserve", encoding="utf-8")

    unsupported = subprocess.run(
        [str(exporter), "export", str(source), "--out", str(tmp_path / "out.pdf")],
        check=False, capture_output=True, text=True, encoding="utf-8",
    )
    assert unsupported.returncode == 2
    assert "unsupported_source" in unsupported.stderr
    assert source.read_text(encoding="utf-8") == "preserve"
    assert not (tmp_path / "out.pdf").exists()

    docx = tmp_path / "source.docx"
    docx.write_bytes(b"not-a-real-docx")
    alias = subprocess.run(
        [str(exporter), "export", str(docx), "--out", str(docx)],
        check=False, capture_output=True, text=True, encoding="utf-8",
    )
    assert alias.returncode == 2
    assert "source_target_alias" in alias.stderr
    assert docx.read_bytes() == b"not-a-real-docx"
