[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [string]$OfficeCliBinaryPath
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
$VersionPath = Join-Path $RepositoryRoot 'VERSION'
$SourcePath = Join-Path $RepositoryRoot 'src\foundation.ps1'
$SourceLockPath = Join-Path $RepositoryRoot 'client-sources.lock.json'
$ShimBuildPath = Join-Path $RepositoryRoot 'tools\build-officecli-shim.ps1'
$ExporterBuildPath = Join-Path $RepositoryRoot 'tools\build-officecli-pdf-exporter.ps1'
$CsvAdapterSourcePath = Join-Path $RepositoryRoot 'tools\officecli_csv_batch.py'
$PolicySourcePath = Join-Path $RepositoryRoot 'support\officecli-command-policy.json'

if (Test-Path -LiteralPath $OutputRoot) {
    throw 'OutputRoot must not exist'
}
if (-not (Test-Path -LiteralPath $VersionPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $SourcePath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $SourceLockPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $ShimBuildPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $ExporterBuildPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $CsvAdapterSourcePath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $PolicySourcePath -PathType Leaf)) {
    throw 'Foundation source is incomplete'
}

$Version = ([IO.File]::ReadAllText($VersionPath)).Trim()
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw 'Foundation version is invalid'
}

. (Join-Path $PSScriptRoot '_common.ps1')

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
    "apply",
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

$SourceLock = Get-Content -LiteralPath $SourceLockPath -Raw -Encoding UTF8 |
    ConvertFrom-Json -ErrorAction Stop
$OfficeCli = @($SourceLock.clients | Where-Object {
    [string]$_.id -ceq 'officecli'
})
if ($OfficeCli.Count -ne 1 -or
    [string]$OfficeCli[0].sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]$OfficeCli[0].version -notmatch '^\d+\.\d+\.\d+$') {
    throw 'OfficeCLI source record is invalid'
}
$SharedRoot = Join-Path $OutputRoot 'shared-tools\officecli'
[IO.Directory]::CreateDirectory($SharedRoot) | Out-Null
$PrivatePath = Join-Path $SharedRoot 'officecli.exe'
$ExpectedSha = [string]$OfficeCli[0].sha256
# Каждая сборка тянула officecli из сети: на нестабильном канале это валило
# и локальные прогоны, и CI. Порядок теперь: явный пин -> локальный кеш по
# SHA -> сеть с повторами; загруженное кладётся в кеш. Проверка хеша
# обязательна на любом пути, так что кеш не ослабляет целостность.
$CacheRoot = [Environment]::GetEnvironmentVariable(
    'K7_BUILD_CACHE',
    [EnvironmentVariableTarget]::Process
)
if ([string]::IsNullOrWhiteSpace($CacheRoot)) {
    $CacheRoot = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.k7-build-cache'
}
$CachedBinary = Join-Path (Join-Path $CacheRoot 'officecli') ($ExpectedSha + '.exe')
if (-not [string]::IsNullOrWhiteSpace($OfficeCliBinaryPath)) {
    $PinnedBinary = [IO.Path]::GetFullPath($OfficeCliBinaryPath)
    if (-not (Test-Path -LiteralPath $PinnedBinary -PathType Leaf)) {
        throw 'OfficeCLI binary cache is missing'
    }
    [IO.File]::Copy($PinnedBinary, $PrivatePath, $false)
} elseif (Test-Path -LiteralPath $CachedBinary -PathType Leaf) {
    [IO.File]::Copy($CachedBinary, $PrivatePath, $false)
    Write-Host 'OfficeCLI restored from the local build cache.'
} else {
    $Attempt = 0
    $Downloaded = $false
    while (-not $Downloaded) {
        $Attempt++
        try {
            Invoke-WebRequest -UseBasicParsing `
                -Uri ([string]$OfficeCli[0].url) -OutFile $PrivatePath
            $Downloaded = $true
        }
        catch {
            if ($Attempt -ge 3) { throw }
            Write-Host (
                'OfficeCLI download attempt ' + $Attempt +
                ' failed, retrying: ' + $_.Exception.Message
            )
            Start-Sleep -Seconds (5 * $Attempt)
        }
    }
}
if ((Get-Sha256 $PrivatePath) -cne $ExpectedSha) {
    throw 'OfficeCLI source hash differs'
}
if (-not (Test-Path -LiteralPath $CachedBinary -PathType Leaf)) {
    # Кеш пополняется только проверенным по хешу файлом.
    [IO.Directory]::CreateDirectory((Split-Path -Parent $CachedBinary)) | Out-Null
    [IO.File]::Copy($PrivatePath, $CachedBinary, $false)
}
$ShimPath = Join-Path $SharedRoot 'officecli-shim.exe'
& $ShimBuildPath -OutputPath $ShimPath
if ($LASTEXITCODE -ne 0) { throw 'OfficeCLI shim build failed' }
$PolicyPath = Join-Path $SharedRoot 'officecli-command-policy.json'
[IO.File]::Copy($PolicySourcePath, $PolicyPath, $false)
$ExporterPath = Join-Path $SharedRoot 'k7-officecli-pdf.exe'
& $ExporterBuildPath -OutputPath $ExporterPath
if ($LASTEXITCODE -ne 0) { throw 'OfficeCLI PDF exporter build failed' }
$CsvAdapterPath = Join-Path $SharedRoot 'officecli_csv_batch.py'
[IO.File]::Copy($CsvAdapterSourcePath, $CsvAdapterPath, $false)

$SharedLock = [ordered]@{
    schema_version = 1
    tools = @(
        [ordered]@{
            id = 'officecli'
            version = [string]$OfficeCli[0].version
            compatibility_epoch = 'officecli-managed-v1'
            source_url = [string]$OfficeCli[0].url
            private_exe = [ordered]@{
                path = 'shared-tools/officecli/officecli.exe'
                sha256 = Get-Sha256 $PrivatePath
                bytes = (Get-Item -LiteralPath $PrivatePath).Length
            }
            shim = [ordered]@{
                path = 'shared-tools/officecli/officecli-shim.exe'
                sha256 = Get-Sha256 $ShimPath
                bytes = (Get-Item -LiteralPath $ShimPath).Length
            }
            policy = [ordered]@{
                path = 'shared-tools/officecli/officecli-command-policy.json'
                sha256 = Get-Sha256 $PolicyPath
                bytes = (Get-Item -LiteralPath $PolicyPath).Length
            }
            pdf_exporter = [ordered]@{
                path = 'shared-tools/officecli/k7-officecli-pdf.exe'
                sha256 = Get-Sha256 $ExporterPath
                bytes = (Get-Item -LiteralPath $ExporterPath).Length
            }
            csv_batch_adapter = [ordered]@{
                path = 'shared-tools/officecli/officecli_csv_batch.py'
                sha256 = Get-Sha256 $CsvAdapterPath
                bytes = (Get-Item -LiteralPath $CsvAdapterPath).Length
            }
            environment = [ordered]@{
                OFFICECLI_NO_AUTO_INSTALL = '1'
                OFFICECLI_SKIP_UPDATE = '1'
            }
        }
    )
}
[IO.File]::WriteAllText(
    (Join-Path $OutputRoot 'shared-tools.lock.json'),
    (($SharedLock | ConvertTo-Json -Depth 10 -Compress) + "`n"),
    $Encoding
)
Write-Output "Foundation engine $Version built."
