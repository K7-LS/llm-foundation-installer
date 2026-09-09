from __future__ import annotations

import importlib.util
import json
import os
import shutil
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


@pytest.mark.parametrize('version,files,tests', [('0.5.11', 9, 7), ('0.5.12', 13, 9)])
def test_engine_runner_uses_exact_versioned_payload_and_test_contract(version, files, tests):
    engine_files, selected = runner._engine_contract(version)
    assert len(engine_files) == len(set(engine_files)) == files
    assert len(selected) == len(set(selected)) == tests
    additions = {'foundation-toml.ps1', 'vendor/tomlyn/Tomlyn.dll',
                 'vendor/tomlyn/LICENSE.txt', 'vendor/tomlyn/provenance.json'}
    doctor_tests = {'tests/test_doctor_state.py', 'tests/test_doctor_toml.py'}
    if version == '0.5.12':
        previous_files, previous_tests = runner._engine_contract('0.5.11')
        assert set(engine_files) - set(previous_files) == additions
        assert set(selected) - set(previous_tests) == doctor_tests
        assert selected[:7] == previous_tests
    else:
        assert not additions.intersection(engine_files)
        assert not doctor_tests.intersection(selected)


@pytest.mark.parametrize('version', ['0.5.10', '0.5.13', '0.5.12-test', '', None, True])
def test_engine_runner_rejects_unknown_version_contract(version):
    with pytest.raises(ValueError, match='unsupported isolated engine version'):
        runner._engine_contract(version)


@pytest.fixture
def syntax_archive(tmp_path, monkeypatch, request):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "tools").mkdir()
    (repository / "tools/check-ps-syntax.ps1").write_bytes(
        (ROOT / "tools/check-ps-syntax.ps1").read_bytes())
    script = b"if (\n" if getattr(request, "param", "valid") == "broken" else b"Write-Output 'ok'\n"
    (repository / "tools/valid.PS1").write_bytes(script)
    (repository / ".gitattributes").write_text("* -text\n", encoding="utf-8")
    (repository / "VERSION").write_text("0.5.12\n", encoding="utf-8")
    (repository / "APP_VERSION").write_text("0.4.0\n", encoding="utf-8")
    # This is a private source fixture. The archive remains without .git;
    # no synthetic repository is added to the export to make lint succeed.
    for arguments in (
        ["init", "-q"], ["add", "."],
        ["-c", "user.name=Syntax Test", "-c", "user.email=syntax@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(tmp_path / "no-hooks"),
         "commit", "-qm", "syntax fixture"],
    ):
        subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository,
                            check=True, capture_output=True, text=True).stdout.strip()
    work = tmp_path / "archive-work"
    work.mkdir()
    monkeypatch.setattr(runner, "ROOT", repository)
    exported = runner._export_commit(work)
    assert not (exported / ".git").exists()
    return repository, exported, commit


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
def test_real_git_aware_syntax_check_accepts_bound_git_archive(syntax_archive, shell):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip("This PowerShell host is not installed")
    repository, exported, commit = syntax_archive
    prefix = [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
    old_route = runner._run(
        prefix + [str(exported / "tools/check-ps-syntax.ps1"), "-Root", str(exported)],
        os.environ.copy(), exported)
    assert old_route["status"] == "FAIL"
    assert any(message in old_route["stderr"] for message in (
        "git ls-files failed", "No .ps1 files are visible to git"))
    result = runner._run_syntax(prefix, os.environ.copy(), exported, repository, commit)
    assert result["status"] == "PASS", result
    assert "PowerShell syntax PASS: 2" in result["stdout"].splitlines()
    assert set(result["source_binding"]["files"]) == {
        "tools/check-ps-syntax.ps1", "tools/valid.PS1"}
    assert not (exported / ".git").exists()


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("syntax_archive", ["broken"], indirect=True)
def test_real_syntax_check_still_rejects_invalid_committed_archive(syntax_archive, shell):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip("This PowerShell host is not installed")
    repository, exported, commit = syntax_archive
    prefix = [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
    result = runner._run_syntax(prefix, os.environ.copy(), exported, repository, commit)
    assert result["status"] == "FAIL"
    assert result["returncode"] == 1
    assert "valid.PS1:" in result["stderr"]
    assert result["source_binding"]["status"] == "PASS"
    assert "PowerShell syntax PASS" not in result["stdout"]


@pytest.mark.parametrize("side,change", [
    ("checkout", "content"), ("checkout", "line_endings"),
    ("checkout", "extra"), ("checkout", "missing"),
    ("export", "content"), ("export", "extra"), ("export", "missing"),
])
def test_syntax_binding_rejects_changed_or_uncovered_inputs_before_lint(
        syntax_archive, monkeypatch, side, change):
    repository, exported, commit = syntax_archive
    target = repository if side == "checkout" else exported
    script = target / "tools/valid.PS1"
    if change == "content":
        script.write_bytes(b"if (\n")
    elif change == "line_endings":
        script.write_bytes(script.read_bytes().replace(b"\n", b"\r\n"))
    elif change == "extra":
        (target / "tools/extra.ps1").write_bytes(b"if (\n")
    else:
        script.unlink()
    def forbidden(*args, **kwargs):
        raise AssertionError("changed source must be rejected before lint")
    monkeypatch.setattr(runner, "_run", forbidden)
    with pytest.raises(ValueError, match="PowerShell syntax source"):
        runner._run_syntax([], {}, exported, repository, commit)


def test_syntax_binding_rejects_change_during_lint_and_keeps_diagnostics(
        syntax_archive, monkeypatch):
    repository, exported, commit = syntax_archive
    def changed(*args, **kwargs):
        (repository / "tools/valid.PS1").write_bytes(b"if (\n")
        return {"status": "PASS", "returncode": 0, "stdout": "syntax output", "stderr": ""}
    monkeypatch.setattr(runner, "_run", changed)
    result = runner._run_syntax([], {}, exported, repository, commit)
    assert result["status"] == "FAIL"
    assert result["stdout"] == "syntax output"
    assert "bytes differ from commit" in result["source_binding_failure"]


def test_syntax_check_cannot_pass_after_omitting_a_bound_script(syntax_archive, monkeypatch):
    repository, exported, commit = syntax_archive
    monkeypatch.setattr(runner, "_run", lambda *args: {
        "status": "PASS", "returncode": 0, "stdout": "PowerShell syntax PASS: 1\n", "stderr": ""})
    result = runner._run_syntax([], {}, exported, repository, commit)
    assert result["status"] == "FAIL"
    assert "checked file count differs" in result["source_binding_failure"]


def test_engine_runner_preserves_failed_syntax_and_build_diagnostics(
        syntax_archive, tmp_path, monkeypatch):
    repository, _, commit = syntax_archive
    dependency = tmp_path / "dependency.exe"
    dependency.write_bytes(b"fixture only")
    work = tmp_path / "failed-acceptance"
    monkeypatch.setattr(runner.legacy, "_git_identity", lambda root: {"commit": commit})
    monkeypatch.setattr(runner.legacy, "_source_hashes", lambda root: {})
    monkeypatch.setattr(runner, "_real_environment", lambda: {})
    monkeypatch.setattr(runner.shutil, "which", lambda name: "unused-fixture-host")
    monkeypatch.setattr(runner, "_run_syntax", lambda *args: {
        "status": "FAIL", "returncode": 1, "stdout": "", "stderr": "syntax failed"})
    monkeypatch.setattr(runner, "_run", lambda *args: {
        "status": "FAIL", "returncode": 2, "stdout": "", "stderr": "build failed"})
    assert runner.main(["--workspace", str(tmp_path), "--work", str(work),
                        "--officecli", str(dependency), "--compiler", str(dependency)]) == 1
    evidence = json.loads((work / "foundation-engine-acceptance.json").read_text(encoding="utf-8"))
    assert evidence["FOUNDATION_ENGINE_ACCEPTANCE"] == "FAIL"
    assert evidence["powershell_syntax"]["ps7"]["stderr"] == "syntax failed"
    assert evidence["engine_builds"]["ps7"]["stderr"] == "build failed"
    assert "failure" in evidence
