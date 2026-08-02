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
$Utf8NoBom = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$script:EngineVersion = '0.2.2'
$script:ProtocolVersion = 1
$script:BlockedUserEnvironment = @(
    'ALL_PROXY',
    'APPDATA',
    'COMSPEC',
    'HOME',
    'HTTPS_PROXY',
    'HTTP_PROXY',
    'LOCALAPPDATA',
    'NO_PROXY',
    'PATH',
    'PATHEXT',
    'SYSTEMROOT',
    'TEMP',
    'TMP',
    'USERPROFILE'
)
$script:ExitCode = @{
    INVALID_ARGUMENT = 2
    UNSUPPORTED_CLIENT = 10
    DOWNGRADE_BLOCKED = 10
    NOT_INSTALLED = 20
    RECOVERY_REQUIRED = 20
    LOCKED = 20
    INVALID_PACKAGE = 30
    INSTALL_FAILED = 30
    ACTIVE_DRIFT = 30
    UNSAFE_PATH = 40
}
$script:MutationCount = 0
$script:RollbackMutationCount = 0

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

function Assert-TargetName {
    param([Parameter(Mandatory = $true)][string]$TargetName)
    if ($TargetName -cnotmatch '^[a-z][a-z0-9-]{1,31}$') {
        Throw-Foundation 'INVALID_ARGUMENT' 'Target name is invalid'
    }
}

function Test-PathWithin {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $Absolute = [IO.Path]::GetFullPath($Candidate)
    $Boundary = [IO.Path]::GetFullPath($Root)
    return $Absolute.StartsWith(
        $Boundary + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )
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
        if ($Value.Equals(
                $ProtectedValue,
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            $Value.StartsWith(
                $ProtectedValue + '/',
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            $ProtectedValue.StartsWith(
                $Value + '/',
                [StringComparison]::OrdinalIgnoreCase
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

function Test-ObjectProperty {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Name
    )
    return $null -ne $Value.PSObject.Properties[$Name]
}

function Get-MergeTomlFiles {
    param([Parameter(Mandatory = $true)]$Surface)
    if (Test-ObjectProperty $Surface 'merge_toml_files') {
        return @($Surface.merge_toml_files)
    }
    return @()
}

function Assert-ManagedSurface {
    param([Parameter(Mandatory = $true)]$Surface)
    if ($null -eq $Surface -or
        $Surface -isnot [Management.Automation.PSCustomObject]) {
        Throw-Foundation 'INVALID_PACKAGE' 'managed surface must be an object'
    }
    $Required = @('exact_directories', 'replace_files', 'preserved_paths')
    $Allowed = @($Required + 'merge_toml_files')
    foreach ($Name in @($Surface.PSObject.Properties.Name)) {
        if ($Allowed -cnotcontains [string]$Name) {
            Throw-Foundation 'INVALID_PACKAGE' 'managed surface properties differ'
        }
    }
    foreach ($Name in $Required) {
        if (-not (Test-ObjectProperty $Surface $Name)) {
            Throw-Foundation 'INVALID_PACKAGE' 'managed surface properties differ'
        }
    }
    Assert-StringArray @($Surface.exact_directories) 'exact directories'
    Assert-StringArray @($Surface.replace_files) 'replace files'
    Assert-StringArray @($Surface.preserved_paths) 'preserved paths' -AllowProtected
    $MergeFiles = @(Get-MergeTomlFiles $Surface)
    Assert-StringArray $MergeFiles 'merge TOML files'
    foreach ($Path in $MergeFiles) {
        if (-not ([string]$Path).EndsWith(
                '.toml', [StringComparison]::OrdinalIgnoreCase)) {
            Throw-Foundation 'INVALID_PACKAGE' 'Merge file is not TOML'
        }
    }
}

function Assert-StringArray {
    param(
        [AllowEmptyCollection()][object[]]$Values,
        [Parameter(Mandatory = $true)][string]$Label,
        [switch]$AllowProtected,
        [switch]$AllowUnsorted
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
        if (-not $AllowUnsorted -and
            $null -ne $Previous -and
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

function Sort-OrdinalStrings {
    param([AllowEmptyCollection()][object[]]$Values)
    $Result = [string[]]@(
        foreach ($Value in @($Values)) {
            [string]$Value
        }
    )
    [Array]::Sort($Result, [StringComparer]::Ordinal)
    return @($Result)
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
        'environment',
        'files'
    ) 'package manifest'
    if ($Manifest.schema_version -ne 1 -or
        $Manifest.target -cnotmatch '^[a-z][a-z0-9-]{1,31}$' -or
        $Manifest.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        $Manifest.foundation_engine_version -notmatch
            '^[0-9]+\.[0-9]+\.[0-9]+$') {
        Throw-Foundation 'INVALID_PACKAGE' 'Package manifest constants differ'
    }
    if ([string]$Manifest.foundation_engine_version -cne
        $script:EngineVersion) {
        Throw-Foundation 'INVALID_PACKAGE' (
            "Package requires Foundation engine " +
            "$($Manifest.foundation_engine_version); running engine is " +
            $script:EngineVersion
        )
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
    Assert-ManagedSurface $Manifest.managed_surface
    $MergeTomlFiles = @(Get-MergeTomlFiles $Manifest.managed_surface)
    if (@($Manifest.managed_surface.exact_directories).Count -eq 0 -or
        @($Manifest.managed_surface.replace_files).Count -eq 0 -or
        @($Manifest.managed_surface.preserved_paths).Count -eq 0) {
        Throw-Foundation 'INVALID_PACKAGE' 'Managed surface is empty'
    }
    $ManagedRoots = @(
        @($Manifest.managed_surface.exact_directories) +
        @($Manifest.managed_surface.replace_files) +
        $MergeTomlFiles
    )
    for ($LeftIndex = 0; $LeftIndex -lt $ManagedRoots.Count; $LeftIndex++) {
        for (
            $RightIndex = $LeftIndex + 1;
            $RightIndex -lt $ManagedRoots.Count;
            $RightIndex++
        ) {
            $Left = [string]$ManagedRoots[$LeftIndex]
            $Right = [string]$ManagedRoots[$RightIndex]
            if ($Left.Equals(
                    $Right,
                    [StringComparison]::OrdinalIgnoreCase
                ) -or
                $Left.StartsWith(
                    $Right + '/',
                    [StringComparison]::OrdinalIgnoreCase
                ) -or
                $Right.StartsWith(
                    $Left + '/',
                    [StringComparison]::OrdinalIgnoreCase
                )) {
                Throw-Foundation 'INVALID_PACKAGE' (
                    "Managed roots overlap: $Left and $Right"
                )
            }
        }
    }
    foreach ($Root in @(
        @($Manifest.managed_surface.exact_directories) +
        @($Manifest.managed_surface.replace_files) +
        $MergeTomlFiles
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
    Assert-EnvironmentContract $Manifest.environment

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
            @($Manifest.managed_surface.replace_files) +
            $MergeTomlFiles
        ) -icontains $Path
        if (-not $Managed) {
            foreach ($Root in @($Manifest.managed_surface.exact_directories)) {
                if ($Path.StartsWith(
                    [string]$Root + '/',
                    [StringComparison]::OrdinalIgnoreCase
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
    foreach ($Replace in @($Manifest.managed_surface.replace_files)) {
        if (-not $FilePaths.Contains([string]$Replace)) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Replace file has no payload row: $Replace"
            )
        }
    }
    foreach ($MergeFile in $MergeTomlFiles) {
        if (-not $FilePaths.Contains([string]$MergeFile)) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Merge TOML file has no payload row: $MergeFile"
            )
        }
    }
    foreach ($Root in @($Manifest.managed_surface.exact_directories)) {
        $Covered = $false
        foreach ($Path in $FilePaths) {
            if (([string]$Path).StartsWith(
                [string]$Root + '/',
                [StringComparison]::OrdinalIgnoreCase
            )) {
                $Covered = $true
                break
            }
        }
        if (-not $Covered) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Exact directory has no payload rows: $Root"
            )
        }
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
    if ($ActualId -cne [string]$Expected.id) {
        Throw-Foundation 'UNSUPPORTED_CLIENT' (
            "Supported client is $($Expected.id) $($Expected.supported_version)"
        )
    }
    if ($ActualVersion.Length -gt 128 -or
        $ActualVersion -notmatch
            '^[0-9]+(?:\.[0-9]+){2,7}(?:-[0-9A-Za-z.-]+)?$') {
        Throw-Foundation 'UNSUPPORTED_CLIENT' (
            'Client version evidence is invalid'
        )
    }
}

function Assert-EnvironmentContract {
    param([Parameter(Mandatory = $true)]$Contract)
    Assert-ExactProperties $Contract @(
        'scope',
        'set'
    ) 'environment contract'
    if ([string]$Contract.scope -cne 'current-user') {
        Throw-Foundation 'INVALID_PACKAGE' (
            'Only current-user environment changes are supported'
        )
    }
    $Names = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::Ordinal
    )
    $Previous = $null
    foreach ($Row in @($Contract.set)) {
        Assert-ExactProperties $Row @('name', 'value') 'environment row'
        $Name = [string]$Row.name
        if ($Name -notmatch '^[A-Z][A-Z0-9_]{1,63}$' -or
            $script:BlockedUserEnvironment -ccontains $Name -or
            $Name -match '(^|_)(KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?)(_|$)' -or
            -not $Names.Add($Name) -or
            $Row.value -isnot [string] -or
            ([string]$Row.value).Length -gt 512 -or
            [string]$Row.value -match '[\x00-\x1f\x7f]' -or
            ($null -ne $Previous -and
                [StringComparer]::Ordinal.Compare($Previous, $Name) -ge 0)) {
            Throw-Foundation 'INVALID_PACKAGE' (
                'Environment contract contains an unsafe or unsorted row'
            )
        }
        $Previous = $Name
    }
}

function Get-EnvironmentContractDigest {
    param([Parameter(Mandatory = $true)]$Contract)
    Assert-EnvironmentContract $Contract
    $Builder = New-Object Text.StringBuilder
    $null = $Builder.Append([string]$Contract.scope).Append("`n")
    foreach ($Row in @($Contract.set)) {
        $null = $Builder.Append([string]$Row.name).
            Append("`0").
            Append([string]$Row.value).
            Append("`n")
    }
    return Get-BytesSha256 ([Text.Encoding]::UTF8.GetBytes(
        $Builder.ToString()
    ))
}

function Get-AcceptanceEnvironmentPath {
    param([Parameter(Mandatory = $true)][string]$HomeRoot)
    return Join-Path (
        Join-Path $HomeRoot '.llm-foundation'
    ) 'acceptance-user-environment.json'
}

function Read-AcceptanceEnvironment {
    param([Parameter(Mandatory = $true)][string]$HomeRoot)
    $Path = Get-AcceptanceEnvironmentPath $HomeRoot
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject][ordered]@{
            schema_version = 1
            values = @()
        }
    }
    Assert-SafeAncestors $Path $HomeRoot
    $State = Read-JsonFile $Path
    Assert-ExactProperties $State @(
        'schema_version',
        'values'
    ) 'acceptance environment state'
    if ($State.schema_version -ne 1) {
        Throw-Foundation 'ACTIVE_DRIFT' (
            'Acceptance environment state schema differs'
        )
    }
    $Names = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::Ordinal
    )
    $Previous = $null
    foreach ($Row in @($State.values)) {
        Assert-ExactProperties $Row @('name', 'value') (
            'acceptance environment row'
        )
        $Name = [string]$Row.name
        if ($Name -notmatch '^[A-Z][A-Z0-9_]{1,63}$' -or
            -not $Names.Add($Name) -or
            $Row.value -isnot [string] -or
            ($null -ne $Previous -and
                [StringComparer]::Ordinal.Compare($Previous, $Name) -ge 0)) {
            Throw-Foundation 'ACTIVE_DRIFT' (
                'Acceptance environment state is invalid'
            )
        }
        $Previous = $Name
    }
    return $State
}

function Get-CurrentUserEnvironmentValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    if ($env:FOUNDATION_ACCEPTANCE_MODE -ceq '1') {
        $State = Read-AcceptanceEnvironment $HomeRoot
        foreach ($Row in @($State.values)) {
            if ([string]$Row.name -ceq $Name) {
                return [pscustomobject][ordered]@{
                    exists = $true
                    value = [string]$Row.value
                }
            }
        }
        return [pscustomobject][ordered]@{
            exists = $false
            value = $null
        }
    }
    $Value = [Environment]::GetEnvironmentVariable(
        $Name,
        [EnvironmentVariableTarget]::User
    )
    return [pscustomobject][ordered]@{
        exists = $null -ne $Value
        value = $Value
    }
}

function Set-CurrentUserEnvironmentValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowNull()][string]$Value,
        [Parameter(Mandatory = $true)][bool]$Exists,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    if ($env:FOUNDATION_ACCEPTANCE_MODE -ceq '1') {
        $State = Read-AcceptanceEnvironment $HomeRoot
        $Values = @(
            foreach ($Row in @($State.values)) {
                if ([string]$Row.name -cne $Name) {
                    [pscustomobject][ordered]@{
                        name = [string]$Row.name
                        value = [string]$Row.value
                    }
                }
            }
        )
        if ($Exists) {
            $Values += [pscustomobject][ordered]@{
                name = $Name
                value = [string]$Value
            }
        }
        $Payload = [pscustomobject][ordered]@{
            schema_version = 1
            values = @($Values | Sort-Object name)
        }
        $Path = Get-AcceptanceEnvironmentPath $HomeRoot
        New-SafeDirectory (Split-Path -Parent $Path) $HomeRoot
        Write-JsonFile $Payload $Path
        return
    }
    try {
        [Environment]::SetEnvironmentVariable(
            $Name,
            $(if ($Exists) { [string]$Value } else { $null }),
            [EnvironmentVariableTarget]::User
        )
    } catch {
        Throw-Foundation 'INSTALL_FAILED' (
            "Unable to update current-user environment variable: $Name"
        )
    }
}

function Get-EnvironmentActions {
    param(
        [Parameter(Mandatory = $true)]$Contract,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    Assert-EnvironmentContract $Contract
    return @(
        foreach ($Row in @($Contract.set)) {
            $Current = Get-CurrentUserEnvironmentValue (
                [string]$Row.name
            ) $HomeRoot
            $Action = if (-not [bool]$Current.exists) {
                'CREATE'
            } elseif ([string]$Current.value -ceq [string]$Row.value) {
                'UNCHANGED'
            } else {
                'UPDATE'
            }
            [pscustomobject][ordered]@{
                name = [string]$Row.name
                action = $Action
                value = [string]$Row.value
            }
        }
    )
}

function Apply-EnvironmentContract {
    param(
        [Parameter(Mandatory = $true)]$Contract,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    foreach ($Row in @($Contract.set)) {
        Set-CurrentUserEnvironmentValue (
            [string]$Row.name
        ) ([string]$Row.value) $true $HomeRoot
        Invoke-MutationCheckpoint
    }
}

function Test-EnvironmentContract {
    param(
        [Parameter(Mandatory = $true)]$Contract,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    Assert-EnvironmentContract $Contract
    foreach ($Row in @($Contract.set)) {
        $Current = Get-CurrentUserEnvironmentValue (
            [string]$Row.name
        ) $HomeRoot
        if (-not [bool]$Current.exists -or
            [string]$Current.value -cne [string]$Row.value) {
            Throw-Foundation 'ACTIVE_DRIFT' (
                "Current-user environment differs: $($Row.name)"
            )
        }
    }
}

function Get-FoundationPaths {
    param(
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$TargetName
    )
    Assert-TargetName $TargetName
    $Root = [IO.Path]::GetFullPath($HomeRoot)
    $FoundationRoot = [IO.Path]::GetFullPath(
        (Join-Path $Root '.llm-foundation')
    )
    $StateRoot = [IO.Path]::GetFullPath(
        (Join-Path (Join-Path $FoundationRoot 'state') $TargetName)
    )
    $BackupRoot = [IO.Path]::GetFullPath(
        (Join-Path (Join-Path $FoundationRoot 'backups') $TargetName)
    )
    $LocksRoot = [IO.Path]::GetFullPath(
        (Join-Path $FoundationRoot 'locks')
    )
    foreach ($Path in @($StateRoot, $BackupRoot, $LocksRoot)) {
        if (-not (Test-PathWithin $Path $FoundationRoot)) {
            Throw-Foundation 'UNSAFE_PATH' 'Foundation state escaped its root'
        }
        Assert-SafeAncestors $Path $Root
    }
    return [pscustomobject]@{
        target = $TargetName
        foundation_root = $FoundationRoot
        state_root = $StateRoot
        active = Join-Path $StateRoot 'active.json'
        pending = Join-Path $StateRoot 'pending.json'
        rollback_journal = Join-Path $StateRoot 'rollback.json'
        backup_root = $BackupRoot
        locks_root = $LocksRoot
        lock = Join-Path $LocksRoot ($TargetName + '.lock')
    }
}

function Assert-ActiveState {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$ExpectedTarget
    )
    Assert-ExactProperties $State @(
        'schema_version',
        'target',
        'release_version',
        'client',
        'foundation_engine_version',
        'package_sha256',
        'managed_surface',
        'environment',
        'installed_files',
        'quarantined_unknown',
        'snapshot_path',
        'snapshot_sha256'
    ) 'active state'
    if ($State.schema_version -ne 1 -or
        [string]$State.target -cne $ExpectedTarget -or
        $State.release_version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        $State.foundation_engine_version -notmatch
            '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        $State.package_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $State.snapshot_sha256 -notmatch '^[0-9a-f]{64}$' -or
        [string]::IsNullOrWhiteSpace([string]$State.snapshot_path)) {
        Throw-Foundation 'INVALID_PACKAGE' 'Active state is invalid'
    }
    Assert-EnvironmentContract $State.environment
}

function Assert-PendingState {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$ExpectedTarget
    )
    Assert-ExactProperties $State @(
        'schema_version',
        'target',
        'snapshot_path',
        'snapshot_sha256',
        'release_version',
        'managed_surface',
        'environment'
    ) 'pending state'
    if ($State.schema_version -ne 1 -or
        [string]$State.target -cne $ExpectedTarget -or
        $State.release_version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        $State.snapshot_sha256 -notmatch '^[0-9a-f]{64}$' -or
        [string]::IsNullOrWhiteSpace([string]$State.snapshot_path)) {
        Throw-Foundation 'INVALID_PACKAGE' 'Pending state is invalid'
    }
    Assert-EnvironmentContract $State.environment
}

function Assert-RollbackJournal {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$ExpectedTarget
    )
    Assert-ExactProperties $State @(
        'schema_version',
        'target',
        'snapshot_path',
        'snapshot_sha256',
        'managed_surface',
        'environment'
    ) 'rollback journal'
    if ($State.schema_version -ne 1 -or
        [string]$State.target -cne $ExpectedTarget -or
        $State.snapshot_sha256 -notmatch '^[0-9a-f]{64}$' -or
        [string]::IsNullOrWhiteSpace([string]$State.snapshot_path)) {
        Throw-Foundation 'INVALID_PACKAGE' 'Rollback journal is invalid'
    }
    $null = Get-ManagedSurfaceDigest $State.managed_surface
    Assert-EnvironmentContract $State.environment
}

function Enter-TargetLock {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    New-SafeDirectory $Paths.foundation_root $HomeRoot
    New-SafeDirectory $Paths.locks_root $HomeRoot
    Assert-SafeAncestors $Paths.lock $HomeRoot
    if (Test-Path -LiteralPath $Paths.lock) {
        $ExistingLock = Get-Item -LiteralPath $Paths.lock -Force
        if ($ExistingLock.PSIsContainer -or
            ($ExistingLock.Attributes -band
                [IO.FileAttributes]::ReparsePoint)) {
            Throw-Foundation 'UNSAFE_PATH' 'Lock entry is not a regular file'
        }
    }
    if ($null -eq ('FoundationLockFile' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class FoundationLockFile
{
    private const uint GENERIC_READ = 0x80000000;
    private const uint GENERIC_WRITE = 0x40000000;
    private const uint OPEN_ALWAYS = 4;
    private const uint FILE_ATTRIBUTE_NORMAL = 0x00000080;
    private const uint FILE_ATTRIBUTE_DIRECTORY = 0x00000010;
    private const uint FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400;
    private const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;

    [StructLayout(LayoutKind.Sequential)]
    private struct BY_HANDLE_FILE_INFORMATION
    {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string name,
        uint access,
        uint share,
        IntPtr security,
        uint creation,
        uint flags,
        IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle handle,
        out BY_HANDLE_FILE_INFORMATION information);

    public static FileStream OpenExclusiveRegular(string path)
    {
        SafeFileHandle handle = CreateFile(
            path,
            GENERIC_READ | GENERIC_WRITE,
            0,
            IntPtr.Zero,
            OPEN_ALWAYS,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
            IntPtr.Zero);
        if (handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new IOException(
                "Cannot acquire lock: " + new Win32Exception(error).Message);
        }
        BY_HANDLE_FILE_INFORMATION information;
        if (!GetFileInformationByHandle(handle, out information))
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new IOException(
                "Cannot inspect lock: " + new Win32Exception(error).Message);
        }
        if ((information.FileAttributes & (
                FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) != 0)
        {
            handle.Dispose();
            throw new UnauthorizedAccessException(
                "Lock entry is not a regular file");
        }
        return new FileStream(handle, FileAccess.ReadWrite, 4096, false);
    }
}
'@
    }
    try {
        $Handle = [FoundationLockFile]::OpenExclusiveRegular($Paths.lock)
    } catch {
        $Cursor = $_.Exception
        while ($null -ne $Cursor.InnerException) {
            $Cursor = $Cursor.InnerException
        }
        if ($Cursor -is [UnauthorizedAccessException]) {
            Throw-Foundation 'UNSAFE_PATH' $Cursor.Message
        }
        Throw-Foundation 'LOCKED' (
            'Another destructive Foundation operation is active'
        )
    }
    try {
        $Handle.SetLength(0)
        $Payload = (New-Object Text.UTF8Encoding($false)).GetBytes(
            ("pid={0};utc={1}`n" -f
                $PID,
                [DateTime]::UtcNow.ToString('o'))
        )
        $Handle.Write($Payload, 0, $Payload.Length)
        $Handle.Flush($true)
        if ($env:FOUNDATION_ACCEPTANCE_MODE -ceq '1' -and
            $env:FOUNDATION_HOLD_LOCK_MS -match '^[0-9]+$') {
            Start-Sleep -Milliseconds ([int]$env:FOUNDATION_HOLD_LOCK_MS)
        }
        return $Handle
    } catch {
        $Handle.Dispose()
        throw
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
    $State = Read-JsonFile $Paths.active
    Assert-ActiveState $State ([string]$Paths.target)
    return $State
}

function Assert-NoRecoveryPending {
    param([Parameter(Mandatory = $true)]$Paths)
    if ((Test-Path -LiteralPath $Paths.pending -PathType Leaf) -or
        (Test-Path -LiteralPath $Paths.rollback_journal -PathType Leaf)) {
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
                [StringComparison]::OrdinalIgnoreCase
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

function Read-Utf8TextFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    try {
        return [IO.File]::ReadAllText(
            $Path,
            (New-Object Text.UTF8Encoding($false, $true))
        )
    } catch {
        Throw-Foundation 'INVALID_PACKAGE' "TOML file is not valid UTF-8: $Path"
    }
}

function Get-TomlMergeRequirements {
    param([Parameter(Mandatory = $true)][string]$Text)
    $Section = ''
    $Seen = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::Ordinal
    )
    $Rows = @()
    foreach ($Line in @([regex]::Split($Text, "`r?`n"))) {
        if ($Line -match '^\s*\[([^\[\]]+)\]\s*(?:#.*)?$') {
            $Section = [string]$Matches[1]
            continue
        }
        if ($Line -match '^\s*([A-Za-z0-9_-]+)\s*=') {
            $Key = [string]$Matches[1]
            $Identity = $Section + "`0" + $Key
            if (-not $Seen.Add($Identity)) {
                Throw-Foundation 'INVALID_PACKAGE' (
                    "Merge TOML contains a duplicate key: $Key"
                )
            }
            $Rows += [pscustomobject][ordered]@{
                section = $Section
                key = $Key
                line = $Line.Trim()
            }
            continue
        }
        if (-not [string]::IsNullOrWhiteSpace($Line) -and
            -not $Line.TrimStart().StartsWith('#')) {
            Throw-Foundation 'INVALID_PACKAGE' (
                'Merge TOML supports only scalar assignments and tables'
            )
        }
    }
    if ($Rows.Count -eq 0) {
        Throw-Foundation 'INVALID_PACKAGE' 'Merge TOML contains no assignments'
    }
    return @($Rows)
}

function Merge-TomlText {
    param(
        [AllowEmptyString()][string]$Existing,
        [Parameter(Mandatory = $true)][string]$Required
    )
    $NewLine = if ($Existing.Contains("`r`n")) { "`r`n" } else { "`n" }
    $Initial = @([regex]::Split($Existing, "`r?`n"))
    if ($Initial.Count -gt 0 -and
        [string]::IsNullOrEmpty([string]$Initial[$Initial.Count - 1])) {
        $Initial = @($Initial[0..([Math]::Max(0, $Initial.Count - 2))])
        if ($Initial.Count -eq 1 -and [string]::IsNullOrEmpty($Initial[0])) {
            $Initial = @()
        }
    }
    $Lines = New-Object 'Collections.Generic.List[string]'
    foreach ($Line in $Initial) { $Lines.Add([string]$Line) }
    foreach ($Requirement in @(Get-TomlMergeRequirements $Required)) {
        $Start = 0
        $End = $Lines.Count
        $SectionFound = [string]::IsNullOrEmpty([string]$Requirement.section)
        if ($SectionFound) {
            for ($Index = 0; $Index -lt $Lines.Count; $Index++) {
                if ($Lines[$Index] -match '^\s*\[[^\[\]]+\]') {
                    $End = $Index
                    break
                }
            }
        } else {
            for ($Index = 0; $Index -lt $Lines.Count; $Index++) {
                if ($Lines[$Index] -match '^\s*\[([^\[\]]+)\]\s*(?:#.*)?$' -and
                    [string]$Matches[1] -ceq [string]$Requirement.section) {
                    $SectionFound = $true
                    $Start = $Index + 1
                    $End = $Lines.Count
                    for ($Next = $Start; $Next -lt $Lines.Count; $Next++) {
                        if ($Lines[$Next] -match '^\s*\[[^\[\]]+\]') {
                            $End = $Next
                            break
                        }
                    }
                    break
                }
            }
        }
        if (-not $SectionFound) {
            if ($Lines.Count -gt 0 -and
                -not [string]::IsNullOrWhiteSpace($Lines[$Lines.Count - 1])) {
                $Lines.Add('')
            }
            $Lines.Add('[' + [string]$Requirement.section + ']')
            $Lines.Add([string]$Requirement.line)
            continue
        }
        $Updated = $false
        for ($Index = $Start; $Index -lt $End; $Index++) {
            if ($Lines[$Index] -match '^\s*([A-Za-z0-9_-]+)\s*=' -and
                [string]$Matches[1] -ceq [string]$Requirement.key) {
                $Lines[$Index] = [string]$Requirement.line
                $Updated = $true
                break
            }
        }
        if (-not $Updated) {
            $Lines.Insert($End, [string]$Requirement.line)
        }
    }
    return (($Lines -join $NewLine) + $NewLine)
}

function Merge-TomlFileAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    Assert-SafeAncestors $Destination $HomeRoot
    $Existing = if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        Read-Utf8TextFile $Destination
    } else {
        ''
    }
    $Merged = Merge-TomlText $Existing (Read-Utf8TextFile $Source)
    $Parent = Split-Path -Parent $Destination
    if (-not (Test-Path -LiteralPath $Parent)) {
        [IO.Directory]::CreateDirectory($Parent) | Out-Null
    }
    Assert-SafeDirectory $Parent
    $Temporary = Join-Path $Parent (
        '.' + [IO.Path]::GetFileName($Destination) +
        '.foundation-' + [Guid]::NewGuid().ToString('N') + '.tmp'
    )
    [IO.File]::WriteAllText(
        $Temporary,
        $Merged,
        (New-Object Text.UTF8Encoding($false))
    )
    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        Invoke-AtomicReplace $Temporary $Destination
    } else {
        [IO.File]::Move($Temporary, $Destination)
    }
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
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Exact directory conflicts with a file: $Root"
            )
        }
    }
    foreach ($Path in @(
        @($Manifest.managed_surface.replace_files) +
        @(Get-MergeTomlFiles $Manifest.managed_surface)
    )) {
        $Destination = Resolve-HomePath ([string]$Path) $HomeRoot
        Assert-SafeAncestors $Destination $HomeRoot
        if (Test-Path -LiteralPath $Destination -PathType Container) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Replace file conflicts with a directory: $Path"
            )
        }
    }
    $Rows = @()
    foreach ($Row in @($Manifest.files)) {
        $Destination = Resolve-HomePath ([string]$Row.path) $HomeRoot
        $Action = 'CREATE'
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            if (@(Get-MergeTomlFiles $Manifest.managed_surface) -icontains
                [string]$Row.path) {
                $RequiredBytes = Read-ZipEntryBytes (
                    $Validated.entries[[string]$Row.path]
                )
                $RequiredText = (New-Object Text.UTF8Encoding($false, $true)).
                    GetString($RequiredBytes)
                $ExistingText = Read-Utf8TextFile $Destination
                $Action = if ((Merge-TomlText $ExistingText $RequiredText) -ceq
                    $ExistingText) { 'UNCHANGED' } else { 'MERGE' }
            } else {
                $Action = if (
                    (Get-FileSha256 $Destination) -ceq [string]$Row.sha256
                ) { 'UNCHANGED' } else { 'UPDATE' }
            }
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
        environment_actions = @(
            Get-EnvironmentActions $Manifest.environment $HomeRoot
        )
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

function Get-ManagedSurfaceDigest {
    param([Parameter(Mandatory = $true)]$Surface)
    Assert-ManagedSurface $Surface
    $Lines = @()
    foreach ($Section in @(
        'exact_directories',
        'replace_files',
        $(if (Test-ObjectProperty $Surface 'merge_toml_files') {
            'merge_toml_files'
        }),
        'preserved_paths'
    ) | Where-Object { $null -ne $_ }) {
        $Lines += $Section
        foreach ($Value in @($Surface.$Section)) {
            $Lines += [string]$Value
        }
    }
    $Bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(
        (($Lines -join "`n") + "`n")
    )
    return Get-BytesSha256 $Bytes
}

function Get-SafeTreeFiles {
    param([Parameter(Mandatory = $true)][string]$Root)
    Assert-SafeDirectory $Root
    $Files = @()
    $Queue = New-Object 'Collections.Generic.Queue[string]'
    $Queue.Enqueue([IO.Path]::GetFullPath($Root))
    while ($Queue.Count -gt 0) {
        $Directory = $Queue.Dequeue()
        Assert-SafeDirectory $Directory
        foreach ($Child in @(
            Get-ChildItem -LiteralPath $Directory -Force
        )) {
            if ($Child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                Throw-Foundation 'UNSAFE_PATH' (
                    "Tree contains reparse point: $($Child.FullName)"
                )
            }
            if ($Child.PSIsContainer) {
                $Queue.Enqueue($Child.FullName)
            } else {
                $Files += $Child
            }
        }
    }
    return @($Files | Sort-Object FullName)
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
        @($Validated.manifest.managed_surface.replace_files) +
        @(Get-MergeTomlFiles $Validated.manifest.managed_surface)
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
    $BackupFiles = @()
    $ManagedAbsolute = [IO.Path]::GetFullPath($ManagedRoot)
    foreach ($File in @(Get-SafeTreeFiles $ManagedRoot)) {
        $Relative = $File.FullName.Substring(
            $ManagedAbsolute.Length
        ).TrimStart('\').Replace('\', '/')
        $BackupFiles += [pscustomobject][ordered]@{
            path = $Relative
            backup_path = 'managed/' + $Relative
            sha256 = Get-FileSha256 $File.FullName
            bytes = [int64]$File.Length
        }
    }
    $BackupByPath = @{}
    foreach ($Row in $BackupFiles) {
        $BackupByPath[[string]$Row.path] = $Row
    }
    $SortedBackupFiles = @(
        foreach ($BackupPath in @(
            Sort-OrdinalStrings @($BackupByPath.Keys)
        )) {
            $BackupByPath[$BackupPath]
        }
    )
    $PriorActive = Read-ActiveState $Paths -AllowMissing
    $EnvironmentBefore = @(
        foreach ($Row in @($Validated.manifest.environment.set)) {
            $Current = Get-CurrentUserEnvironmentValue (
                [string]$Row.name
            ) $HomeRoot
            [pscustomobject][ordered]@{
                name = [string]$Row.name
                existed = [bool]$Current.exists
                value = if ([bool]$Current.exists) {
                    [string]$Current.value
                } else {
                    $null
                }
            }
        }
    )
    $Snapshot = [pscustomobject][ordered]@{
        schema_version = 3
        snapshot_id = $SnapshotId
        target = [string]$Validated.manifest.target
        release_version = [string]$Validated.manifest.version
        managed_surface = $Validated.manifest.managed_surface
        environment = $Validated.manifest.environment
        environment_before = $EnvironmentBefore
        existed = @(Sort-OrdinalStrings $Existed)
        backup_files = $SortedBackupFiles
        prior_active = $PriorActive
        quarantined_unknown = @($Plan.quarantined_unknown)
    }
    $SnapshotPath = Join-Path $SnapshotRoot 'snapshot.json'
    Write-JsonFile $Snapshot $SnapshotPath
    return [pscustomobject]@{
        root = $SnapshotRoot
        metadata = $Snapshot
        metadata_path = $SnapshotPath
        metadata_sha256 = Get-FileSha256 $SnapshotPath
    }
}

function Get-ValidatedSnapshot {
    param(
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)]$Paths
    )
    if ([string]$Expected.target -cne [string]$Paths.target -or
        $Expected.snapshot_sha256 -notmatch '^[0-9a-f]{64}$') {
        Throw-Foundation 'INVALID_PACKAGE' 'Snapshot binding is invalid'
    }
    $BackupRoot = [IO.Path]::GetFullPath($Paths.backup_root)
    $SnapshotPath = [IO.Path]::GetFullPath([string]$Expected.snapshot_path)
    if (-not (Test-PathWithin $SnapshotPath $BackupRoot) -or
        -not (Test-Path -LiteralPath $SnapshotPath -PathType Leaf)) {
        Throw-Foundation 'INVALID_PACKAGE' 'Snapshot path is invalid'
    }
    Assert-SafeAncestors $SnapshotPath $HomeRoot
    if ((Get-FileSha256 $SnapshotPath) -cne
        [string]$Expected.snapshot_sha256) {
        Throw-Foundation 'INVALID_PACKAGE' 'Snapshot metadata hash differs'
    }
    $Snapshot = Read-JsonFile $SnapshotPath
    Assert-ExactProperties $Snapshot @(
        'schema_version',
        'snapshot_id',
        'target',
        'release_version',
        'managed_surface',
        'environment',
        'environment_before',
        'existed',
        'backup_files',
        'prior_active',
        'quarantined_unknown'
    ) 'snapshot'
    if ($Snapshot.schema_version -ne 3 -or
        $Snapshot.snapshot_id -notmatch
            '^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}$' -or
        [string]$Snapshot.target -cne [string]$Paths.target -or
        $Snapshot.release_version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        (Get-ManagedSurfaceDigest $Snapshot.managed_surface) -cne
            (Get-ManagedSurfaceDigest $Expected.managed_surface) -or
        (Get-EnvironmentContractDigest $Snapshot.environment) -cne
            (Get-EnvironmentContractDigest $Expected.environment)) {
        Throw-Foundation 'INVALID_PACKAGE' 'Snapshot metadata is invalid'
    }
    $ExpectedRoot = [IO.Path]::GetFullPath(
        (Join-Path $BackupRoot ([string]$Snapshot.snapshot_id))
    )
    $SnapshotRoot = [IO.Path]::GetFullPath(
        (Split-Path -Parent $SnapshotPath)
    )
    if (-not $SnapshotRoot.Equals(
            $ExpectedRoot,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        -not [IO.Path]::GetFileName($SnapshotPath).Equals(
            'snapshot.json',
            [StringComparison]::OrdinalIgnoreCase
        )) {
        Throw-Foundation 'INVALID_PACKAGE' 'Snapshot identity differs'
    }
    # Foundation 0.2.0 wrote these hash-bound local arrays with the
    # culture-aware Sort-Object cmdlet. Accept that legacy order for recovery;
    # new snapshots are emitted in ordinal order by New-Snapshot.
    Assert-StringArray @($Snapshot.existed) 'snapshot existed paths' `
        -AllowUnsorted
    $EnvironmentNames = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::Ordinal
    )
    foreach ($Row in @($Snapshot.environment_before)) {
        Assert-ExactProperties $Row @(
            'name',
            'existed',
            'value'
        ) 'snapshot environment row'
        $Name = [string]$Row.name
        if (-not $EnvironmentNames.Add($Name) -or
            @($Snapshot.environment.set).name -cnotcontains $Name -or
            $Row.existed -isnot [bool] -or
            ([bool]$Row.existed -and $Row.value -isnot [string]) -or
            (-not [bool]$Row.existed -and $null -ne $Row.value)) {
            Throw-Foundation 'INVALID_PACKAGE' (
                'Snapshot environment row is invalid'
            )
        }
    }
    if ($EnvironmentNames.Count -ne @($Snapshot.environment.set).Count) {
        Throw-Foundation 'INVALID_PACKAGE' (
            'Snapshot environment coverage differs'
        )
    }
    $ManagedValues = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($Value in @(
        @($Snapshot.managed_surface.exact_directories) +
        @($Snapshot.managed_surface.replace_files) +
        @(Get-MergeTomlFiles $Snapshot.managed_surface)
    )) {
        $null = $ManagedValues.Add([string]$Value)
    }
    foreach ($Value in @($Snapshot.existed)) {
        if (-not $ManagedValues.Contains([string]$Value)) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Snapshot existed path is unmanaged: $Value"
            )
        }
    }
    if ($null -ne $Snapshot.prior_active) {
        Assert-ActiveState $Snapshot.prior_active ([string]$Paths.target)
    }
    $ManagedRoot = Join-Path $SnapshotRoot 'managed'
    if (-not (Test-Path -LiteralPath $ManagedRoot -PathType Container)) {
        Throw-Foundation 'INVALID_PACKAGE' 'Snapshot managed backup is missing'
    }
    $Rows = @($Snapshot.backup_files)
    $RowsByPath = @{}
    foreach ($Row in $Rows) {
        Assert-ExactProperties $Row @(
            'path',
            'backup_path',
            'sha256',
            'bytes'
        ) 'snapshot backup row'
        $Path = [string]$Row.path
        if (-not (Test-PortablePath $Path) -or
            [string]$Row.backup_path -cne ('managed/' + $Path) -or
            $Row.sha256 -notmatch '^[0-9a-f]{64}$' -or
            ($Row.bytes -isnot [int] -and $Row.bytes -isnot [long]) -or
            [int64]$Row.bytes -lt 0 -or
            $RowsByPath.ContainsKey($Path)) {
            Throw-Foundation 'INVALID_PACKAGE' 'Snapshot backup row is invalid'
        }
        $Covered = $false
        foreach ($Root in @($Snapshot.existed)) {
            if ($Path.Equals(
                    [string]$Root,
                    [StringComparison]::OrdinalIgnoreCase
                ) -or
                $Path.StartsWith(
                    [string]$Root + '/',
                    [StringComparison]::OrdinalIgnoreCase
                )) {
                $Covered = $true
                break
            }
        }
        if (-not $Covered) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Snapshot backup row is not declared existed: $Path"
            )
        }
        $Source = Join-Path $SnapshotRoot (
            ([string]$Row.backup_path).Replace('/', '\')
        )
        if (-not (Test-Path -LiteralPath $Source -PathType Leaf) -or
            (Get-Item -LiteralPath $Source -Force).Length -ne
                [int64]$Row.bytes -or
            (Get-FileSha256 $Source) -cne [string]$Row.sha256) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Snapshot backup object differs: $Path"
            )
        }
        $RowsByPath[$Path] = $Row
    }
    $ActualFiles = @(Get-SafeTreeFiles $ManagedRoot)
    if ($ActualFiles.Count -ne $Rows.Count) {
        Throw-Foundation 'INVALID_PACKAGE' (
            'Snapshot contains missing or extra backup objects'
        )
    }
    $ManagedAbsolute = [IO.Path]::GetFullPath($ManagedRoot)
    foreach ($File in $ActualFiles) {
        $Relative = $File.FullName.Substring(
            $ManagedAbsolute.Length
        ).TrimStart('\').Replace('\', '/')
        if (-not $RowsByPath.ContainsKey($Relative)) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Unexpected snapshot backup object: $Relative"
            )
        }
    }
    foreach ($Root in @($Snapshot.managed_surface.exact_directories)) {
        $Destination = Resolve-HomePath ([string]$Root) $HomeRoot
        Assert-SafeAncestors $Destination $HomeRoot
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Rollback exact directory conflicts with a file: $Root"
            )
        }
        if (@($Snapshot.existed) -icontains [string]$Root) {
            $Source = Join-Path $ManagedRoot (
                ([string]$Root).Replace('/', '\')
            )
            if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
                Throw-Foundation 'INVALID_PACKAGE' (
                    "Snapshot directory backup is missing: $Root"
                )
            }
        }
    }
    foreach ($Relative in @(
        @($Snapshot.managed_surface.replace_files) +
        @(Get-MergeTomlFiles $Snapshot.managed_surface)
    )) {
        $Destination = Resolve-HomePath ([string]$Relative) $HomeRoot
        Assert-SafeAncestors $Destination $HomeRoot
        if (Test-Path -LiteralPath $Destination -PathType Container) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Rollback replace file conflicts with a directory: $Relative"
            )
        }
        if (@($Snapshot.existed) -icontains [string]$Relative) {
            $Source = Join-Path $ManagedRoot (
                ([string]$Relative).Replace('/', '\')
            )
            if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
                Throw-Foundation 'INVALID_PACKAGE' (
                    "Snapshot file backup is missing: $Relative"
                )
            }
        }
    }
    $StagingRoot = Join-Path ([IO.Path]::GetTempPath()) (
        'foundation-restore-' + [Guid]::NewGuid().ToString('N')
    )
    [IO.Directory]::CreateDirectory($StagingRoot) | Out-Null
    $StagingManaged = Join-Path $StagingRoot 'managed'
    Copy-TreeSafe $ManagedRoot $StagingManaged
    return [pscustomobject]@{
        snapshot = $Snapshot
        snapshot_path = $SnapshotPath
        snapshot_sha256 = [string]$Expected.snapshot_sha256
        staging_root = $StagingRoot
        managed_root = $StagingManaged
    }
}

function Invoke-RollbackCheckpoint {
    $script:RollbackMutationCount++
    if ($env:FOUNDATION_ACCEPTANCE_MODE -cne '1') { return }
    if ($env:FOUNDATION_ROLLBACK_CRASH_AFTER -match '^[0-9]+$' -and
        $script:RollbackMutationCount -eq
            [int]$env:FOUNDATION_ROLLBACK_CRASH_AFTER) {
        [Environment]::Exit(98)
    }
}

function Invoke-RollbackStageCheckpoint {
    param([Parameter(Mandatory = $true)][string]$Stage)
    if ($env:FOUNDATION_ACCEPTANCE_MODE -cne '1') { return }
    if ($env:FOUNDATION_ROLLBACK_CRASH_STAGE -ceq $Stage) {
        [Environment]::Exit(97)
    }
}

function Restore-Snapshot {
    param(
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)]$Paths
    )
    $Prepared = Get-ValidatedSnapshot $Expected $HomeRoot $Paths
    try {
        $Snapshot = $Prepared.snapshot
        $ManagedRoot = $Prepared.managed_root
        $Existed = New-Object 'Collections.Generic.HashSet[string]' (
            [StringComparer]::OrdinalIgnoreCase
        )
        foreach ($Value in @($Snapshot.existed)) {
            $null = $Existed.Add([string]$Value)
        }
        $Journal = [pscustomobject][ordered]@{
            schema_version = 1
            target = [string]$Paths.target
            snapshot_path = [string]$Prepared.snapshot_path
            snapshot_sha256 = [string]$Prepared.snapshot_sha256
            managed_surface = $Expected.managed_surface
            environment = $Expected.environment
        }
        Write-JsonFile $Journal $Paths.rollback_journal
        foreach ($Root in @($Snapshot.managed_surface.exact_directories)) {
            $Destination = Resolve-HomePath ([string]$Root) $HomeRoot
            Remove-TreeSafe $Destination $HomeRoot
            if ($Existed.Contains([string]$Root)) {
                $Source = Join-Path $ManagedRoot (
                    ([string]$Root).Replace('/', '\')
                )
                Copy-TreeSafe $Source $Destination
            }
            Invoke-RollbackCheckpoint
        }
        foreach ($Relative in @(
            @($Snapshot.managed_surface.replace_files) +
            @(Get-MergeTomlFiles $Snapshot.managed_surface)
        )) {
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
            Invoke-RollbackCheckpoint
        }
        foreach ($Row in @($Snapshot.environment_before)) {
            Set-CurrentUserEnvironmentValue (
                [string]$Row.name
            ) $Row.value ([bool]$Row.existed) $HomeRoot
            Invoke-RollbackCheckpoint
        }
        if ($null -ne $Snapshot.prior_active) {
            Write-JsonFile $Snapshot.prior_active $Paths.active
        } elseif (Test-Path -LiteralPath $Paths.active -PathType Leaf) {
            Remove-Item -LiteralPath $Paths.active -Force
        }
        Invoke-RollbackStageCheckpoint 'after_active'
        if (Test-Path -LiteralPath $Paths.pending -PathType Leaf) {
            Remove-Item -LiteralPath $Paths.pending -Force
        }
        Invoke-RollbackStageCheckpoint 'after_pending'
        Invoke-RollbackStageCheckpoint 'before_journal_delete'
        if (Test-Path -LiteralPath $Paths.rollback_journal -PathType Leaf) {
            Remove-Item -LiteralPath $Paths.rollback_journal -Force
        }
    } finally {
        if ($null -ne $Prepared -and
            (Test-Path -LiteralPath $Prepared.staging_root -PathType Container)) {
            Remove-Item -LiteralPath $Prepared.staging_root -Recurse -Force
        }
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
    Test-EnvironmentContract $State.environment $HomeRoot
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
                [StringComparison]::OrdinalIgnoreCase
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
        environment_variable_count = @($State.environment.set).Count
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
    $Pending = $null
    try {
        $Staging = Expand-ValidatedPackage $Validated
        $Snapshot = New-Snapshot $Validated $HomeRoot $Paths $Plan
        $Pending = [pscustomobject][ordered]@{
            schema_version = 1
            target = [string]$Validated.manifest.target
            snapshot_path = [string]$Snapshot.metadata_path
            snapshot_sha256 = [string]$Snapshot.metadata_sha256
            release_version = [string]$Validated.manifest.version
            managed_surface = $Validated.manifest.managed_surface
            environment = $Validated.manifest.environment
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
            if (@(Get-MergeTomlFiles (
                    $Validated.manifest.managed_surface
                )) -icontains [string]$Row.path) {
                Merge-TomlFileAtomic $Source $Destination $HomeRoot
            } else {
                Copy-Atomic $Source $Destination $HomeRoot
            }
            Invoke-MutationCheckpoint
        }
        Apply-EnvironmentContract $Validated.manifest.environment $HomeRoot
        $Installed = @(
            foreach ($Row in @($Validated.manifest.files)) {
                $InstalledPath = Resolve-HomePath ([string]$Row.path) $HomeRoot
                $IsMerge = @(Get-MergeTomlFiles (
                    $Validated.manifest.managed_surface
                )) -icontains [string]$Row.path
                [pscustomobject][ordered]@{
                    path = [string]$Row.path
                    sha256 = if ($IsMerge) {
                        Get-FileSha256 $InstalledPath
                    } else {
                        [string]$Row.sha256
                    }
                    bytes = if ($IsMerge) {
                        [int64](Get-Item -LiteralPath $InstalledPath).Length
                    } else {
                        [int64]$Row.bytes
                    }
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
            environment = $Validated.manifest.environment
            installed_files = $Installed
            quarantined_unknown = @($Plan.quarantined_unknown)
            snapshot_path = [string]$Snapshot.metadata_path
            snapshot_sha256 = [string]$Snapshot.metadata_sha256
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
            environment_variable_count = @($State.environment.set).Count
            quarantined_unknown = @($State.quarantined_unknown)
        }
    } catch {
        if ($null -ne $Pending -and
            (Test-Path -LiteralPath $Paths.pending -PathType Leaf)) {
            try {
                Restore-Snapshot $Pending $HomeRoot $Paths
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
    if ((Test-Path -LiteralPath $Paths.pending -PathType Leaf) -or
        (Test-Path -LiteralPath $Paths.rollback_journal -PathType Leaf)) {
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
        environment = $State.environment
        quarantined_unknown = @($State.quarantined_unknown)
    }
}

function Invoke-Rollback {
    param(
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$TargetName
    )
    $Paths = Get-FoundationPaths $HomeRoot $TargetName
    $Expected = $null
    if (Test-Path -LiteralPath $Paths.rollback_journal -PathType Leaf) {
        $Expected = Read-JsonFile $Paths.rollback_journal
        Assert-RollbackJournal $Expected $TargetName
    } elseif (Test-Path -LiteralPath $Paths.pending -PathType Leaf) {
        $Expected = Read-JsonFile $Paths.pending
        Assert-PendingState $Expected $TargetName
    } elseif (Test-Path -LiteralPath $Paths.active -PathType Leaf) {
        $Expected = Read-ActiveState $Paths
    }
    if ($null -eq $Expected) {
        Throw-Foundation 'NOT_INSTALLED' 'No rollback snapshot exists'
    }
    Restore-Snapshot $Expected $HomeRoot $Paths
    return [pscustomobject][ordered]@{
        status = 'ROLLED_BACK'
        target = $TargetName
        snapshot_path = [string]$Expected.snapshot_path
    }
}

$Validated = $null
$OperationLock = $null
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
    if (-not [string]::IsNullOrWhiteSpace($Target)) {
        Assert-TargetName $Target
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
    if ($Command -in @('install', 'rollback')) {
        $OperationPaths = Get-FoundationPaths $TargetHome $Target
        $OperationLock = Enter-TargetLock $OperationPaths $TargetHome
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
    if ($null -ne $OperationLock) {
        $OperationLock.Dispose()
    }
    if ($null -ne $Validated) {
        Close-ValidatedPackage $Validated
    }
}
