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
$Candidates = New-Object System.Collections.Generic.List[string]
foreach ($Root in @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio'),
    (Join-Path $env:ProgramFiles 'Microsoft Visual Studio')
)) {
    if (Test-Path -LiteralPath $Root -PathType Container) {
        @(Get-ChildItem -Path (Join-Path $Root '*\*\MSBuild\Current\Bin\Roslyn\csc.exe') `
            -File -ErrorAction SilentlyContinue | Sort-Object FullName -Descending) |
            ForEach-Object { $Candidates.Add($_.FullName) }
    }
}
$Compiler = @($Candidates | Select-Object -Unique | Where-Object {
    Test-Path -LiteralPath $_ -PathType Leaf
}) | Select-Object -First 1
if ([string]::IsNullOrWhiteSpace($Compiler)) {
    throw 'A Roslyn C# compiler is required for the OfficeCLI PDF exporter.'
}
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
