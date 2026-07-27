from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_ci_uses_explicit_supported_powershell_jobs():
    workflow = (
        ROOT / ".github" / "workflows" / "windows-ci.yml"
    ).read_text(encoding="utf-8")

    assert "matrix.shell" not in workflow
    assert "offline-test-ps7:" in workflow
    assert "offline-test-ps51:" in workflow
    ps7, ps51 = workflow.split("offline-test-ps7:", 1)[1].split(
        "offline-test-ps51:",
        1,
    )
    assert "shell: pwsh" in ps7
    assert "shell: powershell" in ps51
    assert 'python-version: "3.12"' in workflow
    assert "branches: [main]" in workflow
