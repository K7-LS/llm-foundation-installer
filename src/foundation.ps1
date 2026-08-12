[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('plan', 'apply', 'install', 'doctor', 'inventory', 'rollback')]
    [string]$Command,
    [string]$Package,
    [string]$ReleaseManifest,
    [string]$ReleaseManifestSha256,
    [Parameter(Mandatory = $true)]
    [Alias('Home')]
    [string]$TargetHome,
    [string]$Target,
    [string]$ClientId,
    [string]$ClientVersion,
    [Alias('Plan')]
    [string]$PlanFile,
    [string]$LocalExceptionPath = '',
    [switch]$ConfirmRemoveUnknown,
    [switch]$Interactive,
    [switch]$Strict,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$script:EngineVersion = '0.4.0'
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
    BLOCKED_USER_DECISION = 20
    INVALID_PACKAGE = 30
    INSTALL_FAILED = 30
    ACTIVE_DRIFT = 30
    UNSAFE_PATH = 40
}
$script:MutationCount = 0
$script:RollbackMutationCount = 0
$script:ActiveLocalTomlExceptions = @()
$script:RequestedLocalExceptionPaths = @(
    ([string]$LocalExceptionPath).Split('|') |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)

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
        $JsonCommand = Get-Command ConvertFrom-Json -ErrorAction Stop
        if ($JsonCommand.Parameters.ContainsKey('DateKind')) {
            return ConvertFrom-Json `
                -InputObject $Text `
                -DateKind String `
                -ErrorAction Stop
        }
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
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [switch]$AllowSessionState,
        [switch]$AllowSharedToolPath
    )
    $IsAllowedSessionState = $AllowSessionState -and
        $Relative -cmatch (
            '^\.llm-foundation/state/session-tools/' +
            '[a-z][a-z0-9-]{1,31}/state\.json$'
        )
    $IsAllowedSharedToolPath = $AllowSharedToolPath -and
        $Relative -cin @(
            '.llm-foundation/bin',
            '.llm-foundation/bin/officecli.exe',
            '.llm-foundation/libexec/officecli/officecli.exe',
            '.llm-foundation/libexec/officecli/officecli-command-policy.json',
            '.llm-foundation/libexec/officecli/officecli_csv_batch.py',
            '.llm-foundation/libexec/officecli/plugins/exporter/pdf/plugin.exe',
            '.llm-foundation/state/shared-tools/officecli/current.json'
        )
    if (-not (Test-PortablePath $Relative) -or
        ((Test-ProtectedPath $Relative) -and
            -not $IsAllowedSessionState -and
            -not $IsAllowedSharedToolPath)) {
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
    param(
        [Parameter(Mandatory = $true)]$Surface,
        [switch]$AllowSessionState
    )
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
    Assert-StringArray @($Surface.replace_files) 'replace files' `
        -AllowSessionState:$AllowSessionState
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
        [switch]$AllowSessionState,
        [switch]$AllowTomlIdentity,
        [switch]$AllowUnsorted
    )
    $Seen = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    $Previous = $null
    foreach ($Value in @($Values)) {
        $IsTomlIdentity = $AllowTomlIdentity -and
            [string]$Value -cmatch (
                '^toml:\.[A-Za-z0-9._/-]+#[A-Za-z0-9_.-]+$'
            )
        if ($Value -isnot [string] -or
            (-not $IsTomlIdentity -and
                -not (Test-PortablePath ([string]$Value))) -or
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
        $IsAllowedSessionState = $AllowSessionState -and
            [string]$Value -cmatch (
                '^\.llm-foundation/state/session-tools/' +
                '[a-z][a-z0-9-]{1,31}/state\.json$'
            )
        if (-not $AllowProtected -and
            (Test-ProtectedPath ([string]$Value)) -and
            -not $IsAllowedSessionState) {
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

function Assert-ManifestProperties {
    param([Parameter(Mandatory = $true)]$Manifest)
    $Required = @(
        'schema_version',
        'target',
        'version',
        'client',
        'foundation_engine_version',
        'managed_surface',
        'sync_policy',
        'environment',
        'files'
    )
    $Optional = @(
        'desired_state',
        'retired_managed_paths',
        'session_tools_baseline',
        'shared_tools'
    )
    if ($null -eq $Manifest -or
        $Manifest -isnot [Management.Automation.PSCustomObject]) {
        Throw-Foundation 'INVALID_PACKAGE' 'package manifest must be an object'
    }
    foreach ($Name in @($Manifest.PSObject.Properties.Name)) {
        if ($Required -cnotcontains [string]$Name -and
            $Optional -cnotcontains [string]$Name) {
            Throw-Foundation 'INVALID_PACKAGE' 'package manifest properties differ'
        }
    }
    foreach ($Name in $Required) {
        if (-not (Test-ObjectProperty $Manifest $Name)) {
            Throw-Foundation 'INVALID_PACKAGE' 'package manifest properties differ'
        }
    }
}

function Assert-DesiredStateContract {
    param([Parameter(Mandatory = $true)]$DesiredState)
    Assert-ExactProperties $DesiredState @(
        'schema_version',
        'unknown_policy',
        'local_exceptions',
        'strict_doctor',
        'inventory_roots',
        'platform_owned',
        'toml_reconcile'
    ) 'desired state'
    if ([int]$DesiredState.schema_version -ne 1 -or
        [string]$DesiredState.unknown_policy -cne 'prompt-every-run' -or
        -not [bool]$DesiredState.local_exceptions -or
        -not [bool]$DesiredState.strict_doctor -or
        $DesiredState.toml_reconcile -isnot [Array]) {
        Throw-Foundation 'INVALID_PACKAGE' 'Desired-state contract differs'
    }
    Assert-StringArray @($DesiredState.inventory_roots) `
        'desired-state inventory roots'
    Assert-StringArray @($DesiredState.platform_owned) `
        'desired-state platform-owned paths'
    foreach ($PathValue in @(
        @($DesiredState.inventory_roots) + @($DesiredState.platform_owned)
    )) {
        if (-not (Test-PortablePath ([string]$PathValue)) -or
            (Test-ProtectedPath ([string]$PathValue))) {
            Throw-Foundation 'INVALID_PACKAGE' 'Desired-state path is invalid'
        }
    }
    $PreviousPath = $null
    foreach ($Rule in @($DesiredState.toml_reconcile)) {
        Assert-ExactProperties $Rule @(
            'path',
            'exact_tables',
            'protected_tables',
            'allowed_entries'
        ) 'TOML reconcile rule'
        if (-not (Test-PortablePath ([string]$Rule.path)) -or
            -not ([string]$Rule.path).EndsWith(
                '.toml', [StringComparison]::OrdinalIgnoreCase)) {
            Throw-Foundation 'INVALID_PACKAGE' 'TOML reconcile path is invalid'
        }
        Assert-StringArray @($Rule.exact_tables) 'TOML exact tables' -AllowUnsorted
        Assert-StringArray @($Rule.protected_tables) `
            'TOML protected tables'
        Assert-StringArray @($Rule.allowed_entries) 'TOML allowed entries' -AllowUnsorted
        foreach ($Table in @($Rule.exact_tables)) {
            if ([string]$Table -cnotmatch '^[A-Za-z0-9_-]+$') {
                Throw-Foundation 'INVALID_PACKAGE' 'TOML exact table is invalid'
            }
        }
        foreach ($Table in @($Rule.protected_tables)) {
            if ([string]$Table -cnotmatch (
                '^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+$'
            )) {
                Throw-Foundation 'INVALID_PACKAGE' (
                    'TOML protected table is invalid'
                )
            }
        }
        if ($null -ne $PreviousPath -and [StringComparer]::Ordinal.Compare(
                [string]$PreviousPath, [string]$Rule.path) -ge 0) {
            Throw-Foundation 'INVALID_PACKAGE' 'TOML reconcile rules are not sorted'
        }
        $PreviousPath = [string]$Rule.path
    }
}

function Get-SessionToolsRelativePaths {
    param(
        [Parameter(Mandatory = $true)][string]$TargetName,
        [Parameter(Mandatory = $true)]$ManagedSurface
    )
    $ManagedPaths = @(
        @($ManagedSurface.exact_directories) +
        @($ManagedSurface.replace_files) +
        @(Get-MergeTomlFiles $ManagedSurface)
    )
    $SkillRoots = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    $BaseRoots = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($ManagedPath in $ManagedPaths) {
        $Normalized = [string]$ManagedPath
        $SkillMatch = [regex]::Match(
            $Normalized,
            '^(.+/skills)(?:/.*)?$',
            [Text.RegularExpressions.RegexOptions]::CultureInvariant
        )
        if ($SkillMatch.Success) {
            $null = $SkillRoots.Add([string]$SkillMatch.Groups[1].Value)
        }
        $BaseMatch = [regex]::Match(
            $Normalized,
            '^(.+/base)/(?:VERSION|runtime(?:/.*)?)$',
            [Text.RegularExpressions.RegexOptions]::CultureInvariant
        )
        if ($BaseMatch.Success) {
            $null = $BaseRoots.Add([string]$BaseMatch.Groups[1].Value)
        }
    }
    if ($SkillRoots.Count -ne 1 -or $BaseRoots.Count -ne 1) {
        Throw-Foundation 'INVALID_PACKAGE' (
            'Session tools roots are missing or ambiguous'
        )
    }
    $TargetRoot = [string]@($SkillRoots)[0]
    $TargetBase = [string]@($BaseRoots)[0]
    return [pscustomobject]@{
        target = $TargetName
        skills_root_relative = $TargetRoot
        runtime_relative = (
            $TargetBase + '/runtime/session-tools-baseline.json'
        )
        state_relative = (
            '.llm-foundation/state/session-tools/' +
            $TargetName + '/state.json'
        )
    }
}

function Assert-SessionToolRecords {
    param(
        [Parameter(Mandatory = $true)]$Tools,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ($Tools -isnot [Array] -or @($Tools).Count -gt 32) {
        Throw-Foundation 'INVALID_PACKAGE' "$Label tools are invalid"
    }
    $ToolIds = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    $PreviousTool = $null
    $Payloads = @{}
    [int64]$ExpandedBytes = 0
    [int]$FileCount = 0
    $Lines = @()
    foreach ($Tool in @($Tools)) {
        Assert-ExactProperties $Tool @('id', 'files') "$Label tool"
        $ToolId = [string]$Tool.id
        if ($Tool.id -isnot [string] -or
            $ToolId -cnotmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,63}$' -or
            -not $ToolIds.Add($ToolId) -or
            ($null -ne $PreviousTool -and
                [StringComparer]::Ordinal.Compare(
                    [string]$PreviousTool,
                    $ToolId
                ) -ge 0)) {
            Throw-Foundation 'INVALID_PACKAGE' "$Label tool ids are invalid"
        }
        if ($Tool.files -isnot [Array] -or @($Tool.files).Count -eq 0) {
            Throw-Foundation 'INVALID_PACKAGE' "$Label tool files are invalid"
        }
        $FilePaths = New-Object 'Collections.Generic.HashSet[string]' (
            [StringComparer]::OrdinalIgnoreCase
        )
        $PreviousPath = $null
        $Lines += $ToolId
        foreach ($Row in @($Tool.files)) {
            Assert-ExactProperties $Row @('path', 'sha256', 'bytes') (
                "$Label tool file"
            )
            $Path = [string]$Row.path
            $Extension = [IO.Path]::GetExtension($Path).ToLowerInvariant()
            if ($Row.path -isnot [string] -or
                -not (Test-PortablePath $Path) -or
                $Extension -cnotin @('.md', '.json', '.yaml', '.yml', '.toml', '.txt') -or
                -not $FilePaths.Add($Path) -or
                ($null -ne $PreviousPath -and
                    [StringComparer]::Ordinal.Compare(
                        [string]$PreviousPath,
                        $Path
                    ) -ge 0) -or
                $Row.sha256 -isnot [string] -or
                [string]$Row.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
                ($Row.bytes -isnot [int] -and $Row.bytes -isnot [long]) -or
                [int64]$Row.bytes -lt 0 -or
                [int64]$Row.bytes -gt 1048576) {
                Throw-Foundation 'INVALID_PACKAGE' "$Label tool file is invalid"
            }
            $FileCount++
            $ExpandedBytes += [int64]$Row.bytes
            if ($FileCount -gt 256 -or $ExpandedBytes -gt 8388608) {
                Throw-Foundation 'INVALID_PACKAGE' "$Label limits are exceeded"
            }
            $PayloadPath = (
                'session-tools-baseline/tools/' + $ToolId + '/' + $Path
            )
            $Payloads[$PayloadPath] = [pscustomobject]@{
                sha256 = [string]$Row.sha256
                bytes = [int64]$Row.bytes
            }
            $Lines += ($Path + "`0" + [string]$Row.sha256 + "`0" +
                [string][int64]$Row.bytes)
            $PreviousPath = $Path
        }
        $PreviousTool = $ToolId
    }
    $Digest = Get-BytesSha256 (
        (New-Object Text.UTF8Encoding($false)).GetBytes(
            (($Lines -join "`n") + "`n")
        )
    )
    return [pscustomobject]@{
        digest = $Digest
        payloads = $Payloads
    }
}

function Assert-SessionToolsBaseline {
    param(
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)]$EntriesByName
    )
    $Baseline = $Manifest.session_tools_baseline
    Assert-ExactProperties $Baseline @(
        'manifest_path',
        'manifest_sha256',
        'tools',
        'retired_tool_ids'
    ) 'session tools baseline'
    if ($Baseline.manifest_path -isnot [string] -or
        [string]$Baseline.manifest_path -cne
            'session-tools-baseline/session-tools-manifest.json' -or
        $Baseline.manifest_sha256 -isnot [string] -or
        [string]$Baseline.manifest_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        -not $EntriesByName.ContainsKey([string]$Baseline.manifest_path)) {
        Throw-Foundation 'INVALID_PACKAGE' 'Session tools baseline manifest is invalid'
    }
    $ManifestBytes = Read-ZipEntryBytes (
        $EntriesByName[[string]$Baseline.manifest_path]
    ) 8388608
    if ((Get-BytesSha256 $ManifestBytes) -cne
        [string]$Baseline.manifest_sha256) {
        Throw-Foundation 'INVALID_PACKAGE' 'Session tools baseline manifest hash differs'
    }
    try {
        $Text = (New-Object Text.UTF8Encoding($false, $true)).GetString(
            $ManifestBytes
        )
        $Internal = ConvertFrom-Json -InputObject $Text -ErrorAction Stop
    } catch {
        Throw-Foundation 'INVALID_PACKAGE' 'Session tools baseline manifest JSON is invalid'
    }
    Assert-ExactProperties $Internal @(
        'schema_version',
        'target',
        'release_tag',
        'base_version',
        'tools'
    ) 'session tools manifest'
    if ($Internal.schema_version -ne 1 -or
        $Internal.target -isnot [string] -or
        [string]$Internal.target -cne [string]$Manifest.target -or
        $Internal.base_version -isnot [string] -or
        [string]$Internal.base_version -cne [string]$Manifest.version -or
        $Internal.release_tag -isnot [string] -or
        [string]$Internal.release_tag -cne (
            [string]$Manifest.target + '-v' + [string]$Manifest.version
        )) {
        Throw-Foundation 'INVALID_PACKAGE' 'Session tools baseline identity differs'
    }
    $Declared = Assert-SessionToolRecords $Baseline.tools 'baseline'
    $Embedded = Assert-SessionToolRecords $Internal.tools 'session manifest'
    if ([string]$Declared.digest -cne [string]$Embedded.digest) {
        Throw-Foundation 'INVALID_PACKAGE' 'Session tools baseline tools differ'
    }
    if ($Baseline.retired_tool_ids -isnot [Array]) {
        Throw-Foundation 'INVALID_PACKAGE' 'Retired session tool ids are invalid'
    }
    $Retired = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    $PreviousRetired = $null
    foreach ($ToolIdValue in @($Baseline.retired_tool_ids)) {
        $ToolId = [string]$ToolIdValue
        if ($ToolIdValue -isnot [string] -or
            $ToolId -cnotmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,63}$' -or
            -not $Retired.Add($ToolId) -or
            ($null -ne $PreviousRetired -and
                [StringComparer]::Ordinal.Compare(
                    [string]$PreviousRetired,
                    $ToolId
                ) -ge 0)) {
            Throw-Foundation 'INVALID_PACKAGE' 'Retired session tool ids are invalid'
        }
        $PreviousRetired = $ToolId
    }
    foreach ($Tool in @($Baseline.tools)) {
        if ($Retired.Contains([string]$Tool.id)) {
            Throw-Foundation 'INVALID_PACKAGE' 'Active and retired session tools overlap'
        }
    }
    $Payloads = @{}
    $Payloads[[string]$Baseline.manifest_path] = [pscustomobject]@{
        sha256 = [string]$Baseline.manifest_sha256
        bytes = [int64]$ManifestBytes.Length
    }
    foreach ($Path in $Declared.payloads.Keys) {
        $Payloads[[string]$Path] = $Declared.payloads[$Path]
    }
    return [pscustomobject]@{
        manifest = $Internal
        manifest_bytes = $ManifestBytes
        payloads = $Payloads
    }
}

function Assert-ProcessEnvironmentRows {
    param([Parameter(Mandatory = $true)]$Rows)
    if ($Rows -isnot [Array]) {
        Throw-Foundation 'INVALID_PACKAGE' 'Process environment is invalid'
    }
    $Contract = [pscustomobject][ordered]@{
        scope = 'current-user'
        set = $Rows
    }
    Assert-EnvironmentContract $Contract
    return $Contract
}

function Assert-SharedTools {
    param(
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)]$EntriesByName
    )
    if ($Manifest.shared_tools -isnot [Array]) {
        Throw-Foundation 'INVALID_PACKAGE' 'Shared tools must be an array'
    }
    $Ids = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    $PreviousId = $null
    $Payloads = @{}
    foreach ($Tool in @($Manifest.shared_tools)) {
        Assert-ExactProperties $Tool @(
            'id',
            'version',
            'bundle_version',
            'compatibility_epoch',
            'minimum_compatible_version',
            'maximum_exclusive_version',
            'payload_path',
            'sha256',
            'bytes',
            'install_path',
            'version_arguments',
            'version_pattern',
            'timeout_seconds',
            'path_entry',
            'environment',
            'shim'
        ) 'shared tool'
        $Id = [string]$Tool.id
        if ($Tool.id -isnot [string] -or
            $Id -cnotmatch '^[a-z][a-z0-9-]{1,63}$' -or
            -not $Ids.Add($Id) -or
            ($null -ne $PreviousId -and
                [StringComparer]::Ordinal.Compare(
                    [string]$PreviousId,
                    $Id
                ) -ge 0)) {
            Throw-Foundation 'INVALID_PACKAGE' 'Shared tool ids are invalid'
        }
        foreach ($VersionField in @(
            'version',
            'bundle_version',
            'minimum_compatible_version',
            'maximum_exclusive_version'
        )) {
            if ($Tool.$VersionField -isnot [string] -or
                [string]$Tool.$VersionField -cnotmatch
                    '^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$') {
                Throw-Foundation 'INVALID_PACKAGE' 'Shared tool version is invalid'
            }
        }
        try {
            $Version = [version][string]$Tool.version
            $Minimum = [version][string]$Tool.minimum_compatible_version
            $Maximum = [version][string]$Tool.maximum_exclusive_version
        } catch {
            Throw-Foundation 'INVALID_PACKAGE' 'Shared tool version is invalid'
        }
        if ($Version -lt $Minimum -or $Version -ge $Maximum -or
            $Tool.compatibility_epoch -isnot [string] -or
            [string]$Tool.compatibility_epoch -cnotmatch
                '^[a-z][a-z0-9-]{1,63}$' -or
            $Tool.payload_path -isnot [string] -or
            -not (Test-PortablePath ([string]$Tool.payload_path)) -or
            $Tool.install_path -isnot [string] -or
            -not (Test-PortablePath ([string]$Tool.install_path)) -or
            -not ([string]$Tool.install_path).StartsWith(
                '.llm-foundation/libexec/',
                [StringComparison]::Ordinal
            ) -or
            $Tool.path_entry -isnot [string] -or
            -not (Test-PortablePath ([string]$Tool.path_entry)) -or
            [string]$Tool.path_entry -cne '.llm-foundation/bin' -or
            $Tool.sha256 -isnot [string] -or
            [string]$Tool.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            ($Tool.bytes -isnot [int] -and $Tool.bytes -isnot [long]) -or
            [int64]$Tool.bytes -le 0 -or
            ($Tool.timeout_seconds -isnot [int] -and
                $Tool.timeout_seconds -isnot [long]) -or
            [int]$Tool.timeout_seconds -lt 1 -or
            [int]$Tool.timeout_seconds -gt 60 -or
            $Tool.version_pattern -isnot [string] -or
            [string]$Tool.version_pattern -notmatch '\(\?<version>') {
            Throw-Foundation 'INVALID_PACKAGE' 'Shared tool contract is invalid'
        }
        if ($Tool.version_arguments -isnot [Array] -or
            @($Tool.version_arguments).Count -eq 0) {
            Throw-Foundation 'INVALID_PACKAGE' 'Shared tool version arguments are invalid'
        }
        foreach ($Argument in @($Tool.version_arguments)) {
            if ($Argument -isnot [string] -or
                [string]::IsNullOrWhiteSpace([string]$Argument) -or
                [string]$Argument -match '[\x00-\x1f\x7f]') {
                Throw-Foundation 'INVALID_PACKAGE' (
                    'Shared tool version arguments are invalid'
                )
            }
        }
        Assert-EnvironmentContract $Tool.environment
        $Shim = $Tool.shim
        Assert-ExactProperties $Shim @(
            'schema_version',
            'payload_path',
            'sha256',
            'bytes',
            'command_path',
            'policy_payload_path',
            'policy_install_path',
            'policy_sha256',
            'policy_bytes',
            'process_environment'
        ) 'shared tool shim'
        $ProcessEnvironment = Assert-ProcessEnvironmentRows (
            $Shim.process_environment
        )
        if ((Get-EnvironmentContractDigest $Tool.environment) -cne
            (Get-EnvironmentContractDigest $ProcessEnvironment) -or
            $Shim.schema_version -ne 1 -or
            $Shim.payload_path -isnot [string] -or
            -not (Test-PortablePath ([string]$Shim.payload_path)) -or
            $Shim.command_path -isnot [string] -or
            -not (Test-PortablePath ([string]$Shim.command_path)) -or
            -not ([string]$Shim.command_path).StartsWith(
                '.llm-foundation/bin/',
                [StringComparison]::Ordinal
            ) -or
            $Shim.policy_payload_path -isnot [string] -or
            -not (Test-PortablePath ([string]$Shim.policy_payload_path)) -or
            $Shim.policy_install_path -isnot [string] -or
            -not (Test-PortablePath ([string]$Shim.policy_install_path)) -or
            -not ([string]$Shim.policy_install_path).StartsWith(
                '.llm-foundation/libexec/',
                [StringComparison]::Ordinal
            ) -or
            $Shim.sha256 -isnot [string] -or
            [string]$Shim.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            $Shim.policy_sha256 -isnot [string] -or
            [string]$Shim.policy_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            ($Shim.bytes -isnot [int] -and $Shim.bytes -isnot [long]) -or
            [int64]$Shim.bytes -le 0 -or
            ($Shim.policy_bytes -isnot [int] -and
                $Shim.policy_bytes -isnot [long]) -or
            [int64]$Shim.policy_bytes -le 0) {
            Throw-Foundation 'INVALID_PACKAGE' 'Shared tool shim is invalid'
        }
        foreach ($Payload in @(
            [pscustomobject]@{
                path = [string]$Tool.payload_path
                sha256 = [string]$Tool.sha256
                bytes = [int64]$Tool.bytes
            },
            [pscustomobject]@{
                path = [string]$Shim.payload_path
                sha256 = [string]$Shim.sha256
                bytes = [int64]$Shim.bytes
            },
            [pscustomobject]@{
                path = [string]$Shim.policy_payload_path
                sha256 = [string]$Shim.policy_sha256
                bytes = [int64]$Shim.policy_bytes
            }
        )) {
            if ($Payloads.ContainsKey([string]$Payload.path) -or
                -not $EntriesByName.ContainsKey([string]$Payload.path)) {
                Throw-Foundation 'INVALID_PACKAGE' 'Shared tool payload is invalid'
            }
            $Payloads[[string]$Payload.path] = $Payload
        }
        $PreviousId = $Id
    }
    return $Payloads
}

function Assert-Manifest {
    param(
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)]$EntriesByName
    )
    Assert-ManifestProperties $Manifest
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
    if (Test-ObjectProperty $Manifest 'desired_state') {
        Assert-DesiredStateContract $Manifest.desired_state
        foreach ($Rule in @($Manifest.desired_state.toml_reconcile)) {
            if (@(Get-MergeTomlFiles $Manifest.managed_surface) -inotcontains
                [string]$Rule.path) {
                Throw-Foundation 'INVALID_PACKAGE' (
                    'TOML reconcile path is not a managed merge file'
                )
            }
        }
    }
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

    if (Test-ObjectProperty $Manifest 'retired_managed_paths') {
        if ($Manifest.retired_managed_paths -isnot [Array]) {
            Throw-Foundation 'INVALID_PACKAGE' 'Retired managed paths are invalid'
        }
        Assert-StringArray @($Manifest.retired_managed_paths) (
            'retired managed paths'
        )
        foreach ($RetiredPath in @($Manifest.retired_managed_paths)) {
            if (Test-DeclaredPreservedPath (
                [string]$RetiredPath
            ) @($Manifest.managed_surface.preserved_paths)) {
                Throw-Foundation 'UNSAFE_PATH' (
                    "Retired managed path overlaps preserved path: $RetiredPath"
                )
            }
            foreach ($ManagedPath in $ManagedRoots) {
                if (([string]$RetiredPath).Equals(
                        [string]$ManagedPath,
                        [StringComparison]::OrdinalIgnoreCase
                    ) -or
                    ([string]$RetiredPath).StartsWith(
                        [string]$ManagedPath + '/',
                        [StringComparison]::OrdinalIgnoreCase
                    ) -or
                    ([string]$ManagedPath).StartsWith(
                        [string]$RetiredPath + '/',
                        [StringComparison]::OrdinalIgnoreCase
                    )) {
                    Throw-Foundation 'INVALID_PACKAGE' (
                        "Retired and active managed paths overlap: $RetiredPath"
                    )
                }
            }
        }
    }

    $BaselineContract = $null
    $SupplementalRows = @{}
    if (Test-ObjectProperty $Manifest 'session_tools_baseline') {
        $BaselineContract = Assert-SessionToolsBaseline $Manifest $EntriesByName
        $SessionPaths = Get-SessionToolsRelativePaths `
            ([string]$Manifest.target) `
            $Manifest.managed_surface
        foreach ($Tool in @($BaselineContract.manifest.tools)) {
            $SessionDestination = (
                [string]$SessionPaths.skills_root_relative + '/' +
                [string]$Tool.id
            )
            foreach ($ManagedPathValue in $ManagedRoots) {
                $ManagedPath = [string]$ManagedPathValue
                if ($SessionDestination.Equals(
                        $ManagedPath,
                        [StringComparison]::OrdinalIgnoreCase
                    ) -or
                    $SessionDestination.StartsWith(
                        $ManagedPath + '/',
                        [StringComparison]::OrdinalIgnoreCase
                    ) -or
                    $ManagedPath.StartsWith(
                        $SessionDestination + '/',
                        [StringComparison]::OrdinalIgnoreCase
                    )) {
                    Throw-Foundation 'INVALID_PACKAGE' (
                        'Session and package managed destinations overlap'
                    )
                }
            }
        }
        foreach ($Path in $BaselineContract.payloads.Keys) {
            $SupplementalRows[[string]$Path] = $BaselineContract.payloads[$Path]
        }
    }
    if (Test-ObjectProperty $Manifest 'shared_tools') {
        $SharedPayloads = Assert-SharedTools $Manifest $EntriesByName
        foreach ($Path in $SharedPayloads.Keys) {
            if ($SupplementalRows.ContainsKey([string]$Path)) {
                Throw-Foundation 'INVALID_PACKAGE' 'Supplemental payload paths overlap'
            }
            $SupplementalRows[[string]$Path] = $SharedPayloads[$Path]
        }
    }

    $FilePaths = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    $BaseFilePaths = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    $BaseFileRows = @()
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
        $Supplemental = $SupplementalRows.ContainsKey($Path)
        if (-not $Managed -and -not $Supplemental) {
            Throw-Foundation 'UNSAFE_PATH' "File is outside managed surface: $Path"
        }
        if ($Managed -and $Supplemental) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "File cannot be both package and supplemental payload: $Path"
            )
        }
        if ($Supplemental) {
            $ExpectedSupplemental = $SupplementalRows[$Path]
            if ([string]$Row.sha256 -cne
                    [string]$ExpectedSupplemental.sha256 -or
                [int64]$Row.bytes -ne [int64]$ExpectedSupplemental.bytes) {
                Throw-Foundation 'INVALID_PACKAGE' (
                    "Supplemental payload row differs: $Path"
                )
            }
        } else {
            $null = $BaseFilePaths.Add($Path)
            $BaseFileRows += $Row
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
    foreach ($Path in $SupplementalRows.Keys) {
        if (-not $FilePaths.Contains([string]$Path)) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Supplemental payload has no file row: $Path"
            )
        }
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
        foreach ($Path in $BaseFilePaths) {
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
    return [pscustomobject]@{
        base_file_rows = @($BaseFileRows)
        session_tools_baseline = $BaselineContract
        supplemental_rows = $SupplementalRows
        requires_release_manifest = [bool](
            $null -ne $BaselineContract -or
            (Test-ObjectProperty $Manifest 'shared_tools')
        )
    }
}

function Read-StreamBytesExact {
    param(
        [Parameter(Mandatory = $true)][IO.Stream]$Stream,
        [int64]$MaximumBytes = 8388608
    )
    if (-not $Stream.CanRead -or $Stream.Length -le 0 -or
        $Stream.Length -gt $MaximumBytes -or
        $Stream.Length -gt [int]::MaxValue) {
        Throw-Foundation 'INVALID_PACKAGE' 'Release manifest is outside limits'
    }
    $Bytes = New-Object byte[] ([int]$Stream.Length)
    [int]$Offset = 0
    while ($Offset -lt $Bytes.Length) {
        $Read = $Stream.Read($Bytes, $Offset, $Bytes.Length - $Offset)
        if ($Read -le 0) {
            Throw-Foundation 'INVALID_PACKAGE' 'Release manifest read is incomplete'
        }
        $Offset += $Read
    }
    return ,$Bytes
}

function Assert-ReleaseManifestBinding {
    param(
        [Parameter(Mandatory = $true)]$Release,
        [Parameter(Mandatory = $true)]$PackageManifest,
        [Parameter(Mandatory = $true)][byte[]]$PackageManifestBytes,
        [Parameter(Mandatory = $true)][string]$PackageName,
        [Parameter(Mandatory = $true)][string]$PackageSha256,
        [Parameter(Mandatory = $true)][int64]$PackageBytes
    )
    $Properties = @(
        'schema_version',
        'target',
        'version',
        'tag',
        'channel',
        'client',
        'foundation_engine_version',
        'foundation_engine_manifest_sha256',
        'source',
        'asset',
        'package_manifest_sha256',
        'components_lock_sha256',
        'requires'
    )
    if (Test-ObjectProperty $Release 'session_tools_asset') {
        $Properties += 'session_tools_asset'
    }
    Assert-ExactProperties $Release $Properties 'release manifest'
    Assert-ExactProperties $Release.client @(
        'id',
        'supported_version'
    ) 'release client'
    Assert-ExactProperties $Release.asset @(
        'name',
        'sha256',
        'bytes'
    ) 'release asset'
    Assert-ExactProperties $Release.source @(
        'repository',
        'commit',
        'tree',
        'transformation'
    ) 'release source'
    Assert-ExactProperties $Release.requires @(
        'immutable_release',
        'release_attestation'
    ) 'release requirements'
    $ExpectedTag = (
        [string]$PackageManifest.target + '-v' +
        [string]$PackageManifest.version
    )
    if (($Release.schema_version -isnot [int] -and
            $Release.schema_version -isnot [long]) -or
        [int64]$Release.schema_version -ne 1 -or
        $Release.target -isnot [string] -or
        [string]$Release.target -cne [string]$PackageManifest.target -or
        $Release.version -isnot [string] -or
        [string]$Release.version -cne [string]$PackageManifest.version -or
        $Release.tag -isnot [string] -or
        [string]$Release.tag -cne $ExpectedTag -or
        $Release.channel -isnot [string] -or
        [string]$Release.channel -cne 'stable' -or
        $Release.client.id -isnot [string] -or
        [string]$Release.client.id -cne [string]$PackageManifest.client.id -or
        $Release.client.supported_version -isnot [string] -or
        [string]$Release.client.supported_version -cne
            [string]$PackageManifest.client.supported_version -or
        $Release.foundation_engine_version -isnot [string] -or
        [string]$Release.foundation_engine_version -cne
            [string]$PackageManifest.foundation_engine_version -or
        $Release.foundation_engine_manifest_sha256 -isnot [string] -or
        [string]$Release.foundation_engine_manifest_sha256 -cnotmatch
            '^[0-9a-f]{64}$' -or
        $Release.package_manifest_sha256 -isnot [string] -or
        [string]$Release.package_manifest_sha256 -cne
            (Get-BytesSha256 $PackageManifestBytes) -or
        $Release.components_lock_sha256 -isnot [string] -or
        [string]$Release.components_lock_sha256 -cnotmatch
            '^[0-9a-f]{64}$' -or
        $Release.asset.name -isnot [string] -or
        [string]$Release.asset.name -cne $PackageName -or
        $Release.asset.sha256 -isnot [string] -or
        [string]$Release.asset.sha256 -cne $PackageSha256 -or
        ($Release.asset.bytes -isnot [int] -and
            $Release.asset.bytes -isnot [long]) -or
        [int64]$Release.asset.bytes -ne $PackageBytes -or
        $Release.requires.immutable_release -isnot [bool] -or
        -not [bool]$Release.requires.immutable_release -or
        $Release.requires.release_attestation -isnot [bool] -or
        -not [bool]$Release.requires.release_attestation) {
        Throw-Foundation 'INVALID_PACKAGE' 'Release manifest binding differs'
    }
    foreach ($Property in $Release.source.PSObject.Properties) {
        if ($Property.Value -isnot [string] -or
            [string]::IsNullOrWhiteSpace([string]$Property.Value)) {
            Throw-Foundation 'INVALID_PACKAGE' 'Release source is invalid'
        }
    }
    if (Test-ObjectProperty $Release 'session_tools_asset') {
        Assert-ExactProperties $Release.session_tools_asset @(
            'name',
            'sha256',
            'bytes',
            'manifest_sha256',
            'tool_count',
            'file_count'
        ) 'release session tools asset'
        $SessionAsset = $Release.session_tools_asset
        if ($SessionAsset.name -isnot [string] -or
            [string]::IsNullOrWhiteSpace([string]$SessionAsset.name) -or
            $SessionAsset.sha256 -isnot [string] -or
            [string]$SessionAsset.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            $SessionAsset.manifest_sha256 -isnot [string] -or
            [string]$SessionAsset.manifest_sha256 -cnotmatch
                '^[0-9a-f]{64}$' -or
            ($SessionAsset.bytes -isnot [int] -and
                $SessionAsset.bytes -isnot [long]) -or
            [int64]$SessionAsset.bytes -le 0 -or
            ($SessionAsset.tool_count -isnot [int] -and
                $SessionAsset.tool_count -isnot [long]) -or
            [int64]$SessionAsset.tool_count -le 0 -or
            ($SessionAsset.file_count -isnot [int] -and
                $SessionAsset.file_count -isnot [long]) -or
            [int64]$SessionAsset.file_count -le 0) {
            Throw-Foundation 'INVALID_PACKAGE' (
                'Release session tools asset is invalid'
            )
        }
    }
}

function Open-ValidatedPackage {
    param(
        [Parameter(Mandatory = $true)][string]$PackagePath,
        [string]$ReleaseManifestPath,
        [string]$ExpectedReleaseManifestSha256
    )
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
            [IO.FileShare]::None
        )
        $PackageBytes = [int64]$Stream.Length
        $PackageSha256 = Get-StreamSha256 $Stream
        $Stream.Position = 0
        $Archive = New-Object IO.Compression.ZipArchive(
            $Stream,
            [IO.Compression.ZipArchiveMode]::Read,
            $false
        )
    } catch {
        if ($null -ne $Archive) { $Archive.Dispose() }
        if ($null -ne $Stream) { $Stream.Dispose() }
        $Cursor = $_.Exception
        while ($null -ne $Cursor.InnerException) {
            $Cursor = $Cursor.InnerException
        }
        $NativeError = $Cursor.HResult -band 0xffff
        if ($NativeError -in @(32, 33)) {
            Throw-Foundation 'LOCKED' 'Package ZIP is locked by another operation'
        }
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
        $Contract = Assert-Manifest $Manifest $Entries
        $ReleaseStream = $null
        $ReleaseBytes = $null
        $ReleaseSha256 = $null
        $HasExplicitRelease = -not [string]::IsNullOrWhiteSpace(
            $ReleaseManifestPath
        )
        if ([bool]$Contract.requires_release_manifest -or
            $HasExplicitRelease) {
            $BoundReleasePath = if ($HasExplicitRelease) {
                [IO.Path]::GetFullPath($ReleaseManifestPath)
            } else {
                [IO.Path]::GetFullPath(
                    (Join-Path $Item.DirectoryName 'release-manifest.json')
                )
            }
            if ($BoundReleasePath.Equals(
                    $Item.FullName,
                    [StringComparison]::OrdinalIgnoreCase
                ) -or
                -not (Test-Path -LiteralPath $BoundReleasePath -PathType Leaf)) {
                Throw-Foundation 'INVALID_PACKAGE' (
                    'Release manifest path is invalid'
                )
            }
            $ReleaseItem = Get-Item -LiteralPath $BoundReleasePath -Force
            if ($ReleaseItem.PSIsContainer -or
                ($ReleaseItem.Attributes -band
                    [IO.FileAttributes]::ReparsePoint) -or
                $ReleaseItem.Length -le 0 -or
                $ReleaseItem.Length -gt 8388608) {
                Throw-Foundation 'INVALID_PACKAGE' (
                    'Release manifest is outside limits'
                )
            }
            try {
                $ReleaseStream = [IO.File]::Open(
                    $ReleaseItem.FullName,
                    [IO.FileMode]::Open,
                    [IO.FileAccess]::Read,
                    [IO.FileShare]::None
                )
                $ReleaseBytes = Read-StreamBytesExact $ReleaseStream
                $ReleaseSha256 = Get-BytesSha256 $ReleaseBytes
                if ($HasExplicitRelease -and
                    $ReleaseSha256 -cne
                        $ExpectedReleaseManifestSha256) {
                    Throw-Foundation 'INVALID_PACKAGE' (
                        'Release manifest hash differs'
                    )
                }
                try {
                    $ReleaseText = (
                        New-Object Text.UTF8Encoding($false, $true)
                    ).GetString($ReleaseBytes)
                    $ReleaseValue = ConvertFrom-Json -InputObject (
                        $ReleaseText
                    ) -ErrorAction Stop
                } catch {
                    Throw-Foundation 'INVALID_PACKAGE' (
                        'Release manifest JSON is invalid'
                    )
                }
                Assert-ReleaseManifestBinding `
                    $ReleaseValue `
                    $Manifest `
                    $Bytes `
                    $Item.Name `
                    $PackageSha256 `
                    $PackageBytes
            } catch {
                if ($null -ne $ReleaseStream) {
                    $ReleaseStream.Dispose()
                }
                throw
            }
        }
        return [pscustomobject]@{
            stream = $Stream
            archive = $Archive
            entries = $Entries
            manifest = $Manifest
            manifest_bytes = $Bytes
            package_path = $Item.FullName
            package_sha256 = $PackageSha256
            release_manifest_stream = $ReleaseStream
            release_manifest_bytes = $ReleaseBytes
            release_manifest_sha256 = $ReleaseSha256
            base_file_rows = @($Contract.base_file_rows)
            session_tools_baseline = $Contract.session_tools_baseline
            supplemental_rows = $Contract.supplemental_rows
        }
    } catch {
        if ($null -ne $ReleaseStream) { $ReleaseStream.Dispose() }
        $Archive.Dispose()
        $Stream.Dispose()
        throw
    }
}

function Close-ValidatedPackage {
    param($Validated)
    if ($null -ne $Validated.release_manifest_stream) {
        $Validated.release_manifest_stream.Dispose()
    }
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
        local_exceptions = Join-Path $StateRoot 'local-exceptions.json'
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
        'local_exceptions',
        'desired_state',
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
    Assert-StringArray @($State.local_exceptions) 'local exceptions' -AllowTomlIdentity
    if ($State.desired_state -isnot [bool] -and
        $State.desired_state -isnot [Management.Automation.PSCustomObject]) {
        Throw-Foundation 'INVALID_PACKAGE' 'Active desired-state marker is invalid'
    }
    if ($State.desired_state -is [Management.Automation.PSCustomObject]) {
        Assert-DesiredStateContract $State.desired_state
    }
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
    $null = Get-ManagedSurfaceDigest `
        $State.managed_surface -AllowSessionState
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
    $Unknown = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    $InventoryRoots = @($Manifest.managed_surface.exact_directories)
    $PlatformOwned = @()
    if (Test-ObjectProperty $Manifest 'desired_state') {
        $InventoryRoots += @($Manifest.desired_state.inventory_roots)
        $PlatformOwned = @($Manifest.desired_state.platform_owned)
    }
    foreach ($Root in @(Sort-OrdinalStrings $InventoryRoots)) {
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
        foreach ($OwnedPathValue in $PlatformOwned) {
            $OwnedPath = [string]$OwnedPathValue
            $Prefix = [string]$Root + '/'
            if ($OwnedPath.StartsWith(
                $Prefix,
                [StringComparison]::OrdinalIgnoreCase
            )) {
                $Remainder = $OwnedPath.Substring($Prefix.Length)
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
                $null = $Unknown.Add([string]$Root + '/' + $Child.Name)
            }
        }
    }
    return @(Sort-OrdinalStrings @($Unknown))
}

function Get-TomlUnknownEntries {
    param(
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    if (-not (Test-ObjectProperty $Manifest 'desired_state')) { return @() }
    $Unknown = @()
    foreach ($Rule in @($Manifest.desired_state.toml_reconcile)) {
        $Relative = [string]$Rule.path
        $Path = Resolve-HomePath $Relative $HomeRoot
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { continue }
        $Existing = Read-Utf8TextFile $Path
        $AllowedEntries = New-Object 'Collections.Generic.HashSet[string]' (
            [StringComparer]::Ordinal
        )
        foreach ($Value in @($Rule.allowed_entries)) {
            $null = $AllowedEntries.Add([string]$Value)
        }
        $Protected = New-Object 'Collections.Generic.HashSet[string]' (
            [StringComparer]::Ordinal
        )
        foreach ($Value in @($Rule.protected_tables)) {
            $null = $Protected.Add([string]$Value)
        }
        $Owned = @($Rule.exact_tables)
        $Section = ''
        foreach ($Line in @([regex]::Split($Existing, '\r?\n'))) {
            if ($Line -match '^\s*\[([^\[\]]+)\]') {
                $Section = [string]$Matches[1]
                $Root = ($Section -split '\.', 2)[0]
                $IsProtected = @($Protected | Where-Object {
                    $Section -ceq $_ -or $Section.StartsWith(
                        [string]$_ + '.', [StringComparison]::Ordinal
                    )
                }).Count -gt 0
                if ($Owned -contains $Root -and -not $IsProtected -and
                    -not $AllowedEntries.Contains($Section) -and
                    $Section -cne 'plugins') {
                    $Unknown += ('toml:' + $Relative + '#' + $Section)
                }
            } elseif ($Section -ceq 'plugins' -and
                $Line -match '^\s*([A-Za-z0-9_-]+)\s*=') {
                $Key = [string]$Matches[1]
                if (-not $AllowedEntries.Contains('plugins.' + $Key)) {
                    $Unknown += ('toml:' + $Relative + '#plugins.' + $Key)
                }
            }
        }
    }
    return @(Sort-OrdinalStrings $Unknown)
}

function Get-UnknownEntryDetails {
    param(
        [AllowEmptyCollection()][object[]]$Paths,
        [string]$HomeRoot
    )
    $Rows = @(
        foreach ($PathValue in @(Sort-OrdinalStrings $Paths)) {
            $Path = [string]$PathValue
            $Kind = if ($Path -cmatch '^toml:.*#mcp_servers\.') {
                'mcp'
            } elseif ($Path -cmatch '^toml:.*#plugin_marketplaces\.') {
                'marketplace'
            } elseif ($Path -cmatch '^toml:.*#plugins\.') {
                'plugin'
            } elseif ($Path -cmatch '^\.agents/skills/') {
                'skill'
            } elseif ($Path -cmatch '/agents/') {
                'agent'
            } else {
                'managed-entry'
            }
            $LaunchCommand = $null
            if ($Path -cmatch '^toml:([^#]+)#(.+)$' -and $HomeRoot) {
                $TomlPath = Resolve-HomePath ([string]$Matches[1]) $HomeRoot
                $WantedSection = [string]$Matches[2]
                if (Test-Path -LiteralPath $TomlPath -PathType Leaf) {
                    $Section = ''
                    foreach ($Line in [regex]::Split((Read-Utf8TextFile $TomlPath), '\r?\n')) {
                        if ($Line -match '^\s*\[([^\[\]]+)\]') {
                            $Section = [string]$Matches[1]
                        } elseif ($Section -ceq $WantedSection -and
                            $Line -match '^\s*(command|url)\s*=\s*["'']([^"'']+)["'']') {
                            $LaunchCommand = [string]$Matches[2]
                            break
                        }
                    }
                }
            }
            [pscustomobject][ordered]@{
                path = $Path
                kind = $Kind
                active = $true
                registration_path = $Path
                launch_command = $LaunchCommand
                duplicates = @()
                source = if ($Path.StartsWith('toml:')) {
                    'codex-config-toml'
                } else { 'local-unmanaged' }
                risk = if ($Kind -ceq 'skill') {
                    'UNREVIEWED_EXECUTABLE_INSTRUCTIONS'
                } elseif ($Kind -in @('mcp', 'plugin', 'marketplace')) {
                    'UNMANAGED_RUNTIME_REGISTRATION'
                } else {
                    'UNREVIEWED_RUNTIME_COMPONENT'
                }
            }
        }
    )
    foreach ($Row in $Rows) {
        if (-not [string]::IsNullOrWhiteSpace([string]$Row.launch_command)) {
            $Row.duplicates = @($Rows | Where-Object {
                [string]$_.path -cne [string]$Row.path -and
                [string]$_.launch_command -ceq [string]$Row.launch_command
            } | ForEach-Object { [string]$_.path })
        }
    }
    return $Rows
}

function Get-ValidatedLocalExceptions {
    param(
        [AllowEmptyCollection()][object[]]$Requested,
        [AllowEmptyCollection()][object[]]$Unknown
    )
    $UnknownSet = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($Path in @($Unknown)) { $null = $UnknownSet.Add([string]$Path) }
    $Result = @()
    foreach ($PathValue in @(Sort-OrdinalStrings $Requested)) {
        $Path = [string]$PathValue
        $IsTomlIdentity = $Path -cmatch (
            '^toml:\.[A-Za-z0-9._/-]+#[A-Za-z0-9_.-]+$'
        )
        if ((-not $IsTomlIdentity -and -not (Test-PortablePath $Path)) -or
            -not $UnknownSet.Contains($Path)) {
            Throw-Foundation 'INVALID_ARGUMENT' (
                "Local exception is not an unknown managed entry: $Path"
            )
        }
        $Result += $Path
    }
    return @(Sort-OrdinalStrings $Result)
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

function Reconcile-TomlText {
    param(
        [AllowEmptyString()][string]$Existing,
        [Parameter(Mandatory = $true)][string]$Required,
        [AllowEmptyCollection()][object[]]$ExactTables,
        [AllowEmptyCollection()][object[]]$ProtectedTables = @()
    )
    $Owned = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::Ordinal
    )
    foreach ($Table in @($ExactTables)) { $null = $Owned.Add([string]$Table) }
    $Protected = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::Ordinal
    )
    foreach ($Table in @($ProtectedTables)) {
        $null = $Protected.Add([string]$Table)
    }
    foreach ($IdentityValue in @($script:ActiveLocalTomlExceptions)) {
        $Identity = [string]$IdentityValue
        if ($Identity -match '^toml:[^#]+#(.+)$') {
            $Table = [string]$Matches[1]
            if ($Table.StartsWith('plugins.', [StringComparison]::Ordinal)) {
                $Table = 'plugins'
            }
            $null = $Protected.Add($Table)
        }
    }
    $Kept = New-Object 'Collections.Generic.List[string]'
    $Skip = $false
    foreach ($Line in @([regex]::Split($Existing, "`r?`n"))) {
        if ($Line -match '^\s*\[([^\[\]]+)\]\s*(?:#.*)?$') {
            $Header = [string]$Matches[1]
            $RootTable = ($Header -split '\.', 2)[0]
            $IsProtected = $false
            foreach ($ProtectedTable in $Protected) {
                if ($Header -ceq $ProtectedTable -or
                    $Header.StartsWith(
                        $ProtectedTable + '.',
                        [StringComparison]::Ordinal
                    )) {
                    $IsProtected = $true
                    break
                }
            }
            $Skip = $Owned.Contains($RootTable) -and -not $IsProtected
        }
        if (-not $Skip) { $Kept.Add([string]$Line) }
    }
    return Merge-TomlText (($Kept -join "`n").TrimEnd() + "`n") $Required
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
    $Required = Read-Utf8TextFile $Source
    $Rule = $null
    if ($null -ne $script:ActiveDesiredState) {
        $Relative = $script:ActiveMergeTomlRelativePath
        $Rule = @($script:ActiveDesiredState.toml_reconcile | Where-Object {
            [string]$_.path -ceq [string]$Relative
        }) | Select-Object -First 1
    }
    $Merged = if ($null -ne $Rule) {
        Reconcile-TomlText $Existing $Required @($Rule.exact_tables) `
            @($Rule.protected_tables)
    } else {
        Merge-TomlText $Existing $Required
    }
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

function Get-SessionToolsPaths {
    param(
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$TargetName,
        [Parameter(Mandatory = $true)]$ManagedSurface
    )
    $Relative = Get-SessionToolsRelativePaths $TargetName $ManagedSurface
    return [pscustomobject]@{
        target = $TargetName
        skills_root_relative = [string]$Relative.skills_root_relative
        skills_root = Resolve-HomePath `
            ([string]$Relative.skills_root_relative) $HomeRoot
        runtime_relative = [string]$Relative.runtime_relative
        runtime_path = Resolve-HomePath `
            ([string]$Relative.runtime_relative) $HomeRoot
        state_relative = [string]$Relative.state_relative
        state_path = Resolve-HomePath `
            ([string]$Relative.state_relative) $HomeRoot -AllowSessionState
    }
}

function Get-SessionToolDestinationRelative {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)][string]$ToolId
    )
    return [string]$Paths.skills_root_relative + '/' + $ToolId
}

function Assert-SessionToolDestinationFiles {
    param(
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)]$Files,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$FailureCode
    )
    Assert-SafeAncestors $Destination $HomeRoot
    if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
        Throw-Foundation $FailureCode 'Session tool destination is missing'
    }
    Assert-SafeDirectory $Destination
    $Expected = @{}
    foreach ($Row in @($Files)) {
        $Relative = [string]$Row.path
        $Expected[$Relative] = $Row
        $Path = Join-Path $Destination ($Relative.Replace('/', '\'))
        Assert-SafeAncestors $Path $HomeRoot
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf) -or
            (Get-Item -LiteralPath $Path -Force).Length -ne
                [int64]$Row.bytes -or
            (Get-FileSha256 $Path) -cne [string]$Row.sha256) {
            Throw-Foundation $FailureCode (
                "Session tool file differs: $Relative"
            )
        }
    }
    $Actual = @(Get-SafeTreeFiles $Destination)
    if ($Actual.Count -ne $Expected.Count) {
        Throw-Foundation $FailureCode 'Session tool destination has extra files'
    }
    $Root = [IO.Path]::GetFullPath($Destination)
    foreach ($File in $Actual) {
        $Relative = $File.FullName.Substring($Root.Length).
            TrimStart('\').Replace('\', '/')
        if (-not $Expected.ContainsKey($Relative)) {
            Throw-Foundation $FailureCode (
                "Session tool destination has an extra file: $Relative"
            )
        }
    }
}

function Assert-SessionToolsState {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$TargetName,
        [Parameter(Mandatory = $true)]$Paths,
        [switch]$CheckDestination
    )
    Assert-ExactProperties $State @(
        'schema_version',
        'target',
        'release_tag',
        'release_version',
        'release_manifest_sha256',
        'session_manifest_sha256',
        'verified_at',
        'tools'
    ) 'session tools state'
    if (($State.schema_version -isnot [int] -and
            $State.schema_version -isnot [long]) -or
        [int64]$State.schema_version -ne 1 -or
        $State.target -isnot [string] -or
        [string]$State.target -cne $TargetName -or
        $State.release_tag -isnot [string] -or
        [string]$State.release_tag -cne
            ($TargetName + '-v' + [string]$State.release_version) -or
        $State.release_version -isnot [string] -or
        [string]$State.release_version -cnotmatch
            '^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$' -or
        $State.release_manifest_sha256 -isnot [string] -or
        [string]$State.release_manifest_sha256 -cnotmatch
            '^[0-9a-f]{64}$' -or
        $State.session_manifest_sha256 -isnot [string] -or
        [string]$State.session_manifest_sha256 -cnotmatch
            '^[0-9a-f]{64}$' -or
        $State.verified_at -isnot [string] -or
        [string]::IsNullOrWhiteSpace([string]$State.verified_at) -or
        $State.tools -isnot [Array] -or
        @($State.tools).Count -eq 0) {
        Throw-Foundation 'INVALID_PACKAGE' 'Session tools state is invalid'
    }
    try {
        $null = [DateTimeOffset]::Parse(
            [string]$State.verified_at,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        )
    } catch {
        Throw-Foundation 'INVALID_PACKAGE' 'Session tools state time is invalid'
    }
    $PreviousTool = $null
    $Ids = New-Object 'Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($Tool in @($State.tools)) {
        Assert-ExactProperties $Tool @(
            'id',
            'destination',
            'ownership_marker',
            'files'
        ) 'session tools state tool'
        $ToolId = [string]$Tool.id
        $ExpectedDestination = [IO.Path]::GetFullPath(
            (Join-Path $Paths.skills_root $ToolId)
        )
        if ($Tool.id -isnot [string] -or
            $ToolId -cnotmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,63}$' -or
            -not $Ids.Add($ToolId) -or
            ($null -ne $PreviousTool -and
                [StringComparer]::Ordinal.Compare(
                    [string]$PreviousTool,
                    $ToolId
                ) -ge 0) -or
            $Tool.destination -isnot [string] -or
            -not ([string]$Tool.destination).Equals(
                $ExpectedDestination,
                [StringComparison]::Ordinal
            ) -or
            $Tool.ownership_marker -isnot [string] -or
            [string]$Tool.ownership_marker -cne
                ('session-tools-v1:' + $TargetName + ':' + $ToolId)) {
            Throw-Foundation 'INVALID_PACKAGE' (
                'Session tools state ownership is invalid'
            )
        }
        $StateToolContract = [pscustomobject]@{
            id = [string]$Tool.id
            files = @($Tool.files)
        }
        $null = Assert-SessionToolRecords (, $StateToolContract) 'state'
        if ($CheckDestination) {
            Assert-SessionToolDestinationFiles `
                $ExpectedDestination `
                $Tool.files `
                $HomeRoot `
                'ACTIVE_DRIFT'
        }
        $PreviousTool = $ToolId
    }
    return $State
}

function Get-SessionToolsBaselinePlan {
    param(
        [Parameter(Mandatory = $true)]$Validated,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    if ($null -eq $Validated.session_tools_baseline) { return $null }
    $TargetName = [string]$Validated.manifest.target
    $Paths = Get-SessionToolsPaths `
        $HomeRoot $TargetName $Validated.manifest.managed_surface
    foreach ($Path in @(
        $Paths.skills_root,
        $Paths.runtime_path,
        $Paths.state_path
    )) {
        Assert-SafeAncestors $Path $HomeRoot
    }
    if (Test-Path -LiteralPath $Paths.state_path -PathType Leaf) {
        $State = Read-JsonFile $Paths.state_path
        $null = Assert-SessionToolsState `
            $State $HomeRoot $TargetName $Paths -CheckDestination
        return [pscustomobject]@{
            action = 'PRESERVE'
            paths = $Paths
            state = $State
        }
    }
    if (Test-Path -LiteralPath $Paths.state_path) {
        Throw-Foundation 'INVALID_PACKAGE' 'Session tools state path conflicts'
    }
    $Actions = @()
    foreach ($Tool in @($Validated.session_tools_baseline.manifest.tools)) {
        $Destination = [IO.Path]::GetFullPath(
            (Join-Path $Paths.skills_root ([string]$Tool.id))
        )
        $Action = 'INSTALL'
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Unmanaged session tool collision: $($Tool.id)"
            )
        }
        if (Test-Path -LiteralPath $Destination -PathType Container) {
            try {
                Assert-SessionToolDestinationFiles `
                    $Destination `
                    $Tool.files `
                    $HomeRoot `
                    'INVALID_PACKAGE'
                $Action = 'ADOPT'
            } catch {
                Throw-Foundation 'INVALID_PACKAGE' (
                    "Unmanaged session tool collision: $($Tool.id)"
                )
            }
        }
        $Actions += [pscustomobject]@{
            id = [string]$Tool.id
            destination = $Destination
            action = $Action
        }
    }
    return [pscustomobject]@{
        action = 'INITIALIZE'
        paths = $Paths
        tools = $Actions
    }
}

function New-BaselineSessionToolsState {
    param(
        [Parameter(Mandatory = $true)]$Validated,
        [Parameter(Mandatory = $true)]$BaselinePlan
    )
    $TargetName = [string]$Validated.manifest.target
    $Tools = @(
        foreach ($Tool in @(
            $Validated.session_tools_baseline.manifest.tools
        )) {
            [pscustomobject][ordered]@{
                id = [string]$Tool.id
                destination = [IO.Path]::GetFullPath(
                    (Join-Path $BaselinePlan.paths.skills_root (
                        [string]$Tool.id
                    ))
                )
                ownership_marker = (
                    'session-tools-v1:' + $TargetName + ':' +
                    [string]$Tool.id
                )
                files = @($Tool.files)
            }
        }
    )
    return [pscustomobject][ordered]@{
        schema_version = 1
        target = $TargetName
        release_tag = [string](
            $Validated.session_tools_baseline.manifest.release_tag
        )
        release_version = [string]$Validated.manifest.version
        release_manifest_sha256 = [string](
            $Validated.release_manifest_sha256
        )
        session_manifest_sha256 = [string](
            $Validated.manifest.session_tools_baseline.manifest_sha256
        )
        verified_at = [DateTimeOffset]::UtcNow.ToString('o')
        tools = $Tools
    }
}

function Install-SessionToolsBaseline {
    param(
        [Parameter(Mandatory = $true)]$Validated,
        [Parameter(Mandatory = $true)]$BaselinePlan,
        [Parameter(Mandatory = $true)][string]$StagingRoot,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    $ManifestSource = Join-Path $StagingRoot (
        ([string]$Validated.manifest.session_tools_baseline.manifest_path).
            Replace('/', '\')
    )
    Copy-Atomic `
        $ManifestSource `
        $BaselinePlan.paths.runtime_path `
        $HomeRoot
    Invoke-MutationCheckpoint
    if ([string]$BaselinePlan.action -ceq 'INITIALIZE') {
        foreach ($Action in @($BaselinePlan.tools)) {
            $Tool = @(
                $Validated.session_tools_baseline.manifest.tools |
                    Where-Object { [string]$_.id -ceq [string]$Action.id }
            )[0]
            if ([string]$Action.action -ceq 'INSTALL') {
                New-SafeDirectory $Action.destination $HomeRoot
                foreach ($Row in @($Tool.files)) {
                    $PayloadPath = (
                        'session-tools-baseline/tools/' +
                        [string]$Tool.id + '/' + [string]$Row.path
                    )
                    $Source = Join-Path $StagingRoot (
                        $PayloadPath.Replace('/', '\')
                    )
                    $Destination = Join-Path $Action.destination (
                        ([string]$Row.path).Replace('/', '\')
                    )
                    Copy-Atomic $Source $Destination $HomeRoot
                    Invoke-MutationCheckpoint
                }
            }
        }
        $State = New-BaselineSessionToolsState $Validated $BaselinePlan
        $null = Assert-SessionToolsState `
            $State `
            $HomeRoot `
            ([string]$Validated.manifest.target) `
            $BaselinePlan.paths `
            -CheckDestination
        Write-JsonFile $State $BaselinePlan.paths.state_path
        Invoke-MutationCheckpoint
    }
    return [pscustomobject][ordered]@{
        path = [string]$BaselinePlan.paths.runtime_relative
        sha256 = [string](
            $Validated.manifest.session_tools_baseline.manifest_sha256
        )
        bytes = [int64](
            $Validated.session_tools_baseline.manifest_bytes.Length
        )
    }
}

function Get-RetiredManagedPlan {
    param(
        [Parameter(Mandatory = $true)]$Validated,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        $ActiveState
    )
    if (-not (Test-ObjectProperty (
            $Validated.manifest
        ) 'retired_managed_paths')) {
        return @()
    }
    $Actions = @()
    foreach ($RetiredValue in @(
        $Validated.manifest.retired_managed_paths
    )) {
        $Retired = [string]$RetiredValue
        $Destination = Resolve-HomePath $Retired $HomeRoot
        Assert-SafeAncestors $Destination $HomeRoot
        if ($null -eq $ActiveState -or
            -not (Test-Path -LiteralPath $Destination)) {
            continue
        }
        $PreviouslyOwned = [bool](
            @($ActiveState.managed_surface.replace_files) -ccontains $Retired
        )
        if (-not $PreviouslyOwned) {
            foreach ($Root in @(
                $ActiveState.managed_surface.exact_directories
            )) {
                if ($Retired.Equals(
                        [string]$Root,
                        [StringComparison]::OrdinalIgnoreCase
                    ) -or
                    $Retired.StartsWith(
                        [string]$Root + '/',
                        [StringComparison]::OrdinalIgnoreCase
                    )) {
                    $PreviouslyOwned = $true
                    break
                }
            }
        }
        if (-not $PreviouslyOwned) { continue }
        $Rows = @(
            $ActiveState.installed_files | Where-Object {
                [string]$_.path -ceq $Retired -or
                ([string]$_.path).StartsWith(
                    $Retired + '/',
                    [StringComparison]::OrdinalIgnoreCase
                )
            }
        )
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            if ($Rows.Count -ne 1 -or
                [string]$Rows[0].path -cne $Retired -or
                (Get-Item -LiteralPath $Destination -Force).Length -ne
                    [int64]$Rows[0].bytes -or
                (Get-FileSha256 $Destination) -cne
                    [string]$Rows[0].sha256) {
                continue
            }
            $Actions += [pscustomobject]@{
                path = $Retired
                destination = $Destination
                kind = 'file'
            }
            continue
        }
        if (-not (Test-Path -LiteralPath $Destination -PathType Container) -or
            $Rows.Count -eq 0) {
            continue
        }
        Assert-SafeDirectory $Destination
        $Expected = @{}
        $Unchanged = $true
        foreach ($Row in $Rows) {
            $Relative = ([string]$Row.path).Substring(
                $Retired.Length
            ).TrimStart('/')
            if ([string]::IsNullOrWhiteSpace($Relative) -or
                $Expected.ContainsKey($Relative)) {
                $Unchanged = $false
                break
            }
            $Expected[$Relative] = $Row
            $FilePath = Join-Path $Destination ($Relative.Replace('/', '\'))
            if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf) -or
                (Get-Item -LiteralPath $FilePath -Force).Length -ne
                    [int64]$Row.bytes -or
                (Get-FileSha256 $FilePath) -cne [string]$Row.sha256) {
                $Unchanged = $false
                break
            }
        }
        if (-not $Unchanged) { continue }
        $Actual = @(Get-SafeTreeFiles $Destination)
        if ($Actual.Count -ne $Expected.Count) { continue }
        $RootPath = [IO.Path]::GetFullPath($Destination)
        foreach ($File in $Actual) {
            $Relative = $File.FullName.Substring($RootPath.Length).
                TrimStart('\').Replace('\', '/')
            if (-not $Expected.ContainsKey($Relative)) {
                $Unchanged = $false
                break
            }
        }
        if ($Unchanged) {
            $Actions += [pscustomobject]@{
                path = $Retired
                destination = $Destination
                kind = 'directory'
            }
        }
    }
    return @($Actions)
}

function New-FoundationPlan {
    param(
        [Parameter(Mandatory = $true)]$Validated,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$ActualClientId,
        [Parameter(Mandatory = $true)][string]$ActualClientVersion,
        [AllowEmptyCollection()][object[]]$RequestedLocalExceptions = @(),
        [switch]$RemoveUnknownConfirmed
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
    $BaselinePlan = Get-SessionToolsBaselinePlan $Validated $HomeRoot
    $RetiredPlan = @(Get-RetiredManagedPlan $Validated $HomeRoot $Active)
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
    foreach ($Row in @($Validated.base_file_rows)) {
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
    $UnknownEntries = @(Sort-OrdinalStrings @(
        @(Get-UnknownEntries $Manifest $HomeRoot) +
        @(Get-TomlUnknownEntries $Manifest $HomeRoot)
    ))
    if ($null -ne $BaselinePlan) {
        $UnknownEntries = @(
            $UnknownEntries | Where-Object {
                [string]$_ -cne [string]$BaselinePlan.paths.runtime_relative
            }
        )
    }
    $LocalExceptions = @(
        Get-ValidatedLocalExceptions $RequestedLocalExceptions $UnknownEntries
    )
    if ((Test-ObjectProperty $Manifest 'desired_state') -and
        $UnknownEntries.Count -gt 0 -and
        -not $RemoveUnknownConfirmed -and
        $LocalExceptions.Count -ne $UnknownEntries.Count) {
        return [pscustomobject][ordered]@{
            status = 'BLOCKED_USER_DECISION'
            target = [string]$Manifest.target
            release_version = [string]$Manifest.version
            unknown_entries = @(Get-UnknownEntryDetails $UnknownEntries $HomeRoot)
            local_exceptions = $LocalExceptions
            package_path = [string]$Validated.package_path
            package_sha256 = [string]$Validated.package_sha256
            target_home = [IO.Path]::GetFullPath($HomeRoot)
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
        quarantined_unknown = $UnknownEntries
        local_exceptions = $LocalExceptions
        package_path = [string]$Validated.package_path
        package_sha256 = [string]$Validated.package_sha256
        target_home = [IO.Path]::GetFullPath($HomeRoot)
        remove_unknown = @(
            $UnknownEntries | Where-Object {
                $LocalExceptions -cnotcontains $_ -and
                -not ([string]$_).StartsWith('toml:')
            }
        )
    }
}

function Restore-LocalExceptions {
    param(
        [Parameter(Mandatory = $true)]$Snapshot,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [AllowEmptyCollection()][object[]]$Paths
    )
    foreach ($RelativeValue in @(Sort-OrdinalStrings $Paths)) {
        $Relative = [string]$RelativeValue
        if ($Relative.StartsWith('toml:')) { continue }
        $Source = Join-Path (Join-Path $Snapshot.root 'managed') (
            $Relative.Replace('/', '\')
        )
        $Destination = Resolve-HomePath $Relative $HomeRoot
        Assert-SafeAncestors $Destination $HomeRoot
        if (Test-Path -LiteralPath $Destination) { continue }
        if (Test-Path -LiteralPath $Source -PathType Container) {
            Copy-TreeSafe $Source $Destination
        } elseif (Test-Path -LiteralPath $Source -PathType Leaf) {
            Copy-Atomic $Source $Destination $HomeRoot
        } else {
            Throw-Foundation 'INVALID_PACKAGE' (
                "Local exception is missing from snapshot: $Relative"
            )
        }
        Invoke-MutationCheckpoint
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
    param(
        [Parameter(Mandatory = $true)]$Surface,
        [switch]$AllowSessionState
    )
    Assert-ManagedSurface $Surface -AllowSessionState:$AllowSessionState
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
        [Parameter(Mandatory = $true)]$Plan,
        $BaselinePlan,
        [AllowEmptyCollection()][object[]]$RetiredPlan = @()
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
    $BaseManagedSurface = $Validated.manifest.managed_surface
    $SnapshotManagedSurface = $BaseManagedSurface
    if ($null -ne $BaselinePlan -or @($RetiredPlan).Count -gt 0 -or
        @($Plan.remove_unknown).Count -gt 0) {
        $AdditionalExactDirectories = @(
            if ($null -ne $BaselinePlan) {
                foreach ($Tool in @(
                    $Validated.session_tools_baseline.manifest.tools
                )) {
                    Get-SessionToolDestinationRelative `
                        $BaselinePlan.paths `
                        ([string]$Tool.id)
                }
            }
            foreach ($Action in @($RetiredPlan)) {
                if ([string]$Action.kind -ceq 'directory') {
                    [string]$Action.path
                }
            }
            foreach ($UnknownValue in @($Plan.remove_unknown)) {
                $Unknown = [string]$UnknownValue
                $Covered = $false
                foreach ($BaseRootValue in @(
                    $BaseManagedSurface.exact_directories
                )) {
                    $BaseRoot = [string]$BaseRootValue
                    if ($Unknown.Equals(
                            $BaseRoot,
                            [StringComparison]::OrdinalIgnoreCase
                        ) -or $Unknown.StartsWith(
                            $BaseRoot + '/',
                            [StringComparison]::OrdinalIgnoreCase
                        )) {
                        $Covered = $true
                        break
                    }
                }
                if (-not $Covered) {
                    $UnknownAbsolute = Resolve-HomePath $Unknown $HomeRoot
                    if (Test-Path -LiteralPath $UnknownAbsolute -PathType Container) {
                        $Unknown
                    }
                }
            }
        )
        $AdditionalReplaceFiles = @(
            if ($null -ne $BaselinePlan) {
                [string]$BaselinePlan.paths.state_relative
            }
            foreach ($Action in @($RetiredPlan)) {
                if ([string]$Action.kind -ceq 'file') {
                    [string]$Action.path
                }
            }
            foreach ($UnknownValue in @($Plan.remove_unknown)) {
                $Unknown = [string]$UnknownValue
                $Covered = $false
                foreach ($BaseRootValue in @(
                    $BaseManagedSurface.exact_directories
                )) {
                    $BaseRoot = [string]$BaseRootValue
                    if ($Unknown.Equals(
                            $BaseRoot,
                            [StringComparison]::OrdinalIgnoreCase
                        ) -or $Unknown.StartsWith(
                            $BaseRoot + '/',
                            [StringComparison]::OrdinalIgnoreCase
                        )) {
                        $Covered = $true
                        break
                    }
                }
                if (-not $Covered) {
                    $UnknownAbsolute = Resolve-HomePath $Unknown $HomeRoot
                    if (Test-Path -LiteralPath $UnknownAbsolute -PathType Leaf) {
                        $Unknown
                    }
                }
            }
        )
        $ExactDirectories = @(Sort-OrdinalStrings @(
            @($BaseManagedSurface.exact_directories) +
            $AdditionalExactDirectories
        ))
        $ReplaceFiles = @(Sort-OrdinalStrings @(
            @($BaseManagedSurface.replace_files) +
            $AdditionalReplaceFiles
        ))
        $SnapshotManagedSurface = [pscustomobject][ordered]@{
            exact_directories = $ExactDirectories
            replace_files = $ReplaceFiles
            preserved_paths = @($BaseManagedSurface.preserved_paths)
        }
        if (Test-ObjectProperty $BaseManagedSurface 'merge_toml_files') {
            $SnapshotManagedSurface | Add-Member -NotePropertyName (
                'merge_toml_files'
            ) -NotePropertyValue @($BaseManagedSurface.merge_toml_files)
        }
    }
    $Existed = @()
    foreach ($Root in @(
        $SnapshotManagedSurface.exact_directories
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
        @($SnapshotManagedSurface.replace_files) +
        @(Get-MergeTomlFiles $SnapshotManagedSurface)
    )) {
        $Source = Resolve-HomePath `
            ([string]$Relative) $HomeRoot -AllowSessionState
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
        schema_version = 4
        snapshot_id = $SnapshotId
        target = [string]$Validated.manifest.target
        release_version = [string]$Validated.manifest.version
        managed_surface = $SnapshotManagedSurface
        base_managed_surface = $BaseManagedSurface
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
    $SnapshotProperties = @(
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
    )
    if ($Snapshot.schema_version -eq 4) {
        $SnapshotProperties += 'base_managed_surface'
    }
    Assert-ExactProperties $Snapshot $SnapshotProperties 'snapshot'
    $AllowSessionState = $Snapshot.schema_version -eq 4
    $ExpectedSurfaceDigest = Get-ManagedSurfaceDigest `
        $Expected.managed_surface `
        -AllowSessionState:$AllowSessionState
    $SnapshotSurfaceMatches = (
        (Get-ManagedSurfaceDigest `
            $Snapshot.managed_surface `
            -AllowSessionState:$AllowSessionState) -ceq
            $ExpectedSurfaceDigest
    )
    if ($Snapshot.schema_version -eq 4) {
        $SnapshotSurfaceMatches = $SnapshotSurfaceMatches -or (
            (Get-ManagedSurfaceDigest $Snapshot.base_managed_surface) -ceq
                $ExpectedSurfaceDigest
        )
    }
    if ($Snapshot.schema_version -notin @(3, 4) -or
        $Snapshot.snapshot_id -notmatch
            '^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}$' -or
        [string]$Snapshot.target -cne [string]$Paths.target -or
        $Snapshot.release_version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        -not $SnapshotSurfaceMatches -or
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
        $Destination = Resolve-HomePath `
            ([string]$Relative) $HomeRoot -AllowSessionState
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
            $Destination = Resolve-HomePath `
                ([string]$Relative) $HomeRoot -AllowSessionState
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
        if ($null -ne $Snapshot.prior_active) {
            Write-JsonFile ([pscustomobject][ordered]@{
                schema_version = 1
                target = [string]$Snapshot.prior_active.target
                release_version = [string]$Snapshot.prior_active.release_version
                paths = @($Snapshot.prior_active.local_exceptions)
                reconfirmation = 'every-sync'
            }) $Paths.local_exceptions
        } elseif (Test-Path -LiteralPath $Paths.local_exceptions -PathType Leaf) {
            Remove-Item -LiteralPath $Paths.local_exceptions -Force
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
                $IsLocalException = $false
                foreach ($ExceptionPathValue in @($State.local_exceptions)) {
                    $ExceptionPath = [string]$ExceptionPathValue
                    if ($Relative.Equals(
                            $ExceptionPath,
                            [StringComparison]::OrdinalIgnoreCase
                        ) -or $Relative.StartsWith(
                            $ExceptionPath + '/',
                            [StringComparison]::OrdinalIgnoreCase
                        )) {
                        $IsLocalException = $true
                        break
                    }
                }
                if ($IsLocalException) { continue }
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

function Get-BundledOfficeCliContract {
    $LockPath = Join-Path $PSScriptRoot 'shared-tools.lock.json'
    if (-not (Test-Path -LiteralPath $LockPath -PathType Leaf)) { return $null }
    try {
        $Lock = Get-Content -LiteralPath $LockPath -Raw -Encoding UTF8 |
            ConvertFrom-Json -ErrorAction Stop
    } catch {
        Throw-Foundation 'INVALID_PACKAGE' 'Shared tools lock is invalid'
    }
    $Tools = @($Lock.tools)
    if ([int]$Lock.schema_version -ne 1 -or $Tools.Count -ne 1 -or
        [string]$Tools[0].id -cne 'officecli' -or
        [string]$Tools[0].version -notmatch '^\d+\.\d+\.\d+$') {
        Throw-Foundation 'INVALID_PACKAGE' 'OfficeCLI bundle contract differs'
    }
    foreach ($Record in @(
        $Tools[0].private_exe,
        $Tools[0].shim,
        $Tools[0].policy,
        $(if (Test-ObjectProperty $Tools[0] 'pdf_exporter') {
            $Tools[0].pdf_exporter
        }),
        $(if (Test-ObjectProperty $Tools[0] 'csv_batch_adapter') {
            $Tools[0].csv_batch_adapter
        })
    )) {
        if ([string]$Record.path -notmatch '^shared-tools/officecli/[A-Za-z0-9._-]+$' -or
            [string]$Record.sha256 -notmatch '^[0-9a-f]{64}$' -or
            [int64]$Record.bytes -le 0) {
            Throw-Foundation 'INVALID_PACKAGE' 'OfficeCLI bundle record differs'
        }
        $Source = Join-Path $PSScriptRoot (
            ([string]$Record.path).Replace('/', '\')
        )
        if (-not (Test-Path -LiteralPath $Source -PathType Leaf) -or
            (Get-FileSha256 $Source) -cne [string]$Record.sha256 -or
            (Get-Item -LiteralPath $Source).Length -ne [int64]$Record.bytes) {
            Throw-Foundation 'INVALID_PACKAGE' 'OfficeCLI bundle bytes differ'
        }
    }
    return $Tools[0]
}

function Add-FoundationUserPath {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$HomeRoot
    )
    Assert-SafeAncestors $Directory $HomeRoot
    $UserPath = [Environment]::GetEnvironmentVariable(
        'Path', [EnvironmentVariableTarget]::User
    )
    $Parts = @(
        ([string]$UserPath).Split(';') |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if (-not @($Parts | Where-Object {
        ([IO.Path]::GetFullPath($_)).Equals(
            [IO.Path]::GetFullPath($Directory),
            [StringComparison]::OrdinalIgnoreCase
        )
    })) {
        $NewPath = (@($Parts) + $Directory) -join ';'
        [Environment]::SetEnvironmentVariable(
            'Path', $NewPath, [EnvironmentVariableTarget]::User
        )
    }
    $ProcessParts = @(
        ([string]$env:Path).Split(';') |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if (-not @($ProcessParts | Where-Object {
        ([IO.Path]::GetFullPath($_)).Equals(
            [IO.Path]::GetFullPath($Directory),
            [StringComparison]::OrdinalIgnoreCase
        )
    })) {
        $env:Path = (@($ProcessParts) + $Directory) -join ';'
    }
}

function Install-BundledOfficeCli {
    param([Parameter(Mandatory = $true)][string]$HomeRoot)
    $Tool = Get-BundledOfficeCliContract
    if ($null -eq $Tool) { return }
    $PrivateDestination = Resolve-HomePath (
        '.llm-foundation/libexec/officecli/officecli.exe'
    ) $HomeRoot -AllowSharedToolPath
    $ShimDestination = Resolve-HomePath (
        '.llm-foundation/bin/officecli.exe'
    ) $HomeRoot -AllowSharedToolPath
    $PolicyDestination = Resolve-HomePath (
        '.llm-foundation/libexec/officecli/officecli-command-policy.json'
    ) $HomeRoot -AllowSharedToolPath
    $ExporterDestination = Resolve-HomePath (
        '.llm-foundation/libexec/officecli/plugins/exporter/pdf/plugin.exe'
    ) $HomeRoot -AllowSharedToolPath
    $CsvAdapterDestination = Resolve-HomePath (
        '.llm-foundation/libexec/officecli/officecli_csv_batch.py'
    ) $HomeRoot -AllowSharedToolPath
    $Pairs = @(
        @($Tool.private_exe, $PrivateDestination),
        @($Tool.shim, $ShimDestination),
        @($Tool.policy, $PolicyDestination)
    )
    if (Test-ObjectProperty $Tool 'pdf_exporter') {
        $Pairs += ,@($Tool.pdf_exporter, $ExporterDestination)
    }
    if (Test-ObjectProperty $Tool 'csv_batch_adapter') {
        $Pairs += ,@($Tool.csv_batch_adapter, $CsvAdapterDestination)
    }
    foreach ($Pair in $Pairs) {
        $Source = Join-Path $PSScriptRoot (
            ([string]$Pair[0].path).Replace('/', '\')
        )
        Copy-Atomic $Source ([string]$Pair[1]) $HomeRoot
    }
    foreach ($Property in @($Tool.environment.psobject.Properties)) {
        [Environment]::SetEnvironmentVariable(
            [string]$Property.Name,
            [string]$Property.Value,
            [EnvironmentVariableTarget]::User
        )
        [Environment]::SetEnvironmentVariable(
            [string]$Property.Name,
            [string]$Property.Value,
            [EnvironmentVariableTarget]::Process
        )
    }
    Add-FoundationUserPath (
        Resolve-HomePath '.llm-foundation/bin' $HomeRoot -AllowSharedToolPath
    ) $HomeRoot
    $ReceiptPath = Resolve-HomePath (
        '.llm-foundation/state/shared-tools/officecli/current.json'
    ) $HomeRoot -AllowSharedToolPath
    New-SafeDirectory (Split-Path -Parent $ReceiptPath) $HomeRoot
    $Receipt = [pscustomobject][ordered]@{
        schema_version = 1
        id = 'officecli'
        version = [string]$Tool.version
        compatibility_epoch = [string]$Tool.compatibility_epoch
        files = @(
            [pscustomobject][ordered]@{
                path = '.llm-foundation/libexec/officecli/officecli.exe'
                sha256 = [string]$Tool.private_exe.sha256
                bytes = [int64]$Tool.private_exe.bytes
            },
            [pscustomobject][ordered]@{
                path = '.llm-foundation/bin/officecli.exe'
                sha256 = [string]$Tool.shim.sha256
                bytes = [int64]$Tool.shim.bytes
            },
            [pscustomobject][ordered]@{
                path = '.llm-foundation/libexec/officecli/officecli-command-policy.json'
                sha256 = [string]$Tool.policy.sha256
                bytes = [int64]$Tool.policy.bytes
            }
            if (Test-ObjectProperty $Tool 'pdf_exporter') {
                [pscustomobject][ordered]@{
                    path = '.llm-foundation/libexec/officecli/plugins/exporter/pdf/plugin.exe'
                    sha256 = [string]$Tool.pdf_exporter.sha256
                    bytes = [int64]$Tool.pdf_exporter.bytes
                }
            }
            if (Test-ObjectProperty $Tool 'csv_batch_adapter') {
                [pscustomobject][ordered]@{
                    path = '.llm-foundation/libexec/officecli/officecli_csv_batch.py'
                    sha256 = [string]$Tool.csv_batch_adapter.sha256
                    bytes = [int64]$Tool.csv_batch_adapter.bytes
                }
            }
        )
    }
    Write-JsonFile $Receipt $ReceiptPath
}

function Test-BundledOfficeCliState {
    param([Parameter(Mandatory = $true)][string]$HomeRoot)
    $Tool = Get-BundledOfficeCliContract
    if ($null -eq $Tool) { return }
    $ReceiptPath = Resolve-HomePath (
        '.llm-foundation/state/shared-tools/officecli/current.json'
    ) $HomeRoot -AllowSharedToolPath
    if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) {
        Throw-Foundation 'ACTIVE_DRIFT' 'OfficeCLI receipt is missing'
    }
    $Receipt = Read-JsonFile $ReceiptPath
    if ([int]$Receipt.schema_version -ne 1 -or
        [string]$Receipt.id -cne 'officecli' -or
        [string]$Receipt.version -cne [string]$Tool.version) {
        Throw-Foundation 'ACTIVE_DRIFT' 'OfficeCLI receipt differs'
    }
    foreach ($Row in @($Receipt.files)) {
        $Destination = Resolve-HomePath ([string]$Row.path) $HomeRoot `
            -AllowSharedToolPath
        if (-not (Test-Path -LiteralPath $Destination -PathType Leaf) -or
            (Get-FileSha256 $Destination) -cne [string]$Row.sha256 -or
            (Get-Item -LiteralPath $Destination).Length -ne [int64]$Row.bytes) {
            Throw-Foundation 'ACTIVE_DRIFT' 'OfficeCLI managed bytes differ'
        }
    }
}

function Invoke-Install {
    param(
        [Parameter(Mandatory = $true)]$Validated,
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$ActualClientId,
        [Parameter(Mandatory = $true)][string]$ActualClientVersion,
        [AllowEmptyCollection()][object[]]$RequestedLocalExceptions = @(),
        [switch]$RemoveUnknownConfirmed
    )
    $Plan = New-FoundationPlan $Validated $HomeRoot $ActualClientId `
        $ActualClientVersion $RequestedLocalExceptions `
        -RemoveUnknownConfirmed:$RemoveUnknownConfirmed
    if ([string]$Plan.status -ceq 'BLOCKED_USER_DECISION') {
        Throw-Foundation 'BLOCKED_USER_DECISION' (
            'Unknown managed entries require an explicit decision'
        )
    }
    $BaselinePlan = Get-SessionToolsBaselinePlan $Validated $HomeRoot
    $Paths = Get-FoundationPaths $HomeRoot ([string]$Validated.manifest.target)
    $ActiveBeforeInstall = Read-ActiveState $Paths -AllowMissing
    $RetiredPlan = @(
        Get-RetiredManagedPlan $Validated $HomeRoot $ActiveBeforeInstall
    )
    $Staging = $null
    $Snapshot = $null
    $Pending = $null
    try {
        $Staging = Expand-ValidatedPackage $Validated
        $Snapshot = New-Snapshot `
            $Validated $HomeRoot $Paths $Plan $BaselinePlan $RetiredPlan
        $Pending = [pscustomobject][ordered]@{
            schema_version = 1
            target = [string]$Validated.manifest.target
            snapshot_path = [string]$Snapshot.metadata_path
            snapshot_sha256 = [string]$Snapshot.metadata_sha256
            release_version = [string]$Validated.manifest.version
            managed_surface = $Snapshot.metadata.managed_surface
            environment = $Validated.manifest.environment
        }
        Write-JsonFile $Pending $Paths.pending
        if ($env:FOUNDATION_ACCEPTANCE_MODE -ceq '1' -and
            $env:FOUNDATION_HOLD_AFTER_SNAPSHOT_MS -match '^[0-9]+$') {
            Start-Sleep -Milliseconds (
                [int]$env:FOUNDATION_HOLD_AFTER_SNAPSHOT_MS
            )
        }
        foreach ($Root in @(
            $Validated.manifest.managed_surface.exact_directories
        )) {
            $Destination = Resolve-HomePath ([string]$Root) $HomeRoot
            Remove-TreeSafe $Destination $HomeRoot
            New-SafeDirectory $Destination $HomeRoot
            Invoke-MutationCheckpoint
        }
        foreach ($Row in @($Validated.base_file_rows)) {
            $Source = Join-Path $Staging (
                ([string]$Row.path).Replace('/', '\')
            )
            $Destination = Resolve-HomePath ([string]$Row.path) $HomeRoot
            if (@(Get-MergeTomlFiles (
                    $Validated.manifest.managed_surface
                )) -icontains [string]$Row.path) {
                $script:ActiveDesiredState = if (Test-ObjectProperty (
                    $Validated.manifest
                ) 'desired_state') { $Validated.manifest.desired_state } else { $null }
                $script:ActiveMergeTomlRelativePath = [string]$Row.path
                $script:ActiveLocalTomlExceptions = @(
                    $Plan.local_exceptions | Where-Object {
                        ([string]$_).StartsWith('toml:')
                    }
                )
                try {
                    Merge-TomlFileAtomic $Source $Destination $HomeRoot
                } finally {
                    $script:ActiveDesiredState = $null
                    $script:ActiveMergeTomlRelativePath = $null
                    $script:ActiveLocalTomlExceptions = @()
                }
            } else {
                Copy-Atomic $Source $Destination $HomeRoot
            }
            Invoke-MutationCheckpoint
        }
        foreach ($UnknownValue in @($Plan.remove_unknown)) {
            $Unknown = [string]$UnknownValue
            if ($Unknown.StartsWith('toml:')) { continue }
            $Destination = Resolve-HomePath $Unknown $HomeRoot
            Assert-SafeAncestors $Destination $HomeRoot
            if (Test-Path -LiteralPath $Destination -PathType Container) {
                Remove-TreeSafe $Destination $HomeRoot
            } elseif (Test-Path -LiteralPath $Destination -PathType Leaf) {
                Remove-Item -LiteralPath $Destination -Force
            }
            Invoke-MutationCheckpoint
        }
        Restore-LocalExceptions $Snapshot $HomeRoot @($Plan.local_exceptions)
        foreach ($Action in $RetiredPlan) {
            if ([string]$Action.kind -ceq 'directory') {
                Remove-TreeSafe $Action.destination $HomeRoot
            } elseif (Test-Path -LiteralPath $Action.destination -PathType Leaf) {
                Remove-Item -LiteralPath $Action.destination -Force
            }
            Invoke-MutationCheckpoint
        }
        $BaselineInstalledRow = $null
        if ($null -ne $BaselinePlan) {
            $BaselineInstalledRow = Install-SessionToolsBaseline `
                $Validated `
                $BaselinePlan `
                $Staging `
                $HomeRoot
        }
        Apply-EnvironmentContract $Validated.manifest.environment $HomeRoot
        Install-BundledOfficeCli $HomeRoot
        $Installed = @(
            foreach ($Row in @($Validated.base_file_rows)) {
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
            if ($null -ne $BaselineInstalledRow) {
                $BaselineInstalledRow
            }
        )
        $State = [pscustomobject][ordered]@{
            schema_version = 1
            target = [string]$Validated.manifest.target
            release_version = [string]$Validated.manifest.version
            client = $Validated.manifest.client
            foundation_engine_version = [string]$Validated.manifest.foundation_engine_version
            package_sha256 = [string]$Validated.package_sha256
            managed_surface = $Validated.manifest.managed_surface
            environment = $Validated.manifest.environment
            installed_files = $Installed
            quarantined_unknown = @($Plan.quarantined_unknown)
            local_exceptions = @($Plan.local_exceptions)
            desired_state = if (Test-ObjectProperty (
                $Validated.manifest
            ) 'desired_state') {
                $Validated.manifest.desired_state
            } else {
                $false
            }
            snapshot_path = [string]$Snapshot.metadata_path
            snapshot_sha256 = [string]$Snapshot.metadata_sha256
        }
        $null = Test-InstalledState $State $HomeRoot $ActualClientId `
            $ActualClientVersion
        Write-JsonFile $State $Paths.active
        Write-JsonFile ([pscustomobject][ordered]@{
            schema_version = 1
            target = [string]$State.target
            release_version = [string]$State.release_version
            paths = @($State.local_exceptions)
            reconfirmation = 'every-sync'
        }) $Paths.local_exceptions
        Remove-Item -LiteralPath $Paths.pending -Force
        return [pscustomobject][ordered]@{
            status = if (@($State.local_exceptions).Count -gt 0) {
                'CANONICAL_WITH_LOCAL_EXCEPTIONS'
            } else {
                'CANONICAL'
            }
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
    $Health = Test-InstalledState $State $HomeRoot $ActualClientId `
        $ActualClientVersion
    $SessionPaths = Get-SessionToolsPaths `
        $HomeRoot $TargetName $State.managed_surface
    $SessionStateExists = Test-Path -LiteralPath (
        $SessionPaths.state_path
    ) -PathType Leaf
    $SessionStateRequired = @($State.installed_files).path -ccontains (
        [string]$SessionPaths.runtime_relative
    )
    if ($SessionStateRequired -and -not $SessionStateExists) {
        Throw-Foundation 'ACTIVE_DRIFT' 'Session tools state is missing'
    }
    if ($SessionStateExists) {
        $SessionState = Read-JsonFile $SessionPaths.state_path
        $null = Assert-SessionToolsState `
            $SessionState $HomeRoot $TargetName $SessionPaths -CheckDestination
    }
    Test-BundledOfficeCliState $HomeRoot
    if ($State.desired_state -is [Management.Automation.PSCustomObject]) {
        $CurrentManifest = [pscustomobject]@{
            managed_surface = $State.managed_surface
            files = $State.installed_files
            desired_state = $State.desired_state
        }
        $CurrentUnknown = @(Sort-OrdinalStrings @(
            @(Get-UnknownEntries $CurrentManifest $HomeRoot) +
            @(Get-TomlUnknownEntries $CurrentManifest $HomeRoot)
        ))
        $Declared = @(Sort-OrdinalStrings @($State.local_exceptions))
        $Actual = @(Sort-OrdinalStrings $CurrentUnknown)
        if (@(Compare-Object -ReferenceObject $Declared -DifferenceObject $Actual).Count -ne 0) {
            Throw-Foundation 'ACTIVE_DRIFT' 'Local exception inventory differs'
        }
        $Health.status = if ($Declared.Count -gt 0) {
            'CANONICAL_WITH_LOCAL_EXCEPTIONS'
        } else {
            'CANONICAL'
        }
    }
    return $Health
}

function Invoke-Inventory {
    param(
        [Parameter(Mandatory = $true)][string]$HomeRoot,
        [Parameter(Mandatory = $true)][string]$TargetName
    )
    $Paths = Get-FoundationPaths $HomeRoot $TargetName
    Assert-NoRecoveryPending $Paths
    $State = Read-ActiveState $Paths -AllowMissing
    if ($null -eq $State) {
        $InventoryRoots = switch ($TargetName) {
            'codex' { @('.agents/skills', '.codex/agents', '.codex/plugins') }
            'claude' { @('.claude/skills', '.claude/agents', '.claude/plugins') }
            'opencode' { @('.config/opencode/skills', '.config/opencode/agents', '.config/opencode/plugins') }
            default { @() }
        }
        $Synthetic = [pscustomobject]@{
            managed_surface = [pscustomobject]@{ exact_directories = @() }
            files = @()
            desired_state = [pscustomobject]@{
                inventory_roots = $InventoryRoots
                platform_owned = @()
                toml_reconcile = @()
            }
        }
        $Unknown = @(Get-UnknownEntries $Synthetic $HomeRoot)
        return [pscustomobject][ordered]@{
            status = 'UNMANAGED_PROFILE'
            target = $TargetName
            unknown_entries = @(Get-UnknownEntryDetails $Unknown $HomeRoot)
            local_exceptions = @()
            note = 'Run plan with the signed package to classify registrations and reconcile desired state.'
        }
    }
    return [pscustomobject][ordered]@{
        status = 'INSTALLED'
        target = [string]$State.target
        release_version = [string]$State.release_version
        client = $State.client
        installed_file_count = @($State.installed_files).Count
        managed_surface = $State.managed_surface
        environment = $State.environment
        quarantined_unknown = @($State.quarantined_unknown)
        local_exceptions = @($State.local_exceptions)
        desired_state = $State.desired_state
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
    if ($Command -ceq 'apply') {
        if ([string]::IsNullOrWhiteSpace($PlanFile) -or
            -not (Test-Path -LiteralPath $PlanFile -PathType Leaf)) {
            Throw-Foundation 'INVALID_ARGUMENT' 'Apply requires -Plan <file>'
        }
        $SavedPlan = Read-JsonFile ([IO.Path]::GetFullPath($PlanFile))
        if ([string]$SavedPlan.status -cne 'READY' -or
            [string]$SavedPlan.package_sha256 -notmatch '^[0-9a-f]{64}$' -or
            [string]$SavedPlan.target_home -cne $TargetHome -or
            [string]::IsNullOrWhiteSpace([string]$SavedPlan.package_path) -or
            [string]::IsNullOrWhiteSpace([string]$SavedPlan.target) -or
            [string]::IsNullOrWhiteSpace([string]$SavedPlan.client.id) -or
            [string]::IsNullOrWhiteSpace(
                [string]$SavedPlan.client.supported_version
            )) {
            Throw-Foundation 'INVALID_ARGUMENT' 'Saved plan is invalid'
        }
        $Package = [string]$SavedPlan.package_path
        if (-not (Test-Path -LiteralPath $Package -PathType Leaf) -or
            (Get-FileSha256 $Package) -cne [string]$SavedPlan.package_sha256) {
            Throw-Foundation 'INVALID_PACKAGE' 'Saved-plan package differs'
        }
        $Target = [string]$SavedPlan.target
        $ClientId = [string]$SavedPlan.client.id
        $ClientVersion = [string]$SavedPlan.client.supported_version
        $script:RequestedLocalExceptionPaths = @(
            $SavedPlan.local_exceptions
        )
        $ConfirmRemoveUnknown = @($SavedPlan.remove_unknown).Count -gt 0
    }
    $HasReleaseManifest = -not [string]::IsNullOrWhiteSpace($ReleaseManifest)
    $HasReleaseManifestSha256 = -not [string]::IsNullOrWhiteSpace(
        $ReleaseManifestSha256
    )
    if ($HasReleaseManifest -ne $HasReleaseManifestSha256 -or
        ($HasReleaseManifestSha256 -and
            $ReleaseManifestSha256 -cnotmatch '^[0-9a-f]{64}$')) {
        Throw-Foundation 'INVALID_PACKAGE' (
            'Release manifest arguments must be an exact pair'
        )
    }
    if ($Command -in @('plan', 'install') -and
        [string]::IsNullOrWhiteSpace($Package)) {
        Throw-Foundation 'INVALID_ARGUMENT' 'Package is required'
    }
    if ($Command -in @('inventory', 'rollback') -and
        [string]::IsNullOrWhiteSpace($Target)) {
        Throw-Foundation 'INVALID_ARGUMENT' 'Target is required'
    }
    if (-not [string]::IsNullOrWhiteSpace($Package)) {
        $Validated = Open-ValidatedPackage `
            $Package `
            $ReleaseManifest `
            $ReleaseManifestSha256
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
    if ($Command -in @('plan', 'apply', 'install', 'doctor') -and (
        [string]::IsNullOrWhiteSpace($ClientId) -or
        [string]::IsNullOrWhiteSpace($ClientVersion)
    )) {
        Throw-Foundation 'UNSUPPORTED_CLIENT' (
            'ClientId and ClientVersion are required'
        )
    }
    if ($Command -in @('apply', 'install', 'rollback')) {
        $OperationPaths = Get-FoundationPaths $TargetHome $Target
        $OperationLock = Enter-TargetLock $OperationPaths $TargetHome
    }
    $Result = switch ($Command) {
        'plan' {
            $PlanResult = New-FoundationPlan $Validated $TargetHome $ClientId `
                $ClientVersion $script:RequestedLocalExceptionPaths `
                -RemoveUnknownConfirmed:$ConfirmRemoveUnknown
            if ($Interactive -and [string]$PlanResult.status -ceq
                'BLOCKED_USER_DECISION') {
                $Keep = @()
                foreach ($Unknown in @($PlanResult.unknown_entries)) {
                    $Answer = Read-Host (
                        "Unknown $($Unknown.kind) $($Unknown.path): REMOVE or KEEP"
                    )
                    if ([string]$Answer -cmatch '^(?i:keep)$') {
                        $Keep += [string]$Unknown.path
                    } elseif ([string]$Answer -cnotmatch '^(?i:remove)$') {
                        Throw-Foundation 'BLOCKED_USER_DECISION' (
                            "Decision is required for $($Unknown.path)"
                        )
                    }
                }
                $PlanResult = New-FoundationPlan $Validated $TargetHome `
                    $ClientId $ClientVersion $Keep `
                    -RemoveUnknownConfirmed
            }
            $PlanResult
            break
        }
        'install' {
            Invoke-Install $Validated $TargetHome $ClientId $ClientVersion `
                $script:RequestedLocalExceptionPaths `
                -RemoveUnknownConfirmed:$ConfirmRemoveUnknown
            break
        }
        'apply' {
            Invoke-Install $Validated $TargetHome $ClientId $ClientVersion `
                $script:RequestedLocalExceptionPaths `
                -RemoveUnknownConfirmed:$ConfirmRemoveUnknown
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
    if ([string]$Result.status -ceq 'BLOCKED_USER_DECISION') { exit 20 }
    exit 0
} catch {
    $Code = [string]$_.Exception.Data['FoundationCode']
    if ([string]::IsNullOrWhiteSpace($Code)) {
        $Code = 'INVALID_PACKAGE'
    }
    $Exit = $script:ExitCode[$Code]
    if ($null -eq $Exit) { $Exit = 30 }
    Write-Result ([pscustomobject][ordered]@{
        status = if ($Command -ceq 'doctor') {
            'FAILED_DOCTOR'
        } elseif ($Code -ceq 'BLOCKED_USER_DECISION') {
            'BLOCKED_USER_DECISION'
        } else {
            'BLOCKED'
        }
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
