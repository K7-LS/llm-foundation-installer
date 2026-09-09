# Typed TOML contract adapter. The parser is bundled; no process or network calls.
$script:FoundationTomlAssemblySha256 = 'a7d2ea40533a5a912bc6a64ab8f1347e60fe92ea0ab456d423b64db2214c904b'
$script:FoundationTomlAdapterRoot = $PSScriptRoot
$script:FoundationTomlRuntimeRoots = @(
    'model', 'model_reasoning_effort', 'model_provider',
    'model_providers', 'providers', 'model_verbosity'
)

function Get-FoundationTomlDigest {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
    $Sha = [Security.Cryptography.SHA256]::Create()
    try {
        $Bytes = [Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($Sha.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    } finally { $Sha.Dispose() }
}

function Get-FoundationTomlFileDigest {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Sha = [Security.Cryptography.SHA256]::Create()
    $Stream = $null
    try {
        $Stream = [IO.File]::OpenRead($Path)
        return ([BitConverter]::ToString($Sha.ComputeHash($Stream))).Replace('-', '').ToLowerInvariant()
    } finally {
        if ($null -ne $Stream) { $Stream.Dispose() }
        $Sha.Dispose()
    }
}

function Initialize-FoundationTomlParser {
    $Path = [IO.Path]::GetFullPath((Join-Path $script:FoundationTomlAdapterRoot 'vendor/tomlyn/Tomlyn.dll'))
    if (-not [IO.File]::Exists($Path) -or
        (Get-FoundationTomlFileDigest $Path) -cne $script:FoundationTomlAssemblySha256) {
        Throw-Foundation 'INVALID_PACKAGE' 'Bundled TOML parser integrity check failed'
    }
    $Loaded = @([AppDomain]::CurrentDomain.GetAssemblies() | Where-Object { $_.GetName().Name -ceq 'Tomlyn' })
    if ($Loaded.Count -gt 1) { Throw-Foundation 'INVALID_PACKAGE' 'Conflicting TOML parser assemblies' }
    if ($Loaded.Count -eq 0) {
        try { $Assembly = [Reflection.Assembly]::LoadFrom($Path) }
        catch { Throw-Foundation 'INVALID_PACKAGE' 'Bundled TOML parser could not be loaded' }
    } else { $Assembly = $Loaded[0] }
    # LoadFrom can return an assembly already loaded from another directory.
    if ([string]::IsNullOrEmpty($Assembly.Location) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals([IO.Path]::GetFullPath($Assembly.Location), $Path) -or
        (Get-FoundationTomlFileDigest $Assembly.Location) -cne $script:FoundationTomlAssemblySha256 -or
        $Assembly.GetName().Version.ToString() -cne '0.19.0.0') {
        Throw-Foundation 'INVALID_PACKAGE' 'Loaded TOML parser identity check failed'
    }
}

function Read-FoundationTomlModel {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text,
        [string]$ErrorCode = 'INVALID_PACKAGE'
    )
    if ([Text.Encoding]::UTF8.GetByteCount($Text) -gt 1048576) {
        Throw-Foundation $ErrorCode 'TOML document exceeds the supported size limit'
    }
    Initialize-FoundationTomlParser
    try { $Syntax = [Tomlyn.Toml]::Parse($Text, 'configuration', [Tomlyn.TomlParserOptions]::ParseAndValidate) }
    catch {
        # Parser diagnostics can contain configuration values or source excerpts.
        Throw-Foundation $ErrorCode 'Invalid TOML document (syntax or duplicate key)'
    }
    if ($Syntax.HasErrors) { Throw-Foundation $ErrorCode 'Invalid TOML document (syntax or duplicate key)' }
    Assert-FoundationTomlSyntax $Syntax $ErrorCode
    try { $Model = [Tomlyn.Toml]::ToModel($Syntax) }
    catch { Throw-Foundation $ErrorCode 'Invalid TOML document (model conversion)' }
    return ,$Model
}

function Assert-FoundationTomlSyntax {
    param($Syntax, [string]$ErrorCode)
    $Pending = New-Object 'System.Collections.Generic.Stack[object]'
    $Pending.Push($Syntax)
    $Count = 0
    while ($Pending.Count -gt 0) {
        $Node = $Pending.Pop()
        $Count++
        if ($Count -gt 100000) { Throw-Foundation $ErrorCode 'TOML document exceeds the supported structural limits' }
        if ($Node.Kind.ToString() -ceq 'Integer') {
            # Tomlyn 0.19 can wrap integer overflow while constructing its model.
            # Validate the already parsed integer token against the signed 64-bit
            # range before using that model. TOML syntax is handled by Tomlyn.
            $Digits = $Node.Token.Text.Replace('_', '')
            $Limit = '9223372036854775807'
            if ($Digits.StartsWith('-')) { $Limit = '9223372036854775808'; $Digits = $Digits.Substring(1) }
            elseif ($Digits.StartsWith('+')) { $Digits = $Digits.Substring(1) }
            elseif ($Digits.StartsWith('0x')) { $Limit = '7fffffffffffffff'; $Digits = $Digits.Substring(2).ToLowerInvariant() }
            elseif ($Digits.StartsWith('0o')) { $Limit = '777777777777777777777'; $Digits = $Digits.Substring(2) }
            elseif ($Digits.StartsWith('0b')) { $Limit = '111111111111111111111111111111111111111111111111111111111111111'; $Digits = $Digits.Substring(2) }
            $Digits = $Digits.TrimStart([char]'0')
            if ($Digits.Length -gt $Limit.Length -or
                ($Digits.Length -eq $Limit.Length -and [StringComparer]::Ordinal.Compare($Digits, $Limit) -gt 0)) {
                Throw-Foundation $ErrorCode 'TOML integer is outside the supported signed 64-bit range'
            }
        }
        for ($Index = 0; $Index -lt $Node.ChildrenCount; $Index++) {
            $Child = $Node.GetChild($Index)
            if ($null -ne $Child) { $Pending.Push($Child) }
        }
    }
}

function Get-FoundationTomlKind {
    param([Parameter(Mandatory = $true)]$Value)
    switch ($Value.GetType().FullName) {
        'System.String' { return 'string' }
        'System.Int64' { return 'integer' }
        'System.Double' { return 'float' }
        'System.Boolean' { return 'boolean' }
        'Tomlyn.Model.TomlArray' { return 'array' }
        'Tomlyn.Model.TomlTableArray' { return 'array' }
        'Tomlyn.Model.TomlTable' { return 'table' }
        'Tomlyn.TomlDateTime' {
            switch ($Value.Kind.ToString()) {
                'OffsetDateTimeByZ' { return 'offset-date-time' }
                'OffsetDateTimeByNumber' { return 'offset-date-time' }
                'LocalDateTime' { return 'local-date-time' }
                'LocalDate' { return 'local-date' }
                'LocalTime' { return 'local-time' }
            }
        }
    }
    Throw-Foundation 'INVALID_PACKAGE' 'Unsupported TOML model value type'
}

function Get-FoundationTomlSortedKeys {
    param([Parameter(Mandatory = $true)]$Table)
    [string[]]$Keys = @($Table.Keys)
    [Array]::Sort($Keys, [StringComparer]::Ordinal)
    return ,$Keys
}

function ConvertTo-FoundationTomlCanonical {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][ref]$NodeCount,
        [int]$Depth = 0,
        [string]$ErrorCode = 'INVALID_PACKAGE'
    )
    $NodeCount.Value++
    if ($Depth -gt 64 -or $NodeCount.Value -gt 20000) {
        Throw-Foundation $ErrorCode 'TOML document exceeds the supported structural limits'
    }
    $Kind = Get-FoundationTomlKind $Value
    $Invariant = [Globalization.CultureInfo]::InvariantCulture
    switch ($Kind) {
        'string' { return 's:' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Value)) + ';' }
        'integer' { return 'i:' + $Value.ToString($Invariant) + ';' }
        'float' {
            if ([double]::IsNaN($Value)) { return 'f:nan;' }
            if ($Value -eq 0.0) { return 'f:0000000000000000;' }
            return 'f:' + [BitConverter]::DoubleToInt64Bits($Value).ToString('x16', $Invariant) + ';'
        }
        'boolean' { if ($Value) { return 'b:1;' }; return 'b:0;' }
        'offset-date-time' { return 'z:' + $Value.DateTime.UtcDateTime.Ticks.ToString($Invariant) + ';' }
        'local-date-time' { return 'd:' + $Value.DateTime.DateTime.Ticks.ToString($Invariant) + ';' }
        'local-date' { return 'D:' + $Value.DateTime.ToString('yyyy-MM-dd', $Invariant) + ';' }
        'local-time' { return 't:' + $Value.DateTime.TimeOfDay.Ticks.ToString($Invariant) + ';' }
        'array' {
            $Builder = New-Object Text.StringBuilder
            [void]$Builder.Append('a:[')
            foreach ($Child in $Value) {
                [void]$Builder.Append((ConvertTo-FoundationTomlCanonical $Child $NodeCount ($Depth + 1) $ErrorCode))
            }
            [void]$Builder.Append('];')
            return $Builder.ToString()
        }
        'table' {
            $Builder = New-Object Text.StringBuilder
            [void]$Builder.Append('m:{')
            foreach ($Key in (Get-FoundationTomlSortedKeys $Value)) {
                [void]$Builder.Append('k:' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Key)) + ';')
                [void]$Builder.Append((ConvertTo-FoundationTomlCanonical $Value[$Key] $NodeCount ($Depth + 1) $ErrorCode))
            }
            [void]$Builder.Append('};')
            return $Builder.ToString()
        }
    }
}

function ConvertFrom-FoundationTomlSection {
    param([Parameter(Mandatory = $true)][string]$Section)
    # Use the same TOML parser for quoted/dotted identities. The resulting tree
    # must contain exactly the single empty table specified by the header.
    if ($Section.Length -gt 4096 -or $Section.Contains("`n") -or $Section.Contains("`r")) {
        Throw-Foundation 'INVALID_PACKAGE' 'Invalid protected TOML section identity'
    }
    $Header = '[' + $Section + "]`n"
    $Node = Read-FoundationTomlModel $Header
    $Syntax = [Tomlyn.Toml]::Parse($Header, 'section', [Tomlyn.TomlParserOptions]::ParseAndValidate)
    $Tables = @($Syntax.Tables)
    if ($Syntax.HasErrors -or $Tables.Count -ne 1 -or
        $Tables[0].Kind.ToString() -cne 'Table' -or
        $Tables[0].CloseBracket.Span.Start.Offset -ne ($Section.Length + 1)) {
        Throw-Foundation 'INVALID_PACKAGE' 'Invalid protected TOML section identity'
    }
    $Segments = New-Object 'System.Collections.Generic.List[string]'
    while ($Node.Count -ne 0) {
        if ($Node.Count -ne 1 -or $Segments.Count -ge 64) {
            Throw-Foundation 'INVALID_PACKAGE' 'Invalid protected TOML section identity'
        }
        $Keys = Get-FoundationTomlSortedKeys $Node
        $Key = $Keys[0]
        [void]$Segments.Add($Key)
        $Node = $Node[$Key]
        if ((Get-FoundationTomlKind $Node) -cne 'table') {
            Throw-Foundation 'INVALID_PACKAGE' 'Invalid protected TOML section identity'
        }
    }
    if ($Segments.Count -eq 0) { Throw-Foundation 'INVALID_PACKAGE' 'Invalid protected TOML section identity' }
    return ,$Segments.ToArray()
}

function Test-FoundationTomlExcludedPath {
    param([string[]]$Path, [object[]]$Prefixes)
    if ($Path.Count -gt 0) {
        foreach ($Root in $script:FoundationTomlRuntimeRoots) {
            if ([StringComparer]::Ordinal.Equals($Root, $Path[0])) { return $true }
        }
    }
    foreach ($Prefix in $Prefixes) {
        if ($Path.Count -lt $Prefix.Count) { continue }
        $Matches = $true
        for ($Index = 0; $Index -lt $Prefix.Count; $Index++) {
            if (-not [StringComparer]::Ordinal.Equals($Path[$Index], $Prefix[$Index])) { $Matches = $false; break }
        }
        if ($Matches) { return $true }
    }
    return $false
}

function Add-FoundationTomlLeaves {
    param($Table, [string[]]$Path, [object[]]$Prefixes, $Entries)
    foreach ($Key in (Get-FoundationTomlSortedKeys $Table)) {
        [string[]]$Next = @($Path) + @($Key)
        if (Test-FoundationTomlExcludedPath $Next $Prefixes) { continue }
        $Value = $Table[$Key]
        $Kind = Get-FoundationTomlKind $Value
        if ($Kind -ceq 'table' -and $Value.Count -ne 0) {
            Add-FoundationTomlLeaves $Value $Next $Prefixes $Entries
            continue
        }
        if ($Entries.Count -ge 10000) { Throw-Foundation 'INVALID_PACKAGE' 'Too many managed TOML requirements' }
        $Nodes = 0
        $Canonical = ConvertTo-FoundationTomlCanonical $Value ([ref]$Nodes)
        [void]$Entries.Add([pscustomobject]@{
            path_segments = $Next
            kind = $Kind
            value_sha256 = Get-FoundationTomlDigest $Canonical
        })
    }
}

function Get-FoundationTomlRequirements {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text,
        [string[]]$ProtectedSections = @()
    )
    $Model = Read-FoundationTomlModel $Text
    # Validate structural limits on the whole document, including user-owned
    # sections, before removing them from the managed requirements.
    $Nodes = 0
    $null = ConvertTo-FoundationTomlCanonical $Model ([ref]$Nodes)
    $Prefixes = New-Object 'System.Collections.Generic.List[object]'
    foreach ($Section in $ProtectedSections) {
        [void]$Prefixes.Add((ConvertFrom-FoundationTomlSection $Section))
    }
    $Entries = New-Object 'System.Collections.Generic.List[object]'
    Add-FoundationTomlLeaves $Model @() $Prefixes.ToArray() $Entries
    $Result = $Entries.ToArray()
    $null = Assert-FoundationTomlRequirements -Requirements $Result
    return $Result
}

function Assert-FoundationTomlRequirements {
    param([Parameter(Mandatory = $true)][AllowNull()][AllowEmptyCollection()][object[]]$Requirements)
    if ($null -eq $Requirements -or $Requirements.Count -eq 0 -or $Requirements.Count -gt 10000) {
        Throw-Foundation 'INVALID_PACKAGE' 'Invalid TOML requirements count'
    }
    $Seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    $AllowedKinds = @('string', 'integer', 'float', 'boolean', 'offset-date-time', 'local-date-time', 'local-date', 'local-time', 'array', 'table')
    foreach ($Entry in $Requirements) {
        if ($null -eq $Entry) { Throw-Foundation 'INVALID_PACKAGE' 'Invalid TOML requirement entry' }
        if ($Entry -is [Collections.IDictionary]) { $Fields = @($Entry.Keys) }
        elseif ($Entry -is [pscustomobject]) { $Fields = @($Entry.PSObject.Properties.Name) }
        else { Throw-Foundation 'INVALID_PACKAGE' 'Invalid TOML requirement entry' }
        if ($Fields.Count -ne 3 -or -not ($Fields -ccontains 'path_segments') -or
            -not ($Fields -ccontains 'kind') -or -not ($Fields -ccontains 'value_sha256')) {
            Throw-Foundation 'INVALID_PACKAGE' 'Invalid TOML requirement fields'
        }
        $Path = $Entry.path_segments
        if ($Path -isnot [Collections.IList] -or $Path.Count -eq 0 -or $Path.Count -gt 64) {
            Throw-Foundation 'INVALID_PACKAGE' 'Invalid TOML requirement key path'
        }
        $Tokens = New-Object 'System.Collections.Generic.List[string]'
        foreach ($Segment in $Path) {
            if ($Segment -isnot [string] -or [Text.Encoding]::UTF8.GetByteCount($Segment) -gt 4096) {
                Throw-Foundation 'INVALID_PACKAGE' 'Invalid TOML requirement key segment'
            }
            [void]$Tokens.Add([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Segment)))
        }
        if (-not $Seen.Add([string]::Join('.', $Tokens.ToArray()))) {
            Throw-Foundation 'INVALID_PACKAGE' 'Duplicate TOML requirement key path'
        }
        if ($Entry.kind -isnot [string] -or -not ($AllowedKinds -ccontains $Entry.kind) -or
            $Entry.value_sha256 -isnot [string] -or $Entry.value_sha256 -cnotmatch '\A[0-9a-f]{64}\z') {
            Throw-Foundation 'INVALID_PACKAGE' 'Invalid TOML requirement kind or digest'
        }
        if ($Entry.kind -ceq 'table' -and $Entry.value_sha256 -cne (Get-FoundationTomlDigest 'm:{};')) {
            Throw-Foundation 'INVALID_PACKAGE' 'Invalid empty-table requirement digest'
        }
    }
    return $true
}

function Test-FoundationTomlRequirements {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text,
        [Parameter(Mandatory = $true)][AllowNull()][AllowEmptyCollection()][object[]]$Requirements
    )
    $null = Assert-FoundationTomlRequirements $Requirements
    $Model = Read-FoundationTomlModel $Text 'ACTIVE_DRIFT'
    $Nodes = 0
    $null = ConvertTo-FoundationTomlCanonical $Model ([ref]$Nodes) 0 'ACTIVE_DRIFT'
    foreach ($Entry in $Requirements) {
        $Node = $Model
        $Found = $true
        foreach ($Segment in $Entry.path_segments) {
            if ((Get-FoundationTomlKind $Node) -cne 'table' -or -not $Node.ContainsKey($Segment)) {
                $Found = $false; break
            }
            $Node = $Node[$Segment]
        }
        # Error text contains only the key path, never old/current values.
        $DisplayPath = ConvertTo-Json -InputObject @($Entry.path_segments) -Compress
        if (-not $Found) { Throw-Foundation 'ACTIVE_DRIFT' ('Missing managed TOML key: ' + $DisplayPath) }
        $Kind = Get-FoundationTomlKind $Node
        if ($Kind -cne $Entry.kind) { Throw-Foundation 'ACTIVE_DRIFT' ('Managed TOML key type differs: ' + $DisplayPath) }
        if ($Kind -ceq 'table') { $Canonical = 'm:{};' }
        else {
            $Nodes = 0
            $Canonical = ConvertTo-FoundationTomlCanonical $Node ([ref]$Nodes) 0 'ACTIVE_DRIFT'
        }
        if ((Get-FoundationTomlDigest $Canonical) -cne $Entry.value_sha256) {
            Throw-Foundation 'ACTIVE_DRIFT' ('Managed TOML key value differs: ' + $DisplayPath)
        }
    }
    return $true
}
