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
