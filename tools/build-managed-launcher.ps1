[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$SourceDirectory = Join-Path $RepositoryRoot 'src\managed-launcher'
$Sources = @(
    (Join-Path $SourceDirectory 'Program.cs'),
    (Join-Path $SourceDirectory 'SessionRecovery.cs')
)
if (@($Sources | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) }).Count -ne 0) {
    throw 'Managed launcher source is missing'
}

$CompilerCandidates = New-Object System.Collections.Generic.List[string]
$VsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (Test-Path -LiteralPath $VsWhere -PathType Leaf) {
    @(& $VsWhere -products '*' -requires Microsoft.Component.MSBuild -property installationPath) |
        ForEach-Object {
            if (-not [string]::IsNullOrWhiteSpace($_)) {
                $CompilerCandidates.Add((Join-Path $_ 'MSBuild\Current\Bin\Roslyn\csc.exe'))
            }
        }
}
foreach ($VisualStudioRoot in @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio'),
    (Join-Path $env:ProgramFiles 'Microsoft Visual Studio')
)) {
    if (Test-Path -LiteralPath $VisualStudioRoot -PathType Container) {
        @(Get-ChildItem -Path (Join-Path $VisualStudioRoot '*\*\MSBuild\Current\Bin\Roslyn\csc.exe') -File -ErrorAction SilentlyContinue |
            Sort-Object -Property FullName -Descending) | ForEach-Object { $CompilerCandidates.Add($_.FullName) }
    }
}
$Compiler = @($CompilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -Unique) | Select-Object -First 1
if ([string]::IsNullOrWhiteSpace($Compiler)) {
    throw 'A Roslyn C# compiler is required for a deterministic managed launcher build.'
}

$DestinationDirectory = [IO.Path]::GetFullPath($OutputDirectory)
[IO.Directory]::CreateDirectory($DestinationDirectory) | Out-Null
$TemporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) ('managed-launcher-' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($TemporaryDirectory) | Out-Null

try {
    $TemporaryOutput = Join-Path $TemporaryDirectory 'managed-launcher.exe'
    $CompilerArguments = @(
        '/nologo', '/target:exe', '/platform:anycpu', '/optimize+', '/checked+', '/deterministic+',
        '/codepage:65001', '/utf8output', "/out:$TemporaryOutput", '/reference:System.Web.Extensions.dll'
    ) + $Sources
    & $Compiler @CompilerArguments
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $TemporaryOutput -PathType Leaf)) {
        throw 'Managed launcher compilation failed'
    }
    foreach ($Target in @('claude', 'codex', 'opencode')) {
        [IO.File]::Copy($TemporaryOutput, (Join-Path $DestinationDirectory ($Target + '-managed.exe')), $true)
    }
}
finally {
    if (Test-Path -LiteralPath $TemporaryDirectory -PathType Container) {
        Remove-Item -LiteralPath $TemporaryDirectory -Recurse -Force
    }
}
