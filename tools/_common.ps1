# Общие функции build-скриптов (кандидат №17 этапа 2а, 2026-08-28).
# Подключение: . (Join-Path $PSScriptRoot '_common.ps1')
# Намеренно без Set-StrictMode: dot-source протащил бы его в скоуп вызывающего.

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Stream = [IO.File]::Open(
        $Path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
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

function Find-RoslynCompiler {
    param([Parameter(Mandatory = $true)][string]$Purpose)
    $Candidates = New-Object System.Collections.Generic.List[string]
    $VsWhere = Join-Path ${env:ProgramFiles(x86)} (
        'Microsoft Visual Studio\Installer\vswhere.exe'
    )
    if (Test-Path -LiteralPath $VsWhere -PathType Leaf) {
        @(& $VsWhere -products '*' -requires Microsoft.Component.MSBuild `
            -property installationPath) | ForEach-Object {
            if (-not [string]::IsNullOrWhiteSpace($_)) {
                $Candidates.Add(
                    (Join-Path $_ 'MSBuild\Current\Bin\Roslyn\csc.exe')
                )
            }
        }
    }
    foreach ($VisualStudioRoot in @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio'),
        (Join-Path $env:ProgramFiles 'Microsoft Visual Studio')
    )) {
        if (Test-Path -LiteralPath $VisualStudioRoot -PathType Container) {
            @(Get-ChildItem -Path (Join-Path $VisualStudioRoot `
                '*\*\MSBuild\Current\Bin\Roslyn\csc.exe') -File `
                -ErrorAction SilentlyContinue |
                Sort-Object -Property FullName -Descending) |
                ForEach-Object { $Candidates.Add($_.FullName) }
        }
    }
    $Compiler = @($Candidates | Where-Object {
        Test-Path -LiteralPath $_ -PathType Leaf
    } | Select-Object -Unique) | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($Compiler)) {
        throw "A Roslyn C# compiler is required for $Purpose."
    }
    return $Compiler
}
