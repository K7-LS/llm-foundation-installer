[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$SourcePath = Join-Path $RepositoryRoot 'src\officecli-pdf-exporter\Program.cs'
if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
    throw 'OfficeCLI PDF exporter source is missing'
}
. (Join-Path $PSScriptRoot '_common.ps1')
$Compiler = Find-RoslynCompiler -Purpose 'the OfficeCLI PDF exporter'
$Destination = [IO.Path]::GetFullPath($OutputPath)
[IO.Directory]::CreateDirectory((Split-Path -Parent $Destination)) | Out-Null
& $Compiler @(
    '/nologo', '/target:exe', '/platform:anycpu', '/optimize+', '/checked+',
    '/deterministic+', '/codepage:65001', '/utf8output',
    "/out:$Destination", '/reference:Microsoft.CSharp.dll', $SourcePath
)
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
    throw 'OfficeCLI PDF exporter compilation failed'
}
