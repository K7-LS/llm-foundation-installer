<#
.SYNOPSIS
Запускает выбранное AI-приложение через sing-box и обычный HTTP upstream-прокси.

.DESCRIPTION
Поднимает временный локальный mixed-прокси sing-box, включает пользовательский
системный прокси Windows, передаёт HTTP_PROXY/HTTPS_PROXY дочернему процессу и
после завершения приложения восстанавливает исходное состояние.

Пароль хранится только в DPAPI-хранилище текущего Windows-пользователя.
#>

[CmdletBinding()]
param(
    [ValidateSet(
        "",
        "ChatGPT",
        "ClaudeDesktop",
        "OpenCode",
        "CodexCLI",
        "ClaudeCLI",
        "VSCodeCodex",
        "VSCodeClaude"
    )]
    [string]$Mode = "",
    [switch]$Reset,
    [switch]$ResetPassword,
    [switch]$SelfTest,
    [string]$SingBoxPathForTest = ""
)

$ErrorActionPreference = "Stop"
$script:Transport = "HTTP"
$script:StateStem = ".ai-singbox-http"
$script:ConfigPath = Join-Path $env:USERPROFILE "$($script:StateStem).json"
$script:CredPath = Join-Path $env:USERPROFILE "$($script:StateStem).cred"
$script:DownloadUrl = "https://github.com/SagerNet/sing-box/releases/download/v1.13.14/sing-box-1.13.14-windows-amd64.zip"
$script:InternetSettingsSubKey = "Software\Microsoft\Windows\CurrentVersion\Internet Settings"
$script:ProxyValueNames = @("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL")
$script:AllModes = @(
    "ChatGPT",
    "ClaudeDesktop",
    "OpenCode",
    "CodexCLI",
    "ClaudeCLI",
    "VSCodeCodex",
    "VSCodeClaude"
)

function Write-Step {
    param([string]$Message)
    Write-Host "[AI proxy] $Message" -ForegroundColor Cyan
}

function Get-ScriptDirectory {
    if ($PSScriptRoot) {
        return $PSScriptRoot
    }
    return (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

function Remove-StateFiles {
    param([switch]$PasswordOnly)

    if (Test-Path -LiteralPath $script:CredPath) {
        Remove-Item -LiteralPath $script:CredPath -Force
        Write-Host "Удалён сохранённый DPAPI-пароль: $($script:CredPath)" -ForegroundColor Yellow
    }

    if (-not $PasswordOnly -and (Test-Path -LiteralPath $script:ConfigPath)) {
        Remove-Item -LiteralPath $script:ConfigPath -Force
        Write-Host "Удалена конфигурация: $($script:ConfigPath)" -ForegroundColor Yellow
    }
}

function Resolve-SingBoxPath {
    param([string]$SavedPath)

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($SavedPath)) {
        $candidates.Add($SavedPath)
    }

    $scriptDirectory = Get-ScriptDirectory
    $candidates.Add((Join-Path $scriptDirectory "sing-box.exe"))

    Get-ChildItem -LiteralPath $scriptDirectory -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "sing-box*" } |
        ForEach-Object {
            $candidates.Add((Join-Path $_.FullName "sing-box.exe"))
        }

    $desktop = [Environment]::GetFolderPath("Desktop")
    if (-not [string]::IsNullOrWhiteSpace($desktop) -and (Test-Path -LiteralPath $desktop)) {
        Get-ChildItem -LiteralPath $desktop -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "sing-box*windows-amd64*" } |
            Sort-Object LastWriteTime -Descending |
            ForEach-Object {
                $candidates.Add((Join-Path $_.FullName "sing-box.exe"))
            }
    }

    $command = Get-Command "sing-box" -ErrorAction SilentlyContinue
    if ($command -and $command.Path) {
        $candidates.Add($command.Path)
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and
            (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    Write-Host ""
    Write-Host "sing-box.exe не найден." -ForegroundColor Yellow
    Write-Host "Скачайте и распакуйте официальный архив:" -ForegroundColor Yellow
    Write-Host $script:DownloadUrl -ForegroundColor Cyan
    $openDownload = Read-Host "Открыть ссылку в браузере? (y/N)"
    if ($openDownload -match "^(?i)y|yes|д|да$") {
        Start-Process $script:DownloadUrl
    }

    $manualPath = Read-Host "Укажите полный путь к sing-box.exe"
    $manualPath = $manualPath.Trim().Trim('"')
    if (-not (Test-Path -LiteralPath $manualPath -PathType Leaf)) {
        throw "sing-box.exe не найден по указанному пути: $manualPath"
    }

    return (Resolve-Path -LiteralPath $manualPath).Path
}

function Read-ProxyConfiguration {
    $saved = $null
    if (Test-Path -LiteralPath $script:ConfigPath) {
        try {
            $saved = Get-Content -LiteralPath $script:ConfigPath -Raw | ConvertFrom-Json
        }
        catch {
            Write-Host "Сохранённая конфигурация повреждена и будет запрошена заново." -ForegroundColor Yellow
            $saved = $null
        }
    }

    $server = if ($saved -and $saved.server) { [string]$saved.server } else { Read-Host "Адрес HTTP-прокси" }
    if ([string]::IsNullOrWhiteSpace($server)) {
        throw "Адрес прокси не указан."
    }

    $portText = if ($saved -and $saved.port) { [string]$saved.port } else { Read-Host "Порт HTTP-прокси" }
    $port = 0
    if (-not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw "Некорректный порт прокси: $portText"
    }

    $username = if ($saved -and $saved.username) { [string]$saved.username } else { Read-Host "Имя пользователя прокси" }
    if ([string]::IsNullOrWhiteSpace($username)) {
        throw "Имя пользователя прокси не указано."
    }

    $savedSingBox = if ($saved -and $saved.singBoxPath) { [string]$saved.singBoxPath } else { "" }
    $singBoxPath = Resolve-SingBoxPath -SavedPath $savedSingBox

    $configuration = [pscustomobject]@{
        server = $server.Trim()
        port = $port
        username = $username
        singBoxPath = $singBoxPath
    }

    $configuration |
        ConvertTo-Json |
        Set-Content -LiteralPath $script:ConfigPath -Encoding UTF8

    return $configuration
}

function Read-ProxyPassword {
    if (Test-Path -LiteralPath $script:CredPath) {
        try {
            return ((Get-Content -LiteralPath $script:CredPath -Raw).Trim() |
                ConvertTo-SecureString -ErrorAction Stop)
        }
        catch {
            Write-Host "DPAPI-пароль не расшифровывается. Требуется новый ввод." -ForegroundColor Yellow
        }
    }

    $securePassword = Read-Host "Пароль прокси (будет сохранён через DPAPI)" -AsSecureString
    if (-not $securePassword -or $securePassword.Length -eq 0) {
        throw "Пустой пароль не допускается."
    }

    $securePassword |
        ConvertFrom-SecureString |
        Set-Content -LiteralPath $script:CredPath -Encoding ASCII

    return $securePassword
}

function Convert-SecureStringToPlainText {
    param([Security.SecureString]$SecureValue)

    $pointer = [IntPtr]::Zero
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    }
}

function Get-ModeProcessNames {
    param([string]$SelectedMode)

    switch ($SelectedMode) {
        "ChatGPT"       { return @("ChatGPT.exe", "OpenAI.exe") }
        "ClaudeDesktop" { return @("claude.exe", "Claude.exe") }
        "OpenCode"      { return @("OpenCode.exe", "opencode.exe") }
        "CodexCLI"      { return @("codex.exe", "node.exe") }
        "ClaudeCLI"     { return @("claude.exe", "node.exe") }
        "VSCodeCodex"   { return @("Code.exe") }
        "VSCodeClaude"  { return @("Code.exe") }
        default         { throw "Неизвестный режим: $SelectedMode" }
    }
}

function New-SingBoxConfig {
    param(
        [pscustomobject]$ProxyConfig,
        [string]$PlainPassword,
        [int]$ListenPort,
        [string[]]$ProcessNames
    )

    $proxyOutbound = [ordered]@{
        type = "http"
        tag = "upstream-proxy"
        server = $ProxyConfig.server
        server_port = [int]$ProxyConfig.port
        username = $ProxyConfig.username
        password = $PlainPassword
    }

    return [ordered]@{
        log = [ordered]@{
            level = "info"
            timestamp = $true
        }
        inbounds = @(
            [ordered]@{
                type = "mixed"
                tag = "local-mixed"
                listen = "127.0.0.1"
                listen_port = $ListenPort
                set_system_proxy = $true
            }
        )
        outbounds = @(
            $proxyOutbound,
            [ordered]@{
                type = "direct"
                tag = "direct"
            }
        )
        route = [ordered]@{
            auto_detect_interface = $true
            rules = @(
                [ordered]@{
                    ip_is_private = $true
                    action = "route"
                    outbound = "direct"
                },
                [ordered]@{
                    process_name = $ProcessNames
                    action = "route"
                    outbound = "upstream-proxy"
                },
                [ordered]@{
                    domain_suffix = @(
                        "chatgpt.com",
                        "openai.com",
                        "oaistatic.com",
                        "oaiusercontent.com",
                        "oaistatsig.com",
                        "openaimerge.com",
                        "claude.ai",
                        "claude.com",
                        "anthropic.com",
                        "claudeusercontent.com",
                        "workos.com",
                        "workoscdn.com",
                        "intercom.io",
                        "intercomcdn.com",
                        "sentry.io",
                        "datadoghq.com",
                        "sendgrid.net",
                        "statsig.com",
                        "github.com",
                        "githubusercontent.com",
                        "npmjs.org",
                        "models.dev",
                        "opencode.ai"
                    )
                    action = "route"
                    outbound = "upstream-proxy"
                },
                [ordered]@{
                    domain = @(
                        "accounts.google.com",
                        "challenges.cloudflare.com",
                        "js.stripe.com",
                        "workos.imgix.net"
                    )
                    action = "route"
                    outbound = "upstream-proxy"
                }
            )
            final = "direct"
        }
    }
}

function Get-FreeListenPort {
    foreach ($port in 18082..18120) {
        $listener = $null
        try {
            $listener = New-Object Net.Sockets.TcpListener(
                [Net.IPAddress]::Loopback,
                $port
            )
            $listener.Start()
            return $port
        }
        catch {
        }
        finally {
            if ($listener) {
                $listener.Stop()
            }
        }
    }
    throw "Не найден свободный локальный порт в диапазоне 18082-18120."
}

function Get-ProxySnapshot {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
        $script:InternetSettingsSubKey,
        $false
    )
    if (-not $key) {
        throw "Не найден раздел HKCU Internet Settings."
    }

    try {
        $valueNames = @($key.GetValueNames())
        $entries = @()
        foreach ($name in $script:ProxyValueNames) {
            $exists = $valueNames -contains $name
            $entries += [pscustomobject]@{
                Name = $name
                Exists = $exists
                Value = if ($exists) { $key.GetValue($name, $null, "DoNotExpandEnvironmentNames") } else { $null }
                Kind = if ($exists) { $key.GetValueKind($name) } else { $null }
            }
        }
        return $entries
    }
    finally {
        $key.Close()
    }
}

function Initialize-WinInetNotifier {
    if (-not ("AiProxy.WinInetNative" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace AiProxy {
    public static class WinInetNative {
        [DllImport("wininet.dll", SetLastError = true)]
        public static extern bool InternetSetOption(
            IntPtr hInternet,
            int dwOption,
            IntPtr lpBuffer,
            int dwBufferLength
        );
    }
}
"@
    }
}

function Notify-ProxySettingsChanged {
    Initialize-WinInetNotifier
    [void][AiProxy.WinInetNative]::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0)
    [void][AiProxy.WinInetNative]::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0)
}

function Restore-ProxySnapshot {
    param([object[]]$Snapshot)

    if (-not $Snapshot) {
        return
    }

    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
        $script:InternetSettingsSubKey,
        $true
    )
    if (-not $key) {
        throw "Не удалось открыть HKCU Internet Settings для восстановления."
    }

    try {
        foreach ($entry in $Snapshot) {
            if ($entry.Exists) {
                $key.SetValue($entry.Name, $entry.Value, $entry.Kind)
            }
            else {
                $key.DeleteValue($entry.Name, $false)
            }
        }
    }
    finally {
        $key.Close()
    }

    Notify-ProxySettingsChanged

    if (-not (Test-ProxySnapshotRestored -Snapshot $Snapshot)) {
        throw "Проверка после восстановления системного прокси не пройдена."
    }
}

function Test-ProxySnapshotRestored {
    param([object[]]$Snapshot)

    $actualSnapshot = Get-ProxySnapshot
    foreach ($expected in $Snapshot) {
        $actual = $actualSnapshot |
            Where-Object { $_.Name -eq $expected.Name } |
            Select-Object -First 1

        if (-not $actual -or $actual.Exists -ne $expected.Exists) {
            return $false
        }
        if ($expected.Exists) {
            if ($actual.Kind -ne $expected.Kind) {
                return $false
            }
            if (-not [object]::Equals($actual.Value, $expected.Value)) {
                return $false
            }
        }
    }
    return $true
}

function Get-EnvironmentSnapshot {
    $names = @(
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy"
    )
    $snapshot = @{}
    foreach ($name in $names) {
        $snapshot[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    }
    return $snapshot
}

function Restore-EnvironmentSnapshot {
    param([hashtable]$Snapshot)

    if (-not $Snapshot) {
        return
    }
    foreach ($name in $Snapshot.Keys) {
        [Environment]::SetEnvironmentVariable($name, $Snapshot[$name], "Process")
    }
}

function Set-LocalProxyEnvironment {
    param([int]$ListenPort)

    $localProxy = "http://127.0.0.1:$ListenPort"
    $env:HTTP_PROXY = $localProxy
    $env:HTTPS_PROXY = $localProxy
    $env:http_proxy = $localProxy
    $env:https_proxy = $localProxy
    $env:NO_PROXY = "localhost,127.0.0.1,::1"
    $env:no_proxy = $env:NO_PROXY
    Remove-Item Env:ALL_PROXY -ErrorAction SilentlyContinue
    Remove-Item Env:all_proxy -ErrorAction SilentlyContinue
}

function Select-LaunchMode {
    Write-Host ""
    Write-Host "Выберите режим запуска:" -ForegroundColor Cyan
    Write-Host "  1) ChatGPT Desktop"
    Write-Host "  2) Claude Desktop"
    Write-Host "  3) OpenCode"
    Write-Host "  4) Codex CLI"
    Write-Host "  5) Claude CLI"
    Write-Host "  6) VS Code - Codex"
    Write-Host "  7) VS Code - Claude"

    $map = @{
        "1" = "ChatGPT"
        "2" = "ClaudeDesktop"
        "3" = "OpenCode"
        "4" = "CodexCLI"
        "5" = "ClaudeCLI"
        "6" = "VSCodeCodex"
        "7" = "VSCodeClaude"
    }

    do {
        $choice = Read-Host "Режим (1-7)"
    } while (-not $map.ContainsKey($choice))

    return $map[$choice]
}

function Find-ChatGPTExecutable {
    $package = Get-AppxPackage |
        Where-Object {
            $_.Name -match "(?i)ChatGPT|OpenAI" -or
            $_.PackageFullName -match "(?i)ChatGPT|OpenAI"
        } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_.InstallLocation) } |
        Sort-Object Version -Descending |
        Select-Object -First 1

    if (-not $package) {
        throw "Пакет ChatGPT Desktop не найден."
    }

    $manifestPath = Join-Path $package.InstallLocation "AppxManifest.xml"
    [xml]$manifest = Get-Content -LiteralPath $manifestPath -Raw
    $nodes = @($manifest.SelectNodes(
        "/*[local-name()='Package']/*[local-name()='Applications']/*[local-name()='Application']"
    ))
    $node = $nodes |
        Where-Object { $_.GetAttribute("Executable") -match "(?i)(ChatGPT|OpenAI).*\.exe$" } |
        Select-Object -First 1
    if (-not $node) {
        $node = $nodes |
            Where-Object { $_.GetAttribute("Executable") -match "(?i)\.exe$" } |
            Select-Object -First 1
    }
    if (-not $node) {
        throw "В AppxManifest.xml не найден исполняемый файл ChatGPT."
    }

    $path = Join-Path $package.InstallLocation $node.GetAttribute("Executable")
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Исполняемый файл ChatGPT не найден: $path"
    }
    return $path
}

function Find-ClaudeDesktopExecutable {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "AnthropicClaude\claude.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Claude\Claude.exe"),
        (Join-Path $env:LOCALAPPDATA "Claude\Claude.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    throw "Claude Desktop не найден в стандартных пользовательских папках."
}

function Find-OpenCodeExecutable {
    $running = Get-Process -Name "OpenCode" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and (Test-Path -LiteralPath $_.Path) } |
        Select-Object -First 1
    if ($running) {
        return $running.Path
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\OpenCode\OpenCode.exe"),
        (Join-Path $env:LOCALAPPDATA "OpenCode\OpenCode.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    foreach ($commandName in @("OpenCode", "opencode")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command -and $command.Path) {
            return $command.Path
        }
    }
    throw "OpenCode не найден."
}

function Find-VSCodeExecutable {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Microsoft VS Code\Code.exe"),
        (Join-Path $env:ProgramFiles "Microsoft VS Code\Code.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft VS Code\Code.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }
    $command = Get-Command "code" -ErrorAction SilentlyContinue
    if ($command -and $command.Path) {
        return $command.Path
    }
    throw "VS Code не найден."
}

function Find-CliCommandPath {
    param(
        [ValidateSet("codex", "claude")]
        [string]$CommandName
    )

    $candidates = New-Object System.Collections.Generic.List[string]
    if ($CommandName -eq "claude") {
        $candidates.Add((Join-Path $env:USERPROFILE ".local\bin\claude.exe"))
        $candidates.Add((Join-Path $env:APPDATA "npm\claude.cmd"))
    }
    else {
        $candidates.Add((Join-Path $env:LOCALAPPDATA "nodejs\codex.cmd"))
        $candidates.Add((Join-Path $env:LOCALAPPDATA "nodejs\codex.ps1"))
        $candidates.Add((Join-Path $env:APPDATA "npm\codex.cmd"))
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    foreach ($exactName in @("${CommandName}.exe", "${CommandName}.cmd", "${CommandName}.ps1", $CommandName)) {
        $command = Get-Command $exactName -ErrorAction SilentlyContinue
        if ($command -and -not [string]::IsNullOrWhiteSpace($command.Path) -and
            (Test-Path -LiteralPath $command.Path -PathType Leaf)) {
            return $command.Path
        }
    }

    throw "$CommandName CLI не найден."
}

function Resolve-LaunchTarget {
    param([string]$SelectedMode)

    switch ($SelectedMode) {
        "ChatGPT" {
            return [pscustomobject]@{
                Kind = "Desktop"
                Path = Find-ChatGPTExecutable
                ProcessNames = @("ChatGPT", "OpenAI")
                ForceClose = $true
            }
        }
        "ClaudeDesktop" {
            return [pscustomobject]@{
                Kind = "Desktop"
                Path = Find-ClaudeDesktopExecutable
                ProcessNames = @("Claude")
                ForceClose = $true
                MatchExecutablePath = $true
            }
        }
        "OpenCode" {
            return [pscustomobject]@{
                Kind = "Desktop"
                Path = Find-OpenCodeExecutable
                ProcessNames = @("OpenCode")
                ForceClose = $true
            }
        }
        "CodexCLI" {
            return [pscustomobject]@{
                Kind = "CLI"
                Path = Find-CliCommandPath -CommandName "codex"
                ProcessNames = @("codex")
                ForceClose = $false
            }
        }
        "ClaudeCLI" {
            return [pscustomobject]@{
                Kind = "CLI"
                Path = Find-CliCommandPath -CommandName "claude"
                ProcessNames = @("claude")
                ForceClose = $false
            }
        }
        { $_ -in @("VSCodeCodex", "VSCodeClaude") } {
            if (Get-Process -Name "Code" -ErrorAction SilentlyContinue) {
                throw "VS Code уже открыт. Сохраните работу, закройте его вручную и повторите запуск."
            }
            return [pscustomobject]@{
                Kind = "VSCode"
                Path = Find-VSCodeExecutable
                ProcessNames = @("Code")
                ForceClose = $false
            }
        }
        default {
            throw "Неизвестный режим: $SelectedMode"
        }
    }
}

function Get-TargetProcesses {
    param([pscustomobject]$Target)

    $processes = foreach ($processName in $Target.ProcessNames) {
        Get-Process -Name $processName -ErrorAction SilentlyContinue
    }

    if ($Target.MatchExecutablePath) {
        $targetPath = [IO.Path]::GetFullPath($Target.Path)
        $processes = $processes | Where-Object {
            try {
                $_.Path -and
                    [string]::Equals(
                        [IO.Path]::GetFullPath($_.Path),
                        $targetPath,
                        [StringComparison]::OrdinalIgnoreCase
                    )
            }
            catch {
                $false
            }
        }
    }

    return @($processes)
}

function Stop-ExistingTargetProcesses {
    param([pscustomobject]$Target)

    if (-not $Target.ForceClose) {
        return
    }

    $existingProcesses = @(Get-TargetProcesses -Target $Target)
    foreach ($process in $existingProcesses) {
        Stop-Process -InputObject $process -Force -ErrorAction Stop
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (@(Get-TargetProcesses -Target $Target).Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 200
    }

    throw "Не удалось принудительно закрыть выбранное приложение."
}

function Wait-TargetProcesses {
    param(
        [pscustomobject]$Target,
        [System.Diagnostics.Process]$InitialProcess
    )

    if ($Target.Kind -eq "CLI") {
        return
    }

    Start-Sleep -Seconds 2
    while ($true) {
        $alive = @(Get-TargetProcesses -Target $Target).Count -gt 0

        if (-not $alive) {
            if ($InitialProcess -and -not $InitialProcess.HasExited) {
                $alive = $true
            }
        }

        if (-not $alive) {
            break
        }
        Start-Sleep -Seconds 1
    }
}

function Invoke-SelectedTarget {
    param([pscustomobject]$Target)

    if ($Target.Kind -eq "CLI") {
        & $Target.Path
        return
    }

    $workingDirectory = Split-Path -Parent $Target.Path
    $process = Start-Process -FilePath $Target.Path `
        -WorkingDirectory $workingDirectory `
        -PassThru
    Wait-TargetProcesses -Target $Target -InitialProcess $process
}

function Wait-LocalPort {
    param(
        [int]$Port,
        [System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds = 15
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($Process.HasExited) {
            throw "sing-box завершился до открытия локального порта. Код: $($Process.ExitCode)"
        }
        $client = New-Object Net.Sockets.TcpClient
        try {
            $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
            if ($async.AsyncWaitHandle.WaitOne(250) -and $client.Connected) {
                $client.EndConnect($async)
                return
            }
        }
        catch {
        }
        finally {
            $client.Close()
        }
        Start-Sleep -Milliseconds 200
    }
    throw "sing-box не открыл порт 127.0.0.1:$Port за $TimeoutSeconds секунд."
}

function Test-ProxyRoute {
    param([int]$ListenPort)

    $curl = Get-Command "curl.exe" -ErrorAction SilentlyContinue
    if (-not $curl) {
        Write-Host "curl.exe не найден: live smoke test пропущен." -ForegroundColor Yellow
        return
    }

    $status = & $curl.Path -sS `
        --proxy "http://127.0.0.1:$ListenPort" `
        --connect-timeout 10 `
        --max-time 25 `
        -o NUL `
        -w "%{http_code}" `
        "https://chatgpt.com/cdn-cgi/trace"

    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($status | Out-String).Trim()) -or
        (($status | Out-String).Trim() -eq "000")) {
        throw "Проверка upstream-прокси через sing-box не прошла."
    }
    Write-Host "Проверка маршрута через sing-box: HTTP $(($status | Out-String).Trim())" -ForegroundColor Green
}

function Invoke-SelfTest {
    param([string]$ExternalSingBoxPath)

    $dummy = [pscustomobject]@{
        server = "proxy.example.invalid"
        port = 8443
        username = "test-user"
        singBoxPath = $ExternalSingBoxPath
    }
    $config = New-SingBoxConfig `
        -ProxyConfig $dummy `
        -PlainPassword "test-password" `
        -ListenPort 18082 `
        -ProcessNames @("ChatGPT.exe")

    if ($config.outbounds[0].Contains("tls")) {
        throw "SelfTest: HTTP transport must not contain TLS settings."
    }
    if ($config.route.final -ne "direct") {
        throw "SelfTest: final route must be direct."
    }
    if ($script:AllModes.Count -ne 7) {
        throw "SelfTest: expected seven launch modes."
    }
    if ($script:DownloadUrl -ne "https://github.com/SagerNet/sing-box/releases/download/v1.13.14/sing-box-1.13.14-windows-amd64.zip") {
        throw "SelfTest: unexpected sing-box download URL."
    }

    $expectedClaudeCli = Join-Path $env:USERPROFILE ".local\bin\claude.exe"
    if (Test-Path -LiteralPath $expectedClaudeCli -PathType Leaf) {
        $resolvedClaudeCli = Find-CliCommandPath -CommandName "claude"
        if ($resolvedClaudeCli -ne $expectedClaudeCli) {
            throw "SelfTest: Claude CLI alias shadowed the real executable."
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($ExternalSingBoxPath)) {
        if (-not (Test-Path -LiteralPath $ExternalSingBoxPath -PathType Leaf)) {
            throw "SelfTest: sing-box path does not exist: $ExternalSingBoxPath"
        }
        $selfTestRoot = Join-Path ([IO.Path]::GetTempPath()) ("ai-singbox-selftest-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $selfTestRoot -Force | Out-Null
        try {
            $configPath = Join-Path $selfTestRoot "config.json"
            $json = $config | ConvertTo-Json -Depth 20
            [IO.File]::WriteAllText($configPath, $json, (New-Object Text.UTF8Encoding($false)))
            & $ExternalSingBoxPath check -c $configPath
            if ($LASTEXITCODE -ne 0) {
                throw "SelfTest: sing-box check failed with code $LASTEXITCODE."
            }
        }
        finally {
            if (Test-Path -LiteralPath $selfTestRoot) {
                Remove-Item -LiteralPath $selfTestRoot -Recurse -Force
            }
        }
    }

    Write-Host "HTTP launcher self-test passed." -ForegroundColor Green
    & $env:ComSpec /c exit 0
}

function Invoke-Launcher {
    param([string]$SelectedMode)

    $proxySnapshot = $null
    $environmentSnapshot = $null
    $singBoxProcess = $null
    $tempRoot = $null
    $securePassword = $null
    $plainPassword = $null
    $operationError = $null

    try {
        $proxyConfig = Read-ProxyConfiguration
        $securePassword = Read-ProxyPassword
        $plainPassword = Convert-SecureStringToPlainText -SecureValue $securePassword
        $target = Resolve-LaunchTarget -SelectedMode $SelectedMode
        $listenPort = Get-FreeListenPort

        $tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
            "ai-singbox-launcher\" + [guid]::NewGuid().ToString("N")
        )
        New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
        $tempConfigPath = Join-Path $tempRoot "config.json"
        $stdoutPath = Join-Path $tempRoot "sing-box.stdout.log"
        $stderrPath = Join-Path $tempRoot "sing-box.stderr.log"

        $config = New-SingBoxConfig `
            -ProxyConfig $proxyConfig `
            -PlainPassword $plainPassword `
            -ListenPort $listenPort `
            -ProcessNames (Get-ModeProcessNames -SelectedMode $SelectedMode)

        $json = $config | ConvertTo-Json -Depth 20
        [IO.File]::WriteAllText(
            $tempConfigPath,
            $json,
            (New-Object Text.UTF8Encoding($false))
        )

        Write-Step "Проверяю временную конфигурацию sing-box."
        & $proxyConfig.singBoxPath check -c $tempConfigPath
        if ($LASTEXITCODE -ne 0) {
            throw "sing-box check завершился с кодом $LASTEXITCODE."
        }

        $proxySnapshot = Get-ProxySnapshot
        $environmentSnapshot = Get-EnvironmentSnapshot
        Stop-ExistingTargetProcesses -Target $target

        Write-Step "Запускаю sing-box на 127.0.0.1:$listenPort."
        $singBoxProcess = Start-Process `
            -FilePath $proxyConfig.singBoxPath `
            -ArgumentList @("run", "-c", $tempConfigPath) `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -PassThru

        Wait-LocalPort -Port $listenPort -Process $singBoxProcess
        # sing-box полностью читает конфигурацию при старте. Удаляем файл с
        # открытым паролем сразу после готовности локального порта.
        Remove-Item -LiteralPath $tempConfigPath -Force
        Set-LocalProxyEnvironment -ListenPort $listenPort
        Test-ProxyRoute -ListenPort $listenPort

        $plainPassword = $null
        $securePassword = $null
        [GC]::Collect()

        Write-Step "Запускаю режим $SelectedMode. После закрытия прокси будет восстановлен."
        Invoke-SelectedTarget -Target $target
    }
    catch {
        $operationError = $_
    }
    finally {
        $cleanupFailures = New-Object System.Collections.Generic.List[string]
        $plainPassword = $null
        $securePassword = $null
        [GC]::Collect()

        if ($singBoxProcess) {
            try {
                $singBoxProcess.Refresh()
                if (-not $singBoxProcess.HasExited) {
                    Stop-Process -InputObject $singBoxProcess -Force -ErrorAction Stop
                    if (-not $singBoxProcess.WaitForExit(5000)) {
                        throw "Процесс sing-box PID $($singBoxProcess.Id) не завершился за 5 секунд."
                    }
                    $singBoxProcess.Refresh()
                    if (-not $singBoxProcess.HasExited) {
                        throw "Процесс sing-box PID $($singBoxProcess.Id) остался запущен."
                    }
                }
            }
            catch {
                $cleanupFailures.Add("Не удалось завершить sing-box: $($_.Exception.Message)")
            }
        }

        if ($proxySnapshot) {
            try {
                Restore-ProxySnapshot -Snapshot $proxySnapshot
            }
            catch {
                $cleanupFailures.Add("Не удалось восстановить системный прокси: $($_.Exception.Message)")
            }
        }

        if ($environmentSnapshot) {
            try {
                Restore-EnvironmentSnapshot -Snapshot $environmentSnapshot
            }
            catch {
                $cleanupFailures.Add("Не удалось восстановить переменные окружения: $($_.Exception.Message)")
            }
        }

        if ($tempRoot -and (Test-Path -LiteralPath $tempRoot)) {
            try {
                Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction Stop
                if (Test-Path -LiteralPath $tempRoot) {
                    throw "Временный каталог остался на диске: $tempRoot"
                }
            }
            catch {
                $cleanupFailures.Add("Не удалось удалить временные файлы: $($_.Exception.Message)")
            }
        }

        if ($cleanupFailures.Count -gt 0) {
            $details = $cleanupFailures -join " | "
            if ($operationError) {
                $details = "Основная ошибка: $($operationError.Exception.Message) | $details"
            }
            throw "Не удалось безопасно завершить запускник: $details"
        }

        Write-Host "Прокси остановлен, исходные настройки Windows восстановлены и проверены." -ForegroundColor Green
    }

    if ($operationError) {
        throw $operationError
    }
}

if ($SelfTest) {
    Invoke-SelfTest -ExternalSingBoxPath $SingBoxPathForTest
    return
}

if ($Reset) {
    Remove-StateFiles
}
elseif ($ResetPassword) {
    Remove-StateFiles -PasswordOnly
}

if (-not $Mode) {
    $Mode = Select-LaunchMode
}

Invoke-Launcher -SelectedMode $Mode
