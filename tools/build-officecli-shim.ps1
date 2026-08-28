[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot '_common.ps1')

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$SourcePath = Join-Path $RepositoryRoot 'src\officecli-shim\Program.cs'
$PolicyPath = Join-Path $RepositoryRoot 'support\officecli-command-policy.json'
if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $PolicyPath -PathType Leaf)) {
    throw 'OfficeCLI shim source or policy is missing'
}

$Compiler = Find-RoslynCompiler -Purpose 'a deterministic OfficeCLI shim build'

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
