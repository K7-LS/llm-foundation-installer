from __future__ import annotations

import importlib.util
import stat
import subprocess
import sys
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


def test_run_preserves_non_utf8_child_output_without_crashing(tmp_path):
    result = _load_runner()._run(
        [
            sys.executable,
            "-c",
            "import sys;sys.stdout.buffer.write(bytes([0xC4]))",
        ],
        tmp_path,
    )

    assert result["returncode"] == 0
    assert result["stdout"] == "\ufffd"
    assert result["stderr"] == ""


def test_remove_tree_clears_read_only_files(tmp_path):
    work = tmp_path / "acceptance"
    work.mkdir()
    locked = work / "git-object"
    locked.write_bytes(b"fixture")
    locked.chmod(stat.S_IREAD)

    _load_runner()._remove_tree(work)

    assert not work.exists()


def test_source_hashes_bind_gui_version_and_client_source_lock(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    tracked = {
        "VERSION": "0.2.1\n",
        "APP_VERSION": "0.3.0\n",
        "client-sources.lock.json": '{"schema_version":1}\n',
        "src/app.txt": "source\n",
        "tests/test_app.py": "def test_app(): pass\n",
        "tools/build.ps1": "Write-Output ok\n",
    }
    for relative, content in tracked.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "acceptance@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Acceptance Test"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repository, check=True)

    hashes = _load_runner()._source_hashes(repository)

    assert set(hashes) == {
        "VERSION",
        "APP_VERSION",
        "client-sources.lock.json",
        "src",
        "tests",
        "tools",
    }


def test_acceptance_evidence_body_hash_excludes_only_its_own_field():
    runner = _load_runner()
    evidence = {
        "schema_version": 1,
        "FOUNDATION_SYNTHETIC": "PASS",
    }
    digest = runner.evidence_body_sha256(evidence)
    evidence["evidence_body_sha256"] = digest

    assert runner.evidence_body_sha256(evidence) == digest
