[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [ValidateRange(1, 7)]
    [int]$ValidityDays = 7,
    [switch]$ConfirmEmployeeLocationEligibility,
    [switch]$ConfirmOrganizationEligibility,
    [switch]$ConfirmIndividualAccounts,
    [switch]$ConfirmNoRegionOrBanBypass,
    [switch]$ConfirmNoUnattendedConsumerAutomation
)

$ErrorActionPreference = 'Stop'

if (-not (
    $ConfirmEmployeeLocationEligibility.IsPresent -and
    $ConfirmOrganizationEligibility.IsPresent -and
    $ConfirmIndividualAccounts.IsPresent -and
    $ConfirmNoRegionOrBanBypass.IsPresent -and
    $ConfirmNoUnattendedConsumerAutomation.IsPresent
)) {
    throw 'All provider eligibility confirmations are required'
}

$FullPath = [IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $FullPath) {
    throw 'Provider eligibility evidence output already exists'
}
$Parent = [IO.Path]::GetDirectoryName($FullPath)
if ([string]::IsNullOrWhiteSpace($Parent) -or
    -not (Test-Path -LiteralPath $Parent -PathType Container)) {
    throw 'Provider eligibility evidence parent directory does not exist'
}
$ParentItem = Get-Item -LiteralPath $Parent -Force
if (($ParentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'Provider eligibility evidence parent cannot be a reparse point'
}

$ReviewedAt = [DateTimeOffset]::UtcNow
$ExpiresAt = $ReviewedAt.AddDays($ValidityDays)
$Format = "yyyy-MM-dd'T'HH:mm:ss'Z'"
$Culture = [Globalization.CultureInfo]::InvariantCulture
$Evidence = [ordered]@{
    schema_version = 1
    reviewed_at_utc = $ReviewedAt.ToString($Format, $Culture)
    expires_at_utc = $ExpiresAt.ToString($Format, $Culture)
    sources = [ordered]@{
        supported_regions = 'https://www.anthropic.com/supported-countries'
        usage_policy = 'https://www.anthropic.com/legal/aup'
        consumer_terms = 'https://www.anthropic.com/legal/consumer-terms'
        safeguards_appeals = (
            'https://support.claude.com/en/articles/' +
            '8241253-safeguards-warnings-and-appeals'
        )
    }
    claude = [ordered]@{
        employee_location_eligibility_verified = $true
        organization_eligibility_verified = $true
        individual_accounts_only = $true
        transport_not_used_for_region_or_ban_bypass = $true
        unattended_consumer_automation = $false
    }
}

$Encoding = New-Object Text.UTF8Encoding($false)
$Bytes = $Encoding.GetBytes(
    ((ConvertTo-Json $Evidence -Depth 8) + "`n")
)
$Stream = [IO.File]::Open(
    $FullPath,
    [IO.FileMode]::CreateNew,
    [IO.FileAccess]::Write,
    [IO.FileShare]::None
)
try {
    $Stream.Write($Bytes, 0, $Bytes.Length)
    $Stream.Flush($true)
} finally {
    $Stream.Dispose()
}

Write-Output $FullPath
