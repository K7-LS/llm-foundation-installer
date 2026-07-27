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
$Files = Get-ChildItem -LiteralPath $Root -Recurse -File -Filter '*.ps1'
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
