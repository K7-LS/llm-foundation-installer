# Диагностика комплекта на рабочей станции.
# Ничего не устанавливает и не меняет: только читает состояние и пишет отчёт
# рядом с собой. Права администратора не нужны.
[CmdletBinding()]
param(
    [string]$BundleRoot = $PSScriptRoot,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Continue'
$exe = Join-Path $BundleRoot 'K7-AI-Foundation-Employee-Preview.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    $exe = Join-Path $BundleRoot 'LLMFoundationInstaller.exe'
}

function Invoke-BundleJson {
    # Вызов EXE с JSON-выводом. Любая ошибка не роняет отчёт, а попадает в него.
    param([string[]]$Arguments)
    try {
        $raw = & $exe @Arguments 2>&1 | Out-String
        return ($raw | ConvertFrom-Json)
    } catch {
        return [pscustomobject]@{ error = [string]$_ }
    }
}

$targets = @(
    'codex-cli', 'claude-code', 'codex-desktop', 'opencode-cli',
    'opencode-desktop', 'chrome-browser', 'vscode-codex'
)

$resolutions = foreach ($id in $targets) {
    $value = Invoke-BundleJson @('--resolve-launch-target-json', $HOME, $id)
    [pscustomobject]@{
        target   = $id
        status   = $value.status
        reason   = $value.reason
        note     = $value.action          # строка про версию клиента, если есть
        launched = $value.executable_path
    }
}

# Junction в каталоге скиллов Codex: из-за них установка падала с UNSAFE_PATH.
$skillsRoot = Join-Path $HOME '.agents\skills'
$junctions = @()
if (Test-Path -LiteralPath $skillsRoot) {
    $junctions = @(
        Get-ChildItem -LiteralPath $skillsRoot -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } |
            ForEach-Object { $_.Name }
    )
}

# План установки: главный шаг, на котором раньше был замкнутый круг.
$plans = foreach ($pair in @(
        @{ target = 'codex'; version = '0.146.0-alpha.3.1' },
        @{ target = 'claude'; version = '2.1.218' })) {
    $value = Invoke-BundleJson @(
        '--workflow-json', 'plan', $pair.target, $HOME, $pair.version
    )
    $unknown = 0
    if ($null -ne $value.unknown_entries) {
        $unknown = @($value.unknown_entries).Count
    }
    [pscustomobject]@{
        target          = $pair.target
        status          = $value.status
        code            = $value.code
        message         = $value.message
        unknown_entries = $unknown
    }
}

$catalog = Invoke-BundleJson @('--catalog-json')
$packages = @()
if ($null -ne $catalog.targets) {
    $packages = @(
        $catalog.targets | ForEach-Object {
            [pscustomobject]@{ id = $_.id; package = $_.package_state }
        }
    )
}

# Признак админ-прав: на рабочей станции их нет, и это норма.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

$report = [pscustomobject]@{
    schema_version  = 1
    created_at      = (Get-Date).ToString('o')
    machine         = $env:COMPUTERNAME
    user_is_admin   = $isAdmin
    bundle          = $BundleRoot
    install_enabled = $catalog.install_enabled
    packages        = $packages
    launch_targets  = $resolutions
    install_plans   = $plans
    skill_junctions = $junctions
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
Write-Host "Целей запуска готово: $resolved из $($targets.Count)"
Write-Host "Установка разрешена:  $($catalog.install_enabled)"
if ($junctions.Count -gt 0) {
    Write-Host "Ссылок-junction в скиллах: $($junctions.Count) — их нужно снять"
}
Write-Host ''
Write-Host "Отчёт сохранён: $path"
Write-Host 'Файл уже лежит в папке синка — ничего копировать не нужно.'
