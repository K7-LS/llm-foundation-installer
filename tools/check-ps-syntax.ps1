[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Root
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$Failed = $false
# Только файлы, которые видит git под $Root: отслеживаемые и новые
# неигнорируемые. Артефакты в .work/, .worktrees/, dist/ и вложенные
# worktree не проверяются; core.quotePath=false отдаёт не-ASCII пути как есть.
$Paths = @(& git -C $Root -c core.quotePath=false ls-files `
    --cached --others --exclude-standard -- '*.ps1')
if ($LASTEXITCODE -ne 0) {
    throw ('git ls-files failed for ' + $Root + ' (exit ' + $LASTEXITCODE + ')')
}
$Files = @($Paths |
    ForEach-Object { Join-Path $Root $_ } |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    ForEach-Object { Get-Item -LiteralPath $_ })
foreach ($File in $Files) {
    $Tokens = $null
    $Errors = $null
    [void][Management.Automation.Language.Parser]::ParseFile(
        $File.FullName,
        [ref]$Tokens,
        [ref]$Errors
    )
    if ($Errors.Count -gt 0) {
        $Failed = $true
        foreach ($ErrorItem in $Errors) {
            [Console]::Error.WriteLine(
                $File.FullName + ':' +
                $ErrorItem.Extent.StartLineNumber + ': ' +
                $ErrorItem.Message
            )
        }
    }
}
if ($Failed) { exit 1 }
Write-Output ('PowerShell syntax PASS: ' + $Files.Count)
exit 0
