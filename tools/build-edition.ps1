[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [Parameter(Mandatory = $true)]
    [ValidateSet('Employee', 'Owner')]
    [string]$Edition,
    [ValidateSet('Preview', 'InternalUnsigned', 'PublicSigned')]
    [string]$DistributionMode = 'Preview',
    [string]$PackageRoot,
    [string]$FoundationPackageRoot,
    [string]$ProviderEligibilityEvidence,
    [string]$ClientSourcesLock,
    [string]$RuntimeSourcesLock,
    [switch]$AllowLocalTestSources,
    [string]$SigningCertificateThumbprint,
    [string]$TimestampServer = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$OutputParent = Split-Path -Parent $OutputRoot
if ([string]::IsNullOrWhiteSpace($OutputParent)) {
    throw 'OutputRoot parent is invalid'
}
if (Test-Path -LiteralPath $OutputRoot) {
    throw 'OutputRoot must not exist'
}

$KnownInternalArtifactNames = @(
    'K7-AI-Foundation-Employee-InternalUnsigned.exe',
    'K7-AI-Launch-Center-Employee-InternalUnsigned.exe',
    'K7-AI-Foundation-Owner-InternalUnsigned.exe',
    'K7-AI-Launch-Center-Owner-InternalUnsigned.exe'
)
$InstallerName = "K7-AI-Foundation-$Edition-$DistributionMode.exe"
$LaunchCenterName = (
    "K7-AI-Launch-Center-$Edition-$DistributionMode.exe"
)
$ExpectedChildDistributionMode = switch ($DistributionMode) {
    'Preview' { 'preview' }
    'InternalUnsigned' { 'internal_unsigned' }
    'PublicSigned' { 'public_signed' }
}
if ($DistributionMode -ceq 'InternalUnsigned' -and
    ($KnownInternalArtifactNames -cnotcontains $InstallerName -or
        $KnownInternalArtifactNames -cnotcontains $LaunchCenterName)) {
    throw 'Internal artifact naming contract is invalid'
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Stream = [IO.File]::OpenRead($Path)
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

function Invoke-ProductBuild {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $Arguments = @(
        '-NoLogo',
        '-NoProfile',
        '-NonInteractive',
        '-File',
        (Join-Path $PSScriptRoot 'build-gui.ps1'),
        '-OutputRoot',
        $Destination,
        '-Edition',
        $Edition,
        '-ProductRole',
        $Role,
        '-DistributionMode',
        $DistributionMode
    )
    foreach ($Pair in @(
        @('PackageRoot', $PackageRoot),
        @('FoundationPackageRoot', $FoundationPackageRoot),
        @('ProviderEligibilityEvidence', $ProviderEligibilityEvidence),
        @('ClientSourcesLock', $ClientSourcesLock),
        @('RuntimeSourcesLock', $RuntimeSourcesLock),
        @('SigningCertificateThumbprint', $SigningCertificateThumbprint),
        @('TimestampServer', $TimestampServer)
    )) {
        if (-not [string]::IsNullOrWhiteSpace([string]$Pair[1])) {
            $Arguments += "-$($Pair[0])"
            $Arguments += [string]$Pair[1]
        }
    }
    if ($AllowLocalTestSources) {
        $Arguments += '-AllowLocalTestSources'
    }
    & (Get-Process -Id $PID).Path @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Role build failed"
    }
}

function Read-ChildManifest {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $Path = Join-Path $Root 'bundle-manifest.json'
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Role child manifest is missing"
    }
    try {
        $Value = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    } catch {
        throw "$Role child manifest is invalid"
    }
    if ([int]$Value.schema_version -ne 1 -or
        [string]$Value.edition_id -cne $Edition -or
        [string]$Value.product_role -cne $Role -or
        [string]$Value.distribution_mode -cne
            $ExpectedChildDistributionMode) {
        throw "$Role child manifest contract differs"
    }
    $Executable = Join-Path $Root 'LLMFoundationInstaller.exe'
    $Record = $Value.artifacts.'LLMFoundationInstaller.exe'
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf) -or
        [string]$Record.sha256 -cne (Get-Sha256 $Executable) -or
        [long]$Record.bytes -ne (
            Get-Item -LiteralPath $Executable
        ).Length) {
        throw "$Role child executable differs from its manifest"
    }
    return $Value
}

$WorkRoot = $OutputRoot + '.build-' + [Guid]::NewGuid().ToString('N')
$InstallerRoot = Join-Path $WorkRoot 'installer'
$LaunchCenterRoot = Join-Path $WorkRoot 'launch-center'
$OutputCreated = $false
try {
    [IO.Directory]::CreateDirectory($WorkRoot) | Out-Null
    Invoke-ProductBuild -Role 'Installer' -Destination $InstallerRoot
    Invoke-ProductBuild -Role 'LaunchCenter' -Destination $LaunchCenterRoot
    $InstallerManifest = Read-ChildManifest `
        -Root $InstallerRoot -Role 'Installer'
    $LaunchCenterManifest = Read-ChildManifest `
        -Root $LaunchCenterRoot -Role 'LaunchCenter'
    if ((@($InstallerManifest.targets) -join ',') -cne
        (@($LaunchCenterManifest.targets) -join ',') -or
        [string]$InstallerManifest.theme_id -cne
            [string]$LaunchCenterManifest.theme_id) {
        throw 'Child product contracts differ'
    }

    [IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
    $OutputCreated = $true
    $InstallerOutput = Join-Path $OutputRoot $InstallerName
    $LaunchCenterOutput = Join-Path $OutputRoot $LaunchCenterName
    Copy-Item -LiteralPath (
        Join-Path $InstallerRoot 'LLMFoundationInstaller.exe'
    ) -Destination $InstallerOutput
    Copy-Item -LiteralPath (
        Join-Path $LaunchCenterRoot 'LLMFoundationInstaller.exe'
    ) -Destination $LaunchCenterOutput

    $Manifest = [ordered]@{
        schema_version = 1
        app_id = 'k7-ai-edition-bundle'
        edition_id = $Edition
        theme_id = [string]$InstallerManifest.theme_id
        owner_controlled = [bool]$InstallerManifest.owner_controlled
        distribution_allowed = [bool](
            $InstallerManifest.distribution_allowed
        )
        distribution_mode = $DistributionMode
        targets = @($InstallerManifest.targets)
        verdicts = $InstallerManifest.verdicts
        products = [ordered]@{
            installer = [ordered]@{
                product_role = 'Installer'
                file = $InstallerName
                sha256 = Get-Sha256 $InstallerOutput
                bytes = (Get-Item -LiteralPath $InstallerOutput).Length
            }
            launch_center = [ordered]@{
                product_role = 'LaunchCenter'
                file = $LaunchCenterName
                sha256 = Get-Sha256 $LaunchCenterOutput
                bytes = (
                    Get-Item -LiteralPath $LaunchCenterOutput
                ).Length
            }
        }
    }
    [IO.File]::WriteAllText(
        (Join-Path $OutputRoot 'bundle-manifest.json'),
        ((ConvertTo-Json $Manifest -Depth 8) + "`n"),
        $Utf8NoBom
    )
} catch {
    if ($OutputCreated -and
        (Test-Path -LiteralPath $OutputRoot -PathType Container)) {
        Remove-Item -LiteralPath $OutputRoot -Recurse -Force
    }
    throw
} finally {
    if (Test-Path -LiteralPath $WorkRoot -PathType Container) {
        Remove-Item -LiteralPath $WorkRoot -Recurse -Force
    }
}

Write-Output $OutputRoot
