[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [Parameter(Mandatory = $true)]
    [ValidateSet('Employee', 'Owner')]
    [string]$Edition,
    [Parameter(Mandatory = $true)]
    [ValidateSet('Installer', 'LaunchCenter')]
    [string]$ProductRole,
    [string]$PackageRoot,
    [string]$FoundationPackageRoot,
    [string]$ProviderEligibilityEvidence,
    [ValidateSet('Preview', 'InternalUnsigned', 'PublicSigned')]
    [string]$DistributionMode = 'Preview',
    [string]$ClientSourcesLock,
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
if (-not [string]::IsNullOrWhiteSpace($PackageRoot)) {
    $PackageRoot = [IO.Path]::GetFullPath($PackageRoot)
    if (-not (Test-Path -LiteralPath $PackageRoot -PathType Container)) {
        throw 'PackageRoot does not exist'
    }
}
if ([string]::IsNullOrWhiteSpace($FoundationPackageRoot) -and
    -not [string]::IsNullOrWhiteSpace($PackageRoot)) {
    $FoundationCandidate = Join-Path $PackageRoot 'foundation'
    if (Test-Path -LiteralPath $FoundationCandidate -PathType Container) {
        $FoundationPackageRoot = $FoundationCandidate
    }
}
if (-not [string]::IsNullOrWhiteSpace($FoundationPackageRoot)) {
    $FoundationPackageRoot = [IO.Path]::GetFullPath(
        $FoundationPackageRoot
    )
    if (-not (
        Test-Path -LiteralPath $FoundationPackageRoot -PathType Container
    )) {
        throw 'FoundationPackageRoot does not exist'
    }
}
if ([string]::IsNullOrWhiteSpace($ClientSourcesLock)) {
    $ClientSourcesLock = Join-Path (
        [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    ) 'client-sources.lock.json'
}
$ClientSourcesLock = [IO.Path]::GetFullPath($ClientSourcesLock)
if (-not (Test-Path -LiteralPath $ClientSourcesLock -PathType Leaf)) {
    throw 'ClientSourcesLock does not exist'
}
$ClientSourcesItem = Get-Item -LiteralPath $ClientSourcesLock -Force
if (($ClientSourcesItem.Attributes -band
    [IO.FileAttributes]::ReparsePoint) -ne 0 -or
    $ClientSourcesItem.Length -lt 2 -or
    $ClientSourcesItem.Length -gt 65536) {
    throw 'ClientSourcesLock file is unsafe'
}

if (Test-Path -LiteralPath $OutputRoot) {
    throw 'OutputRoot must not exist'
}

function Get-AssemblyPath {
    param(
        [Parameter(Mandatory = $true)][string[]]$Roots,
        [Parameter(Mandatory = $true)][string]$Name
    )
    foreach ($Root in $Roots) {
        $Match = Get-ChildItem -LiteralPath $Root -Recurse -Filter $Name `
            -ErrorAction SilentlyContinue |
            Sort-Object FullName |
            Select-Object -First 1
        if ($null -ne $Match) {
            return $Match.FullName
        }
    }
    throw "Required framework assembly is missing: $Name"
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

function Assert-ExactJsonProperties {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string[]]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ($null -eq $Value) {
        throw "$Label is missing"
    }
    $Actual = @($Value.PSObject.Properties.Name)
    $Unexpected = @(
        $Actual | Where-Object { $Expected -cnotcontains $_ }
    )
    $Missing = @(
        $Expected | Where-Object { $Actual -cnotcontains $_ }
    )
    if ($Unexpected.Count -gt 0 -or $Missing.Count -gt 0) {
        throw "$Label contains unexpected or personal-data fields"
    }
}

function Assert-JsonBoolean {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][bool]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ($Value -isnot [bool] -or [bool]$Value -ne $Expected) {
        throw "Provider eligibility control is not accepted: $Label"
    }
}

function Convert-ProviderTimestamp {
    param(
        [Parameter(Mandatory = $true)]$Value
    )
    if ($Value -is [DateTime]) {
        if ($Value.Kind -ne [DateTimeKind]::Utc) {
            throw 'Provider eligibility timestamp must be UTC'
        }
        return ([DateTimeOffset]$Value).ToUniversalTime()
    }
    if ($Value -isnot [string]) {
        throw 'Provider eligibility timestamp must be a string'
    }
    return [DateTimeOffset]::ParseExact(
        [string]$Value,
        "yyyy-MM-dd'T'HH:mm:ss'Z'",
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal
    ).ToUniversalTime()
}

function Read-ProviderEligibilityEvidence {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }
    $FullPath = [IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        throw 'Provider eligibility evidence does not exist'
    }
    $Item = Get-Item -LiteralPath $FullPath -Force
    if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Provider eligibility evidence cannot be a reparse point'
    }
    if ($Item.Length -lt 1 -or $Item.Length -gt 16384) {
        throw 'Provider eligibility evidence size is invalid'
    }
    $RawEvidence = [IO.File]::ReadAllText($FullPath)
    try {
        $Evidence = $RawEvidence | ConvertFrom-Json
    } catch {
        throw 'Provider eligibility evidence JSON is invalid'
    }

    Assert-ExactJsonProperties $Evidence @(
        'schema_version',
        'reviewed_at_utc',
        'expires_at_utc',
        'sources',
        'claude'
    ) 'Provider eligibility evidence'
    Assert-ExactJsonProperties $Evidence.sources @(
        'supported_regions',
        'usage_policy',
        'consumer_terms',
        'safeguards_appeals'
    ) 'Provider eligibility sources'
    Assert-ExactJsonProperties $Evidence.claude @(
        'employee_location_eligibility_verified',
        'organization_eligibility_verified',
        'individual_accounts_only',
        'transport_not_used_for_region_or_ban_bypass',
        'unattended_consumer_automation'
    ) 'Provider eligibility Claude controls'

    if (($Evidence.schema_version -isnot [int] -and
        $Evidence.schema_version -isnot [long]) -or
        [long]$Evidence.schema_version -ne 1) {
        throw 'Provider eligibility evidence schema is unsupported'
    }
    $ExpectedSources = [ordered]@{
        supported_regions = 'https://www.anthropic.com/supported-countries'
        usage_policy = 'https://www.anthropic.com/legal/aup'
        consumer_terms = 'https://www.anthropic.com/legal/consumer-terms'
        safeguards_appeals = (
            'https://support.claude.com/en/articles/' +
            '8241253-safeguards-warnings-and-appeals'
        )
    }
    foreach ($Name in $ExpectedSources.Keys) {
        if ([string]$Evidence.sources.$Name -cne $ExpectedSources[$Name]) {
            throw "Provider eligibility source is not canonical: $Name"
        }
    }

    Assert-JsonBoolean `
        $Evidence.claude.employee_location_eligibility_verified `
        $true 'employee location eligibility'
    Assert-JsonBoolean `
        $Evidence.claude.organization_eligibility_verified `
        $true 'organization eligibility'
    Assert-JsonBoolean `
        $Evidence.claude.individual_accounts_only `
        $true 'individual accounts'
    Assert-JsonBoolean `
        $Evidence.claude.transport_not_used_for_region_or_ban_bypass `
        $true 'transport is not a region or ban bypass'
    Assert-JsonBoolean `
        $Evidence.claude.unattended_consumer_automation `
        $false 'unattended consumer automation'

    $CanonicalTimestamp = (
        '\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z'
    )
    $ReviewedMatches = [regex]::Matches(
        $RawEvidence,
        '"reviewed_at_utc"\s*:\s*"(?<value>' +
            $CanonicalTimestamp + ')"'
    )
    $ExpiresMatches = [regex]::Matches(
        $RawEvidence,
        '"expires_at_utc"\s*:\s*"(?<value>' +
            $CanonicalTimestamp + ')"'
    )
    if ($ReviewedMatches.Count -ne 1 -or
        $ExpiresMatches.Count -ne 1) {
        throw 'Provider eligibility timestamps are not canonical UTC'
    }
    try {
        $ReviewedAt = Convert-ProviderTimestamp (
            $ReviewedMatches[0].Groups['value'].Value
        )
        $ExpiresAt = Convert-ProviderTimestamp (
            $ExpiresMatches[0].Groups['value'].Value
        )
    } catch {
        throw 'Provider eligibility evidence timestamps are invalid'
    }
    $Now = [DateTimeOffset]::UtcNow
    if ($ReviewedAt -gt $Now.AddMinutes(5)) {
        throw 'Provider eligibility evidence review time is in the future'
    }
    if ($ExpiresAt -le $Now) {
        throw 'Provider eligibility evidence is expired'
    }
    if ($ExpiresAt -le $ReviewedAt -or
        ($ExpiresAt - $ReviewedAt) -gt [TimeSpan]::FromDays(7)) {
        throw 'Provider eligibility evidence validity window is invalid'
    }

    return [ordered]@{
        status = 'PASS'
        path = $FullPath
        sha256 = Get-Sha256 $FullPath
        bytes = [long]$Item.Length
        reviewed_at_utc = $ReviewedAt.ToString(
            'yyyy-MM-ddTHH:mm:ssZ',
            [Globalization.CultureInfo]::InvariantCulture
        )
        expires_at_utc = $ExpiresAt.ToString(
            'yyyy-MM-ddTHH:mm:ssZ',
            [Globalization.CultureInfo]::InvariantCulture
        )
        contains_personal_data = $false
    }
}

function Assert-SafeLeafName {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ([string]::IsNullOrWhiteSpace($Name) -or
        [IO.Path]::GetFileName($Name) -cne $Name -or
        $Name -in @('.', '..')) {
        throw "Package acceptance contains unsafe $Label"
    }
}

function Assert-FileBinding {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)]$Record,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Assert-SafeLeafName ([string]$Record.name) $Label
    $Path = Join-Path $Directory ([string]$Record.name)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Package acceptance references missing $Label"
    }
    $Item = Get-Item -LiteralPath $Path -Force
    if (($Item.Attributes -band
        [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Package acceptance $Label cannot be a reparse point"
    }
    $ExpectedHash = [string]$Record.sha256
    $ExpectedBytes = [long]$Record.bytes
    if ($ExpectedHash -notmatch '^[a-f0-9]{64}$' -or
        $ExpectedBytes -lt 0 -or
        (Get-Sha256 $Path) -cne $ExpectedHash -or
        $Item.Length -ne $ExpectedBytes) {
        throw "Package acceptance $Label binding mismatch"
    }
    return $Path
}

function Assert-ReleaseBinding {
    param(
        [Parameter(Mandatory = $true)]$Evidence,
        [Parameter(Mandatory = $true)]$Release
    )
    if ($null -eq $Evidence.release_binding) {
        throw 'Acceptance evidence release binding is missing'
    }
    $Fields = @(
        'target',
        'version',
        'tag',
        'asset',
        'package_manifest_sha256',
        'components_lock_sha256',
        'source',
        'foundation_engine_version',
        'foundation_engine_manifest_sha256'
    )
    if ($null -ne $Evidence.release_binding.PSObject.Properties['client']) {
        $Fields += 'client'
    }
    foreach ($Field in $Fields) {
        $EvidenceValue = $Evidence.release_binding.$Field |
            ConvertTo-Json -Depth 30 -Compress
        $ReleaseValue = $Release.$Field |
            ConvertTo-Json -Depth 30 -Compress
        if ($EvidenceValue -cne $ReleaseValue) {
            throw "Acceptance release binding differs: $Field"
        }
    }
}

function Read-AcceptedFoundation {
    param([string]$Root)
    if ([string]::IsNullOrWhiteSpace($Root)) {
        return $null
    }
    $Directory = Get-Item -LiteralPath $Root -Force
    if (-not $Directory.PSIsContainer -or
        ($Directory.Attributes -band
            [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Foundation package directory is unsafe'
    }
    $AcceptancePath = Join-Path $Directory.FullName (
        'package-acceptance.json'
    )
    if (-not (Test-Path -LiteralPath $AcceptancePath -PathType Leaf)) {
        throw 'Foundation package acceptance is missing'
    }
    try {
        $Acceptance = Get-Content -LiteralPath $AcceptancePath -Raw |
            ConvertFrom-Json
    } catch {
        throw 'Foundation package acceptance JSON is invalid'
    }
    if ([int]$Acceptance.schema_version -ne 1 -or
        [string]$Acceptance.target -cne 'foundation' -or
        [string]$Acceptance.engine_version -cne '0.2.1' -or
        [string]$Acceptance.package_acceptance -cne 'PASS' -or
        $Acceptance.immutable_release -isnot [bool] -or
        [bool]$Acceptance.immutable_release -ne $true -or
        $Acceptance.release_attestation -isnot [bool] -or
        [bool]$Acceptance.release_attestation -ne $true) {
        throw 'Foundation package acceptance contract is not PASS'
    }
    $AssetPath = Assert-FileBinding $Directory.FullName `
        $Acceptance.asset 'Foundation asset'
    $ReleasePath = Assert-FileBinding $Directory.FullName `
        $Acceptance.release_manifest 'Foundation release manifest'
    $EvidencePath = Assert-FileBinding $Directory.FullName `
        $Acceptance.acceptance_evidence 'Foundation acceptance evidence'
    $VerificationPath = Assert-FileBinding $Directory.FullName `
        $Acceptance.release_verification 'Foundation release verification'
    try {
        $Release = Get-Content -LiteralPath $ReleasePath -Raw |
            ConvertFrom-Json
        $Evidence = Get-Content -LiteralPath $EvidencePath -Raw |
            ConvertFrom-Json
        $Verification = Get-Content -LiteralPath $VerificationPath -Raw |
            ConvertFrom-Json
    } catch {
        throw 'Foundation package evidence JSON is invalid'
    }
    $ExpectedRepository = (
        [string]$Release.source.repository
    ) -replace '^https://github\.com/', ''
    $VerifiedAssets = @($Verification.assets)
    $ReleaseFiles = @(
        $Release.engine_files.PSObject.Properties.Name | Sort-Object
    )
    $AcceptanceFiles = @(
        $Acceptance.engine_files.PSObject.Properties.Name | Sort-Object
    )
    if ([int]$Release.schema_version -ne 1 -or
        [string]$Release.target -cne 'foundation' -or
        [string]$Release.version -cne '0.2.1' -or
        [string]$Release.tag -cne 'foundation-engine-v0.2.1' -or
        [string]$Release.channel -cne 'stable' -or
        [string]$Release.asset.name -cne (
            [string]$Acceptance.asset.name
        ) -or
        [string]$Release.asset.sha256 -cne (
            [string]$Acceptance.asset.sha256
        ) -or
        [long]$Release.asset.bytes -ne (
            [long]$Acceptance.asset.bytes
        ) -or
        [string]$Release.acceptance_evidence_sha256 -cne (
            Get-Sha256 $EvidencePath
        ) -or
        [bool]$Release.requires.immutable_release -ne $true -or
        [bool]$Release.requires.release_attestation -ne $true -or
        ($ReleaseFiles -join ',') -cne (
            'engine-manifest.json,foundation.ps1,VERSION'
        ) -or
        ($AcceptanceFiles -join ',') -cne ($ReleaseFiles -join ',') -or
        ($Release.engine_files | ConvertTo-Json -Depth 10 -Compress) -cne (
            $Acceptance.engine_files |
                ConvertTo-Json -Depth 10 -Compress
        ) -or
        [string]$Release.evidence_body_sha256 -notmatch (
            '^[a-f0-9]{64}$'
        )) {
        throw 'Foundation package release manifest is invalid'
    }
    foreach ($Name in $ReleaseFiles) {
        $Record = $Release.engine_files.PSObject.Properties[$Name].Value
        if ([string]$Record.sha256 -notmatch '^[a-f0-9]{64}$' -or
            ($Record.bytes -isnot [int] -and
                $Record.bytes -isnot [long]) -or
            [long]$Record.bytes -lt 1 -or
            [long]$Record.bytes -gt 16777216) {
            throw "Foundation engine file record is invalid: $Name"
        }
    }
    if ([int]$Evidence.schema_version -ne 1 -or
        [string]$Evidence.engine_version -cne '0.2.1' -or
        [string]$Evidence.installer_version -cne '0.3.0' -or
        [string]$Evidence.FOUNDATION_SYNTHETIC -cne 'PASS' -or
        [string]$Evidence.deterministic_engine_bundle -cne 'PASS' -or
        [string]$Evidence.evidence_body_sha256 -notmatch (
            '^[a-f0-9]{64}$'
        )) {
        throw 'Foundation synthetic acceptance evidence is not PASS'
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedRepository) -or
        [int]$Verification.schema_version -ne 1 -or
        [string]$Verification.repository -cne $ExpectedRepository -or
        [string]$Verification.tag -cne [string]$Release.tag -or
        $Verification.release_state.draft -isnot [bool] -or
        [bool]$Verification.release_state.draft -ne $false -or
        $Verification.release_state.prerelease -isnot [bool] -or
        [bool]$Verification.release_state.prerelease -ne $false -or
        $Verification.release_state.immutable -isnot [bool] -or
        [bool]$Verification.release_state.immutable -ne $true -or
        [string]$Verification.release_attestation -cne 'PASS' -or
        [string]$Verification.RELEASE_INTEGRITY -cne 'PASS' -or
        [string]$Verification.evidence_body_sha256 -notmatch (
            '^[a-f0-9]{64}$'
        ) -or
        $VerifiedAssets.Count -ne 1 -or
        [string]$VerifiedAssets[0].name -cne (
            [string]$Release.asset.name
        ) -or
        [string]$VerifiedAssets[0].sha256 -cne (
            [string]$Release.asset.sha256
        ) -or
        [long]$VerifiedAssets[0].bytes -ne (
            [long]$Release.asset.bytes
        ) -or
        [string]$VerifiedAssets[0].attestation -cne 'PASS') {
        throw 'Foundation release verification is not PASS'
    }
    return [ordered]@{
        engine_version = '0.2.1'
        engine_files = $Release.engine_files
        asset_path = $AssetPath
        asset = [ordered]@{
            relative_path = 'foundation/' + (
                [string]$Acceptance.asset.name
            )
            sha256 = [string]$Acceptance.asset.sha256
            bytes = [long]$Acceptance.asset.bytes
        }
        release_manifest = [ordered]@{
            relative_path = 'foundation/' + (
                [string]$Acceptance.release_manifest.name
            )
            sha256 = [string]$Acceptance.release_manifest.sha256
            bytes = [long]$Acceptance.release_manifest.bytes
        }
        acceptance_evidence = [ordered]@{
            relative_path = 'foundation/' + (
                [string]$Acceptance.acceptance_evidence.name
            )
            sha256 = [string]$Acceptance.acceptance_evidence.sha256
            bytes = [long]$Acceptance.acceptance_evidence.bytes
        }
        release_verification = [ordered]@{
            relative_path = 'foundation/' + (
                [string]$Acceptance.release_verification.name
            )
            sha256 = [string]$Acceptance.release_verification.sha256
            bytes = [long]$Acceptance.release_verification.bytes
        }
        package_acceptance = [ordered]@{
            relative_path = 'foundation/package-acceptance.json'
            sha256 = Get-Sha256 $AcceptancePath
            bytes = (Get-Item -LiteralPath $AcceptancePath).Length
        }
        source_directory = $Directory.FullName
    }
}

function Read-AcceptedPackages {
    param([string]$Root)
    $Definitions = [ordered]@{
        codex = [ordered]@{
            client = 'codex-cli'
            verdict = 'FULL_RELEASE_CODEX'
        }
        claude = [ordered]@{
            client = 'claude-code'
            verdict = 'FULL_RELEASE_CLAUDE'
        }
        opencode = [ordered]@{
            client = 'opencode'
            verdict = 'FULL_RELEASE_OPENCODE'
        }
    }
    $Rows = @()
    if ([string]::IsNullOrWhiteSpace($Root)) {
        return $Rows
    }
    foreach ($Directory in @(
        Get-ChildItem -LiteralPath $Root -Directory | Sort-Object Name
    )) {
        $Target = $Directory.Name
        if ($Target -ceq 'foundation') {
            continue
        }
        if (-not $Definitions.Contains($Target)) {
            throw "Package acceptance has unknown target: $Target"
        }
        $AcceptancePath = Join-Path $Directory.FullName 'package-acceptance.json'
        if (-not (Test-Path -LiteralPath $AcceptancePath -PathType Leaf)) {
            throw "Package acceptance is missing for target: $Target"
        }
        try {
            $Acceptance = Get-Content -LiteralPath $AcceptancePath -Raw |
                ConvertFrom-Json
        } catch {
            throw "Package acceptance JSON is invalid for target: $Target"
        }
        if ([int]$Acceptance.schema_version -ne 1 -or
            [string]$Acceptance.target -cne $Target -or
            [string]$Acceptance.package_acceptance -cne 'PASS' -or
            [bool]$Acceptance.immutable_release -ne $true -or
            [bool]$Acceptance.release_attestation -ne $true) {
            throw "Package acceptance contract is not PASS for target: $Target"
        }
        $Definition = $Definitions[$Target]
        if ([string]$Acceptance.client.id -cne [string]$Definition.client -or
            [string]::IsNullOrWhiteSpace(
                [string]$Acceptance.client.supported_version
            )) {
            throw "Package acceptance client contract is invalid for target: $Target"
        }

        $AssetPath = Assert-FileBinding $Directory.FullName `
            $Acceptance.asset 'asset'
        $ReleasePath = Assert-FileBinding $Directory.FullName `
            $Acceptance.release_manifest 'release manifest'
        $EvidencePath = Assert-FileBinding $Directory.FullName `
            $Acceptance.acceptance_evidence 'acceptance evidence'
        $VerificationPath = Assert-FileBinding $Directory.FullName `
            $Acceptance.release_verification 'release verification'

        try {
            $Release = Get-Content -LiteralPath $ReleasePath -Raw |
                ConvertFrom-Json
            $Evidence = Get-Content -LiteralPath $EvidencePath -Raw |
                ConvertFrom-Json
            $Verification = Get-Content -LiteralPath $VerificationPath -Raw |
                ConvertFrom-Json
        } catch {
            throw "Package acceptance evidence JSON is invalid for target: $Target"
        }
        if ([int]$Release.schema_version -ne 1 -or
            [string]$Release.target -cne $Target -or
            [string]$Release.tag -cne (
                $Target + '-v' + [string]$Release.version
            ) -or
            [string]$Release.channel -cne 'stable' -or
            [string]$Release.client.id -cne (
                [string]$Acceptance.client.id
            ) -or
            [string]$Release.client.supported_version -cne (
                [string]$Acceptance.client.supported_version
            ) -or
            [string]$Release.asset.name -cne [string]$Acceptance.asset.name -or
            [string]$Release.asset.sha256 -cne [string]$Acceptance.asset.sha256 -or
            [long]$Release.asset.bytes -ne [long]$Acceptance.asset.bytes -or
            [bool]$Release.requires.immutable_release -ne $true -or
            [bool]$Release.requires.release_attestation -ne $true) {
            throw "Package acceptance release manifest is invalid for target: $Target"
        }
        $ExpectedRepository = (
            [string]$Release.source.repository
        ) -replace '^https://github\.com/', ''
        $VerifiedAssets = @($Verification.assets)
        if ([string]::IsNullOrWhiteSpace($ExpectedRepository) -or
            [int]$Verification.schema_version -ne 1 -or
            [string]$Verification.repository -cne $ExpectedRepository -or
            [string]$Verification.tag -cne [string]$Release.tag -or
            $Verification.release_state.draft -isnot [bool] -or
            [bool]$Verification.release_state.draft -ne $false -or
            $Verification.release_state.prerelease -isnot [bool] -or
            [bool]$Verification.release_state.prerelease -ne $false -or
            $Verification.release_state.immutable -isnot [bool] -or
            [bool]$Verification.release_state.immutable -ne $true -or
            [string]$Verification.release_attestation -cne 'PASS' -or
            [string]$Verification.RELEASE_INTEGRITY -cne 'PASS' -or
            [string]$Verification.evidence_body_sha256 -notmatch (
                '^[a-f0-9]{64}$'
            ) -or
            $VerifiedAssets.Count -ne 1 -or
            [string]$VerifiedAssets[0].name -cne (
                [string]$Release.asset.name
            ) -or
            [string]$VerifiedAssets[0].sha256 -cne (
                [string]$Release.asset.sha256
            ) -or
            [long]$VerifiedAssets[0].bytes -ne (
                [long]$Release.asset.bytes
            ) -or
            [string]$VerifiedAssets[0].attestation -cne 'PASS') {
            throw "Package release verification is not PASS for target: $Target"
        }
        Assert-ReleaseBinding $Evidence $Release
        if ($Target -ceq 'codex') {
            $VerdictProperty = $Evidence.PSObject.Properties[
                [string]$Definition.verdict
            ]
            $IntegrityProperty = $Evidence.PSObject.Properties[
                'RELEASE_INTEGRITY'
            ]
            $EvidenceBindingValid = (
                [string]$Release.acceptance_evidence_sha256 -ceq (
                    Get-Sha256 $EvidencePath
                )
            )
        }
        else {
            $VerdictProperty = $Evidence.verdicts.PSObject.Properties[
                [string]$Definition.verdict
            ]
            $IntegrityProperty = $Evidence.verdicts.PSObject.Properties[
                'RELEASE_INTEGRITY'
            ]
            $EvidenceBindingValid = (
                [string]$Release.acceptance_evidence_sha256 -ceq (
                    Get-Sha256 $EvidencePath
                ) -and
                [string]$Evidence.asset_sha256 -ceq (
                    [string]$Acceptance.asset.sha256
                )
            )
        }
        if ([int]$Evidence.schema_version -ne 1 -or
            [string]$Evidence.target -cne $Target -or
            [string]$Evidence.version -cne [string]$Release.version -or
            [string]$Evidence.evidence_body_sha256 -notmatch (
                '^[a-f0-9]{64}$'
            ) -or
            $null -eq $VerdictProperty -or
            [string]$VerdictProperty.Value -cne 'PASS' -or
            $null -eq $IntegrityProperty -or
            [string]$IntegrityProperty.Value -cne 'PENDING_PUBLICATION' -or
            -not $EvidenceBindingValid) {
            throw "Package acceptance evidence is not PASS for target: $Target"
        }

        $AcceptanceHash = Get-Sha256 $AcceptancePath
        $Rows += [ordered]@{
            target = $Target
            client_id = [string]$Acceptance.client.id
            supported_version = [string]$Acceptance.client.supported_version
            foundation_engine_manifest_sha256 = [string](
                $Release.foundation_engine_manifest_sha256
            )
            asset = [ordered]@{
                relative_path = "packages/$Target/$(
                    [string]$Acceptance.asset.name
                )"
                resource_name = "TargetPackage.$Target.asset"
                sha256 = [string]$Acceptance.asset.sha256
                bytes = [long]$Acceptance.asset.bytes
            }
            release_manifest = [ordered]@{
                relative_path = "packages/$Target/$(
                    [string]$Acceptance.release_manifest.name
                )"
                resource_name = "TargetPackage.$Target.release_manifest"
                sha256 = [string]$Acceptance.release_manifest.sha256
                bytes = (Get-Item -LiteralPath $ReleasePath).Length
            }
            acceptance_evidence = [ordered]@{
                relative_path = "packages/$Target/$(
                    [string]$Acceptance.acceptance_evidence.name
                )"
                resource_name = "TargetPackage.$Target.acceptance_evidence"
                sha256 = [string]$Acceptance.acceptance_evidence.sha256
                bytes = (Get-Item -LiteralPath $EvidencePath).Length
            }
            release_verification = [ordered]@{
                relative_path = "packages/$Target/$(
                    [string]$Acceptance.release_verification.name
                )"
                resource_name = "TargetPackage.$Target.release_verification"
                sha256 = [string]$Acceptance.release_verification.sha256
                bytes = (Get-Item -LiteralPath $VerificationPath).Length
            }
            package_acceptance = [ordered]@{
                relative_path = "packages/$Target/package-acceptance.json"
                resource_name = "TargetPackage.$Target.package_acceptance"
                sha256 = $AcceptanceHash
                bytes = (Get-Item -LiteralPath $AcceptancePath).Length
            }
            source_directory = $Directory.FullName
        }
    }
    return $Rows
}

function Export-AcceptedFoundationEngine {
    param(
        [Parameter(Mandatory = $true)]$Foundation,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if (Test-Path -LiteralPath $Destination) {
        throw 'Foundation engine destination must not exist'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    $Archive = [IO.Compression.ZipFile]::OpenRead(
        [string]$Foundation.asset_path
    )
    try {
        $Entries = @($Archive.Entries)
        $Names = @($Entries.FullName | Sort-Object)
        if ($Entries.Count -ne 3 -or
            ($Names -join ',') -cne (
                'engine-manifest.json,foundation.ps1,VERSION'
            )) {
            throw 'Foundation engine archive inventory differs'
        }
        foreach ($Entry in $Entries) {
            $Record = $Foundation.engine_files.PSObject.Properties[
                $Entry.FullName
            ].Value
            if ($null -eq $Record -or
                [long]$Entry.Length -ne [long]$Record.bytes -or
                [long]$Entry.Length -gt 16777216) {
                throw "Foundation engine archive entry differs: $(
                    $Entry.FullName
                )"
            }
            $DestinationPath = Join-Path $Destination $Entry.FullName
            $InputStream = $Entry.Open()
            $OutputStream = [IO.File]::Open(
                $DestinationPath,
                [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write,
                [IO.FileShare]::None
            )
            try {
                $InputStream.CopyTo($OutputStream)
            } finally {
                $OutputStream.Dispose()
                $InputStream.Dispose()
            }
            if ((Get-Sha256 $DestinationPath) -cne (
                    [string]$Record.sha256
                ) -or
                (Get-Item -LiteralPath $DestinationPath).Length -ne (
                    [long]$Record.bytes
                )) {
                throw "Foundation engine extracted bytes differ: $(
                    $Entry.FullName
                )"
            }
        }
    } finally {
        $Archive.Dispose()
    }
    if (([IO.File]::ReadAllText(
            (Join-Path $Destination 'VERSION')
        )).Trim() -cne '0.2.1') {
        throw 'Foundation engine extracted version differs'
    }
    try {
        $EngineManifest = Get-Content -LiteralPath (
            Join-Path $Destination 'engine-manifest.json'
        ) -Raw | ConvertFrom-Json
    } catch {
        throw 'Foundation engine extracted manifest is invalid'
    }
    if ([int]$EngineManifest.schema_version -ne 1 -or
        [int]$EngineManifest.protocol_version -ne 1 -or
        [string]$EngineManifest.engine_version -cne '0.2.1' -or
        [string]$EngineManifest.network -cne 'offline' -or
        (@($EngineManifest.commands) -join ',') -cne (
            'doctor,install,inventory,plan,rollback'
        ) -or
        (@($EngineManifest.supported_powershell) -join ',') -cne '5.1,7' -or
        [string]$EngineManifest.foundation_ps1_sha256 -cne (
            Get-Sha256 (Join-Path $Destination 'foundation.ps1')
        )) {
        throw 'Foundation engine extracted contract differs'
    }
}

$Version = ([IO.File]::ReadAllText(
    (Join-Path $RepositoryRoot 'APP_VERSION')
)).Trim()
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw 'Installer application version is invalid'
}

$WindowsRoot = [Environment]::GetFolderPath('Windows')
$CompilerCandidates = New-Object System.Collections.Generic.List[string]
$VsWhere = Join-Path ${env:ProgramFiles(x86)} (
    'Microsoft Visual Studio\Installer\vswhere.exe'
)
if (Test-Path -LiteralPath $VsWhere -PathType Leaf) {
    $VsInstallations = @(
        & $VsWhere -products '*' -requires Microsoft.Component.MSBuild `
            -property installationPath
    )
    foreach ($VsInstallation in $VsInstallations) {
        if (-not [string]::IsNullOrWhiteSpace($VsInstallation)) {
            $CompilerCandidates.Add(
                (Join-Path $VsInstallation (
                    'MSBuild\Current\Bin\Roslyn\csc.exe'
                ))
            )
        }
    }
}
foreach ($VisualStudioRoot in @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio'),
    (Join-Path $env:ProgramFiles 'Microsoft Visual Studio')
)) {
    if (Test-Path -LiteralPath $VisualStudioRoot -PathType Container) {
        @(
            Get-ChildItem -Path (
                Join-Path $VisualStudioRoot (
                    '*\*\MSBuild\Current\Bin\Roslyn\csc.exe'
                )
            ) -File -ErrorAction SilentlyContinue |
                Sort-Object -Property FullName -Descending
        ) | ForEach-Object {
            $CompilerCandidates.Add($_.FullName)
        }
    }
}
$Compiler = @(
    $CompilerCandidates |
        Where-Object {
            Test-Path -LiteralPath $_ -PathType Leaf
        } |
        Select-Object -Unique
) | Select-Object -First 1
if ([string]::IsNullOrWhiteSpace($Compiler)) {
    throw (
        'A Roslyn C# compiler is required for a deterministic GUI build. ' +
        'Install Microsoft Visual Studio Build Tools with MSBuild.'
    )
}

$AssemblyRoots = @(
    (Join-Path $WindowsRoot 'Microsoft.NET\assembly\GAC_MSIL'),
    (Join-Path $WindowsRoot 'Microsoft.NET\assembly\GAC_64')
)
$References = @(
    (Get-AssemblyPath $AssemblyRoots 'PresentationFramework.dll'),
    (Get-AssemblyPath $AssemblyRoots 'PresentationCore.dll'),
    (Get-AssemblyPath $AssemblyRoots 'WindowsBase.dll'),
    (Get-AssemblyPath $AssemblyRoots 'System.Xaml.dll'),
    (Get-AssemblyPath $AssemblyRoots 'System.IO.Compression.dll'),
    (Get-AssemblyPath $AssemblyRoots 'System.IO.Compression.FileSystem.dll')
)

$AcceptedPackages = @(Read-AcceptedPackages $PackageRoot)
$AcceptedFoundation = Read-AcceptedFoundation $FoundationPackageRoot
$ProviderEligibility = Read-ProviderEligibilityEvidence `
    $ProviderEligibilityEvidence
$AcceptedTargets = @($AcceptedPackages.target | Sort-Object)
$IncludedTargets = if ($Edition -ceq 'Employee') {
    @('codex', 'opencode')
}
else {
    @('claude', 'codex', 'opencode')
}
$RequiredTargets = @('codex', 'opencode')
$IsPackagedRelease = $DistributionMode -cne 'Preview'
$IsPublicSigned = $DistributionMode -ceq 'PublicSigned'
$ClientSources = $null
try {
    $ClientSources = [IO.File]::ReadAllText(
        $ClientSourcesLock
    ) | ConvertFrom-Json
} catch {
    throw 'ClientSourcesLock JSON is invalid'
}
if ($null -eq $ClientSources -or
    ($ClientSources.schema_version -isnot [int] -and
        $ClientSources.schema_version -isnot [long]) -or
    [int]$ClientSources.schema_version -ne 1 -or
    $ClientSources.official_only -isnot [bool] -or
    $ClientSources.test_only -isnot [bool] -or
    $null -eq $ClientSources.platform -or
    [string]$ClientSources.platform.os -cne 'windows' -or
    [string]$ClientSources.platform.architecture -cne 'x64' -or
    ($ClientSources.platform.minimum_build -isnot [int] -and
        $ClientSources.platform.minimum_build -isnot [long]) -or
    [int]$ClientSources.platform.minimum_build -lt 19041 -or
    @($ClientSources.clients).Count -lt 1) {
    throw 'ClientSourcesLock schema is invalid'
}
$ClientSourcesOfficialOnly = [bool]$ClientSources.official_only
$ClientSourcesTestOnly = [bool]$ClientSources.test_only
if ($ClientSourcesTestOnly) {
    if (-not $AllowLocalTestSources) {
        throw 'Local test client sources require AllowLocalTestSources'
    }
    if ($DistributionMode -cne 'Preview') {
        throw 'Local test client sources are only allowed for Preview'
    }
    if ($ClientSourcesOfficialOnly) {
        throw 'Local test client sources cannot be official-only'
    }
}
elseif (-not $ClientSourcesOfficialOnly) {
    throw 'Non-official client sources must be marked test-only'
}
$ApprovedHosts = @(
    'chatgpt.com',
    'downloads.claude.ai',
    'github.com',
    'openai.com',
    'apps.microsoft.com'
)
$SeenClientIds = @{}
foreach ($ClientSource in @($ClientSources.clients)) {
    $ClientId = [string]$ClientSource.id
    $Uri = $null
    if ([string]::IsNullOrWhiteSpace($ClientId) -or
        $SeenClientIds.ContainsKey($ClientId) -or
        -not [Uri]::TryCreate(
            [string]$ClientSource.url,
            [UriKind]::Absolute,
            [ref]$Uri
        ) -or
        -not [string]::IsNullOrWhiteSpace($Uri.UserInfo)) {
        throw 'ClientSourcesLock contains an invalid client entry'
    }
    $SeenClientIds[$ClientId] = $true
    if ($ClientSourcesTestOnly) {
        if (-not $Uri.IsLoopback -or
            $Uri.Scheme -notin @('http', 'https')) {
            throw 'Local test client source is unsafe'
        }
    }
    elseif ($Uri.Scheme -cne 'https' -or
        $ApprovedHosts -cnotcontains $Uri.Host) {
        throw 'Client source URL is not an approved official endpoint'
    }
    if ([string]$ClientSource.source_kind -ceq 'download') {
        $Hash = [string]$ClientSource.sha256
        if ($Hash -notmatch '^[0-9A-Fa-f]{64}$') {
            throw 'Client source hash is invalid'
        }
    }
    elseif ($ClientSourcesOfficialOnly -and
        $ClientId -ceq 'codex-desktop' -and (
            [string]$ClientSource.store_product_id -cne '9PLM9XGG6VKS' -or
            [string]$ClientSource.store_identity -cne 'OpenAI.Codex' -or
            [string]$ClientSource.store_publisher -cne (
                'CN=50BDFD77-8903-4850-9FFE-6E8522F64D5B'
            ) -or
            [string]$ClientSource.store_signature_kind -cne 'Store'
        )) {
        throw 'Codex Store client identity is invalid'
    }
}
if ($ClientSourcesOfficialOnly) {
    $ExpectedClients = @(
        'claude-code',
        'codex-cli',
        'codex-desktop',
        'opencode-cli',
        'opencode-desktop'
    )
    $ActualClients = @($SeenClientIds.Keys | Sort-Object)
    if (($ActualClients -join ',') -cne ($ExpectedClients -join ',')) {
        throw 'Official client source inventory is incomplete'
    }
}
if ($Edition -ceq 'Employee' -and
    $null -ne $ProviderEligibility) {
    throw 'Employee edition cannot include provider eligibility evidence'
}
if ($Edition -ceq 'Employee' -and
    @($AcceptedTargets | Where-Object {
        $IncludedTargets -cnotcontains $_
    }).Count -ne 0) {
    throw 'Employee target set differs from the edition contract'
}
if ($IsPackagedRelease) {
    if (($AcceptedTargets -join ',') -cne ($IncludedTargets -join ',')) {
        throw "$Edition target set differs from the edition contract"
    }
    if ($null -eq $AcceptedFoundation) {
        throw (
            "$Edition release requires an accepted immutable Foundation " +
            'package'
        )
    }
    $FoundationManifestRecord = (
        $AcceptedFoundation.engine_files.PSObject.Properties[
            'engine-manifest.json'
        ].Value
    )
    foreach ($Package in $AcceptedPackages) {
        if ([string]$Package.foundation_engine_manifest_sha256 -cne (
                [string]$FoundationManifestRecord.sha256
            )) {
            throw (
                'Target package Foundation binding differs: ' +
                [string]$Package.target
            )
        }
    }
    if ($IsPublicSigned -and
        [string]::IsNullOrWhiteSpace($SigningCertificateThumbprint)) {
        throw "$Edition release requires a code-signing certificate"
    }
}
if (-not $IsPublicSigned -and
    -not [string]::IsNullOrWhiteSpace($SigningCertificateThumbprint)) {
    throw (
        'SigningCertificateThumbprint is only valid for PublicSigned builds'
    )
}
[IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
$EditionContract = if ($Edition -ceq 'Employee') {
    [ordered]@{
        edition_id = 'Employee'
        display_name = 'K-7 AI Foundation Employee'
        distribution_allowed = $true
        included_target_ids = @('codex', 'opencode')
        required_target_ids = @('codex', 'opencode')
        theme_id = 'K7Signal'
        owner_controlled = $false
        product_role = $ProductRole
    }
}
else {
    [ordered]@{
        edition_id = 'Owner'
        display_name = 'K-7 AI Foundation Owner'
        distribution_allowed = $false
        included_target_ids = @('claude', 'codex', 'opencode')
        required_target_ids = @('codex', 'opencode')
        theme_id = 'SignalConsole'
        owner_controlled = $true
        product_role = $ProductRole
    }
}
$EditionResource = Join-Path $OutputRoot '.edition-profile.json'
[IO.File]::WriteAllText(
    $EditionResource,
    ((ConvertTo-Json $EditionContract -Depth 4 -Compress) + "`n"),
    $Utf8NoBom
)
$EffectiveClientSourcesPath = Join-Path (
    $OutputRoot
) 'client-sources.lock.json'
if ($Edition -ceq 'Owner' -or $ClientSourcesTestOnly) {
    Copy-Item -LiteralPath $ClientSourcesLock -Destination (
        $EffectiveClientSourcesPath
    )
}
else {
    $EffectiveClientSources = [ordered]@{
        schema_version = [int]$ClientSources.schema_version
        official_only = [bool]$ClientSources.official_only
        test_only = [bool]$ClientSources.test_only
        platform = $ClientSources.platform
        clients = @(
            $ClientSources.clients | Where-Object {
                $IncludedTargets -ccontains [string]$_.target
            }
        )
    }
    if (@($EffectiveClientSources.clients).Count -lt 1) {
        throw 'Edition client source inventory is empty'
    }
    [IO.File]::WriteAllText(
        $EffectiveClientSourcesPath,
        ((ConvertTo-Json $EffectiveClientSources -Depth 8) + "`n"),
        $Utf8NoBom
    )
}
$EngineRoot = Join-Path $OutputRoot 'engine'
if ($IsPackagedRelease) {
    Export-AcceptedFoundationEngine $AcceptedFoundation $EngineRoot
}
else {
    & (Join-Path $RepositoryRoot 'tools\build-engine.ps1') `
        -OutputRoot $EngineRoot
    if (-not $?) {
        throw 'Foundation engine build failed'
    }
}

$TrustedProviderEligibility = [ordered]@{
    status = 'NOT_PROVIDED'
}
if ($null -ne $ProviderEligibility) {
    $TrustedProviderEligibility = [ordered]@{
        status = 'PASS'
        reviewed_at_utc = [string]$ProviderEligibility.reviewed_at_utc
        expires_at_utc = [string]$ProviderEligibility.expires_at_utc
        evidence = [ordered]@{
            relative_path = 'provider-eligibility-evidence.json'
            resource_name = 'ProviderEligibilityEvidence.json'
            sha256 = [string]$ProviderEligibility.sha256
            bytes = [long]$ProviderEligibility.bytes
        }
    }
}
$TrustedIndex = [ordered]@{
    schema_version = 1
    provider_eligibility = $TrustedProviderEligibility
    packages = @(
        $AcceptedPackages | ForEach-Object {
            [ordered]@{
                target = $_.target
                client_id = $_.client_id
                supported_version = $_.supported_version
                asset = $_.asset
                release_manifest = $_.release_manifest
                acceptance_evidence = $_.acceptance_evidence
                release_verification = $_.release_verification
                package_acceptance = $_.package_acceptance
            }
        }
    )
}
$Encoding = New-Object Text.UTF8Encoding($false)
$TrustedResource = Join-Path $OutputRoot '.trusted-packages.json'
[IO.File]::WriteAllText(
    $TrustedResource,
    ((ConvertTo-Json $TrustedIndex -Depth 8 -Compress) + "`n"),
    $Encoding
)

$Executable = Join-Path $OutputRoot 'LLMFoundationInstaller.exe'
$Source = Join-Path $RepositoryRoot 'src\gui\InstallerApp.cs'
$EditionSource = Join-Path $RepositoryRoot 'src\gui\EditionProfile.cs'
$ConnectionSource = Join-Path $RepositoryRoot 'src\gui\ConnectionProfile.cs'
$ClientBootstrapSource = Join-Path (
    $RepositoryRoot
) 'src\gui\ClientBootstrap.cs'
$ClientSourcesBytes = (
    Get-Item -LiteralPath $EffectiveClientSourcesPath
).Length
$ClientSourcesHash = Get-Sha256 $EffectiveClientSourcesPath
$View = Join-Path $RepositoryRoot 'src\gui\InstallerView.xaml'
$ApplicationManifest = Join-Path $RepositoryRoot 'src\gui\app.manifest'
$ApplicationIcon = Join-Path $OutputRoot '.installer.ico'
& (Join-Path $RepositoryRoot 'tools\build-icon.ps1') `
    -OutputPath $ApplicationIcon | Out-Null
$CompilerArguments = @(
    '/nologo',
    '/target:winexe',
    '/platform:anycpu',
    '/optimize+',
    '/checked+',
    '/deterministic+',
    '/utf8output',
    "/out:$Executable",
    "/win32manifest:$ApplicationManifest",
    "/win32icon:$ApplicationIcon",
    "/resource:$View,InstallerView.xaml",
    "/resource:$EditionResource,EditionProfile.json",
    "/resource:$TrustedResource,TrustedPackages.json",
    "/resource:$EffectiveClientSourcesPath,ClientSources.lock.json",
    "/resource:$(Join-Path $EngineRoot 'foundation.ps1'),FoundationEngine.foundation.ps1",
    "/resource:$(Join-Path $EngineRoot 'engine-manifest.json'),FoundationEngine.engine-manifest.json",
    "/resource:$(Join-Path $EngineRoot 'VERSION'),FoundationEngine.VERSION",
    "/resource:$(Join-Path $RepositoryRoot 'APP_VERSION'),FoundationInstaller.VERSION"
)
$CompilerArguments += @(
    foreach ($Package in $AcceptedPackages) {
        foreach ($Record in @(
            $Package.asset,
            $Package.release_manifest,
            $Package.acceptance_evidence,
            $Package.release_verification,
            $Package.package_acceptance
        )) {
            $Name = [IO.Path]::GetFileName([string]$Record.relative_path)
            $SourcePath = Join-Path $Package.source_directory $Name
            "/resource:$SourcePath,$($Record.resource_name)"
        }
    }
)
if ($null -ne $ProviderEligibility) {
    $CompilerArguments += (
        "/resource:$($ProviderEligibility.path)," +
        'ProviderEligibilityEvidence.json'
    )
}
$CompilerArguments += $References | ForEach-Object { "/reference:$_" }
$CompilerArguments += @(
    $Source,
    $EditionSource,
    $ConnectionSource,
    $ClientBootstrapSource
)

& $Compiler @CompilerArguments
if ($LASTEXITCODE -ne 0 -or
    -not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw 'GUI compilation failed'
}

Remove-Item -LiteralPath @(
    $TrustedResource,
    $EditionResource,
    $ApplicationIcon
) -Force

$SignatureState = if ($DistributionMode -ceq 'InternalUnsigned') {
    'unsigned-internal'
} else {
    'unsigned-preview'
}
if ($IsPublicSigned) {
    $NormalizedThumbprint = (
        $SigningCertificateThumbprint -replace '\s', ''
    ).ToUpperInvariant()
    $Certificate = Get-ChildItem -LiteralPath Cert:\CurrentUser\My |
        Where-Object {
            $_.Thumbprint.ToUpperInvariant() -ceq $NormalizedThumbprint -and
            $_.HasPrivateKey -and
            @($_.EnhancedKeyUsageList.ObjectId.Value) -contains (
                '1.3.6.1.5.5.7.3.3'
            )
        } |
        Select-Object -First 1
    if ($null -eq $Certificate) {
        throw 'Requested current-user code-signing certificate is unavailable'
    }
    if ([string]::IsNullOrWhiteSpace($TimestampServer)) {
        throw 'A timestamp server is required for signed builds'
    }
    $Signature = Set-AuthenticodeSignature `
        -FilePath $Executable `
        -Certificate $Certificate `
        -HashAlgorithm SHA256 `
        -TimestampServer $TimestampServer
    if ([string]$Signature.Status -cne 'Valid') {
        throw "Authenticode signing failed: $($Signature.StatusMessage)"
    }
    $SignatureState = 'valid-authenticode'
}

foreach ($Package in $AcceptedPackages) {
    $Destination = Join-Path $OutputRoot "packages\$($Package.target)"
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    foreach ($Record in @(
        $Package.asset,
        $Package.release_manifest,
        $Package.acceptance_evidence,
        $Package.release_verification,
        $Package.package_acceptance
    )) {
        $Name = [IO.Path]::GetFileName([string]$Record.relative_path)
        Copy-Item -LiteralPath (
            Join-Path $Package.source_directory $Name
        ) -Destination (Join-Path $Destination $Name)
    }
}
if ($null -ne $AcceptedFoundation) {
    $FoundationDestination = Join-Path $OutputRoot 'foundation'
    [IO.Directory]::CreateDirectory($FoundationDestination) | Out-Null
    foreach ($Record in @(
        $AcceptedFoundation.asset,
        $AcceptedFoundation.release_manifest,
        $AcceptedFoundation.acceptance_evidence,
        $AcceptedFoundation.release_verification,
        $AcceptedFoundation.package_acceptance
    )) {
        $Name = [IO.Path]::GetFileName([string]$Record.relative_path)
        Copy-Item -LiteralPath (
            Join-Path $AcceptedFoundation.source_directory $Name
        ) -Destination (Join-Path $FoundationDestination $Name)
    }
}
$ProviderEligibilityManifest = [ordered]@{
    status = 'NOT_PROVIDED'
}
if ($null -ne $ProviderEligibility) {
    Copy-Item -LiteralPath $ProviderEligibility.path -Destination (
        Join-Path $OutputRoot 'provider-eligibility-evidence.json'
    )
    $ProviderEligibilityManifest = [ordered]@{
        status = [string]$ProviderEligibility.status
        sha256 = [string]$ProviderEligibility.sha256
        reviewed_at_utc = [string]$ProviderEligibility.reviewed_at_utc
        expires_at_utc = [string]$ProviderEligibility.expires_at_utc
        contains_personal_data = $false
    }
}
$FoundationReleaseManifest = if ($IsPackagedRelease) {
    [ordered]@{
        package_acceptance = 'PASS'
        engine_version = [string]$AcceptedFoundation.engine_version
        asset = $AcceptedFoundation.asset
        release_manifest = $AcceptedFoundation.release_manifest
        acceptance_evidence = $AcceptedFoundation.acceptance_evidence
        release_verification = $AcceptedFoundation.release_verification
        package_acceptance_record = (
            $AcceptedFoundation.package_acceptance
        )
    }
}
else {
    [ordered]@{
        package_acceptance = 'LOCAL_PREVIEW'
        engine_version = '0.2.1'
    }
}

[IO.File]::WriteAllText(
    (Join-Path $OutputRoot 'VERSION'),
    $Version + "`n",
    $Encoding
)

$ProviderReady = (
    $null -ne $ProviderEligibility -and
    [string]$ProviderEligibility.status -ceq 'PASS'
)
$RequiredReady = @(
    $RequiredTargets | Where-Object {
        $AcceptedTargets -cnotcontains $_
    }
).Count -eq 0
$EditionTargetSetReady = (
    ($AcceptedTargets -join ',') -ceq ($IncludedTargets -join ',')
)
$EmployeeInternalReady = (
    $Edition -ceq 'Employee' -and
    $DistributionMode -ceq 'InternalUnsigned' -and
    $EditionTargetSetReady -and
    $null -ne $AcceptedFoundation
)
$PublicSignedReady = (
    $Edition -ceq 'Employee' -and
    $DistributionMode -ceq 'PublicSigned' -and
    $SignatureState -ceq 'valid-authenticode' -and
    $RequiredReady
)
$Verdicts = if ($Edition -ceq 'Employee') {
    [ordered]@{
        FULL_RELEASE_CODEX = if (
            $AcceptedTargets -ccontains 'codex'
        ) { 'PASS' } else { 'NOT_PASS' }
        FULL_RELEASE_OPENCODE = if (
            $AcceptedTargets -ccontains 'opencode'
        ) { 'PASS' } else { 'NOT_PASS' }
        PROGRAM_RELEASE = if ($RequiredReady) {
            '2/2'
        } else {
            "$(@(
                $RequiredTargets | Where-Object {
                    $AcceptedTargets -ccontains $_
                }
            ).Count)/2"
        }
        EMPLOYEE_INSTALLER_INTERNAL = if ($EmployeeInternalReady) {
            'PASS'
        } else {
            'NOT_PASS'
        }
        PUBLIC_SIGNED_RELEASE = if (
            $DistributionMode -ceq 'InternalUnsigned'
        ) {
            'DEFERRED_BY_OWNER'
        } elseif ($PublicSignedReady) {
            'PASS'
        } else {
            'NOT_PASS'
        }
    }
}
else {
    [ordered]@{
        FULL_RELEASE_CODEX = if (
            $AcceptedTargets -ccontains 'codex'
        ) { 'PASS' } else { 'NOT_PASS' }
        FULL_RELEASE_CLAUDE = if (
            $AcceptedTargets -ccontains 'claude' -and
            $ProviderReady
        ) { 'PASS' } else { 'NOT_PASS' }
        FULL_RELEASE_OPENCODE = if (
            $AcceptedTargets -ccontains 'opencode'
        ) { 'PASS' } else { 'NOT_PASS' }
        PROGRAM_RELEASE = if (
            $EditionTargetSetReady -and $ProviderReady
        ) {
            '3/3'
        } elseif ($RequiredReady) {
            '2/3'
        } else {
            "$(@(
                $RequiredTargets | Where-Object {
                    $AcceptedTargets -ccontains $_
                }
            ).Count)/3"
        }
        OWNER_INSTALLER_INTERNAL = if (
            $DistributionMode -ceq 'InternalUnsigned' -and
            $EditionTargetSetReady -and
            $null -ne $AcceptedFoundation
        ) { 'OWNER_CANDIDATE' } else { 'NOT_PASS' }
        PUBLIC_SIGNED_RELEASE = 'NOT_APPLICABLE'
    }
}
$Manifest = [ordered]@{
    schema_version = 1
    app_id = 'llm-foundation-installer'
    edition_id = $Edition
    product_role = $ProductRole
    theme_id = [string]$EditionContract.theme_id
    owner_controlled = [bool]$EditionContract.owner_controlled
    distribution_allowed = [bool]$EditionContract.distribution_allowed
    version = $Version
    network = 'user-initiated-only'
    automatic_network = $false
    telemetry = $false
    reverse_flow = $false
    distribution = 'single-executable'
    distribution_mode = switch ($DistributionMode) {
        'Preview' { 'preview' }
        'InternalUnsigned' { 'internal_unsigned' }
        'PublicSigned' { 'public_signed' }
    }
    embedded_foundation = $true
    embedded_target_count = $AcceptedPackages.Count
    signature = $SignatureState
    employee_release = (
        $Edition -ceq 'Employee' -and $IsPackagedRelease
    )
    employee_distribution_allowed = [bool]$EmployeeInternalReady
    public_distribution_allowed = [bool]$PublicSignedReady
    windows_warning_expected = (
        $DistributionMode -ceq 'InternalUnsigned'
    )
    verdicts = $Verdicts
    provider_eligibility = $ProviderEligibilityManifest
    foundation_release = $FoundationReleaseManifest
    client_sources = [ordered]@{
        schema_version = 1
        official_only = $ClientSourcesOfficialOnly
        test_only = $ClientSourcesTestOnly
        relative_path = 'client-sources.lock.json'
        resource_name = 'ClientSources.lock.json'
        sha256 = $ClientSourcesHash
        bytes = $ClientSourcesBytes
    }
    targets = $IncludedTargets
    artifacts = [ordered]@{
        'LLMFoundationInstaller.exe' = [ordered]@{
            sha256 = Get-Sha256 $Executable
            bytes = (Get-Item -LiteralPath $Executable).Length
        }
        'engine/foundation.ps1' = [ordered]@{
            sha256 = Get-Sha256 (Join-Path $EngineRoot 'foundation.ps1')
            bytes = (Get-Item -LiteralPath (
                Join-Path $EngineRoot 'foundation.ps1'
            )).Length
        }
        'engine/engine-manifest.json' = [ordered]@{
            sha256 = Get-Sha256 (
                Join-Path $EngineRoot 'engine-manifest.json'
            )
            bytes = (Get-Item -LiteralPath (
                Join-Path $EngineRoot 'engine-manifest.json'
            )).Length
        }
        'engine/VERSION' = [ordered]@{
            sha256 = Get-Sha256 (Join-Path $EngineRoot 'VERSION')
            bytes = (Get-Item -LiteralPath (
                Join-Path $EngineRoot 'VERSION'
            )).Length
        }
        'VERSION' = [ordered]@{
            sha256 = Get-Sha256 (Join-Path $OutputRoot 'VERSION')
            bytes = (Get-Item -LiteralPath (
                Join-Path $OutputRoot 'VERSION'
            )).Length
        }
        'client-sources.lock.json' = [ordered]@{
            sha256 = $ClientSourcesHash
            bytes = $ClientSourcesBytes
        }
    }
}
if ($Edition -ceq 'Owner') {
    $Manifest['owner_claude_state'] = if ($ProviderReady) {
        'PROVIDER_READY'
    } else {
        'OWNER_CANDIDATE'
    }
}
foreach ($Package in $AcceptedPackages) {
    foreach ($Record in @(
        $Package.asset,
        $Package.release_manifest,
        $Package.acceptance_evidence,
        $Package.release_verification,
        $Package.package_acceptance
    )) {
        $Relative = [string]$Record.relative_path
        $Manifest.artifacts[$Relative] = [ordered]@{
            sha256 = [string]$Record.sha256
            bytes = [long]$Record.bytes
        }
    }
}
if ($null -ne $AcceptedFoundation) {
    foreach ($Record in @(
        $AcceptedFoundation.asset,
        $AcceptedFoundation.release_manifest,
        $AcceptedFoundation.acceptance_evidence,
        $AcceptedFoundation.release_verification,
        $AcceptedFoundation.package_acceptance
    )) {
        $Relative = [string]$Record.relative_path
        $Manifest.artifacts[$Relative] = [ordered]@{
            sha256 = [string]$Record.sha256
            bytes = [long]$Record.bytes
        }
    }
}
if ($null -ne $ProviderEligibility) {
    $Manifest.artifacts['provider-eligibility-evidence.json'] = (
        [ordered]@{
            sha256 = [string]$ProviderEligibility.sha256
            bytes = [long]$ProviderEligibility.bytes
        }
    )
}
$ManifestJson = (ConvertTo-Json $Manifest -Depth 8) + "`n"
[IO.File]::WriteAllText(
    (Join-Path $OutputRoot 'bundle-manifest.json'),
    $ManifestJson,
    $Encoding
)

Write-Output "LLM Foundation GUI $Version built at $OutputRoot"
