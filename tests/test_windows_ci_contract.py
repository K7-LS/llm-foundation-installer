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


def test_foundation_release_workflow_pins_match_engine_version():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    workflow = (
        ROOT / ".github" / "workflows" / "windows-ci.yml"
    ).read_text(encoding="utf-8")
    release_job = workflow.split("  build-foundation-release:\n", 1)[1]

    assert "python tools/run-acceptance.py" in release_job
    assert "python tools/promote_foundation.py" in release_job
    assert f"--output .work/release/foundation-{version}\n" in release_job
    assert f"$root = '.work/release/foundation-{version}'" in release_job
    assert f"'foundation-engine-{version}.zip'" in release_job
    assert f"$manifest.version -cne '{version}'" in release_job
    assert f"$manifest.tag -cne 'foundation-engine-v{version}'" in release_job
    assert "$manifest.channel -cne 'stable'" in release_job
    assert f"name: foundation-engine-{version}-release\n" in release_job
    assert f"path: .work/release/foundation-{version}/*\n" in release_job
