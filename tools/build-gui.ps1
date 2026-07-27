[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [string]$PackageRoot,
    [switch]$EmployeeRelease,
    [string]$SigningCertificateThumbprint,
    [string]$TimestampServer = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
if (-not [string]::IsNullOrWhiteSpace($PackageRoot)) {
    $PackageRoot = [IO.Path]::GetFullPath($PackageRoot)
    if (-not (Test-Path -LiteralPath $PackageRoot -PathType Container)) {
        throw 'PackageRoot does not exist'
    }
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
    $ExpectedHash = [string]$Record.sha256
    $ExpectedBytes = [long]$Record.bytes
    if ($ExpectedHash -notmatch '^[a-f0-9]{64}$' -or
        $ExpectedBytes -lt 0 -or
        (Get-Sha256 $Path) -cne $ExpectedHash -or
        (Get-Item -LiteralPath $Path).Length -ne $ExpectedBytes) {
        throw "Package acceptance $Label binding mismatch"
    }
    return $Path
}

function Assert-CodexReleaseBinding {
    param(
        [Parameter(Mandatory = $true)]$Evidence,
        [Parameter(Mandatory = $true)]$Release
    )
    if ($null -eq $Evidence.release_binding) {
        throw 'Codex acceptance evidence release binding is missing'
    }
    foreach ($Field in @(
        'target',
        'version',
        'tag',
        'asset',
        'package_manifest_sha256',
        'components_lock_sha256',
        'source',
        'foundation_engine_version',
        'foundation_engine_manifest_sha256'
    )) {
        $EvidenceValue = $Evidence.release_binding.$Field |
            ConvertTo-Json -Depth 30 -Compress
        $ReleaseValue = $Release.$Field |
            ConvertTo-Json -Depth 30 -Compress
        if ($EvidenceValue -cne $ReleaseValue) {
            throw "Codex acceptance release binding differs: $Field"
        }
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

        try {
            $Release = Get-Content -LiteralPath $ReleasePath -Raw |
                ConvertFrom-Json
            $Evidence = Get-Content -LiteralPath $EvidencePath -Raw |
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
        if ($Target -ceq 'codex') {
            $VerdictProperty = $Evidence.PSObject.Properties[
                [string]$Definition.verdict
            ]
            $IntegrityProperty = $Evidence.PSObject.Properties[
                'RELEASE_INTEGRITY'
            ]
            Assert-CodexReleaseBinding $Evidence $Release
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
                [string]$Evidence.asset_sha256 -ceq (
                    [string]$Acceptance.asset.sha256
                ) -and
                [string]$Evidence.release_manifest_sha256 -ceq (
                    Get-Sha256 $ReleasePath
                )
            )
        }
        if ([int]$Evidence.schema_version -ne 1 -or
            [string]$Evidence.target -cne $Target -or
            $null -eq $VerdictProperty -or
            [string]$VerdictProperty.Value -cne 'PASS' -or
            $null -eq $IntegrityProperty -or
            [string]$IntegrityProperty.Value -cne 'PASS' -or
            -not $EvidenceBindingValid) {
            throw "Package acceptance evidence is not PASS for target: $Target"
        }

        $AcceptanceHash = Get-Sha256 $AcceptancePath
        $Rows += [ordered]@{
            target = $Target
            client_id = [string]$Acceptance.client.id
            supported_version = [string]$Acceptance.client.supported_version
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

$Version = ([IO.File]::ReadAllText(
    (Join-Path $RepositoryRoot 'VERSION')
)).Trim()
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw 'Foundation version is invalid'
}

$WindowsRoot = [Environment]::GetFolderPath('Windows')
$Compiler = Join-Path $WindowsRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $Compiler -PathType Leaf)) {
    throw 'The Windows .NET Framework compiler is not available'
}

$AssemblyRoots = @(
    (Join-Path $WindowsRoot 'Microsoft.NET\assembly\GAC_MSIL'),
    (Join-Path $WindowsRoot 'Microsoft.NET\assembly\GAC_64')
)
$References = @(
    (Get-AssemblyPath $AssemblyRoots 'PresentationFramework.dll'),
    (Get-AssemblyPath $AssemblyRoots 'PresentationCore.dll'),
    (Get-AssemblyPath $AssemblyRoots 'WindowsBase.dll'),
    (Get-AssemblyPath $AssemblyRoots 'System.Xaml.dll')
)

[IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
$EngineRoot = Join-Path $OutputRoot 'engine'
& (Join-Path $RepositoryRoot 'tools\build-engine.ps1') -OutputRoot $EngineRoot
if (-not $?) {
    throw 'Foundation engine build failed'
}

$AcceptedPackages = @(Read-AcceptedPackages $PackageRoot)
if ($EmployeeRelease) {
    $AcceptedTargets = @($AcceptedPackages.target | Sort-Object)
    if (($AcceptedTargets -join ',') -cne 'claude,codex,opencode') {
        throw (
            'Employee release requires accepted Codex, Claude, and ' +
            'OpenCode packages'
        )
    }
    if ([string]::IsNullOrWhiteSpace($SigningCertificateThumbprint)) {
        throw 'Employee release requires a code-signing certificate'
    }
}
$TrustedIndex = [ordered]@{
    schema_version = 1
    packages = @(
        $AcceptedPackages | ForEach-Object {
            [ordered]@{
                target = $_.target
                client_id = $_.client_id
                supported_version = $_.supported_version
                asset = $_.asset
                release_manifest = $_.release_manifest
                acceptance_evidence = $_.acceptance_evidence
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
$ConnectionSource = Join-Path $RepositoryRoot 'src\gui\ConnectionProfile.cs'
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
    '/utf8output',
    "/out:$Executable",
    "/win32manifest:$ApplicationManifest",
    "/win32icon:$ApplicationIcon",
    "/resource:$View,InstallerView.xaml",
    "/resource:$TrustedResource,TrustedPackages.json",
    "/resource:$(Join-Path $EngineRoot 'foundation.ps1'),FoundationEngine.foundation.ps1",
    "/resource:$(Join-Path $EngineRoot 'engine-manifest.json'),FoundationEngine.engine-manifest.json",
    "/resource:$(Join-Path $EngineRoot 'VERSION'),FoundationEngine.VERSION",
    "/resource:$(Join-Path $RepositoryRoot 'VERSION'),FoundationInstaller.VERSION"
)
$CompilerArguments += @(
    foreach ($Package in $AcceptedPackages) {
        foreach ($Record in @(
            $Package.asset,
            $Package.release_manifest,
            $Package.acceptance_evidence,
            $Package.package_acceptance
        )) {
            $Name = [IO.Path]::GetFileName([string]$Record.relative_path)
            $SourcePath = Join-Path $Package.source_directory $Name
            "/resource:$SourcePath,$($Record.resource_name)"
        }
    }
)
$CompilerArguments += $References | ForEach-Object { "/reference:$_" }
$CompilerArguments += @($Source, $ConnectionSource)

& $Compiler @CompilerArguments
if ($LASTEXITCODE -ne 0 -or
    -not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw 'GUI compilation failed'
}

Remove-Item -LiteralPath @($TrustedResource, $ApplicationIcon) -Force

$SignatureState = 'unsigned-preview'
if (-not [string]::IsNullOrWhiteSpace($SigningCertificateThumbprint)) {
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
        $Package.package_acceptance
    )) {
        $Name = [IO.Path]::GetFileName([string]$Record.relative_path)
        Copy-Item -LiteralPath (
            Join-Path $Package.source_directory $Name
        ) -Destination (Join-Path $Destination $Name)
    }
}

[IO.File]::WriteAllText(
    (Join-Path $OutputRoot 'VERSION'),
    $Version + "`n",
    $Encoding
)

$Manifest = [ordered]@{
    schema_version = 1
    app_id = 'llm-foundation-installer'
    version = $Version
    network = 'user-initiated-only'
    automatic_network = $false
    telemetry = $false
    reverse_flow = $false
    distribution = 'single-executable'
    embedded_foundation = $true
    embedded_target_count = $AcceptedPackages.Count
    signature = $SignatureState
    employee_release = [bool]$EmployeeRelease
    employee_distribution_allowed = (
        [bool]$EmployeeRelease -and
        $SignatureState -ceq 'valid-authenticode'
    )
    targets = @('codex', 'claude', 'opencode')
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
    }
}
foreach ($Package in $AcceptedPackages) {
    foreach ($Record in @(
        $Package.asset,
        $Package.release_manifest,
        $Package.acceptance_evidence,
        $Package.package_acceptance
    )) {
        $Relative = [string]$Record.relative_path
        $Manifest.artifacts[$Relative] = [ordered]@{
            sha256 = [string]$Record.sha256
            bytes = [long]$Record.bytes
        }
    }
}
$ManifestJson = (ConvertTo-Json $Manifest -Depth 8) + "`n"
[IO.File]::WriteAllText(
    (Join-Path $OutputRoot 'bundle-manifest.json'),
    $ManifestJson,
    $Encoding
)

Write-Output "LLM Foundation GUI $Version built at $OutputRoot"
