"""Ф2: God-файл InstallerApp.cs разнесён на модули по границам типов.

Каждый top-level тип живёт в своём файле; InstallerApp.cs хранит только
Program и assembly-атрибуты. Сборка компилирует все модули.
"""

from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
GUI = REPOSITORY / "src" / "gui"

EXPECTED_MODULES = {
    "InstallerModels.cs": [
        "TargetRow",
        "ClientDetectionResult",
        "TrustedFile",
        "TrustedPackage",
        "ProviderEligibilityRecord",
        "TrustedPackageIndex",
        "CatalogResult",
        "ConnectionProbeResult",
        "SuccessReportResult",
        "PlatformCompatibilityResult",
    ],
    "PlatformCompatibility.cs": ["PlatformCompatibility"],
    "ProductCatalog.cs": ["ProductCatalog"],
    "ClientDetector.cs": ["ClientDetector"],
    "RuntimePayload.cs": ["RuntimePayload"],
    "FoundationWorkflow.cs": ["FoundationWorkflow"],
    "BundleIntegrity.cs": ["BundleIntegrity"],
    "InstallerView.cs": ["InstallerView"],
    "LaunchCenterActions.cs": ["LaunchCenterActions"],
    "InstallerActions.cs": ["InstallerActions"],
    "ChromeProxyLauncher.cs": ["ChromeProxyLauncher"],
    "ConnectionUi.cs": ["ConnectionUi", "ConnectionUiContract"],
    "ConnectionProbe.cs": ["ConnectionProbe"],
    "InstallerApp.cs": ["Program"],
}


def _type_headers(source: str) -> set[str]:
    names = set()
    for line in source.splitlines():
        if line.startswith("    internal sealed class ") or line.startswith(
            "    internal static class "
        ):
            names.add(line.split(" class ", 1)[1].split(" ")[0].split(":")[0])
    return names


def test_every_module_holds_exactly_its_types():
    for module, types in EXPECTED_MODULES.items():
        source = (GUI / module).read_text(encoding="utf-8")
        assert _type_headers(source) == set(types), module


def test_installer_app_keeps_only_program_and_assembly_metadata():
    source = (GUI / "InstallerApp.cs").read_text(encoding="utf-8")
    assert "[assembly: AssemblyTitle" in source
    assert len(source.splitlines()) < 1300


def test_build_script_compiles_every_module():
    build_script = (
        REPOSITORY / "tools" / "build-gui.ps1"
    ).read_text(encoding="utf-8")
    for module in EXPECTED_MODULES:
        assert module in build_script, module
