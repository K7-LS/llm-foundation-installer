[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('plan', 'install', 'doctor', 'inventory', 'rollback')]
    [string]$Command,
    [string]$Package,
    [Parameter(Mandatory = $true)]
    [Alias('Home')]
    [string]$TargetHome,
    [string]$Target,
    [string]$ClientId,
    [string]$ClientVersion,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$script:ExitCode = @{
    INVALID_ARGUMENT = 2
    UNSUPPORTED_CLIENT = 10
    DOWNGRADE_BLOCKED = 10
    NOT_INSTALLED = 20
    RECOVERY_REQUIRED = 20
    INVALID_PACKAGE = 30
    INSTALL_FAILED = 30
    ACTIVE_DRIFT = 30
    UNSAFE_PATH = 40
}
$script:MutationCount = 0

function Throw-Foundation {
    param(
        [Parameter(Mandatory = $true)][string]$Code,
        [Parameter(Mandatory = $true)][string]$Message
    )
    $Exception = New-Object System.InvalidOperationException($Message)
    $Exception.Data['FoundationCode'] = $Code
    throw $Exception
}

function Write-Result {
    param([Parameter(Mandatory = $true)]$Value)
    if ($Json) {
        Write-Output (ConvertTo-Json $Value -Depth 30 -Compress)
        return
    }
    foreach ($Property in $Value.PSObject.Properties) {
        Write-Output ("{0}: {1}" -f $Property.Name, $Property.Value)
    }
}

function Invoke-AtomicReplace {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $Backup = $Destination + '.replace-' + [Guid]::NewGuid().ToString('N')
    try {
        [IO.File]::Replace($Source, $Destination, $Backup, $true)
    } finally {
        if (Test-Path -LiteralPath $Backup -PathType Leaf) {
            Remove-Item -LiteralPath $Backup -Force
        }
    }
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $Parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $Parent)) {
        [IO.Directory]::CreateDirectory($Parent) | Out-Null
    }
    $Payload = (ConvertTo-Json $Value -Depth 40) + "`n"
    $Bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($Payload)
    $Temporary = $Path + '.tmp-' + [Guid]::NewGuid().ToString('N')
    [IO.File]::WriteAllBytes($Temporary, $Bytes)
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Invoke-AtomicReplace $Temporary $Path
    } else {
        [IO.File]::Move($Temporary, $Path)
    }
}

function Read-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int64]$MaximumBytes = 8388608
    )
    $Item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($Item.PSIsContainer -or
        ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $Item.Length -le 0 -or $Item.Length -gt $MaximumBytes) {
        Throw-Foundation 'INVALID_PACKAGE' "Invalid JSON file: $Path"
    }
    $Bytes = [IO.File]::ReadAllBytes($Item.FullName)
    try {
        $Text = (New-Object Text.UTF8Encoding($false, $true)).GetString($Bytes)
        return ConvertFrom-Json -InputObject $Text -ErrorAction Stop
    } catch {
        Throw-Foundation 'INVALID_PACKAGE' "Invalid JSON content: $Path"
    }
}

function Get-BytesSha256 {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    $Algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return -join (
            $Algorithm.ComputeHash($Bytes) |
                ForEach-Object { $_.ToString('x2') }
        )
    } finally {
        $Algorithm.Dispose()
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($Item.PSIsContainer -or
        ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Throw-Foundation 'UNSAFE_PATH' "Expected a regular file: $Path"
    }
    $Stream = [IO.File]::Open(
        $Item.FullName,
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

function Get-StreamSha256 {
    param([Parameter(Mandatory = $true)][IO.Stream]$Stream)
    $Algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return -join (
            $Algorithm.ComputeHash($Stream) |
                ForEach-Object { $_.ToString('x2') }
        )
    } finally {
        $Algorithm.Dispose()
    }
}

function Test-PortablePath {
    param([AllowEmptyString()][string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value) -or
        $Value.Length -gt 240 -or
        $Value.Contains('\') -or
        $Value.StartsWith('/') -or
        $Value.Contains(':') -or
        $Value.Contains('//') -or
        $Value.IndexOfAny([char[]]'<>"|?*') -ge 0 -or
        $Value.Normalize([Text.NormalizationForm]::FormC) -cne $Value) {
        return $false
    }
    $Parts = @($Value.Split('/'))
    if ($Parts.Count -eq 0 -or $Parts.Count -gt 48) { return $false }
    $Reserved = '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$'
    foreach ($Part in $Parts) {
        if ($Part -in @('', '.', '..') -or
            $Part.Length -gt 100 -or
            $Part.EndsWith('.') -or
            $Part.EndsWith(' ') -or
            $Part -match $Reserved) {
            return $false
        }
    }
    return $true
}

function Test-ProtectedPath {
    param([Parameter(Mandatory = $true)][string]$Value)
    $Lower = $Value.ToLowerInvariant()
    $Parts = @($Lower.Split('/'))
    if ($Parts.Count -gt 0 -and $Parts[0] -ceq '.llm-foundation') {
        return $true
    }
    $Name = [IO.Path]::GetFileName($Lower)
    if ($Name -in @(
        'auth.json',
        'credentials.json',
        'credentials.toml',
        'tokens.json'
    ) -or
        $Name.EndsWith('.sqlite') -or
        $Name.EndsWith('.sqlite3') -or
        $Name.EndsWith('.db')) {
        return $true
    }
    return $false
}

function Test-DeclaredPreservedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [AllowEmptyCollection()][object[]]$PreservedPaths
    )
    foreach ($Protected in @($PreservedPaths)) {
        $ProtectedValue = [string]$Protected
        if ($Value -ceq $ProtectedValue -or
            $Value.StartsWith(
                $ProtectedValue + '/',
                [StringComparison]::Ordinal
            ) -or
            $ProtectedValue.StartsWith(
                $Value + '/',
                [StringComparison]::Ordinal
            )) {
            return $true
        }
    }
    return $false
}

function Assert-SafeDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $Item.PSIsContainer -or
        ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Throw-Foundation 'UNSAFE_PATH' "Unsafe directory: $Path"
    }
    $Lexical = [IO.Path]::GetFullPath($Item.FullName)
    $Resolved = (Resolve-Path -LiteralPath $Item.FullName).Path
    if (-not $Lexical.Equals(
        $Resolved,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        Throw-Foundation 'UNSAFE_PATH' "Directory identity changed: $Path"
    }
}

function Resolve-HomePath {
    param(
        [Parameter(Mandatory = $true)][string]$Relative,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    if (-not (Test-PortablePath $Relative) -or
        (Test-ProtectedPath $Relative)) {
        Throw-Foundation 'UNSAFE_PATH' "Unsafe managed path: $Relative"
    }
    $Root = [IO.Path]::GetFullPath($HomeRoot)
    $Native = $Relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
    $Result = [IO.Path]::GetFullPath((Join-Path $Root $Native))
    if (-not $Result.StartsWith(
        $Root + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        Throw-Foundation 'UNSAFE_PATH' "Managed path escaped home: $Relative"
    }
    return $Result
}

function Assert-SafeAncestors {
    param(
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    $Root = [IO.Path]::GetFullPath($HomeRoot)
    Assert-SafeDirectory $Root
    $Absolute = [IO.Path]::GetFullPath($Destination)
    if (-not $Absolute.StartsWith(
        $Root + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        Throw-Foundation 'UNSAFE_PATH' 'Destination escaped home'
    }
    $Parent = Split-Path -Parent $Absolute
    $Relative = $Parent.Substring($Root.Length).TrimStart('\', '/')
    $Cursor = $Root
    if (-not [string]::IsNullOrEmpty($Relative)) {
        foreach ($Part in @($Relative -split '[\\/]')) {
            $Cursor = Join-Path $Cursor $Part
            if (-not (Test-Path -LiteralPath $Cursor)) { break }
            Assert-SafeDirectory $Cursor
        }
    }
    if (Test-Path -LiteralPath $Absolute) {
        $Item = Get-Item -LiteralPath $Absolute -Force
        if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Throw-Foundation 'UNSAFE_PATH' "Destination is a reparse point: $Absolute"
        }
    }
}

function New-SafeDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    Assert-SafeAncestors $Path $HomeRoot
    if (-not (Test-Path -LiteralPath $Path)) {
        [IO.Directory]::CreateDirectory($Path) | Out-Null
    }
    Assert-SafeDirectory $Path
}

function Assert-ExactProperties {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string[]]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ($null -eq $Value -or
        $Value -isnot [Management.Automation.PSCustomObject]) {
        Throw-Foundation 'INVALID_PACKAGE' "$Label must be an object"
    }
    $Actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $Wanted = @($Expected | Sort-Object)
    if (@(
        Compare-Object -ReferenceObject $Wanted -DifferenceObject $Actual
    ).Count -ne 0) {
        Throw-Foundation 'INVALID_PACKAGE' "$Label properties differ"
    }
}

function Assert-StringArray {
    param(
        [AllowEmptyCollection()][object[]]$Values,
        [Parameter(Mandatory = $true)][string]$Label,
        [switch]$AllowProtected
    )
    $Seen = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    $Previous = $null
    foreach ($Value in @($Values)) {
        if ($Value -isnot [string] -or
            -not (Test-PortablePath ([string]$Value)) -or
            -not $Seen.Add([string]$Value)) {
            Throw-Foundation 'INVALID_PACKAGE' "$Label contains an invalid path"
        }
        if ($null -ne $Previous -and
            [StringComparer]::Ordinal.Compare(
                [string]$Previous,
                [string]$Value
            ) -ge 0) {
            Throw-Foundation 'INVALID_PACKAGE' "$Label is not sorted"
        }
        if (-not $AllowProtected -and
            (Test-ProtectedPath ([string]$Value))) {
            Throw-Foundation 'UNSAFE_PATH' "$Label contains a protected path"
        }
        $Previous = [string]$Value
    }
}

function Read-ZipEntryBytes {
    param(
        [Parameter(Mandatory = $true)]$Entry,
        [int64]$MaximumBytes = 4194304
    )
    if ([int64]$Entry.Length -le 0 -or
        [int64]$Entry.Length -gt $MaximumBytes) {
        Throw-Foundation 'INVALID_PACKAGE' 'ZIP entry is outside size limits'
    }
    $Stream = $Entry.Open()
    $Memory = New-Object IO.MemoryStream
    try {
        $Stream.CopyTo($Memory)
        return $Memory.ToArray()
    } finally {
        $Memory.Dispose()
        $Stream.Dispose()
    }
}

function Assert-Manifest {
    param(
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)]$EntriesByName
    )
    Assert-ExactProperties $Manifest @(
        'schema_version',
        'target',
        'version',
        'client',
        'foundation_engine_version',
        'managed_surface',
        'sync_policy',
        'files'
    ) 'package manifest'
    if ($Manifest.schema_version -ne 1 -or
        $Manifest.target -notmatch '^[a-z][a-z0-9-]{1,31}$' -or
        $Manifest.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        $Manifest.foundation_engine_version -notmatch
            '^[0-9]+\.[0-9]+\.[0-9]+$') {
        Throw-Foundation 'INVALID_PACKAGE' 'Package manifest constants differ'
    }
    Assert-ExactProperties $Manifest.client @(
        'id',
        'supported_version'
    ) 'client'
    if ($Manifest.client.id -notmatch '^[a-z][a-z0-9._-]{1,63}$' -or
        $Manifest.client.supported_version -notmatch
            '^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$') {
        Throw-Foundation 'INVALID_PACKAGE' 'Client contract is invalid'
    }
    Assert-ExactProperties $Manifest.managed_surface @(
        'exact_directories',
        'replace_files',
        'preserved_paths'
    ) 'managed surface'
    Assert-StringArray @(
        $Manifest.managed_surface.exact_directories
    ) 'exact directories'
    Assert-StringArray @(
        $Manifest.managed_surface.replace_files
    ) 'replace files'
    Assert-StringArray @(
        $Manifest.managed_surface.preserved_paths
    ) 'preserved paths' -AllowProtected
    if (@($Manifest.managed_surface.exact_directories).Count -eq 0 -or
        @($Manifest.managed_surface.replace_files).Count -eq 0 -or
        @($Manifest.managed_surface.preserved_paths).Count -eq 0) {
        Throw-Foundation 'INVALID_PACKAGE' 'Managed surface is empty'
    }
    foreach ($Root in @(
        @($Manifest.managed_surface.exact_directories) +
        @($Manifest.managed_surface.replace_files)
    )) {
        if (Test-DeclaredPreservedPath (
            [string]$Root
        ) @($Manifest.managed_surface.preserved_paths)) {
            Throw-Foundation 'UNSAFE_PATH' (
                "Managed surface overlaps preserved path: $Root"
            )
        }
    }
    Assert-ExactProperties $Manifest.sync_policy @(
        'direction',
        'consumer_feedback_upload',
        'consumer_push',
        'consumer_session_upload',
        'credentials_included'
    ) 'sync policy'
    if ($Manifest.sync_policy.direction -cne 'hub-to-consumer' -or
        [bool]$Manifest.sync_policy.consumer_feedback_upload -or
        [bool]$Manifest.sync_policy.consumer_push -or
        [bool]$Manifest.sync_policy.consumer_session_upload -or
        [bool]$Manifest.sync_policy.credentials_included) {
        Throw-Foundation 'INVALID_PACKAGE' 'Package is not one-way'
    }

    $FilePaths = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    $Previous = $null
    foreach ($Row in @($Manifest.files)) {
        Assert-ExactProperties $Row @('path', 'sha256', 'bytes') 'file row'
        $Path = [string]$Row.path
        if (-not (Test-PortablePath $Path) -or
            (Test-ProtectedPath $Path) -or
            (Test-DeclaredPreservedPath (
                $Path
            ) @($Manifest.managed_surface.preserved_paths)) -or
            -not $FilePaths.Add($Path) -or
            $Row.sha256 -notmatch '^[0-9a-f]{64}$' -or
            ($Row.bytes -isnot [int] -and $Row.bytes -isnot [long]) -or
            [int64]$Row.bytes -lt 0) {
            Throw-Foundation 'INVALID_PACKAGE' 'Invalid file row'
        }
        if ($null -ne $Previous -and
            [StringComparer]::Ordinal.Compare($Previous, $Path) -ge 0) {
            Throw-Foundation 'INVALID_PACKAGE' 'File rows are not sorted'
        }
        $Managed = @(
            $Manifest.managed_surface.replace_files
        ) -ccontains $Path
        if (-not $Managed) {
            foreach ($Root in @($Manifest.managed_surface.exact_directories)) {
                if ($Path.StartsWith(
                    [string]$Root + '/',
                    [StringComparison]::Ordinal
                )) {
                    $Managed = $true
                    break
                }
            }
        }
        if (-not $Managed) {
            Throw-Foundation 'UNSAFE_PATH' "File is outside managed surface: $Path"
        }
        if (-not $EntriesByName.ContainsKey($Path)) {
            Throw-Foundation 'INVALID_PACKAGE' "ZIP entry is missing: $Path"
        }
        $Entry = $EntriesByName[$Path]
        if ([int64]$Entry.Length -ne [int64]$Row.bytes) {
            Throw-Foundation 'INVALID_PACKAGE' "ZIP entry size differs: $Path"
        }
        $Stream = $Entry.Open()
        try {
            $Digest = Get-StreamSha256 $Stream
        } finally {
            $Stream.Dispose()
        }
        if ($Digest -cne [string]$Row.sha256) {
            Throw-Foundation 'INVALID_PACKAGE' "ZIP entry hash differs: $Path"
        }
        $Previous = $Path
    }
    $ManifestPath = 'package-manifest.json'
    $Expected = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($Path in $FilePaths) { $null = $Expected.Add($Path) }
    $null = $Expected.Add($ManifestPath)
    if ($Expected.Count -ne $EntriesByName.Count) {
        Throw-Foundation 'INVALID_PACKAGE' 'ZIP contains missing or extra entries'
    }
    foreach ($Path in $EntriesByName.Keys) {
        if (-not $Expected.Contains([string]$Path)) {
            Throw-Foundation 'INVALID_PACKAGE' "Unexpected ZIP entry: $Path"
        }
    }
}

function Open-ValidatedPackage {
    param([Parameter(Mandatory = $true)][string]$PackagePath)
    if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) {
        Throw-Foundation 'INVALID_PACKAGE' 'Package ZIP is missing'
    }
    $Item = Get-Item -LiteralPath $PackagePath -Force
    if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $Item.Length -le 0 -or $Item.Length -gt 536870912) {
        Throw-Foundation 'INVALID_PACKAGE' 'Package ZIP is outside limits'
    }
    try {
        Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop
        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
        $Stream = [IO.File]::Open(
            $Item.FullName,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        $Archive = New-Object IO.Compression.ZipArchive(
            $Stream,
            [IO.Compression.ZipArchiveMode]::Read,
            $false
        )
    } catch {
        if ($null -ne $Archive) { $Archive.Dispose() }
        if ($null -ne $Stream) { $Stream.Dispose() }
        Throw-Foundation 'INVALID_PACKAGE' 'Package ZIP cannot be opened'
    }
    try {
        if ($Archive.Entries.Count -eq 0 -or $Archive.Entries.Count -gt 5000) {
            Throw-Foundation 'INVALID_PACKAGE' 'Package ZIP entry count is invalid'
        }
        $Entries = @{}
        [int64]$Total = 0
        foreach ($Entry in $Archive.Entries) {
            $Name = [string]$Entry.FullName
            if ($Name.EndsWith('/') -or
                -not (Test-PortablePath $Name) -or
                (Test-ProtectedPath $Name)) {
                Throw-Foundation 'UNSAFE_PATH' "Unsafe ZIP path: $Name"
            }
            if ($Entries.ContainsKey($Name)) {
                Throw-Foundation 'INVALID_PACKAGE' "Duplicate ZIP path: $Name"
            }
            if ([int64]$Entry.Length -gt 134217728) {
                Throw-Foundation 'INVALID_PACKAGE' 'ZIP entry is too large'
            }
            $Total += [int64]$Entry.Length
            if ($Total -gt 536870912) {
                Throw-Foundation 'INVALID_PACKAGE' 'ZIP expansion is too large'
            }
            $Entries[$Name] = $Entry
        }
        $ManifestPath = 'package-manifest.json'
        if (-not $Entries.ContainsKey($ManifestPath)) {
            Throw-Foundation 'INVALID_PACKAGE' 'Package manifest is missing'
        }
        $Bytes = Read-ZipEntryBytes $Entries[$ManifestPath]
        try {
            $Text = (New-Object Text.UTF8Encoding($false, $true)).GetString($Bytes)
            $Manifest = ConvertFrom-Json -InputObject $Text -ErrorAction Stop
        } catch {
            Throw-Foundation 'INVALID_PACKAGE' 'Package manifest JSON is invalid'
        }
        Assert-Manifest $Manifest $Entries
        return [pscustomobject]@{
            stream = $Stream
            archive = $Archive
            entries = $Entries
            manifest = $Manifest
            manifest_bytes = $Bytes
            package_path = $Item.FullName
        }
    } catch {
        $Archive.Dispose()
        $Stream.Dispose()
        throw
    }
}

function Close-ValidatedPackage {
    param($Validated)
    if ($null -ne $Validated.archive) { $Validated.archive.Dispose() }
    if ($null -ne $Validated.stream) { $Validated.stream.Dispose() }
}

function Assert-ClientContract {
    param(
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)][string]$ActualId,
        [Parameter(Mandatory = $true)][string]$ActualVersion
    )
    if ([string]::IsNullOrWhiteSpace($ActualId) -or
        [string]::IsNullOrWhiteSpace($ActualVersion)) {
        Throw-Foundation 'UNSUPPORTED_CLIENT' (
            'Client identity and version evidence are required'
        )
    }
    if ($ActualId -cne [string]$Expected.id -or
        $ActualVersion -cne [string]$Expected.supported_version) {
        Throw-Foundation 'UNSUPPORTED_CLIENT' (
            "Supported client is $($Expected.id) $($Expected.supported_version)"
        )
    }
}

function Get-FoundationPaths {
    param(
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$TargetName
    )
    $StateRoot = Join-Path $HomeRoot (
        '.llm-foundation\state\' + $TargetName
    )
    $BackupRoot = Join-Path $HomeRoot (
        '.llm-foundation\backups\' + $TargetName
    )
    return [pscustomobject]@{
        state_root = $StateRoot
        active = Join-Path $StateRoot 'active.json'
        pending = Join-Path $StateRoot 'pending.json'
        backup_root = $BackupRoot
    }
}

function Read-ActiveState {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [switch]$AllowMissing
    )
    if (-not (Test-Path -LiteralPath $Paths.active -PathType Leaf)) {
        if ($AllowMissing) { return $null }
        Throw-Foundation 'NOT_INSTALLED' 'No active installation exists'
    }
    return Read-JsonFile $Paths.active
}

function Assert-NoRecoveryPending {
    param([Parameter(Mandatory = $true)]$Paths)
    if (Test-Path -LiteralPath $Paths.pending -PathType Leaf) {
        Throw-Foundation 'RECOVERY_REQUIRED' (
            'Interrupted transaction requires rollback'
        )
    }
}

function Get-UnknownEntries {
    param(
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    $Unknown = @()
    foreach ($Root in @($Manifest.managed_surface.exact_directories)) {
        $Absolute = Resolve-HomePath ([string]$Root) $HomeRoot
        Assert-SafeAncestors $Absolute $HomeRoot
        if (-not (Test-Path -LiteralPath $Absolute -PathType Container)) {
            continue
        }
        Assert-SafeDirectory $Absolute
        $Expected = New-Object 'Collections.Generic.HashSet[string]' (
            [StringComparer]::OrdinalIgnoreCase
        )
        foreach ($Row in @($Manifest.files)) {
            $Prefix = [string]$Root + '/'
            if (([string]$Row.path).StartsWith(
                $Prefix,
                [StringComparison]::Ordinal
            )) {
                $Remainder = ([string]$Row.path).Substring($Prefix.Length)
                $null = $Expected.Add(($Remainder -split '/')[0])
            }
        }
        foreach ($Child in @(Get-ChildItem -LiteralPath $Absolute -Force)) {
            if ($Child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                Throw-Foundation 'UNSAFE_PATH' (
                    "Managed directory contains reparse point: $($Child.FullName)"
                )
            }
            if (-not $Expected.Contains($Child.Name)) {
                $Unknown += ([string]$Root + '/' + $Child.Name)
            }
        }
    }
    return @($Unknown | Sort-Object)
}

function New-FoundationPlan {
    param(
        [Parameter(Mandatory = $true)]$Validated,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$ActualClientId,
        [Parameter(Mandatory = $true)][string]$ActualClientVersion
    )
    $Manifest = $Validated.manifest
    Assert-ClientContract $Manifest.client $ActualClientId `
        $ActualClientVersion
    $Paths = Get-FoundationPaths $HomeRoot ([string]$Manifest.target)
    $FoundationRoot = Join-Path $HomeRoot '.llm-foundation'
    Assert-SafeAncestors $FoundationRoot $HomeRoot
    Assert-NoRecoveryPending $Paths
    $Active = Read-ActiveState $Paths -AllowMissing
    if ($null -ne $Active) {
        try {
            $Current = [version]([string]$Active.release_version)
            $Candidate = [version]([string]$Manifest.version)
        } catch {
            Throw-Foundation 'INVALID_PACKAGE' 'Release version is invalid'
        }
        if ($Candidate -lt $Current) {
            Throw-Foundation 'DOWNGRADE_BLOCKED' (
                "Installed version $Current is newer than $Candidate"
            )
        }
    }
    foreach ($Root in @($Manifest.managed_surface.exact_directories)) {
        $Destination = Resolve-HomePath ([string]$Root) $HomeRoot
        Assert-SafeAncestors $Destination $HomeRoot
    }
    foreach ($Path in @($Manifest.managed_surface.replace_files)) {
        $Destination = Resolve-HomePath ([string]$Path) $HomeRoot
        Assert-SafeAncestors $Destination $HomeRoot
    }
    $Rows = @()
    foreach ($Row in @($Manifest.files)) {
        $Destination = Resolve-HomePath ([string]$Row.path) $HomeRoot
        $Action = 'CREATE'
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            $Action = if (
                (Get-FileSha256 $Destination) -ceq [string]$Row.sha256
            ) { 'UNCHANGED' } else { 'UPDATE' }
        }
        $Rows += [pscustomobject][ordered]@{
            path = [string]$Row.path
            action = $Action
            bytes = [int64]$Row.bytes
        }
    }
    return [pscustomobject][ordered]@{
        status = 'READY'
        target = [string]$Manifest.target
        release_version = [string]$Manifest.version
        client = $Manifest.client
        actions = $Rows
        quarantined_unknown = @(
            Get-UnknownEntries $Manifest $HomeRoot
        )
    }
}

function Copy-FileSafe {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $Item = Get-Item -LiteralPath $Source -Force -ErrorAction Stop
    if ($Item.PSIsContainer -or
        ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Throw-Foundation 'UNSAFE_PATH' "Unsafe source file: $Source"
    }
    $Parent = Split-Path -Parent $Destination
    if (-not (Test-Path -LiteralPath $Parent)) {
        [IO.Directory]::CreateDirectory($Parent) | Out-Null
    }
    $Input = [IO.File]::Open(
        $Item.FullName,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    $Output = [IO.File]::Open(
        $Destination,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None
    )
    try {
        $Input.CopyTo($Output)
        $Output.Flush($true)
    } finally {
        $Output.Dispose()
        $Input.Dispose()
    }
}

function Copy-TreeSafe {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    Assert-SafeDirectory $Source
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    $Root = [IO.Path]::GetFullPath($Source)
    $Queue = New-Object 'Collections.Generic.Queue[string]'
    $Queue.Enqueue($Root)
    while ($Queue.Count -gt 0) {
        $Directory = $Queue.Dequeue()
        Assert-SafeDirectory $Directory
        $RelativeDirectory = $Directory.Substring($Root.Length).TrimStart('\')
        $TargetDirectory = if ([string]::IsNullOrEmpty($RelativeDirectory)) {
            $Destination
        } else {
            Join-Path $Destination $RelativeDirectory
        }
        if (-not (Test-Path -LiteralPath $TargetDirectory)) {
            [IO.Directory]::CreateDirectory($TargetDirectory) | Out-Null
        }
        foreach ($Child in @(Get-ChildItem -LiteralPath $Directory -Force)) {
            if ($Child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                Throw-Foundation 'UNSAFE_PATH' (
                    "Tree contains reparse point: $($Child.FullName)"
                )
            }
            if ($Child.PSIsContainer) {
                $Queue.Enqueue($Child.FullName)
            } else {
                Copy-FileSafe $Child.FullName (
                    Join-Path $TargetDirectory $Child.Name
                )
            }
        }
    }
}

function Remove-TreeSafe {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Assert-SafeAncestors $Path $HomeRoot
    Assert-SafeDirectory $Path
    $Queue = New-Object 'Collections.Generic.Queue[string]'
    $Queue.Enqueue($Path)
    while ($Queue.Count -gt 0) {
        $Directory = $Queue.Dequeue()
        foreach ($Child in @(Get-ChildItem -LiteralPath $Directory -Force)) {
            if ($Child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                Throw-Foundation 'UNSAFE_PATH' (
                    "Tree contains reparse point: $($Child.FullName)"
                )
            }
            if ($Child.PSIsContainer) { $Queue.Enqueue($Child.FullName) }
        }
    }
    Remove-Item -LiteralPath $Path -Recurse -Force
}

function Copy-Atomic {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    Assert-SafeAncestors $Destination $HomeRoot
    $Parent = Split-Path -Parent $Destination
    if (-not (Test-Path -LiteralPath $Parent)) {
        [IO.Directory]::CreateDirectory($Parent) | Out-Null
    }
    Assert-SafeDirectory $Parent
    $Temporary = Join-Path $Parent (
        '.' + [IO.Path]::GetFileName($Destination) +
        '.foundation-' + [Guid]::NewGuid().ToString('N') + '.tmp'
    )
    Copy-FileSafe $Source $Temporary
    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        Invoke-AtomicReplace $Temporary $Destination
    } else {
        [IO.File]::Move($Temporary, $Destination)
    }
}

function Expand-ValidatedPackage {
    param([Parameter(Mandatory = $true)]$Validated)
    $Root = Join-Path ([IO.Path]::GetTempPath()) (
        'foundation-' + [Guid]::NewGuid().ToString('N')
    )
    [IO.Directory]::CreateDirectory($Root) | Out-Null
    foreach ($Name in $Validated.entries.Keys) {
        $Destination = [IO.Path]::GetFullPath(
            (Join-Path $Root ([string]$Name).Replace('/', '\'))
        )
        if (-not $Destination.StartsWith(
            $Root + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            Throw-Foundation 'UNSAFE_PATH' 'ZIP extraction escaped staging'
        }
        $Parent = Split-Path -Parent $Destination
        if (-not (Test-Path -LiteralPath $Parent)) {
            [IO.Directory]::CreateDirectory($Parent) | Out-Null
        }
        $Input = $Validated.entries[$Name].Open()
        $Output = [IO.File]::Open(
            $Destination,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None
        )
        try {
            $Input.CopyTo($Output)
            $Output.Flush($true)
        } finally {
            $Output.Dispose()
            $Input.Dispose()
        }
    }
    return $Root
}

function New-Snapshot {
    param(
        [Parameter(Mandatory = $true)]$Validated,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)]$Plan
    )
    New-SafeDirectory (Split-Path -Parent $Paths.state_root) $HomeRoot
    New-SafeDirectory $Paths.state_root $HomeRoot
    New-SafeDirectory $Paths.backup_root $HomeRoot
    $SnapshotId = (
        [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' +
        [Guid]::NewGuid().ToString('N')
    )
    $SnapshotRoot = Join-Path $Paths.backup_root $SnapshotId
    New-SafeDirectory $SnapshotRoot $HomeRoot
    $ManagedRoot = Join-Path $SnapshotRoot 'managed'
    [IO.Directory]::CreateDirectory($ManagedRoot) | Out-Null
    $Existed = @()
    foreach ($Root in @(
        $Validated.manifest.managed_surface.exact_directories
    )) {
        $Source = Resolve-HomePath ([string]$Root) $HomeRoot
        if (Test-Path -LiteralPath $Source -PathType Container) {
            $Destination = Join-Path $ManagedRoot (
                ([string]$Root).Replace('/', '\')
            )
            Copy-TreeSafe $Source $Destination
            $Existed += [string]$Root
        }
    }
    foreach ($Relative in @(
        $Validated.manifest.managed_surface.replace_files
    )) {
        $Source = Resolve-HomePath ([string]$Relative) $HomeRoot
        if (Test-Path -LiteralPath $Source -PathType Leaf) {
            $Destination = Join-Path $ManagedRoot (
                ([string]$Relative).Replace('/', '\')
            )
            Copy-FileSafe $Source $Destination
            $Existed += [string]$Relative
        }
    }
    $PriorActive = $null
    if (Test-Path -LiteralPath $Paths.active -PathType Leaf) {
        $PriorActive = Read-JsonFile $Paths.active
    }
    $Snapshot = [pscustomobject][ordered]@{
        schema_version = 1
        snapshot_id = $SnapshotId
        target = [string]$Validated.manifest.target
        release_version = [string]$Validated.manifest.version
        managed_surface = $Validated.manifest.managed_surface
        existed = @($Existed | Sort-Object)
        prior_active = $PriorActive
        quarantined_unknown = @($Plan.quarantined_unknown)
    }
    $SnapshotPath = Join-Path $SnapshotRoot 'snapshot.json'
    Write-JsonFile $Snapshot $SnapshotPath
    return [pscustomobject]@{
        root = $SnapshotRoot
        metadata = $Snapshot
        metadata_path = $SnapshotPath
    }
}

function Restore-Snapshot {
    param(
        [Parameter(Mandatory = $true)][string]$SnapshotPath,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)]$Paths
    )
    $Snapshot = Read-JsonFile $SnapshotPath
    $SnapshotRoot = Split-Path -Parent $SnapshotPath
    $ManagedRoot = Join-Path $SnapshotRoot 'managed'
    $Existed = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($Value in @($Snapshot.existed)) {
        $null = $Existed.Add([string]$Value)
    }
    foreach ($Root in @($Snapshot.managed_surface.exact_directories)) {
        $Destination = Resolve-HomePath ([string]$Root) $HomeRoot
        Remove-TreeSafe $Destination $HomeRoot
        if ($Existed.Contains([string]$Root)) {
            $Source = Join-Path $ManagedRoot (
                ([string]$Root).Replace('/', '\')
            )
            Copy-TreeSafe $Source $Destination
        }
    }
    foreach ($Relative in @($Snapshot.managed_surface.replace_files)) {
        $Destination = Resolve-HomePath ([string]$Relative) $HomeRoot
        Assert-SafeAncestors $Destination $HomeRoot
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            Remove-Item -LiteralPath $Destination -Force
        }
        if ($Existed.Contains([string]$Relative)) {
            $Source = Join-Path $ManagedRoot (
                ([string]$Relative).Replace('/', '\')
            )
            Copy-Atomic $Source $Destination $HomeRoot
        }
    }
    if ($null -ne $Snapshot.prior_active) {
        Write-JsonFile $Snapshot.prior_active $Paths.active
    } elseif (Test-Path -LiteralPath $Paths.active -PathType Leaf) {
        Remove-Item -LiteralPath $Paths.active -Force
    }
    if (Test-Path -LiteralPath $Paths.pending -PathType Leaf) {
        Remove-Item -LiteralPath $Paths.pending -Force
    }
}

function Invoke-MutationCheckpoint {
    $script:MutationCount++
    if ($env:FOUNDATION_ACCEPTANCE_MODE -cne '1') { return }
    if ($env:FOUNDATION_CRASH_AFTER -match '^[0-9]+$' -and
        $script:MutationCount -eq [int]$env:FOUNDATION_CRASH_AFTER) {
        [Environment]::Exit(99)
    }
    if ($env:FOUNDATION_FAIL_AFTER -match '^[0-9]+$' -and
        $script:MutationCount -eq [int]$env:FOUNDATION_FAIL_AFTER) {
        Throw-Foundation 'INSTALL_FAILED' 'Injected acceptance failure'
    }
}

function Test-InstalledState {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$ActualClientId,
        [Parameter(Mandatory = $true)][string]$ActualClientVersion
    )
    Assert-ClientContract $State.client $ActualClientId `
        $ActualClientVersion
    $ExpectedByRoot = @{}
    foreach ($Root in @($State.managed_surface.exact_directories)) {
        $ExpectedByRoot[[string]$Root] = New-Object (
            'Collections.Generic.HashSet[string]'
        ) ([StringComparer]::OrdinalIgnoreCase)
    }
    foreach ($Row in @($State.installed_files)) {
        $Destination = Resolve-HomePath ([string]$Row.path) $HomeRoot
        Assert-SafeAncestors $Destination $HomeRoot
        if (-not (Test-Path -LiteralPath $Destination -PathType Leaf) -or
            (Get-FileSha256 $Destination) -cne [string]$Row.sha256 -or
            (Get-Item -LiteralPath $Destination).Length -ne [int64]$Row.bytes) {
            Throw-Foundation 'ACTIVE_DRIFT' (
                "Installed file differs: $($Row.path)"
            )
        }
        foreach ($Root in @($State.managed_surface.exact_directories)) {
            $Prefix = [string]$Root + '/'
            if (([string]$Row.path).StartsWith(
                $Prefix,
                [StringComparison]::Ordinal
            )) {
                $null = $ExpectedByRoot[[string]$Root].Add(
                    [string]$Row.path
                )
            }
        }
    }
    foreach ($Root in @($State.managed_surface.exact_directories)) {
        $Absolute = Resolve-HomePath ([string]$Root) $HomeRoot
        Assert-SafeAncestors $Absolute $HomeRoot
        $Actual = New-Object 'Collections.Generic.HashSet[string]' (
            [StringComparer]::OrdinalIgnoreCase
        )
        if (Test-Path -LiteralPath $Absolute -PathType Container) {
            Assert-SafeDirectory $Absolute
            foreach ($File in @(
                Get-ChildItem -LiteralPath $Absolute -Recurse -Force -File
            )) {
                if ($File.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                    Throw-Foundation 'UNSAFE_PATH' 'Managed tree has reparse point'
                }
                $Relative = $File.FullName.Substring(
                    ([IO.Path]::GetFullPath($HomeRoot)).Length
                ).TrimStart('\').Replace('\', '/')
                $null = $Actual.Add($Relative)
            }
        }
        $Expected = $ExpectedByRoot[[string]$Root]
        if ($Actual.Count -ne $Expected.Count) {
            Throw-Foundation 'ACTIVE_DRIFT' (
                "Exact directory differs: $Root"
            )
        }
        foreach ($Value in $Actual) {
            if (-not $Expected.Contains($Value)) {
                Throw-Foundation 'ACTIVE_DRIFT' (
                    "Unexpected exact-directory file: $Value"
                )
            }
        }
    }
    return [pscustomobject][ordered]@{
        status = 'HEALTHY'
        target = [string]$State.target
        release_version = [string]$State.release_version
        installed_file_count = @($State.installed_files).Count
    }
}

function Invoke-Install {
    param(
        [Parameter(Mandatory = $true)]$Validated,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$ActualClientId,
        [Parameter(Mandatory = $true)][string]$ActualClientVersion
    )
    $Plan = New-FoundationPlan $Validated $HomeRoot $ActualClientId `
        $ActualClientVersion
    $Paths = Get-FoundationPaths $HomeRoot ([string]$Validated.manifest.target)
    $Staging = $null
    $Snapshot = $null
    try {
        $Staging = Expand-ValidatedPackage $Validated
        $Snapshot = New-Snapshot $Validated $HomeRoot $Paths $Plan
        $Pending = [pscustomobject][ordered]@{
            schema_version = 1
            target = [string]$Validated.manifest.target
            snapshot_path = [string]$Snapshot.metadata_path
            release_version = [string]$Validated.manifest.version
        }
        Write-JsonFile $Pending $Paths.pending
        foreach ($Root in @(
            $Validated.manifest.managed_surface.exact_directories
        )) {
            $Destination = Resolve-HomePath ([string]$Root) $HomeRoot
            Remove-TreeSafe $Destination $HomeRoot
            New-SafeDirectory $Destination $HomeRoot
            Invoke-MutationCheckpoint
        }
        foreach ($Row in @($Validated.manifest.files)) {
            $Source = Join-Path $Staging (
                ([string]$Row.path).Replace('/', '\')
            )
            $Destination = Resolve-HomePath ([string]$Row.path) $HomeRoot
            Copy-Atomic $Source $Destination $HomeRoot
            Invoke-MutationCheckpoint
        }
        $Installed = @(
            foreach ($Row in @($Validated.manifest.files)) {
                [pscustomobject][ordered]@{
                    path = [string]$Row.path
                    sha256 = [string]$Row.sha256
                    bytes = [int64]$Row.bytes
                }
            }
        )
        $State = [pscustomobject][ordered]@{
            schema_version = 1
            target = [string]$Validated.manifest.target
            release_version = [string]$Validated.manifest.version
            client = $Validated.manifest.client
            foundation_engine_version = [string]$Validated.manifest.foundation_engine_version
            package_sha256 = Get-FileSha256 $Validated.package_path
            managed_surface = $Validated.manifest.managed_surface
            installed_files = $Installed
            quarantined_unknown = @($Plan.quarantined_unknown)
            snapshot_path = [string]$Snapshot.metadata_path
        }
        $null = Test-InstalledState $State $HomeRoot $ActualClientId `
            $ActualClientVersion
        Write-JsonFile $State $Paths.active
        Remove-Item -LiteralPath $Paths.pending -Force
        return [pscustomobject][ordered]@{
            status = 'INSTALLED'
            target = [string]$State.target
            release_version = [string]$State.release_version
            installed_file_count = @($State.installed_files).Count
            quarantined_unknown = @($State.quarantined_unknown)
        }
    } catch {
        if ($null -ne $Snapshot -and
            (Test-Path -LiteralPath $Snapshot.metadata_path -PathType Leaf)) {
            try {
                Restore-Snapshot $Snapshot.metadata_path $HomeRoot $Paths
            } catch {
                Throw-Foundation 'RECOVERY_REQUIRED' (
                    'Install failed and automatic rollback also failed'
                )
            }
        }
        throw
    } finally {
        if ($null -ne $Staging -and
            (Test-Path -LiteralPath $Staging -PathType Container)) {
            Remove-Item -LiteralPath $Staging -Recurse -Force
        }
    }
}

function Invoke-Doctor {
    param(
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$TargetName,
        [Parameter(Mandatory = $true)][string]$ActualClientId,
        [Parameter(Mandatory = $true)][string]$ActualClientVersion
    )
    $Paths = Get-FoundationPaths $HomeRoot $TargetName
    if (Test-Path -LiteralPath $Paths.pending -PathType Leaf) {
        Throw-Foundation 'RECOVERY_REQUIRED' (
            'Interrupted transaction requires rollback'
        )
    }
    $State = Read-ActiveState $Paths
    return Test-InstalledState $State $HomeRoot $ActualClientId `
        $ActualClientVersion
}

function Invoke-Inventory {
    param(
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$TargetName
    )
    $Paths = Get-FoundationPaths $HomeRoot $TargetName
    Assert-NoRecoveryPending $Paths
    $State = Read-ActiveState $Paths
    return [pscustomobject][ordered]@{
        status = 'INSTALLED'
        target = [string]$State.target
        release_version = [string]$State.release_version
        client = $State.client
        installed_file_count = @($State.installed_files).Count
        managed_surface = $State.managed_surface
        quarantined_unknown = @($State.quarantined_unknown)
    }
}

function Invoke-Rollback {
    param(
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$TargetName
    )
    $Paths = Get-FoundationPaths $HomeRoot $TargetName
    $SnapshotPath = $null
    if (Test-Path -LiteralPath $Paths.pending -PathType Leaf) {
        $Pending = Read-JsonFile $Paths.pending
        $SnapshotPath = [string]$Pending.snapshot_path
    } elseif (Test-Path -LiteralPath $Paths.active -PathType Leaf) {
        $State = Read-JsonFile $Paths.active
        $SnapshotPath = [string]$State.snapshot_path
    }
    if ([string]::IsNullOrWhiteSpace($SnapshotPath) -or
        -not (Test-Path -LiteralPath $SnapshotPath -PathType Leaf)) {
        Throw-Foundation 'NOT_INSTALLED' 'No rollback snapshot exists'
    }
    $BackupRoot = [IO.Path]::GetFullPath($Paths.backup_root)
    $Resolved = [IO.Path]::GetFullPath($SnapshotPath)
    if (-not $Resolved.StartsWith(
        $BackupRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        Throw-Foundation 'UNSAFE_PATH' 'Rollback snapshot escaped backup root'
    }
    Restore-Snapshot $Resolved $HomeRoot $Paths
    return [pscustomobject][ordered]@{
        status = 'ROLLED_BACK'
        target = $TargetName
        snapshot_path = $Resolved
    }
}

$Validated = $null
try {
    $TargetHome = [IO.Path]::GetFullPath($TargetHome)
    Assert-SafeDirectory $TargetHome
    if ($Command -in @('plan', 'install') -and
        [string]::IsNullOrWhiteSpace($Package)) {
        Throw-Foundation 'INVALID_ARGUMENT' 'Package is required'
    }
    if ($Command -in @('inventory', 'rollback') -and
        [string]::IsNullOrWhiteSpace($Target)) {
        Throw-Foundation 'INVALID_ARGUMENT' 'Target is required'
    }
    if (-not [string]::IsNullOrWhiteSpace($Package)) {
        $Validated = Open-ValidatedPackage $Package
        if (-not [string]::IsNullOrWhiteSpace($Target) -and
            $Target -cne [string]$Validated.manifest.target) {
            Throw-Foundation 'INVALID_ARGUMENT' 'Target differs from package'
        }
        $Target = [string]$Validated.manifest.target
    }
    if ($Command -eq 'doctor' -and
        [string]::IsNullOrWhiteSpace($Target)) {
        Throw-Foundation 'INVALID_ARGUMENT' (
            'Doctor requires Package or Target'
        )
    }
    if ($Command -in @('plan', 'install', 'doctor') -and (
        [string]::IsNullOrWhiteSpace($ClientId) -or
        [string]::IsNullOrWhiteSpace($ClientVersion)
    )) {
        Throw-Foundation 'UNSUPPORTED_CLIENT' (
            'ClientId and ClientVersion are required'
        )
    }
    $Result = switch ($Command) {
        'plan' {
            New-FoundationPlan $Validated $TargetHome $ClientId `
                $ClientVersion
            break
        }
        'install' {
            Invoke-Install $Validated $TargetHome $ClientId $ClientVersion
            break
        }
        'doctor' {
            Invoke-Doctor $TargetHome $Target $ClientId $ClientVersion
            break
        }
        'inventory' {
            Invoke-Inventory $TargetHome $Target
            break
        }
        'rollback' {
            Invoke-Rollback $TargetHome $Target
            break
        }
    }
    Write-Result $Result
    exit 0
} catch {
    $Code = [string]$_.Exception.Data['FoundationCode']
    if ([string]::IsNullOrWhiteSpace($Code)) {
        $Code = 'INVALID_PACKAGE'
    }
    $Exit = $script:ExitCode[$Code]
    if ($null -eq $Exit) { $Exit = 30 }
    Write-Result ([pscustomobject][ordered]@{
        status = 'BLOCKED'
        code = $Code
        message = [string]$_.Exception.Message
    })
    exit $Exit
} finally {
    if ($null -ne $Validated) {
        Close-ValidatedPackage $Validated
    }
}
