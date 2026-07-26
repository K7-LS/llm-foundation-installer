[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$VersionPath = Join-Path $RepositoryRoot 'VERSION'
$SourcePath = Join-Path $RepositoryRoot 'src\foundation.ps1'

if (Test-Path -LiteralPath $OutputRoot) {
    throw 'OutputRoot must not exist'
}
if (-not (Test-Path -LiteralPath $VersionPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
    throw 'Foundation source is incomplete'
}

$Version = ([IO.File]::ReadAllText($VersionPath)).Trim()
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw 'Foundation version is invalid'
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Stream = [IO.File]::Open(
        $Path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    $Algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return -join (
            $Algorithm.ComputeHash($Stream) |
                ForEach-Object { $_.ToString('x2') }
        )
    } finally {
        $Algorithm.Dispose()
        $Stream.Dispose()
    }
}

[IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
$BundledScript = Join-Path $OutputRoot 'foundation.ps1'
[IO.File]::Copy($SourcePath, $BundledScript, $false)
$Encoding = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText(
    (Join-Path $OutputRoot 'VERSION'),
    $Version + "`n",
    $Encoding
)
$Hash = Get-Sha256 $BundledScript
$Manifest = @"
{
  "commands": [
    "doctor",
    "install",
    "inventory",
    "plan",
    "rollback"
  ],
  "engine_version": "$Version",
  "foundation_ps1_sha256": "$Hash",
  "network": "offline",
  "protocol_version": 1,
  "schema_version": 1,
  "supported_powershell": [
    "5.1",
    "7"
  ]
}
"@.Replace("`r`n", "`n")
[IO.File]::WriteAllText(
    (Join-Path $OutputRoot 'engine-manifest.json'),
    $Manifest,
    $Encoding
)
Write-Output "Foundation engine $Version built."
