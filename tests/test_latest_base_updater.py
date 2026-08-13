from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "gui" / "BaseReleaseUpdater.cs"
APP = ROOT / "src" / "gui" / "InstallerApp.cs"
BUILD = ROOT / "tools" / "build-gui.ps1"


def test_latest_base_sources_are_exact_and_native() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "daniileliseev1337/codex-base" in source
    assert "daniileliseev1337/claude-base-v2" in source
    assert "daniileliseev1337/opencode-base" in source
    assert "/releases/latest" in source
    assert '"codex-v"' in source
    assert '"claude-v"' in source
    assert '"opencode-v"' in source


def test_latest_base_is_fail_closed_before_cache_activation() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    required = (
        "release.draft",
        "release.prerelease",
        "!release.immutable",
        'manifest.channel, "stable"',
        "manifest.foundation_engine_version",
        "manifest.foundation_engine_manifest_sha256",
        "manifest.requires.immutable_release",
        "manifest.requires.release_attestation",
        "DigestValue(packageAsset.digest)",
        "manifest.asset.bytes != packageAsset.size",
        "WriteAtomic(destination, payload)",
    )
    for contract in required:
        assert contract in source


def test_installer_resolves_latest_before_client_and_foundation_plan() -> None:
    app = APP.read_text(encoding="utf-8")
    workflow = app.split("foreach (TargetRow row in selected)", 1)[1]
    assert workflow.index("RunBaseReleaseResolveAsync") < workflow.index(
        "RunClientPlanAsync"
    )
    assert "row.latest_base_package_path" in app
    assert "row.latest_base_manifest_path" in app
    assert "row.latest_base_manifest_sha256" in app
    assert 'arguments.Add("-ReleaseManifest")' in app
    assert 'arguments.Add("-ReleaseManifestSha256")' in app


def test_embedded_package_is_only_explicit_latest_failure_fallback() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    assert 'status = "EMBEDDED_FALLBACK"' in source
    assert "used_embedded_fallback = true" in source
    assert "использован проверенный embedded fallback" in app
    assert 'args[0] == "--latest-base-json"' in app


def test_latest_base_source_is_compiled_into_every_gui() -> None:
    build = BUILD.read_text(encoding="utf-8")
    assert "src\\gui\\BaseReleaseUpdater.cs" in build
    assert "$BaseReleaseUpdaterSource" in build


def test_installer_prompts_for_every_unknown_before_reconcile() -> None:
    app = APP.read_text(encoding="utf-8")
    assert '"BLOCKED_USER_DECISION"' in app
    assert 'MessageBoxButton.YesNoCancel' in app
    assert 'arguments.Add("-LocalExceptionPath")' in app
    assert 'arguments.Add("-ConfirmRemoveUnknown")' in app
    assert "row.local_exception_paths" in app
    assert "row.confirm_remove_unknown" in app
