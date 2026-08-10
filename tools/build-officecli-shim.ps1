[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-Sha256([string]$Path) {
    $Algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $Input = [IO.File]::OpenRead($Path)
        try {
            return ([BitConverter]::ToString($Algorithm.ComputeHash($Input))).Replace(
                '-', ''
            ).ToLowerInvariant()
        }
        finally {
            $Input.Dispose()
        }
    }
    finally {
        $Algorithm.Dispose()
    }
}

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$SourcePath = Join-Path $RepositoryRoot 'src\officecli-shim\Program.cs'
$PolicyPath = Join-Path $RepositoryRoot 'support\officecli-command-policy.json'
if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $PolicyPath -PathType Leaf)) {
    throw 'OfficeCLI shim source or policy is missing'
}

$CompilerCandidates = New-Object System.Collections.Generic.List[string]
$VsWhere = Join-Path ${env:ProgramFiles(x86)} (
    'Microsoft Visual Studio\Installer\vswhere.exe'
)
if (Test-Path -LiteralPath $VsWhere -PathType Leaf) {
    @(& $VsWhere -products '*' -requires Microsoft.Component.MSBuild `
        -property installationPath) | ForEach-Object {
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
        @(Get-ChildItem -Path (Join-Path $VisualStudioRoot `
            '*\*\MSBuild\Current\Bin\Roslyn\csc.exe') -File `
            -ErrorAction SilentlyContinue | Sort-Object -Property FullName -Descending) |
            ForEach-Object { $CompilerCandidates.Add($_.FullName) }
    }
}
$Compiler = @($CompilerCandidates | Where-Object {
    Test-Path -LiteralPath $_ -PathType Leaf
} | Select-Object -Unique) | Select-Object -First 1
if ([string]::IsNullOrWhiteSpace($Compiler)) {
    throw 'A Roslyn C# compiler is required for a deterministic OfficeCLI shim build.'
}

$Destination = [IO.Path]::GetFullPath($OutputPath)
$DestinationDirectory = Split-Path -Parent $Destination
[IO.Directory]::CreateDirectory($DestinationDirectory) | Out-Null
$PolicyHash = Get-Sha256 $PolicyPath
$TemporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) (
    'officecli-shim-' + [Guid]::NewGuid().ToString('N')
)
[IO.Directory]::CreateDirectory($TemporaryDirectory) | Out-Null

try {
    $TemporarySource = Join-Path $TemporaryDirectory 'Program.cs'
    $Source = [IO.File]::ReadAllText($SourcePath, [Text.UTF8Encoding]::new($false))
    if ($Source.IndexOf('__POLICY_SHA256__', [StringComparison]::Ordinal) -lt 0) {
        throw 'OfficeCLI shim policy hash placeholder is missing'
    }
    [IO.File]::WriteAllText(
        $TemporarySource,
        $Source.Replace('__POLICY_SHA256__', $PolicyHash),
        [Text.UTF8Encoding]::new($false)
    )
    $CompilerArguments = @(
        '/nologo',
        '/target:exe',
        '/platform:anycpu',
        '/optimize+',
        '/checked+',
        '/deterministic+',
        '/codepage:65001',
        '/utf8output',
        "/out:$Destination",
        '/reference:System.Web.Extensions.dll',
        $TemporarySource
    )
    & $Compiler @CompilerArguments
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
        throw 'OfficeCLI shim compilation failed'
    }
}
finally {
    if (Test-Path -LiteralPath $TemporaryDirectory -PathType Container) {
        Remove-Item -LiteralPath $TemporaryDirectory -Recurse -Force
    }
}
