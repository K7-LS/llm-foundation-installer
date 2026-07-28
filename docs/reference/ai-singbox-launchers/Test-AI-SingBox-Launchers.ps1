[CmdletBinding()]
param(
    [string]$SingBoxPath = ""
)

$ErrorActionPreference = "Stop"
$script:Failures = New-Object System.Collections.Generic.List[string]
$script:Passes = New-Object System.Collections.Generic.List[string]

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if ($Condition) {
        $script:Passes.Add($Message)
        Write-Host "PASS  $Message" -ForegroundColor Green
    }
    else {
        $script:Failures.Add($Message)
        Write-Host "FAIL  $Message" -ForegroundColor Red
    }
}

function Assert-ContainsText {
    param(
        [string]$Content,
        [string]$Expected,
        [string]$Message
    )

    Assert-True -Condition $Content.Contains($Expected) -Message $Message
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$downloadUrl = "https://github.com/SagerNet/sing-box/releases/download/v1.13.14/sing-box-1.13.14-windows-amd64.zip"
$requiredModes = @(
    "ChatGPT",
    "ClaudeDesktop",
    "OpenCode",
    "CodexCLI",
    "ClaudeCLI",
    "VSCodeCodex",
    "VSCodeClaude"
)

$launchers = @(
    [pscustomobject]@{
        Path = Join-Path $repoRoot "Start-AI-SingBox-HTTPS.ps1"
        Transport = "HTTPS"
        StateStem = ".ai-singbox-https"
    },
    [pscustomobject]@{
        Path = Join-Path $repoRoot "Start-AI-SingBox-HTTP.ps1"
        Transport = "HTTP"
        StateStem = ".ai-singbox-http"
    }
)

foreach ($launcher in $launchers) {
    $exists = Test-Path -LiteralPath $launcher.Path
    Assert-True -Condition $exists -Message "$($launcher.Transport) launcher exists"
    if (-not $exists) {
        continue
    }

    $tokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $launcher.Path,
        [ref]$tokens,
        [ref]$parseErrors
    )
    Assert-True -Condition ($parseErrors.Count -eq 0) `
        -Message "$($launcher.Transport) launcher parses without errors"

    $content = Get-Content -LiteralPath $launcher.Path -Raw
    Assert-ContainsText $content $downloadUrl `
        "$($launcher.Transport) launcher contains the pinned sing-box download URL"
    Assert-ContainsText $content $launcher.StateStem `
        "$($launcher.Transport) launcher uses isolated state files"
    Assert-ContainsText $content "ConvertFrom-SecureString" `
        "$($launcher.Transport) launcher stores password with DPAPI"
    Assert-ContainsText $content "finally" `
        "$($launcher.Transport) launcher has guaranteed cleanup"
    Assert-ContainsText $content "Restore-ProxySnapshot" `
        "$($launcher.Transport) launcher restores Windows proxy settings"
    Assert-ContainsText $content 'Remove-Item -LiteralPath $tempConfigPath -Force' `
        "$($launcher.Transport) launcher removes plaintext temp config after sing-box starts"

    foreach ($mode in $requiredModes) {
        Assert-ContainsText $content "`"$mode`"" `
            "$($launcher.Transport) launcher contains mode $mode"
    }

    foreach ($domain in @("datadoghq.com", "sendgrid.net", "statsig.com")) {
        Assert-ContainsText $content "`"$domain`"" `
            "$($launcher.Transport) launcher routes required service domain $domain"
    }

    Assert-ContainsText $content '.local\bin\claude.exe' `
        "$($launcher.Transport) launcher prefers the real Claude CLI over aliases"
    Assert-True -Condition (-not $content.Contains('Get-Process -Name "Claude"')) `
        -Message "$($launcher.Transport) launcher does not infer Claude Desktop from a running Claude CLI"
    Assert-ContainsText $content 'MatchExecutablePath = $true' `
        "$($launcher.Transport) launcher distinguishes Claude Desktop from Claude CLI by executable path"
    Assert-ContainsText $content 'Get-TargetProcesses' `
        "$($launcher.Transport) launcher uses path-aware target process selection"
    Assert-ContainsText $content 'Не удалось принудительно закрыть выбранное приложение' `
        "$($launcher.Transport) launcher fails if the selected desktop app cannot be closed"
    Assert-ContainsText $content '$cleanupFailures' `
        "$($launcher.Transport) launcher tracks cleanup failures"
    Assert-ContainsText $content 'Не удалось безопасно завершить запускник' `
        "$($launcher.Transport) launcher reports cleanup failure as a fatal error"
    Assert-ContainsText $content 'Test-ProxySnapshotRestored' `
        "$($launcher.Transport) launcher verifies the restored Windows proxy snapshot"

    $selfTestArgs = @{
        SelfTest = $true
    }
    if (-not [string]::IsNullOrWhiteSpace($SingBoxPath)) {
        $selfTestArgs.SingBoxPathForTest = $SingBoxPath
    }

    & $launcher.Path @selfTestArgs
    Assert-True -Condition ($LASTEXITCODE -eq 0) `
        -Message "$($launcher.Transport) internal self-test exits with code 0"
}

$sensitivePatterns = @(
    "Proxy-Authorization\s*:"
)

$productionFiles = $launchers.Path | Where-Object { Test-Path -LiteralPath $_ }
foreach ($path in $productionFiles) {
    $content = Get-Content -LiteralPath $path -Raw
    foreach ($pattern in $sensitivePatterns) {
        Assert-True -Condition (-not [regex]::IsMatch($content, $pattern, "IgnoreCase")) `
            -Message "$(Split-Path $path -Leaf) does not contain sensitive pattern $pattern"
    }
}

Write-Host ""
Write-Host "Passed: $($script:Passes.Count)" -ForegroundColor Cyan
Write-Host "Failed: $($script:Failures.Count)" -ForegroundColor Cyan

if ($script:Failures.Count -gt 0) {
    Write-Host ""
    Write-Host "Failures:" -ForegroundColor Red
    $script:Failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}

Write-Host "All launcher tests passed." -ForegroundColor Green
exit 0
