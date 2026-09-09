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
    [ValidateSet('Preview', 'InternalUnsigned', 'PublicUnsigned', 'PublicSigned')]
    [string]$DistributionMode = 'Preview',
    [string]$ClientSourcesLock,
    [string]$RuntimeSourcesLock,
    [string]$ProductConfigPath,
    [string]$OfficeCliBinaryPath,
    [switch]$AllowLocalTestSources,
    [switch]$TestHooks,
    [string]$SigningCertificateThumbprint,
    [string]$TimestampServer = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'
$PinnedOfficeCliFromEnvironment = [Environment]::GetEnvironmentVariable(
    'K7_OFFICECLI_BINARY_PATH',
    [EnvironmentVariableTarget]::Process
)
if ([string]::IsNullOrWhiteSpace($OfficeCliBinaryPath) -and
    -not [string]::IsNullOrWhiteSpace($PinnedOfficeCliFromEnvironment)) {
    $OfficeCliBinaryPath = $PinnedOfficeCliFromEnvironment
}
$Utf8NoBom = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$FoundationEngineVersion = [IO.File]::ReadAllText(
    (Join-Path $RepositoryRoot 'VERSION')
).Trim()
if ($FoundationEngineVersion -notmatch '^\d+\.\d+\.\d+$') {
    throw 'Foundation engine version is invalid'
}
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
if ([string]::IsNullOrWhiteSpace($RuntimeSourcesLock)) {
    $RuntimeSourcesLock = Join-Path (
        [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    ) 'runtime-sources.lock.json'
}
$RuntimeSourcesLock = [IO.Path]::GetFullPath($RuntimeSourcesLock)
if (-not (Test-Path -LiteralPath $RuntimeSourcesLock -PathType Leaf)) {
    throw 'RuntimeSourcesLock does not exist'
}
$RuntimeSourcesItem = Get-Item -LiteralPath $RuntimeSourcesLock -Force
if (($RuntimeSourcesItem.Attributes -band
    [IO.FileAttributes]::ReparsePoint) -ne 0 -or
    $RuntimeSourcesItem.Length -lt 2 -or
    $RuntimeSourcesItem.Length -gt 65536) {
    throw 'RuntimeSourcesLock file is unsafe'
}

if (Test-Path -LiteralPath $OutputRoot) {
    throw 'OutputRoot must not exist'
}

. (Join-Path $PSScriptRoot '_common.ps1')

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
    if ($null -ne $Release.PSObject.Properties['core_behavior_contract']) {
        $Fields += 'core_behavior_contract'
        if ($null -ne $Release.PSObject.Properties['session_tools_asset']) {
            $Fields += 'session_tools_asset'
        }
        $ActualFields = @($Evidence.release_binding.PSObject.Properties.Name)
        if ($ActualFields.Count -ne $Fields.Count) {
            throw 'Current acceptance release binding properties differ'
        }
        foreach ($Field in $Fields) {
            if ($ActualFields -cnotcontains $Field) {
                throw 'Current acceptance release binding properties differ'
            }
        }
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

function Assert-AcceptedCoreReference {
    param($Contract)
    $Expected = @{
        id = 'k7-professional-core-v1'
        sha256 = '028ba6363bff000b4aa8551ca27a66a69631cb29dacdc5319a4ce0b696b3a184'
        suite_sha256 = '62b45686075d01277f9c924ca245f105dc5c54ba385f74084b7bdd379218d49c'
    }
    if ($Contract -isnot [Management.Automation.PSCustomObject] -or
        @($Contract.PSObject.Properties).Count -ne $Expected.Count) {
        throw 'Accepted core behavior contract structure differs'
    }
    foreach ($Name in $Expected.Keys) {
        if (@($Contract.PSObject.Properties.Name) -cnotcontains $Name -or
            $Contract.$Name -isnot [string] -or
            [string]$Contract.$Name -cne $Expected[$Name]) {
            throw 'Accepted core behavior contract is unknown or changed'
        }
    }
}

function Read-AcceptedCoreZipEntry {
    param($Archive, [string]$Name)
    $Matches = @($Archive.Entries | Where-Object { $_.FullName -ceq $Name })
    if ($Matches.Count -ne 1 -or $Matches[0].Length -le 0 -or
        $Matches[0].Length -gt 8388608) {
        throw "Accepted package entry is missing, duplicated or outside limits: $Name"
    }
    $Stream = $Matches[0].Open()
    $Memory = New-Object IO.MemoryStream
    try {
        $Stream.CopyTo($Memory)
        return ,$Memory.ToArray()
    } finally {
        $Memory.Dispose()
        $Stream.Dispose()
    }
}

function Get-AcceptedCoreBytesSha256 {
    param([byte[]]$Bytes)
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($Hasher.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    } finally { $Hasher.Dispose() }
}

function Assert-AcceptedCodexCore {
    param($Evidence, $Release, [string]$AssetPath)
    # This consumes a trusted producer verdict; it does not reproduce the
    # Python behavioral audit or establish authenticity of execution events.
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
    $Archive = [IO.Compression.ZipFile]::OpenRead($AssetPath)
    try {
        $Names = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        foreach ($Entry in $Archive.Entries) {
            if (-not $Names.Add([string]$Entry.FullName)) {
                throw 'Accepted package contains duplicate ZIP paths'
            }
        }
        $PackageBytes = Read-AcceptedCoreZipEntry $Archive 'package-manifest.json'
        if ((Get-AcceptedCoreBytesSha256 $PackageBytes) -cne [string]$Release.package_manifest_sha256) {
            throw 'Accepted package manifest SHA-256 differs'
        }
        $Package = (New-Object Text.UTF8Encoding($false, $true)).GetString($PackageBytes) |
            ConvertFrom-Json -ErrorAction Stop
        $CorePath = '.codex/base/core-eval-contract.json'
        $Current = $Names.Contains($CorePath) -or
            $null -ne $Package.PSObject.Properties['core_behavior_contract'] -or
            $null -ne $Release.PSObject.Properties['core_behavior_contract']
        foreach ($Marker in @('acceptance_protocol', 'CORE_BEHAVIOR', 'core_behavior_evidence', 'matched_ab_not_required_reason')) {
            $Current = $Current -or $null -ne $Evidence.PSObject.Properties[$Marker]
        }
        if (-not $Current) { return } # Historical accepted-package semantics.
        Assert-AcceptedCoreReference $Package.core_behavior_contract
        Assert-AcceptedCoreReference $Release.core_behavior_contract
        Assert-AcceptedCoreReference $Evidence.release_binding.core_behavior_contract
        if ([string]$Package.target -cne 'codex' -or
            [string]$Package.version -cne [string]$Release.version -or
            [string]$Package.client.id -cne [string]$Release.client.id -or
            [string]$Package.client.supported_version -cne [string]$Release.client.supported_version) {
            throw 'Accepted core package target, version or client differs'
        }
        $CoreBytes = Read-AcceptedCoreZipEntry $Archive $CorePath
        if ((Get-AcceptedCoreBytesSha256 $CoreBytes) -cne [string]$Release.core_behavior_contract.sha256) {
            throw 'Accepted embedded core behavior contract bytes differ'
        }
        if ($Evidence.acceptance_protocol -isnot [string] -or
            [string]$Evidence.acceptance_protocol -cne 'professional-core-v1' -or
            $Evidence.CORE_BEHAVIOR -isnot [string] -or [string]$Evidence.CORE_BEHAVIOR -cne 'PASS' -or
            $Evidence.MATCHED_AB -isnot [string] -or [string]$Evidence.MATCHED_AB -cne 'NOT_REQUIRED' -or
            $Evidence.matched_ab_not_required_reason -isnot [string] -or
            [string]$Evidence.matched_ab_not_required_reason -cne 'Historical startup-token benchmark is separate from professional-core conformance.' -or
            [string]$Evidence.CODEX_CANARY -cne 'PASS') {
            throw 'Accepted professional core protocol or verdict differs'
        }
        if ($Evidence.core_behavior_evidence -isnot [Management.Automation.PSCustomObject] -or
            $Evidence.core_behavior_evidence.evaluation_mode -isnot [string] -or
            [string]$Evidence.core_behavior_evidence.evaluation_mode -cne 'RELEASE_PACKAGE' -or
            ($Evidence.core_behavior_evidence.schema_version -isnot [int] -and
                $Evidence.core_behavior_evidence.schema_version -isnot [long]) -or
            $Evidence.core_behavior_evidence.schema_version -ne 1 -or
            [string]$Evidence.core_behavior_evidence.kind -cne 'core_behavior_evidence' -or
            [string]$Evidence.core_behavior_evidence.CORE_BEHAVIOR -cne 'PASS' -or
            [string]$Evidence.core_behavior_evidence.claim -cne 'SINGLE_RUN_CONFORMANCE_NOT_RELIABILITY') {
            throw 'Accepted core behavior evidence envelope differs'
        }
        Assert-AcceptedCoreReference $Evidence.core_behavior_evidence.protocol
        Assert-ReleaseBinding $Evidence $Release
        Assert-ReleaseBinding $Evidence.core_behavior_evidence $Release
        Assert-AcceptedIsolatedEngine $Evidence $Release $Archive
    } finally { $Archive.Dispose() }
}

function Get-AcceptedIsolatedEngineContract {
    param([string]$Version)
    $Files = @(
        'VERSION', 'engine-manifest.json', 'foundation.ps1', 'shared-tools.lock.json',
        'shared-tools/officecli/officecli.exe', 'shared-tools/officecli/officecli-shim.exe',
        'shared-tools/officecli/officecli-command-policy.json',
        'shared-tools/officecli/k7-officecli-pdf.exe',
        'shared-tools/officecli/officecli_csv_batch.py'
    )
    $Tests = @('tests/test_foundation.py', 'tests/test_foundation_shared_tools.py',
        'tests/test_foundation_release.py', 'tests/test_acceptance_runner.py',
        'tests/test_professional_core_compat.py', 'tests/test_engine_isolated_lifecycle.py',
        'tests/test_engine_acceptance_runner.py')
    switch -CaseSensitive ($Version) {
        '0.5.11' { break }
        '0.5.12' {
            $Files += @('foundation-toml.ps1', 'vendor/tomlyn/Tomlyn.dll',
                'vendor/tomlyn/LICENSE.txt', 'vendor/tomlyn/provenance.json')
            $Tests += @('tests/test_doctor_state.py', 'tests/test_doctor_toml.py')
            break
        }
        default { throw 'Accepted isolated engine version is unsupported' }
    }
    return [pscustomobject]@{ files = $Files; tests = $Tests }
}

function Assert-AcceptedIsolatedEngine {
    param($Evidence, $Release, $Archive)
    $Foundation = $Evidence.foundation
    $Scenarios = @('fresh_install_rollback', 'existing_install_rollback', 'late_failure_rollback',
        'interrupted_recovery', 'snapshot_tamper_rejected', 'receipt_drift_rejected',
        'foreign_generation_rejected')
    $Reason = 'The historical runner includes the full installer/GUI suite; this protocol is explicitly engine-only.'
    foreach ($Counter in @($Foundation.model_requests, $Foundation.scope.model_requests)) {
        if (($Counter -isnot [int] -and $Counter -isnot [long]) -or $Counter -ne 0) {
            throw 'Accepted isolated engine protocol or scope differs'
        }
    }
    if ([string]$Evidence.FOUNDATION_ENGINE_ACCEPTANCE -cne 'PASS' -or
        [string]$Evidence.FOUNDATION_SYNTHETIC -cne 'NOT_RUN' -or
        $Foundation -isnot [Management.Automation.PSCustomObject] -or
        [string]$Foundation.acceptance_protocol -cne 'foundation-engine-isolated-v1' -or
        ($Foundation.schema_version -isnot [int] -and $Foundation.schema_version -isnot [long]) -or
        $Foundation.schema_version -ne 1 -or
        [string]$Foundation.FOUNDATION_ENGINE_ACCEPTANCE -cne 'PASS' -or
        [string]$Foundation.FOUNDATION_SYNTHETIC -cne 'NOT_RUN' -or
        [string]$Foundation.INSTALLER_ACCEPTANCE -cne 'NOT_RUN' -or
        [string]$Foundation.historical_not_run_reason -cne $Reason -or
        [string]$Foundation.engine_version -cne [string]$Release.foundation_engine_version -or
        [string]$Foundation.evidence_body_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
        [string]$Foundation.deterministic_engine_bundle -cne 'PASS' -or
        [string]$Foundation.engine_lifecycle.status -cne 'PASS' -or
        [string]$Foundation.engine_lifecycle.evaluation_mode -cne 'SYNTHETIC_HOME' -or
        $Foundation.scope.fake_homes_only -isnot [bool] -or -not $Foundation.scope.fake_homes_only -or
        $Foundation.scope.real_consumer_executed -isnot [bool] -or $Foundation.scope.real_consumer_executed -or
        $Foundation.scope.gui_executed -isnot [bool] -or $Foundation.scope.gui_executed -or
        $Foundation.model_requests -cne 0 -or $Foundation.scope.model_requests -cne 0) {
        throw 'Accepted isolated engine protocol or scope differs'
    }
    $Contract = Get-AcceptedIsolatedEngineContract ([string]$Foundation.engine_version)
    $ExpectedFiles = $Contract.files
    if ([string]$Foundation.source.repository -cne 'https://github.com/K7-LS/llm-foundation-installer' -or
        [string]$Foundation.source.commit -cnotmatch '^[a-f0-9]{40}$' -or
        [string]$Foundation.source.tree -cnotmatch '^[a-f0-9]{40}$' -or
        @($Foundation.source.hashes.PSObject.Properties).Count -ne 6) {
        throw 'Accepted isolated engine source differs'
    }
    foreach ($Component in @('VERSION', 'APP_VERSION', 'client-sources.lock.json', 'src', 'tests', 'tools')) {
        if ([string]$Foundation.source.hashes.$Component -cnotmatch '^[a-f0-9]{64}$') {
            throw 'Accepted isolated engine source differs'
        }
    }
    if (@($Foundation.engine_lifecycle.required_scenarios).Count -ne 7 -or
        @($Foundation.engine_lifecycle.shells.PSObject.Properties).Count -ne 2 -or
        @($Foundation.engine_builds.PSObject.Properties).Count -ne 2 -or
        @($Foundation.powershell_syntax.PSObject.Properties).Count -ne 2) {
        throw 'Accepted isolated engine matrix differs'
    }
    $Prefix = '.codex/base/foundation/' + [string]$Release.foundation_engine_version + '/'
    $Actual = @{}
    foreach ($Name in $ExpectedFiles) {
        $Entries = @($Archive.Entries | Where-Object { $_.FullName -ceq ($Prefix + $Name) })
        if ($Entries.Count -ne 1 -or $Entries[0].Length -le 0 -or $Entries[0].Length -gt 268435456) {
            throw 'Accepted isolated engine ZIP inventory differs'
        }
        $Stream = $Entries[0].Open(); $Hasher = [Security.Cryptography.SHA256]::Create()
        try { $Actual[$Name] = ([BitConverter]::ToString($Hasher.ComputeHash($Stream))).Replace('-', '').ToLowerInvariant() }
        finally { $Hasher.Dispose(); $Stream.Dispose() }
    }
    if (@($Archive.Entries | Where-Object { $_.FullName.StartsWith($Prefix, [StringComparison]::Ordinal) }).Count -ne $ExpectedFiles.Count -or
        $Actual['engine-manifest.json'] -cne [string]$Release.foundation_engine_manifest_sha256) {
        throw 'Accepted isolated engine ZIP binding differs'
    }
    $ManifestBytes = Read-AcceptedCoreZipEntry $Archive ($Prefix + 'engine-manifest.json')
    $Manifest = (New-Object Text.UTF8Encoding($false, $true)).GetString($ManifestBytes) | ConvertFrom-Json
    $VersionBytes = Read-AcceptedCoreZipEntry $Archive ($Prefix + 'VERSION')
    $ActualVersion = (New-Object Text.UTF8Encoding($false, $true)).GetString($VersionBytes).Trim()
    if ($ActualVersion -cne [string]$Release.foundation_engine_version -or
        [string]$Manifest.engine_version -cne [string]$Foundation.engine_version -or
        [string]$Manifest.foundation_ps1_sha256 -cne $Actual['foundation.ps1'] -or
        [string]$Manifest.network -cne 'offline') { throw 'Accepted isolated engine ZIP binding differs' }
    foreach ($Shell in @('ps7', 'ps51')) {
        $Build = $Foundation.engine_builds.$Shell
        $Syntax = $Foundation.powershell_syntax.$Shell
        $Lifecycle = $Foundation.engine_lifecycle.shells.$Shell
        foreach ($Counter in @($Build.returncode, $Syntax.returncode)) {
            if (($Counter -isnot [int] -and $Counter -isnot [long]) -or $Counter -ne 0) {
                throw 'Accepted isolated engine matrix differs'
            }
        }
        if ([string]$Build.status -cne 'PASS' -or $Build.returncode -cne 0 -or
            [string]$Syntax.status -cne 'PASS' -or $Syntax.returncode -cne 0 -or
            @($Build.files.PSObject.Properties).Count -ne $ExpectedFiles.Count -or
            [string]$Lifecycle.status -cne 'PASS' -or
            [string]$Lifecycle.engine_sha256 -cne $Actual['foundation.ps1'] -or
            [string]$Lifecycle.engine_manifest_sha256 -cne $Actual['engine-manifest.json'] -or
            @($Lifecycle.scenario_ids).Count -ne 7 -or @($Lifecycle.receipts).Count -ne 7) {
            throw 'Accepted isolated engine matrix differs'
        }
        foreach ($Flag in @('user_environment_unchanged', 'installed_files_verified', 'rollback_byte_identical')) {
            if ($Lifecycle.$Flag -isnot [bool] -or -not $Lifecycle.$Flag) { throw 'Accepted isolated engine matrix differs' }
        }
        for ($Index = 0; $Index -lt 7; $Index++) {
            if (($Lifecycle.receipts[$Index].bytes -isnot [int] -and
                $Lifecycle.receipts[$Index].bytes -isnot [long])) { throw 'Accepted isolated engine matrix differs' }
            if ([string]$Lifecycle.scenario_ids[$Index] -cne $Scenarios[$Index] -or
                [string]$Foundation.engine_lifecycle.required_scenarios[$Index] -cne $Scenarios[$Index] -or
                [string]$Lifecycle.receipts[$Index].sha256 -cnotmatch '^[a-f0-9]{64}$' -or
                [string]::IsNullOrWhiteSpace([string]$Lifecycle.receipts[$Index].path) -or
                $Lifecycle.receipts[$Index].bytes -le 0) { throw 'Accepted isolated engine matrix differs' }
        }
        foreach ($Name in $ExpectedFiles) {
            if ([string]$Build.files.$Name -cne $Actual[$Name]) { throw 'Accepted isolated engine ZIP binding differs' }
        }
    }
    foreach ($Counter in @($Foundation.pytest.returncode, $Foundation.pytest.counts.tests,
        $Foundation.pytest.counts.failures, $Foundation.pytest.counts.errors,
        $Foundation.pytest.counts.skipped, $Foundation.pytest.junit_artifact.bytes)) {
        if ($Counter -isnot [int] -and $Counter -isnot [long]) { throw 'Accepted isolated engine test evidence differs' }
    }
    if ([string]$Foundation.pytest.status -cne 'PASS' -or $Foundation.pytest.returncode -cne 0 -or
        $Foundation.pytest.counts.tests -le 0 -or $Foundation.pytest.counts.failures -cne 0 -or
        $Foundation.pytest.counts.errors -cne 0 -or $Foundation.pytest.counts.skipped -ne 0 -or
        [string]$Foundation.pytest.junit_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
        [string]$Foundation.pytest.junit_artifact.sha256 -cne [string]$Foundation.pytest.junit_sha256 -or
        [string]::IsNullOrWhiteSpace([string]$Foundation.pytest.junit_artifact.path) -or
        $Foundation.pytest.junit_artifact.bytes -le 0 -or
        @($Foundation.pytest.selected_files).Count -eq 0 -or
        @($Foundation.pytest.collected_case_ids).Count -ne $Foundation.pytest.counts.tests) {
        throw 'Accepted isolated engine test evidence differs'
    }
    Assert-AcceptedFoundationArtifacts $Evidence $Scenarios
}

function Get-AcceptedFoundationArtifactText {
    param($Artifacts, $Record)
    if ($Record.sha256 -isnot [string] -or $Record.sha256 -cnotmatch '^[a-f0-9]{64}$' -or
        ($Record.bytes -isnot [int] -and $Record.bytes -isnot [long]) -or
        $Record.bytes -le 0 -or $Record.bytes -gt 4194304) { throw 'Accepted isolated engine artifact differs' }
    $Property = $Artifacts.PSObject.Properties[[string]$Record.sha256]
    if ($null -eq $Property) { throw 'Accepted isolated engine artifact is missing' }
    $Artifact = $Property.Value
    if ($Artifact.text -isnot [string] -or [string]$Artifact.sha256 -cne [string]$Record.sha256 -or
        ($Artifact.bytes -isnot [int] -and $Artifact.bytes -isnot [long]) -or
        $Artifact.bytes -ne $Record.bytes) { throw 'Accepted isolated engine artifact differs' }
    $Bytes = (New-Object Text.UTF8Encoding($false, $true)).GetBytes($Artifact.text)
    if ($Bytes.Length -ne $Record.bytes -or
        (Get-AcceptedCoreBytesSha256 $Bytes) -cne [string]$Record.sha256) { throw 'Accepted isolated engine artifact bytes differ' }
    return [string]$Artifact.text
}

function Assert-AcceptedFoundationEnvironmentHashes {
    param($Before, $After)
    if (@($Before.PSObject.Properties).Count -ne 3 -or @($After.PSObject.Properties).Count -ne 3) {
        throw 'Accepted isolated engine User environment differs'
    }
    foreach ($Name in @('PATH', 'OFFICECLI_NO_AUTO_INSTALL', 'OFFICECLI_SKIP_UPDATE')) {
        if ([string]$Before.$Name -cnotmatch '^[a-f0-9]{64}$' -or
            [string]$After.$Name -cne [string]$Before.$Name) { throw 'Accepted isolated engine User environment differs' }
    }
}

function Assert-AcceptedFoundationArtifacts {
    param($Evidence, [string[]]$Scenarios)
    $Foundation = $Evidence.foundation; $Tests = $Foundation.pytest
    $ExpectedTests = (Get-AcceptedIsolatedEngineContract ([string]$Foundation.engine_version)).tests
    if (@($Tests.selected_files).Count -ne $ExpectedTests.Count -or @($Tests.selected_files_sha256.PSObject.Properties).Count -ne $ExpectedTests.Count -or
        @($Evidence.foundation_artifacts.PSObject.Properties).Count -ne 15) {
        throw 'Accepted isolated engine artifact coverage differs'
    }
    $TotalBytes = 0L
    foreach ($Property in $Evidence.foundation_artifacts.PSObject.Properties) {
        if (($Property.Value.bytes -isnot [int] -and $Property.Value.bytes -isnot [long]) -or
            $Property.Value.bytes -le 0) { throw 'Accepted isolated engine artifact differs' }
        $TotalBytes += $Property.Value.bytes
    }
    if ($TotalBytes -gt 4194304) { throw 'Accepted isolated engine artifact size differs' }
    for ($Index = 0; $Index -lt $ExpectedTests.Count; $Index++) {
        if ([string]$Tests.selected_files[$Index] -cne $ExpectedTests[$Index] -or
            [string]$Tests.selected_files_sha256.($ExpectedTests[$Index]) -cnotmatch '^[a-f0-9]{64}$') {
            throw 'Accepted isolated engine artifact coverage differs'
        }
    }
    Assert-AcceptedFoundationEnvironmentHashes $Foundation.real_user_environment_before $Foundation.real_user_environment_after
    $Used = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    foreach ($Shell in @('ps7', 'ps51')) {
        $Run = $Foundation.engine_lifecycle.shells.$Shell
        for ($Index = 0; $Index -lt 7; $Index++) {
            $Record = $Run.receipts[$Index]
            if (-not $Used.Add([string]$Record.sha256)) { throw 'Accepted isolated engine artifact coverage differs' }
            $Text = Get-AcceptedFoundationArtifactText $Evidence.foundation_artifacts $Record
            $Receipt = $Text | ConvertFrom-Json -ErrorAction Stop
            if ([string]$Receipt.scenario_id -cne $Scenarios[$Index] -or [string]$Receipt.shell -cne $Shell -or
                [string]$Receipt.status -cne 'PASS' -or [string]$Receipt.engine_sha256 -cne [string]$Run.engine_sha256 -or
                [string]$Receipt.engine_manifest_sha256 -cne [string]$Run.engine_manifest_sha256 -or
                $Receipt.commands -isnot [Array] -or @($Receipt.commands).Count -eq 0) {
                throw 'Accepted isolated engine receipt differs'
            }
            foreach ($Command in $Receipt.commands) {
                if ($Command.command -isnot [Array] -or @($Command.command).Count -eq 0 -or
                    ($Command.returncode -isnot [int] -and $Command.returncode -isnot [long]) -or
                    $Command.stdout -isnot [string] -or $Command.stderr -isnot [string]) { throw 'Accepted isolated engine receipt differs' }
                foreach ($Argument in $Command.command) {
                    if ($Argument -isnot [string] -or [string]::IsNullOrWhiteSpace($Argument)) { throw 'Accepted isolated engine receipt differs' }
                }
            }
            Assert-AcceptedFoundationEnvironmentHashes $Receipt.real_user_environment_before $Receipt.real_user_environment_after
            Assert-AcceptedFoundationEnvironmentHashes $Foundation.real_user_environment_before $Receipt.real_user_environment_before
            if ($Receipt.before -isnot [Management.Automation.PSCustomObject] -or
                $Receipt.after -isnot [Management.Automation.PSCustomObject] -or
                @($Receipt.before.PSObject.Properties).Count -ne 2 -or @($Receipt.after.PSObject.Properties).Count -ne 2) {
                throw 'Accepted isolated engine rollback bytes differ'
            }
            foreach ($Group in @('files', 'fake_environment')) {
                $Before = $Receipt.before.$Group; $After = $Receipt.after.$Group
                if ($Before -isnot [Management.Automation.PSCustomObject] -or
                    $After -isnot [Management.Automation.PSCustomObject] -or
                    @($Before.PSObject.Properties).Count -ne @($After.PSObject.Properties).Count) {
                    throw 'Accepted isolated engine rollback bytes differ'
                }
                foreach ($Property in $Before.PSObject.Properties) {
                    if ($Property.Value -isnot [string] -or $Property.Value -cnotmatch '^[a-f0-9]{64}$' -or
                        [string]$After.($Property.Name) -cne $Property.Value) { throw 'Accepted isolated engine rollback bytes differ' }
                }
            }
        }
    }
    if (-not $Used.Add([string]$Tests.junit_artifact.sha256) -or $Used.Count -ne 15) { throw 'Accepted isolated engine artifact coverage differs' }
    $XmlText = Get-AcceptedFoundationArtifactText $Evidence.foundation_artifacts $Tests.junit_artifact
    $Settings = New-Object Xml.XmlReaderSettings
    $Settings.DtdProcessing = [Xml.DtdProcessing]::Prohibit
    $Settings.XmlResolver = $null
    $Reader = [Xml.XmlReader]::Create((New-Object IO.StringReader($XmlText)), $Settings)
    $Document = New-Object Xml.XmlDocument; $Document.XmlResolver = $null
    try { $Document.Load($Reader) } finally { $Reader.Dispose() }
    $Cases = @($Document.SelectNodes('//testcase'))
    $Totals = @{ tests = 0; failures = 0; errors = 0; skipped = 0 }
    foreach ($Suite in @($Document.SelectNodes('//testsuite'))) {
        foreach ($Name in @('failures', 'errors')) {
            if ([string]$Suite.GetAttribute($Name) -cnotmatch '^0$') { throw 'Accepted isolated engine JUnit differs' }
        }
    }
    foreach ($Suite in @($Document.SelectNodes('//testsuite[not(testsuite)]'))) {
        foreach ($Name in @('tests', 'failures', 'errors', 'skipped')) {
            if ([string]$Suite.GetAttribute($Name) -cnotmatch '^[0-9]+$') { throw 'Accepted isolated engine JUnit differs' }
            $Totals[$Name] += [int]$Suite.GetAttribute($Name)
        }
    }
    foreach ($Name in @('tests', 'failures', 'errors', 'skipped')) {
        if ($Totals[$Name] -ne $Tests.counts.$Name) { throw 'Accepted isolated engine JUnit differs' }
    }
    if ($Cases.Count -ne $Totals.tests -or @($Document.SelectNodes('//testcase/failure|//testcase/error')).Count -ne 0 -or
        @($Document.SelectNodes('//testcase/skipped')).Count -ne $Totals.skipped) { throw 'Accepted isolated engine JUnit differs' }
    $Observed = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    $Collected = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    $ObservedFiles = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    foreach ($Id in $Tests.collected_case_ids) {
        if ($Id -isnot [string] -or -not $Collected.Add($Id)) { throw 'Accepted isolated engine JUnit identity differs' }
    }
    if ([string]$Tests.collection.status -cne 'PASS' -or
        ($Tests.collection.returncode -isnot [int] -and $Tests.collection.returncode -isnot [long]) -or
        $Tests.collection.returncode -ne 0 -or $Tests.collection.stdout -isnot [string] -or
        $Tests.collection.stderr -isnot [string] -or $Tests.collection.command -isnot [Array] -or
        @($Tests.collection.command) -cnotcontains '--collect-only') { throw 'Accepted isolated engine JUnit identity differs' }
    $RawCollected = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    foreach ($Line in $Tests.collection.stdout.Split("`n")) {
        $CleanLine = $Line.TrimEnd("`r")
        if ($CleanLine.StartsWith('tests/', [StringComparison]::Ordinal) -and $CleanLine.Contains('::')) {
            if (-not $RawCollected.Add($CleanLine)) { throw 'Accepted isolated engine JUnit identity differs' }
        }
    }
    if (-not $RawCollected.SetEquals($Collected)) { throw 'Accepted isolated engine JUnit identity differs' }
    foreach ($Case in $Cases) {
        $Module = [string]$Case.GetAttribute('classname'); $NodeId = $null
        foreach ($File in $ExpectedTests) {
            $Dotted = $File.Substring(0, $File.Length - 3).Replace('/', '.')
            if ($Module -ceq $Dotted) {
                $null = $ObservedFiles.Add($File)
                $NodeId = $File + '::' + $Case.GetAttribute('name'); break
            }
            if ($Module.StartsWith($Dotted + '.', [StringComparison]::Ordinal)) {
                $null = $ObservedFiles.Add($File)
                $NodeId = $File + '::' + $Module.Substring($Dotted.Length + 1).Replace('.', '::') + '::' + $Case.GetAttribute('name'); break
            }
        }
        if ($null -eq $NodeId -or -not $Observed.Add($NodeId) -or -not $Collected.Contains($NodeId)) {
            throw 'Accepted isolated engine JUnit identity differs'
        }
    }
    if (-not $Observed.SetEquals($Collected)) { throw 'Accepted isolated engine JUnit identity differs' }
    if (-not $ObservedFiles.SetEquals([string[]]$ExpectedTests)) { throw 'Accepted isolated engine JUnit module coverage differs' }
    foreach ($Shell in @('ps7', 'ps51')) {
        foreach ($Scenario in $Scenarios) {
            $Matches = @($Cases | Where-Object {
                $_.GetAttribute('classname') -ceq 'tests.test_engine_isolated_lifecycle' -and
                $_.GetAttribute('name').StartsWith('test_actual_built_engine_lifecycle[' + $Scenario + '-', [StringComparison]::Ordinal) -and
                $(if ($Shell -ceq 'ps7') { $_.GetAttribute('name') -imatch 'pwsh\.exe\]$' }
                  else { $_.GetAttribute('name') -imatch 'powershell\.exe\]$' })
            })
            if ($Matches.Count -ne 1 -or @($Matches[0].SelectNodes('skipped|failure|error')).Count -ne 0) {
                throw 'Accepted isolated engine JUnit lifecycle coverage differs'
            }
        }
    }
}

function Get-AcceptedFoundationCoreFiles {
    param([string]$Version)
    $Names = @('VERSION', 'engine-manifest.json', 'foundation.ps1')
    switch -CaseSensitive ($Version) {
        '0.5.11' { break }
        '0.5.12' {
            $Names += @('foundation-toml.ps1', 'vendor/tomlyn/Tomlyn.dll',
                'vendor/tomlyn/LICENSE.txt', 'vendor/tomlyn/provenance.json')
            break
        }
        default { throw 'Foundation engine delivery version is unsupported' }
    }
    return @($Names | Sort-Object)
}

function Test-AcceptedFoundationFileNames {
    param([string[]]$Names, [string[]]$Expected)
    if ($Names.Count -ne $Expected.Count) { return $false }
    $ExpectedSet = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    $Seen = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Name in $Expected) {
        if (-not $ExpectedSet.Add($Name)) { return $false }
    }
    foreach ($Name in $Names) {
        if (-not $ExpectedSet.Contains($Name) -or -not $Seen.Add($Name)) {
            return $false
        }
    }
    return $true
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
        [string]$Acceptance.engine_version -cne $FoundationEngineVersion -or
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
    $ExpectedCoreFiles = @(Get-AcceptedFoundationCoreFiles $FoundationEngineVersion)
    if ([int]$Release.schema_version -ne 1 -or
        [string]$Release.target -cne 'foundation' -or
        [string]$Release.version -cne $FoundationEngineVersion -or
        [string]$Release.tag -cne (
            'foundation-engine-v' + $FoundationEngineVersion
        ) -or
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
        -not (Test-AcceptedFoundationFileNames $ReleaseFiles $ExpectedCoreFiles) -or
        -not (Test-AcceptedFoundationFileNames $AcceptanceFiles $ExpectedCoreFiles) -or
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
        [string]$Evidence.engine_version -cne $FoundationEngineVersion -or
        [string]$Evidence.installer_version -cne $Version -or
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
        engine_version = $FoundationEngineVersion
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
    param([string]$Root, [string]$Mode)
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
        $InternalPath = Join-Path $Directory.FullName 'internal-acceptance.json'
        if ($Mode -ceq 'InternalUnsigned' -and
            (Test-Path -LiteralPath $InternalPath -PathType Leaf)) {
            try {
                $Internal = Get-Content -LiteralPath $InternalPath -Raw |
                    ConvertFrom-Json
            } catch {
                throw "Internal acceptance JSON is invalid for target: $Target"
            }
            $AssetPath = Assert-FileBinding $Directory.FullName $Internal.asset 'internal asset'
            $ReleasePath = Assert-FileBinding $Directory.FullName $Internal.release_manifest 'internal release manifest'
            $Release = Get-Content -LiteralPath $ReleasePath -Raw |
                ConvertFrom-Json
            if ([int]$Internal.schema_version -ne 1 -or
                [string]$Internal.target -cne $Target -or
                [string]$Internal.channel -cne 'InternalUnsigned' -or
                [string]$Internal.TECHNICAL_READY -cne 'PASS' -or
                [string]$Internal.client.id -cne [string]$Definitions[$Target].client -or
                [string]$Release.target -cne $Target -or
                [string]$Release.channel -cne 'candidate' -or
                [string]$Release.asset.name -cne [string]$Internal.asset.name -or
                [string]$Release.asset.sha256 -cne [string]$Internal.asset.sha256 -or
                [long]$Release.asset.bytes -ne [long]$Internal.asset.bytes -or
                [string]$Release.client.id -cne [string]$Internal.client.id -or
                [string]$Release.client.supported_version -cne (
                    [string]$Internal.client.supported_version
                )) {
                throw "Internal acceptance contract differs for target: $Target"
            }
            $Rows += [ordered]@{
                trust_level = 'internal_unsigned'
                target = $Target
                client_id = [string]$Internal.client.id
                supported_version = [string]$Internal.client.supported_version
                foundation_engine_manifest_sha256 = [string](
                    $Release.foundation_engine_manifest_sha256
                )
                asset = [ordered]@{
                    relative_path = "packages/$Target/$([string]$Internal.asset.name)"
                    resource_name = "TargetPackage.$Target.asset"
                    sha256 = [string]$Internal.asset.sha256
                    bytes = [long]$Internal.asset.bytes
                }
                release_manifest = [ordered]@{
                    relative_path = "packages/$Target/$([string]$Internal.release_manifest.name)"
                    resource_name = "TargetPackage.$Target.release_manifest"
                    sha256 = [string]$Internal.release_manifest.sha256
                    bytes = [long]$Internal.release_manifest.bytes
                }
                internal_acceptance = [ordered]@{
                    relative_path = "packages/$Target/internal-acceptance.json"
                    resource_name = "TargetPackage.$Target.internal_acceptance"
                    sha256 = Get-Sha256 $InternalPath
                    bytes = (Get-Item -LiteralPath $InternalPath).Length
                }
                source_directory = $Directory.FullName
            }
            continue
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
            $Evidence = Get-Content -LiteralPath $EvidencePath -Raw -Encoding UTF8 |
                ConvertFrom-Json -ErrorAction Stop
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
            Assert-AcceptedCodexCore $Evidence $Release $AssetPath
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
            trust_level = 'accepted'
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

function Get-PackageRecords {
    param([Parameter(Mandatory = $true)]$Package)
    if ([string]$Package.trust_level -ceq 'accepted') {
        return @(
            $Package.asset,
            $Package.release_manifest,
            $Package.acceptance_evidence,
            $Package.release_verification,
            $Package.package_acceptance
        )
    }
    if ([string]$Package.trust_level -ceq 'internal_unsigned') {
        return @(
            $Package.asset,
            $Package.release_manifest,
            $Package.internal_acceptance
        )
    }
    throw 'Package trust level is invalid'
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
        $CoreNames = @(
            $Foundation.engine_files.PSObject.Properties.Name | Sort-Object
        )
        $ExpectedCoreFiles = @(Get-AcceptedFoundationCoreFiles $FoundationEngineVersion)
        if (-not (Test-AcceptedFoundationFileNames $CoreNames $ExpectedCoreFiles)) {
            throw 'Foundation engine archive core inventory differs'
        }
        $LockEntry = $Archive.GetEntry('shared-tools.lock.json')
        if ($null -eq $LockEntry -or $LockEntry.Length -gt 1048576) {
            throw 'Foundation engine archive inventory differs'
        }
        $LockReader = New-Object IO.StreamReader(
            $LockEntry.Open(),
            [Text.Encoding]::UTF8,
            $true,
            4096,
            $false
        )
        try {
            $SharedLock = $LockReader.ReadToEnd() | ConvertFrom-Json
        } catch {
            throw 'Foundation shared-tools lock is invalid'
        } finally {
            $LockReader.Dispose()
        }
        if ([int]$SharedLock.schema_version -ne 1 -or
            @($SharedLock.tools).Count -ne 1 -or
            [string]$SharedLock.tools[0].id -cne 'officecli' -or
            [string]$SharedLock.tools[0].version -cne '1.0.143') {
            throw 'Foundation shared-tools lock is invalid'
        }
        $SharedRecords = @{}
        foreach ($Field in @(
                'private_exe',
                'shim',
                'policy',
                'pdf_exporter',
                'csv_batch_adapter'
            )) {
            $Property = $SharedLock.tools[0].PSObject.Properties[$Field]
            if ($null -eq $Property) {
                throw 'Foundation shared-tools lock is invalid'
            }
            $SharedRecord = $Property.Value
            $SharedPath = [string]$SharedRecord.path
            if ($SharedPath -notmatch (
                    '^shared-tools/officecli/[A-Za-z0-9._-]+$'
                ) -or
                [string]$SharedRecord.sha256 -notmatch '^[a-f0-9]{64}$' -or
                ($SharedRecord.bytes -isnot [int] -and
                    $SharedRecord.bytes -isnot [long]) -or
                [long]$SharedRecord.bytes -lt 1 -or
                [long]$SharedRecord.bytes -gt 67108864 -or
                $SharedRecords.ContainsKey($SharedPath)) {
                throw 'Foundation shared-tools lock is invalid'
            }
            $SharedRecords[$SharedPath] = $SharedRecord
        }
        $ExpectedNames = @(
            $CoreNames +
                @('shared-tools.lock.json') +
                @($SharedRecords.Keys) |
                Sort-Object
        )
        if ($Entries.Count -ne $ExpectedNames.Count -or
            ($Names -join ',') -cne ($ExpectedNames -join ',')) {
            throw 'Foundation engine archive inventory differs'
        }
        foreach ($Entry in $Entries) {
            $CoreProperty = $Foundation.engine_files.PSObject.Properties[
                $Entry.FullName
            ]
            $Record = if ($null -ne $CoreProperty) {
                $CoreProperty.Value
            } elseif ($SharedRecords.ContainsKey($Entry.FullName)) {
                $SharedRecords[$Entry.FullName]
            } else {
                $null
            }
            if (($Entry.FullName -cne 'shared-tools.lock.json' -and
                    $null -eq $Record) -or
                ($null -ne $Record -and
                    [long]$Entry.Length -ne [long]$Record.bytes)) {
                throw "Foundation engine archive entry differs: $(
                    $Entry.FullName
                )"
            }
            $DestinationPath = Join-Path $Destination $Entry.FullName
            [IO.Directory]::CreateDirectory(
                [IO.Path]::GetDirectoryName($DestinationPath)
            ) | Out-Null
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
            if ($null -ne $Record -and (
                    (Get-Sha256 $DestinationPath) -cne (
                        [string]$Record.sha256
                    ) -or
                    (Get-Item -LiteralPath $DestinationPath).Length -ne (
                        [long]$Record.bytes
                    ))) {
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
        )).Trim() -cne $FoundationEngineVersion) {
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
        [string]$EngineManifest.engine_version -cne $FoundationEngineVersion -or
        [string]$EngineManifest.network -cne 'offline' -or
        (@($EngineManifest.commands) -join ',') -cne (
            'apply,doctor,install,inventory,plan,rollback'
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
# Ф3: компиляция делегирована SDK-style проекту (dotnet build).
$DotnetCli = Get-Command dotnet -ErrorAction SilentlyContinue
if ($null -eq $DotnetCli) {
    throw (
        'The dotnet SDK is required for a deterministic GUI build. ' +
        'Install the .NET SDK (8.0 or newer).'
    )
}

$AcceptedPackages = @(Read-AcceptedPackages $PackageRoot $DistributionMode)
$AcceptedFoundation = Read-AcceptedFoundation $FoundationPackageRoot
$ProviderEligibility = Read-ProviderEligibilityEvidence `
    $ProviderEligibilityEvidence
$AcceptedTargets = @($AcceptedPackages.target | Sort-Object)
$AllPackages = @($AcceptedPackages)
$AvailableTargets = @($AllPackages.target | Sort-Object)
$IncludedTargets = @('claude', 'codex', 'opencode')
$RequiredTargets = @('claude', 'codex', 'opencode')
$IsPackagedRelease = $DistributionMode -cne 'Preview'
$NeedsAcceptedFoundation = $IsPackagedRelease -or (
    $AllowLocalTestSources -and $null -ne $AcceptedFoundation
)
$IsPublicUnsigned = $DistributionMode -ceq 'PublicUnsigned'
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
if ([string]::IsNullOrWhiteSpace($ProductConfigPath)) {
    $ProductConfigPath = Join-Path (
        $RepositoryRoot
    ) 'src\gui\product-config.json'
}
else {
    $ProductConfigPath = [IO.Path]::GetFullPath($ProductConfigPath)
}
$ProductConfig = $null
try {
    $ProductConfig = [IO.File]::ReadAllText(
        $ProductConfigPath
    ) | ConvertFrom-Json
} catch {
    throw 'ProductConfig JSON is invalid'
}
if ($null -eq $ProductConfig -or
    ($ProductConfig.schema_version -isnot [int] -and
        $ProductConfig.schema_version -isnot [long]) -or
    [int]$ProductConfig.schema_version -ne 1 -or
    -not [IO.Path]::IsPathRooted([string]$ProductConfig.chrome_path) -or
    [string]$ProductConfig.chrome_path -notmatch '\\chrome\.exe$' -or
    [string]$ProductConfig.singbox_version -notmatch '^\d+\.\d+\.\d+$') {
    throw 'ProductConfig schema is invalid'
}
$ChromeProxyUri = $null
if (-not [Uri]::TryCreate(
        [string]$ProductConfig.chrome_proxy_url,
        [UriKind]::Absolute,
        [ref]$ChromeProxyUri
    ) -or
    -not [string]::IsNullOrWhiteSpace($ChromeProxyUri.UserInfo) -or
    $ChromeProxyUri.Scheme -notin @('http', 'https')) {
    throw 'ProductConfig chrome proxy URL is invalid'
}
$RuntimeSources = $null
try {
    $RuntimeSources = [IO.File]::ReadAllText(
        $RuntimeSourcesLock
    ) | ConvertFrom-Json
} catch {
    throw 'RuntimeSourcesLock JSON is invalid'
}
if ($null -eq $RuntimeSources -or
    [int]$RuntimeSources.schema_version -ne 1 -or
    $RuntimeSources.test_only -isnot [bool] -or
    $null -eq $RuntimeSources.runtime -or
    [string]$RuntimeSources.runtime.id -cne 'sing-box' -or
    [string]$RuntimeSources.runtime.version -cne (
        [string]$ProductConfig.singbox_version
    ) -or
    [string]$RuntimeSources.runtime.archive_kind -cne 'zip' -or
    [string]$RuntimeSources.runtime.executable_name -cne 'sing-box.exe' -or
    [string]$RuntimeSources.runtime.sha256 -notmatch '^[0-9A-Fa-f]{64}$') {
    throw 'RuntimeSourcesLock schema is invalid'
}
$RuntimeUri = $null
if (-not [Uri]::TryCreate(
        [string]$RuntimeSources.runtime.url,
        [UriKind]::Absolute,
        [ref]$RuntimeUri
    ) -or
    -not [string]::IsNullOrWhiteSpace($RuntimeUri.UserInfo)) {
    throw 'Runtime source URL is invalid'
}
if ([bool]$RuntimeSources.test_only) {
    if (-not $AllowLocalTestSources -or
        $DistributionMode -cne 'Preview' -or
        -not $RuntimeUri.IsLoopback -or
        $RuntimeUri.Scheme -notin @('http', 'https')) {
        throw 'Local test runtime source is unsafe'
    }
}
elseif ($RuntimeUri.Scheme -cne 'https' -or
    $RuntimeUri.Host -cne 'github.com' -or
    $RuntimeUri.AbsolutePath -cne (
        '/SagerNet/sing-box/releases/download/v' +
        [string]$ProductConfig.singbox_version + '/sing-box-' +
        [string]$ProductConfig.singbox_version + '-windows-amd64.zip'
    )) {
    throw 'Official runtime source is not immutable'
}
$ApprovedHosts = @(
    'chatgpt.com',
    'downloads.claude.ai',
    'github.com',
    'openai.com',
    'apps.microsoft.com'
)
$ProbeUri = $null
if (-not [Uri]::TryCreate(
        [string]$ProductConfig.connection_probe_url,
        [UriKind]::Absolute,
        [ref]$ProbeUri
    ) -or
    -not [string]::IsNullOrWhiteSpace($ProbeUri.UserInfo) -or
    $ProbeUri.Scheme -cne 'https' -or
    $ApprovedHosts -cnotcontains $ProbeUri.Host) {
    throw 'ProductConfig probe URL is not an approved official endpoint'
}
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
            [string]$ClientSource.store_signature_kind -cne 'Store' -or
            [string]$ClientSource.store_application_id -cne 'App' -or
            [string]$ClientSource.store_executable -cne (
                'app/ChatGPT.exe'
            ) -or
            [string]$ClientSource.store_entry_point -cne (
                'Windows.FullTrustApplication'
            )
        )) {
        throw 'Codex Store client identity is invalid'
    }
}
if ($ClientSourcesOfficialOnly) {
    $ExpectedClients = @(
        'claude-code',
        'codex-cli',
        'codex-desktop',
        'officecli',
        'opencode-cli',
        'opencode-desktop'
    )
    $ActualClients = @($SeenClientIds.Keys | Sort-Object)
    if (($ActualClients -join ',') -cne ($ExpectedClients -join ',')) {
        throw 'Official client source inventory is incomplete'
    }
}
if ($IsPackagedRelease) {
    if (($AvailableTargets -join ',') -cne ($IncludedTargets -join ',')) {
        throw "$Edition target set differs from the edition contract"
    }
    if ($NeedsAcceptedFoundation -and $null -eq $AcceptedFoundation) {
        throw (
            "$Edition release requires an accepted immutable Foundation " +
            'package'
        )
    }
    $FoundationExpectedManifestSha256 = if ($NeedsAcceptedFoundation) {
        [string](
            $AcceptedFoundation.engine_files.PSObject.Properties[
                'engine-manifest.json'
            ].Value.sha256
        )
    } else {
        [string]$AllPackages[0].foundation_engine_manifest_sha256
    }
    foreach ($Package in $AllPackages) {
        if ([string]$Package.foundation_engine_manifest_sha256 -cne (
                $FoundationExpectedManifestSha256
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
$EditionContract = if ($Edition -ceq 'Owner') {
    [ordered]@{
        edition_id = 'Owner'
        display_name = 'K-7 AI Foundation Owner'
        distribution_allowed = [bool]$IsPublicUnsigned
        included_target_ids = @('claude', 'codex', 'opencode')
        required_target_ids = @('claude', 'codex', 'opencode')
        theme_id = 'SignalConsole'
        owner_controlled = $true
        product_role = $ProductRole
    }
}
else {
    [ordered]@{
        edition_id = 'Employee'
        display_name = 'K-7 AI Foundation Employee'
        distribution_allowed = $true
        included_target_ids = @('claude', 'codex', 'opencode')
        required_target_ids = @('claude', 'codex', 'opencode')
        theme_id = 'K7Signal'
        owner_controlled = $false
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
$EffectiveRuntimeSourcesPath = Join-Path (
    $OutputRoot
) 'runtime-sources.lock.json'
Copy-Item -LiteralPath $RuntimeSourcesLock -Destination (
    $EffectiveRuntimeSourcesPath
)
$EngineRoot = Join-Path $OutputRoot 'engine'
if ($NeedsAcceptedFoundation) {
    Export-AcceptedFoundationEngine $AcceptedFoundation $EngineRoot
}
else {
    & (Join-Path $RepositoryRoot 'tools\build-engine.ps1') `
        -OutputRoot $EngineRoot `
        -OfficeCliBinaryPath $OfficeCliBinaryPath
    if (-not $?) {
        throw 'Foundation engine build failed'
    }
}
if ($IsPackagedRelease -and
    (Get-Sha256 (Join-Path $EngineRoot 'engine-manifest.json')) -cne
        $FoundationExpectedManifestSha256) {
    throw 'Built Foundation engine differs from base release binding'
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
        $AllPackages | ForEach-Object {
            [ordered]@{
                trust_level = $_.trust_level
                target = $_.target
                client_id = $_.client_id
                supported_version = $_.supported_version
                asset = $_.asset
                release_manifest = $_.release_manifest
                acceptance_evidence = $_.acceptance_evidence
                release_verification = $_.release_verification
                package_acceptance = $_.package_acceptance
                internal_acceptance = $_.internal_acceptance
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
$ClientSourcesBytes = (
    Get-Item -LiteralPath $EffectiveClientSourcesPath
).Length
$ClientSourcesHash = Get-Sha256 $EffectiveClientSourcesPath
$RuntimeSourcesBytes = (
    Get-Item -LiteralPath $EffectiveRuntimeSourcesPath
).Length
$RuntimeSourcesHash = Get-Sha256 $EffectiveRuntimeSourcesPath
$EngineCoreNames = @('foundation.ps1', 'engine-manifest.json', 'VERSION')
$EngineExtraIndex = @()
$EngineExtraCounter = 0
$EngineRootPrefix = [IO.Path]::GetFullPath($EngineRoot).TrimEnd('\') + '\'
foreach ($EngineFile in @(Get-ChildItem -LiteralPath $EngineRoot -Recurse -File | Sort-Object FullName)) {
    $EngineFullPath = [IO.Path]::GetFullPath($EngineFile.FullName)
    if (-not $EngineFullPath.StartsWith($EngineRootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Foundation extra file escapes engine root'
    }
    $Relative = $EngineFullPath.Substring($EngineRootPrefix.Length).Replace('\', '/')
    if ($EngineCoreNames -ccontains $Relative) { continue }
    $ResourceName = 'FoundationEngine.extra.' + $EngineExtraCounter.ToString('D4')
    $EngineExtraIndex += [ordered]@{
        relative_path = $Relative
        resource_name = $ResourceName
        sha256 = Get-Sha256 $EngineFile.FullName
        bytes = $EngineFile.Length
        source_path = $EngineFile.FullName
    }
    $EngineExtraCounter++
}
$EngineExtraIndexPath = Join-Path $OutputRoot '.foundation-extra-files.json'
$EngineExtraPublic = @($EngineExtraIndex | ForEach-Object {
    [ordered]@{
        relative_path = $_.relative_path
        resource_name = $_.resource_name
        sha256 = $_.sha256
        bytes = $_.bytes
    }
})
[IO.File]::WriteAllText(
    $EngineExtraIndexPath,
    (($EngineExtraPublic | ConvertTo-Json -Depth 5) + "`n"),
    $Encoding
)
$RoutingDomains = Join-Path (
    $RepositoryRoot
) 'src\gui\launcher-routing-domains.json'
$ApplicationManifest = Join-Path $RepositoryRoot 'src\gui\app.manifest'
$ApplicationIcon = Join-Path $OutputRoot '.installer.ico'
& (Join-Path $RepositoryRoot 'tools\build-icon.ps1') `
    -OutputPath $ApplicationIcon | Out-Null
$EmbeddedResources = New-Object System.Collections.Generic.List[object]
$AddResource = {
    param([string]$SourcePath, [string]$LogicalName)
    $EmbeddedResources.Add(
        [pscustomobject]@{
            path = [IO.Path]::GetFullPath($SourcePath)
            logical_name = $LogicalName
        }
    ) | Out-Null
}
& $AddResource $EditionResource 'EditionProfile.json'
& $AddResource $ProductConfigPath 'ProductConfig.json'
& $AddResource $TrustedResource 'TrustedPackages.json'
& $AddResource $EffectiveClientSourcesPath 'ClientSources.lock.json'
& $AddResource $EffectiveRuntimeSourcesPath 'RuntimeSources.lock.json'
& $AddResource $RoutingDomains 'LauncherRoutingDomains.json'
& $AddResource (Join-Path $EngineRoot 'foundation.ps1') (
    'FoundationEngine.foundation.ps1'
)
& $AddResource (Join-Path $EngineRoot 'engine-manifest.json') (
    'FoundationEngine.engine-manifest.json'
)
& $AddResource (Join-Path $EngineRoot 'VERSION') 'FoundationEngine.VERSION'
& $AddResource $EngineExtraIndexPath 'FoundationEngine.extra-files.json'
& $AddResource (Join-Path $RepositoryRoot 'APP_VERSION') (
    'FoundationInstaller.VERSION'
)
foreach ($EngineExtra in $EngineExtraIndex) {
    & $AddResource $EngineExtra.source_path $EngineExtra.resource_name
}
foreach ($Package in $AllPackages) {
    foreach ($Record in @(Get-PackageRecords $Package)) {
        $Name = [IO.Path]::GetFileName([string]$Record.relative_path)
        & $AddResource (
            Join-Path $Package.source_directory $Name
        ) $Record.resource_name
    }
}
if ($null -ne $ProviderEligibility) {
    & $AddResource $ProviderEligibility.path 'ProviderEligibilityEvidence.json'
}
$ResourcesProps = Join-Path $OutputRoot '.k7-resources.props'
$PropsLines = New-Object System.Collections.Generic.List[string]
$PropsLines.Add('<Project>') | Out-Null
$PropsLines.Add('  <ItemGroup>') | Out-Null
foreach ($Resource in $EmbeddedResources) {
    $EscapedPath = [Security.SecurityElement]::Escape($Resource.path)
    $EscapedName = [Security.SecurityElement]::Escape($Resource.logical_name)
    $PropsLines.Add(
        '    <EmbeddedResource Include="' + $EscapedPath +
        '" LogicalName="' + $EscapedName + '" />'
    ) | Out-Null
}
$PropsLines.Add('  </ItemGroup>') | Out-Null
$PropsLines.Add('</Project>') | Out-Null
[IO.File]::WriteAllText(
    $ResourcesProps,
    (($PropsLines -join "`n") + "`n"),
    $Encoding
)

$ProjectPath = Join-Path $RepositoryRoot 'src\gui\LlmFoundationInstaller.csproj'
$BuildScratch = Join-Path $OutputRoot '.msbuild'
$BuildIntermediate = (Join-Path $BuildScratch 'obj') + '\'
$BuildOutput = (Join-Path $BuildScratch 'bin') + '\'
$DotnetArguments = @(
    'build',
    $ProjectPath,
    '--nologo',
    '-c', 'Release',
    '-v', 'quiet',
    "-p:K7ResourcesProps=$ResourcesProps",
    "-p:K7ApplicationIcon=$ApplicationIcon",
    "-p:BaseIntermediateOutputPath=$BuildIntermediate",
    "-p:OutputPath=$BuildOutput"
)
if ($Edition -ceq 'Owner' -and $IsPublicUnsigned) {
    $DotnetArguments += '-p:K7ExtraDefines=K7_OWNER_DISTRIBUTION_ALLOWED'
}
if ($TestHooks) {
    # Тестовый хост: test-only CLI-точки (src/gui/InstallerTestHost.cs)
    # компилируются только с этим флагом; релизные сборки его не передают.
    $DotnetArguments += '-p:K7TestHooks=true'
}
$DotnetOutput = & dotnet @DotnetArguments 2>&1
$BuiltExecutable = Join-Path $BuildOutput 'LLMFoundationInstaller.exe'
if ($LASTEXITCODE -ne 0 -or
    -not (Test-Path -LiteralPath $BuiltExecutable -PathType Leaf)) {
    Write-Output ($DotnetOutput | Out-String)
    throw 'GUI compilation failed'
}
[IO.File]::Copy($BuiltExecutable, $Executable, $true)
Remove-Item -LiteralPath $BuildScratch -Recurse -Force
Remove-Item -LiteralPath $ResourcesProps -Force

Remove-Item -LiteralPath @(
    $TrustedResource,
    $EditionResource,
    $EngineExtraIndexPath,
    $ApplicationIcon
) -Force

$SignatureState = if ($DistributionMode -ceq 'InternalUnsigned') {
    'unsigned-internal'
} elseif ($IsPublicUnsigned) {
    'unsigned-public'
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

foreach ($Package in $AllPackages) {
    $Destination = Join-Path $OutputRoot "packages\$($Package.target)"
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    foreach ($Record in @(Get-PackageRecords $Package)) {
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
$FoundationReleaseManifest = if ($NeedsAcceptedFoundation) {
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
        package_acceptance = if ($DistributionMode -ceq 'InternalUnsigned') {
            'INTERNAL_UNSIGNED_TECHNICAL'
        } else { 'LOCAL_PREVIEW' }
        engine_version = $FoundationEngineVersion
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
    ($AvailableTargets -join ',') -ceq ($IncludedTargets -join ',')
)
$TechnicalReady = (
    $EditionTargetSetReady -and
    $RequiredReady -and
    ($null -ne $AcceptedFoundation -or
        $DistributionMode -ceq 'InternalUnsigned')
)
$InternalReady = (
    $DistributionMode -ceq 'InternalUnsigned' -and
    $TechnicalReady
)
$PublicUnsignedReady = (
    $IsPublicUnsigned -and
    [bool]$EditionContract.distribution_allowed -and
    $TechnicalReady -and
    $ProviderReady
)
$PublicSignedReady = (
    $DistributionMode -ceq 'PublicSigned' -and
    $SignatureState -ceq 'valid-authenticode' -and
    $TechnicalReady -and
    $ProviderReady
)
$ReadyCount = @(
    $RequiredTargets | Where-Object {
        $AcceptedTargets -ccontains $_
    }
).Count
$Verdicts = [ordered]@{
    FULL_RELEASE_CLAUDE = if (
        $AcceptedTargets -ccontains 'claude'
    ) { 'PASS' } else { 'NOT_PASS' }
    FULL_RELEASE_CODEX = if (
        $AcceptedTargets -ccontains 'codex'
    ) { 'PASS' } else { 'NOT_PASS' }
    FULL_RELEASE_OPENCODE = if (
        $AcceptedTargets -ccontains 'opencode'
    ) { 'PASS' } else { 'NOT_PASS' }
    TECHNICAL_READY = if ($TechnicalReady) { 'PASS' } else { 'NOT_PASS' }
    PROVIDER_LIVE = if ($ProviderReady) {
        'PASS'
    } else {
        'BLOCKED_PROVIDER_ELIGIBILITY'
    }
    PROGRAM_RELEASE = if ($TechnicalReady) { '3/3' } else { "$ReadyCount/3" }
    INTERNAL_UNSIGNED_RELEASE = if ($InternalReady) {
        'PASS'
    } else {
        'NOT_PASS'
    }
    PUBLIC_UNSIGNED_RELEASE = if ($PublicUnsignedReady) {
        'PASS'
    } else {
        'NOT_PASS'
    }
    PUBLIC_SIGNED_RELEASE = if (
        $DistributionMode -ceq 'InternalUnsigned' -or $IsPublicUnsigned
    ) {
        if ($ProviderReady) {
            'DEFERRED_UNSIGNED'
        } else {
            'BLOCKED_PROVIDER_ELIGIBILITY'
        }
    } elseif ($PublicSignedReady) {
        'PASS'
    } else {
        'NOT_PASS'
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
        'PublicUnsigned' { 'public_unsigned' }
        'PublicSigned' { 'public_signed' }
    }
    embedded_foundation = $true
    embedded_target_count = $AllPackages.Count
    signature = $SignatureState
    employee_release = ($Edition -ceq 'Employee' -and $IsPackagedRelease)
    employee_distribution_allowed = [bool](
        $Edition -ceq 'Employee' -and (
            $InternalReady -or $PublicUnsignedReady
        )
    )
    internal_distribution_allowed = [bool]$InternalReady
    public_distribution_allowed = [bool](
        $PublicUnsignedReady -or $PublicSignedReady
    )
    windows_warning_expected = (
        $DistributionMode -ceq 'InternalUnsigned' -or $IsPublicUnsigned
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
    runtime_sources = [ordered]@{
        schema_version = 1
        test_only = [bool]$RuntimeSources.test_only
        relative_path = 'runtime-sources.lock.json'
        resource_name = 'RuntimeSources.lock.json'
        sha256 = $RuntimeSourcesHash
        bytes = $RuntimeSourcesBytes
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
        'runtime-sources.lock.json' = [ordered]@{
            sha256 = $RuntimeSourcesHash
            bytes = $RuntimeSourcesBytes
        }
    }
}
foreach ($Package in $AllPackages) {
    foreach ($Record in @(Get-PackageRecords $Package)) {
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
