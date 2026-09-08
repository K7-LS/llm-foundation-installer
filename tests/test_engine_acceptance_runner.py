from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("engine_acceptance", ROOT / "tools/run-engine-acceptance.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_engine_runner_does_not_inherit_unbounded_pytest_options(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "tests/test_gui.py --override-ini=testpaths=tests")
    monkeypatch.setenv("PYTEST_PLUGINS", "foreign_plugin")
    monkeypatch.setenv("FOUNDATION_FAIL_AFTER", "1")
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    before = dict(os.environ)
    environment = runner._environment(tmp_path, tmp_path / "office.exe", tmp_path / "csc.exe")
    assert "PYTEST_ADDOPTS" not in environment and "PYTEST_PLUGINS" not in environment
    assert "FOUNDATION_FAIL_AFTER" not in environment
    assert 'PYTHONOPTIMIZE' not in environment
    for name in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "K7_BUILD_CACHE"):
        assert Path(environment[name]).is_relative_to(tmp_path)
    assert dict(os.environ) == before


@pytest.mark.parametrize("kind", ["same", "existing", "outside"])
def test_engine_runner_rejects_unsafe_work_before_provenance_or_execution(tmp_path, monkeypatch, kind):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    work = workspace if kind == "same" else workspace / "existing"
    if kind == "existing": work.mkdir()
    if kind == "outside": work = tmp_path / "outside"
    def forbidden(*args, **kwargs):
        raise AssertionError("must not execute or inspect unrelated source")
    monkeypatch.setattr(runner.legacy, "_git_identity", forbidden)
    monkeypatch.setattr(runner, "_run", forbidden)
    with pytest.raises(ValueError, match="new child"):
        runner.main(["--workspace", str(workspace), "--work", str(work),
                     "--officecli", str(tmp_path / "absent"), "--compiler", str(tmp_path / "absent")])


def test_engine_runner_rejects_external_dependency_before_execution(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    dependency = tmp_path / "external.exe"
    dependency.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="inside the authorized workspace"):
        runner.main(["--workspace", str(workspace), "--work", str(workspace / "new"),
                     "--officecli", str(dependency), "--compiler", str(dependency)])
    assert not (workspace / "new").exists()


def test_optimized_python_cannot_issue_acceptance(tmp_path):
    result = subprocess.run([sys.executable, '-O', str(ROOT / 'tools/run-engine-acceptance.py')],
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'TEMP': str(tmp_path), 'TMP': str(tmp_path)},
        capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode != 0
    assert 'engine acceptance requires unoptimized Python' in result.stderr
