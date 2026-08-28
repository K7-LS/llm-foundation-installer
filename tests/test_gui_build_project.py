"""Ф3: GUI собирается SDK-style проектом (dotnet build), не csc.exe напрямую.

Список компилируемых модулей живёт в csproj; build-gui.ps1 остаётся
оркестратором (staging, локи, ресурсы, манифесты) и вызывает dotnet build.
"""

from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
GUI = REPOSITORY / "src" / "gui"
CSPROJ = GUI / "LlmFoundationInstaller.csproj"


def test_every_gui_source_is_compiled_by_the_sdk_project():
    project = CSPROJ.read_text(encoding="utf-8")
    for path in sorted(GUI.glob("*.cs")):
        assert (
            f'<Compile Include="{path.name}" />' in project
        ), path.name


def test_sdk_project_keeps_build_contracts():
    project = CSPROJ.read_text(encoding="utf-8")
    assert "<TargetFramework>net48</TargetFramework>" in project
    assert "<OutputType>WinExe</OutputType>" in project
    assert "<UseWPF>true</UseWPF>" in project
    assert "<GenerateAssemblyInfo>false</GenerateAssemblyInfo>" in project
    assert "<EnableDefaultItems>false</EnableDefaultItems>" in project
    assert "<DebugType>none</DebugType>" in project
    assert "<Deterministic>true</Deterministic>" in project
    assert "app.manifest" in project


def test_views_are_compiled_pages_not_loose_resources():
    project = CSPROJ.read_text(encoding="utf-8")
    for view in (
        "InstallerEmployeeView.xaml",
        "InstallerOwnerView.xaml",
        "LaunchCenterEmployeeView.xaml",
        "LaunchCenterOwnerView.xaml",
    ):
        assert f'<Page Include="{view}" />' in project, view
    build_script = (
        REPOSITORY / "tools" / "build-gui.ps1"
    ).read_text(encoding="utf-8")
    assert "View.xaml" not in build_script
    loader = (GUI / "InstallerView.cs").read_text(encoding="utf-8")
    assert "XamlReader" not in loader
    assert "LoadComponent" in loader


def test_restore_is_offline_by_config():
    # Ни одного PackageReference в репозитории нет — implicit restore не должен
    # ходить в сеть: источники NuGet очищены корневым nuget.config.
    config = (REPOSITORY / "nuget.config").read_text(encoding="utf-8")
    assert "<clear />" in config
    assert "<add " not in config
    project = CSPROJ.read_text(encoding="utf-8")
    assert "PackageReference" not in project


def test_build_script_delegates_compilation_to_dotnet():
    build_script = (
        REPOSITORY / "tools" / "build-gui.ps1"
    ).read_text(encoding="utf-8")
    assert "LlmFoundationInstaller.csproj" in build_script
    assert "dotnet" in build_script
    assert "csc.exe" not in build_script
    assert "Roslyn" not in build_script
    assert "vswhere.exe" not in build_script
    assert "AllowLegacyTestCompiler" not in build_script


def test_installer_app_stays_thin():
    # Ф2-храповик (перенесён из удалённого test_gui_modules.py, п.27):
    # InstallerApp.cs держит только Program и assembly-метаданные; порог
    # ужесточён 1300 → 1200 (факт ~1134). Пофайловая карта типов удалена —
    # она покрывала 14 модулей из 30 и требовала ручного обновления.
    source = (GUI / "InstallerApp.cs").read_text(encoding="utf-8")
    assert "[assembly: AssemblyTitle" in source
    headers = {
        line.split(" class ", 1)[1].split(" ")[0].split(":")[0]
        for line in source.splitlines()
        if line.startswith(
            ("    internal sealed class ", "    internal static class ")
        )
    }
    assert headers == {"Program"}
    assert len(source.splitlines()) < 1200
