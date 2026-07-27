from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_runner():
    path = Path(__file__).resolve().parents[1] / "tools" / "run-acceptance.py"
    spec = importlib.util.spec_from_file_location("foundation_acceptance", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_junit_counts_separate_ps7_ps51_and_shared(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text(
        """<?xml version="1.0"?>
<testsuite tests="3" failures="0" errors="0" skipped="0">
  <testcase name="case[C:\\Program Files\\PowerShell\\7\\pwsh.EXE]" />
  <testcase name="case[C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe]" />
  <testcase name="shared" />
</testsuite>
""",
        encoding="utf-8",
    )

    counts = _load_runner()._junit_counts(report)

    assert counts["passed"] == 3
    assert counts["ps7_cases"] == 1
    assert counts["ps51_cases"] == 1
    assert counts["shared_cases"] == 1


def test_pytest_command_keeps_fake_homes_inside_acceptance_work(tmp_path):
    command = _load_runner()._pytest_command(tmp_path)

    assert command[-1] == f"--basetemp={tmp_path / 'pytest-home'}"
    assert f"--junitxml={tmp_path / 'pytest.xml'}" in command
