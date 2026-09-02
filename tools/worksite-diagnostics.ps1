# Диагностика комплекта на рабочей станции.
# Ничего не устанавливает и не меняет: только читает состояние и пишет отчёт
# рядом с собой. Права администратора не нужны.
#
# Fail-closed (ревью Codex 2026-09-02): любая ошибка вызова EXE, не-JSON в
# ответе или расхождение хеша EXE с манифестом комплекта попадает в отчёт
# как ERROR и даёт ненулевой код возврата. «Успех» печатается только когда
# все вызовы отработали. Версии целей берутся из каталога комплекта, а не
# зашиты в скрипт; план строится для каждой цели каталога (Claude, Codex,
# OpenCode). Отчёт привязан к SHA-256 EXE и версии комплекта.
[CmdletBinding()]
param(
    [string]$BundleRoot = $PSScriptRoot,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Continue'
$errors = New-Object System.Collections.Generic.List[string]

# --- EXE: любое издание и режим распространения --------------------------
$exe = Get-ChildItem -LiteralPath $BundleRoot -File -Filter 'K7-AI-Foundation-*.exe' -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $exe) {
    $fallback = Join-Path $BundleRoot 'LLMFoundationInstaller.exe'
    if (Test-Path -LiteralPath $fallback) { $exe = $fallback }
}
if (-not $exe) {
    $errors.Add('EXE комплекта не найден рядом со скриптом (K7-AI-Foundation-*.exe или LLMFoundationInstaller.exe)')
}

function Get-Sha256Hex {
    param([string]$Path)
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try { return ([System.BitConverter]::ToString($sha.ComputeHash($stream)) -replace '-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
    } finally { $stream.Dispose() }
}

function Invoke-BundleJson {
    # Вызов EXE с JSON-выводом. Ошибка не роняет отчёт, а фиксируется в нём.
    param([string]$Label, [string[]]$Arguments)
    if (-not $exe) { return $null }
    try {
        $raw = & $exe @Arguments 2>&1 | Out-String
        $code = $LASTEXITCODE
        if ([string]::IsNullOrWhiteSpace($raw)) {
            $errors.Add("$Label`: пустой ответ (код $code)")
            return $null
        }
        try {
            return ($raw | ConvertFrom-Json)
        } catch {
            $errors.Add("$Label`: ответ не JSON (код $code): " + $raw.Trim().Substring(0, [Math]::Min(160, $raw.Trim().Length)))
            return $null
        }
    } catch {
        $errors.Add("$Label`: вызов не удался: " + [string]$_)
        return $null
    }
}

# --- Привязка к комплекту --------------------------------------------------
$bundle = [ordered]@{
    root                    = $BundleRoot
    exe                     = $exe
    exe_sha256              = $null
    manifest_version        = $null
    manifest_edition        = $null
    manifest_mode           = $null
    exe_sha256_in_manifest  = $null
    exe_matches_manifest    = $null
}
if ($exe) {
    try { $bundle.exe_sha256 = Get-Sha256Hex $exe } catch { $errors.Add('не удалось посчитать SHA-256 EXE: ' + [string]$_) }
}
$manifestPath = Join-Path $BundleRoot 'bundle-manifest.json'
if (Test-Path -LiteralPath $manifestPath) {
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $bundle.manifest_version = $manifest.version
        $bundle.manifest_edition = $manifest.edition_id
        $bundle.manifest_mode    = $manifest.distribution_mode
        $expected = $manifest.products.installer.sha256
        $bundle.exe_sha256_in_manifest = $expected
        if ($expected -and $bundle.exe_sha256) {
            $bundle.exe_matches_manifest = ($expected -ieq $bundle.exe_sha256)
            if (-not $bundle.exe_matches_manifest) {
                $errors.Add('SHA-256 EXE не совпадает с bundle-manifest.json — комплект подменён или собран из разных частей')
            }
        }
    } catch {
        $errors.Add('bundle-manifest.json не прочитан: ' + [string]$_)
    }
} else {
    $errors.Add('bundle-manifest.json не найден рядом с EXE')
}

# --- Каталог: цели, пакеты, версии из комплекта -----------------------------
$catalog = Invoke-BundleJson 'catalog' @('--catalog-json')
$packages = @()
$catalogTargets = @()
if ($null -ne $catalog -and $null -ne $catalog.targets) {
    $catalogTargets = @($catalog.targets)
    $packages = @(
        $catalogTargets | ForEach-Object {
            [pscustomobject]@{
                id                = $_.id
                package           = $_.package_state
                supported_version = $_.supported_version
            }
        }
    )
}

# --- Цели запуска ------------------------------------------------------------
$launchTargets = @(
    'codex-cli', 'claude-code', 'codex-desktop', 'opencode-cli',
    'opencode-desktop', 'chrome-browser', 'vscode-codex'
)
$resolutions = foreach ($id in $launchTargets) {
    $value = Invoke-BundleJson "resolve $id" @('--resolve-launch-target-json', $HOME, $id)
    [pscustomobject]@{
        target   = $id
        status   = if ($null -eq $value) { 'ERROR' } else { $value.status }
        reason   = if ($null -eq $value) { $null } else { $value.reason }
        note     = if ($null -eq $value) { $null } else { $value.action }
        launched = if ($null -eq $value) { $null } else { $value.executable_path }
    }
}

# --- Планы установки — по каждой цели каталога, версия из каталога -----------
$plans = foreach ($t in $catalogTargets) {
    if (-not $t.supported_version) {
        $errors.Add("plan $($t.id): в каталоге нет supported_version")
        continue
    }
    $value = Invoke-BundleJson "plan $($t.id)" @(
        '--workflow-json', 'plan', $t.id, $HOME, $t.supported_version
    )
    $unknown = 0
    if ($null -ne $value -and $null -ne $value.unknown_entries) {
        $unknown = @($value.unknown_entries).Count
    }
    [pscustomobject]@{
        target          = $t.id
        version         = $t.supported_version
        status          = if ($null -eq $value) { 'ERROR' } else { $value.status }
        code            = if ($null -eq $value) { $null } else { $value.code }
        message         = if ($null -eq $value) { $null } else { $value.message }
        unknown_entries = $unknown
    }
}

# --- Junction в каталогах скиллов трёх клиентов -----------------------------
$junctions = @()
foreach ($rel in @('.agents\skills', '.claude\skills', '.config\opencode\skills')) {
    $root = Join-Path $HOME $rel
    if (-not (Test-Path -LiteralPath $root)) { continue }
    $junctions += @(
        Get-ChildItem -LiteralPath $root -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } |
            ForEach-Object { $rel + '\' + $_.Name }
    )
}

# --- Признак админ-прав: на рабочей станции их нет, и это норма --------------
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

$status = if ($errors.Count -eq 0) { 'OK' } else { 'ERROR' }
$report = [pscustomobject]@{
    schema_version  = 2
    status          = $status
    created_at      = (Get-Date).ToString('o')
    machine         = $env:COMPUTERNAME
    user_is_admin   = $isAdmin
    bundle          = [pscustomobject]$bundle
    install_enabled = if ($null -eq $catalog) { $null } else { $catalog.install_enabled }
    packages        = $packages
    launch_targets  = $resolutions
    install_plans   = $plans
    skill_junctions = $junctions
    errors          = @($errors)
}

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $BundleRoot 'Ответ с рабочего ПК'
}
if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}
$stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
$path = Join-Path $OutputDirectory "диагностика-$stamp.json"
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $path -Encoding UTF8

$resolved = @($resolutions | Where-Object { $_.status -eq 'RESOLVED' }).Count
Write-Host ''
Write-Host "Итог:                 $status"
Write-Host "Комплект:             $($bundle.manifest_edition) $($bundle.manifest_mode) v$($bundle.manifest_version)"
Write-Host "Целей запуска готово: $resolved из $($launchTargets.Count)"
Write-Host "Установка разрешена:  $($report.install_enabled)"
if ($junctions.Count -gt 0) {
    Write-Host "Ссылок-junction в скиллах: $($junctions.Count) — их нужно снять"
}
if ($errors.Count -gt 0) {
    Write-Host ''
    Write-Host 'Ошибки:'
    foreach ($e in $errors) { Write-Host "  - $e" }
}
Write-Host ''
Write-Host "Отчёт сохранён: $path"
Write-Host 'Файл уже лежит в папке синка — ничего копировать не нужно.'

if ($status -ne 'OK') { exit 1 }
