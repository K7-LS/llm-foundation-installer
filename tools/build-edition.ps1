[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [Parameter(Mandatory = $true)]
    [ValidateSet('Employee', 'Owner')]
    [string]$Edition,
    [ValidateSet('Preview', 'InternalUnsigned', 'PublicUnsigned', 'PublicSigned')]
    [string]$DistributionMode = 'Preview',
    [string]$PackageRoot,
    [string]$FoundationPackageRoot,
    [string]$ProviderEligibilityEvidence,
    [string]$ClientSourcesLock,
    [string]$RuntimeSourcesLock,
    [string]$RuntimeArchive,
    [string]$OfficeCliBinaryPath,
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

# Поставка издания — единый EXE: Launch Center живёт внутри установщика
# (--launch-center-ui), отдельный LC-бинарь больше не собирается.
$KnownInternalArtifactNames = @(
    'K7-AI-Foundation-Employee-InternalUnsigned.exe',
    'K7-AI-Foundation-Owner-InternalUnsigned.exe'
)
$InstallerName = "K7-AI-Foundation-$Edition-$DistributionMode.exe"
$LaunchCenterFallbackName = (
    "K7-AI-Launch-Center-$Edition-$DistributionMode.cmd"
)
$ExpectedChildDistributionMode = switch ($DistributionMode) {
    'Preview' { 'preview' }
    'InternalUnsigned' { 'internal_unsigned' }
    'PublicUnsigned' { 'public_unsigned' }
    'PublicSigned' { 'public_signed' }
}
if ($DistributionMode -ceq 'InternalUnsigned' -and
    $KnownInternalArtifactNames -cnotcontains $InstallerName) {
    throw 'Internal artifact naming contract is invalid'
}

. (Join-Path $PSScriptRoot '_common.ps1')

$RuntimeRecord = $null
if ([string]::IsNullOrWhiteSpace($RuntimeArchive)) {
    if ($DistributionMode -cne 'Preview') {
        throw 'RuntimeArchive is required outside Preview mode'
    }
}
else {
    $RuntimeArchive = [IO.Path]::GetFullPath($RuntimeArchive)
    if (-not (Test-Path -LiteralPath $RuntimeArchive -PathType Leaf) -or
        ((Get-Item -LiteralPath $RuntimeArchive).Attributes -band
            [IO.FileAttributes]::ReparsePoint)) {
        throw 'RuntimeArchive is missing or unsafe'
    }
    $RuntimeLockPath = if (
        [string]::IsNullOrWhiteSpace($RuntimeSourcesLock)
    ) {
        Join-Path $RepositoryRoot 'runtime-sources.lock.json'
    }
    else {
        [IO.Path]::GetFullPath($RuntimeSourcesLock)
    }
    try {
        $RuntimeLockValue = Get-Content -LiteralPath $RuntimeLockPath -Raw |
            ConvertFrom-Json
        $RuntimeUri = [Uri][string]$RuntimeLockValue.runtime.url
    }
    catch {
        throw 'RuntimeSourcesLock is invalid'
    }
    $RuntimeFileName = [IO.Path]::GetFileName(
        $RuntimeUri.AbsolutePath
    )
    $PinnedSingBoxVersion = [string](
        Get-Content -LiteralPath (
            Join-Path $RepositoryRoot 'src\gui\product-config.json'
        ) -Raw | ConvertFrom-Json
    ).singbox_version
    if ($PinnedSingBoxVersion -notmatch '^\d+\.\d+\.\d+$') {
        throw 'ProductConfig sing-box version is invalid'
    }
    if ([string]::IsNullOrWhiteSpace($RuntimeFileName) -or
        [string]$RuntimeLockValue.runtime.id -cne 'sing-box' -or
        [string]$RuntimeLockValue.runtime.version -cne (
            $PinnedSingBoxVersion
        ) -or
        [string]$RuntimeLockValue.runtime.sha256 -cne (
            Get-Sha256 $RuntimeArchive
        ) -or
        [IO.Path]::GetExtension($RuntimeFileName) -cne '.zip') {
        throw 'RuntimeArchive differs from RuntimeSourcesLock'
    }
    $RuntimeRecord = [ordered]@{
        id = 'sing-box'
        version = $PinnedSingBoxVersion
        file = $RuntimeFileName
        sha256 = Get-Sha256 $RuntimeArchive
        bytes = (Get-Item -LiteralPath $RuntimeArchive).Length
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
        @('OfficeCliBinaryPath', $OfficeCliBinaryPath),
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
$OutputCreated = $false
try {
    [IO.Directory]::CreateDirectory($WorkRoot) | Out-Null
    Invoke-ProductBuild -Role 'Installer' -Destination $InstallerRoot
    $InstallerManifest = Read-ChildManifest `
        -Root $InstallerRoot -Role 'Installer'

    [IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
    $OutputCreated = $true
    $InstallerOutput = Join-Path $OutputRoot $InstallerName
    Copy-Item -LiteralPath (
        Join-Path $InstallerRoot 'LLMFoundationInstaller.exe'
    ) -Destination $InstallerOutput
    $LaunchCenterFallbackOutput = Join-Path (
        $OutputRoot
    ) $LaunchCenterFallbackName
    $LaunchCenterFallbackContent = (
        "@echo off`r`n" +
        'start "" "%~dp0' + $InstallerName +
        '" --launch-center-ui' + "`r`n"
    )
    [IO.File]::WriteAllText(
        $LaunchCenterFallbackOutput,
        $LaunchCenterFallbackContent,
        $Utf8NoBom
    )
    if ($null -ne $RuntimeRecord) {
        $RuntimeOutput = Join-Path $OutputRoot $RuntimeRecord.file
        Copy-Item -LiteralPath $RuntimeArchive -Destination $RuntimeOutput
        if ((Get-Sha256 $RuntimeOutput) -cne $RuntimeRecord.sha256 -or
            (Get-Item -LiteralPath $RuntimeOutput).Length -ne
                $RuntimeRecord.bytes) {
            throw 'Copied runtime archive differs'
        }
    }

    $Manifest = [ordered]@{
        schema_version = 1
        app_id = 'k7-ai-edition-bundle'
        edition_id = $Edition
        version = [string]$InstallerManifest.version
        theme_id = [string]$InstallerManifest.theme_id
        owner_controlled = [bool]$InstallerManifest.owner_controlled
        distribution_allowed = [bool](
            $InstallerManifest.distribution_allowed
        )
        distribution_mode = $DistributionMode
        targets = @($InstallerManifest.targets)
        verdicts = $InstallerManifest.verdicts
        runtime = $RuntimeRecord
        launch_center_fallback = [ordered]@{
            product_role = 'LaunchCenter'
            file = $LaunchCenterFallbackName
            arguments = '--launch-center-ui'
            sha256 = Get-Sha256 $LaunchCenterFallbackOutput
            bytes = (
                Get-Item -LiteralPath $LaunchCenterFallbackOutput
            ).Length
        }
        products = [ordered]@{
            installer = [ordered]@{
                product_role = 'Installer'
                file = $InstallerName
                sha256 = Get-Sha256 $InstallerOutput
                bytes = (Get-Item -LiteralPath $InstallerOutput).Length
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
