"""Exercise the real pinned parser on PS 5.1/7; tomllib is the independent oracle."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "src" / "foundation-toml.ps1"
DLL = ROOT / "src" / "vendor" / "tomlyn" / "Tomlyn.dll"
RUNTIME_ROOTS = {"model", "model_reasoning_effort", "model_provider", "model_providers", "providers", "model_verbosity"}
SHELLS = [
    pytest.param(shutil.which("pwsh"), id="ps7"),
    pytest.param(shutil.which("powershell.exe"), id="ps51"),
]
HARNESS = r"""
param([string]$InputFile, [string]$AdapterFile, [string]$Preload)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
function Throw-Foundation($Code, $Message) {
    $Exception = New-Object InvalidOperationException($Message)
    $Exception.Data['FoundationCode'] = $Code
    throw $Exception
}
if ($Preload) { [void][Reflection.Assembly]::LoadFrom($Preload) }
. $AdapterFile
$Items = Get-Content -LiteralPath $InputFile -Raw -Encoding UTF8 | ConvertFrom-Json
$Results = New-Object 'System.Collections.Generic.List[object]'
foreach ($Item in $Items) {
    try {
        switch ($Item.op) {
            'get' { $Value = @(Get-FoundationTomlRequirements -Text $Item.text -ProtectedSections @($Item.protected)); break }
            'test' { $Value = Test-FoundationTomlRequirements -Text $Item.text -Requirements @($Item.requirements); break }
            'assert' { $Value = Assert-FoundationTomlRequirements -Requirements @($Item.requirements); break }
        }
        [void]$Results.Add([pscustomobject]@{ok=$true;value=$Value})
    } catch {
        [void]$Results.Add([pscustomobject]@{ok=$false;code=$_.Exception.Data['FoundationCode'];message=$_.Exception.Message})
    }
}
ConvertTo-Json -InputObject $Results.ToArray() -Compress -Depth 100
"""


@pytest.fixture(params=SHELLS)
def shell(request):
    if request.param is None:
        pytest.skip("This PowerShell host is not installed")
    return request.param


def invoke(shell, tmp_path, items, *, adapter=ADAPTER, preload=None):
    fixture = tmp_path / "input.json"
    fixture.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    runner = tmp_path / "run.ps1"
    runner.write_text(HARNESS, encoding="utf-8-sig")
    command = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(runner), "-InputFile", str(fixture), "-AdapterFile", str(adapter)]
    if preload:
        command += ["-Preload", str(preload)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env={**os.environ, "POWERSHELL_UPDATECHECK": "Off", "POWERSHELL_TELEMETRY_OPTOUT": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr, result.stderr
    return json.loads(result.stdout)


def b64(value):
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def ordinal_keys(table):
    return sorted(table, key=lambda key: key.encode("utf-16-be"))


def canonical(value):
    if type(value) is str:
        return "string", "s:" + b64(value) + ";"
    if type(value) is bool:
        return "boolean", f"b:{int(value)};"
    if type(value) is int:
        return "integer", f"i:{value};"
    if type(value) is float:
        representation = "nan" if math.isnan(value) else struct.pack(">d", value if value else 0.0).hex()
        return "float", f"f:{representation};"
    if type(value) is dt.datetime:
        if value.tzinfo:
            value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
            kind, prefix = "offset-date-time", "z"
        else:
            kind, prefix = "local-date-time", "d"
        delta = value - dt.datetime(1, 1, 1)
        ticks = (delta.days * 86400 + delta.seconds) * 10_000_000 + delta.microseconds * 10
        return kind, f"{prefix}:{ticks};"
    if type(value) is dt.date:
        return "local-date", "D:" + value.isoformat() + ";"
    if type(value) is dt.time:
        ticks = ((value.hour * 60 + value.minute) * 60 + value.second) * 10_000_000 + value.microsecond * 10
        return "local-time", f"t:{ticks};"
    if type(value) is list:
        return "array", "a:[" + "".join(canonical(child)[1] for child in value) + "];"
    if type(value) is dict:
        return "table", "m:{" + "".join("k:" + b64(key) + ";" + canonical(value[key])[1] for key in ordinal_keys(value)) + "};"
    raise AssertionError(type(value))


def requirements(text, protected=()):
    model = tomllib.loads(text)
    result = []

    def walk(table, path=()):
        for key in ordinal_keys(table):
            current = (*path, key)
            if current[0] in RUNTIME_ROOTS or any(current[: len(prefix)] == prefix for prefix in protected):
                continue
            value = table[key]
            if isinstance(value, dict) and value:
                walk(value, current)
            else:
                kind, representation = canonical(value)
                result.append({"path_segments": list(current), "kind": kind, "value_sha256": hashlib.sha256(representation.encode()).hexdigest()})

    walk(model)
    return result


VALID = [
    'a = 1\nA = 2\n"" = "empty key"\n"x.y"."z\\u002eq" = "quoted"',
    'decimal=1_000\nhex=0x3e8\noctal=0o1750\nbinary=0b1111101000\nmaximum=9223372036854775807\nminimum=-9223372036854775808',
    'true_value=true\nfalse_value=false\nfloat=1.25e2\npositive=+inf\nnegative=-inf\nnan=nan\nsigned_nan=-nan\nnegative_zero=-0.0',
    'basic="a\\nb\\t\\u03b1"\nliteral=\'a\\nb\'\nmultiline="""\nfirst\nsecond"""\nliteral_multiline=\'\'\'\nfirst\nsecond\'\'\'\ncontinuation="""first\\\n   second"""',
    'date=1979-05-27\ntime=07:32:00.123456\nlocal=1979-05-27T07:32:00.12\noffset=1979-05-27T07:32:00.120Z\nequivalent=1979-05-27T08:32:00.12+01:00',
    'array = [1, "1", true, 1.0, [2, 3], {b=2, a=1}]\nempty=[]\n[empty_table]\n[[rows]]\nname="a"\n[rows.child]\nenabled=true\n[[rows]]\nname="b"',
    '"ключ"="значение"\n"\U0001f600"="emoji"\n"\uffff"="bmp"\n["mcp_servers"."server.with.dot"]\ncommand="cli"',
    'model="one"\nmodel_reasoning_effort="ultra"\nmodel_provider="p"\nmodel_verbosity="high"\nmanaged=true\n[model_providers.p]\nurl="PRIVATE"\n[providers.q]\nkey="PRIVATE"',
    '""=""\nempty_array=[]\n[empty_table]\n[container.""]\n""=[]',
]


def test_typed_requirements_match_independent_tomllib_oracle(shell, tmp_path):
    results = invoke(shell, tmp_path, [{"op": "get", "text": text, "protected": []} for text in VALID])
    for text, result in zip(VALID, results, strict=True):
        assert result["ok"], result
        assert result["value"] == requirements(text)


def test_semantically_equal_spelling_and_user_changes_pass(shell, tmp_path):
    pairs = [
        ('a=1000\nb="line\\nsecond"\n[z]\nflag=true', 'b="""line\nsecond"""\na=0x3e8\n["z"]\n"flag" = true # comment'),
        ('a=1.0\nb=-0.0\nc=nan', 'a=1e0\nb=+0.0\nc=+nan'),
        ('a=1979-05-27T07:32:00.120Z', 'a=1979-05-27T08:32:00.12+01:00'),
        ('a=[{z=1,a=2}]', 'a=[{a=2,z=1}]'),
        ('model="one"\nmanaged=true', 'model="two"\nmodel_reasoning_effort="ultra"\nmanaged=true\n[unmanaged]\nnew="value"'),
        ('[empty]', '[empty]\nnew="value"'),
    ]
    results = invoke(shell, tmp_path, [{"op": "test", "text": current, "requirements": requirements(source)} for source, current in pairs])
    assert all(result == {"ok": True, "value": True} for result in results), results


def test_protected_quoted_section_prefix_and_runtime_roots(shell, tmp_path):
    source = 'model="old"\nmanaged=true\n[mcp_servers."a.b"]\nsecret="before"\n[mcp_servers.a.b]\nrequired=true\n[plugins."bundle:name"]\nenabled=true\n[Model]\nrequired=true'
    protected = ['mcp_servers."a.b"', 'plugins."bundle:name"']
    result = invoke(shell, tmp_path, [{"op": "get", "text": source, "protected": protected}])[0]
    assert result["ok"], result
    expected = requirements(source, (("mcp_servers", "a.b"), ("plugins", "bundle:name")))
    assert result["value"] == expected
    changed = source.replace('model="old"', 'model="new"').replace('secret="before"', 'secret="after"').replace('enabled=true', 'enabled=false')
    results = invoke(shell, tmp_path, [{"op": "test", "text": changed, "requirements": expected}, {"op": "test", "text": changed.replace('required=true', 'required=false'), "requirements": expected}])
    assert results[0] == {"ok": True, "value": True}
    assert results[1]["code"] == "ACTIVE_DRIFT"


def test_changes_missing_keys_and_atomic_arrays_fail_without_values(shell, tmp_path):
    source = 'required="SENSITIVE_ORIGINAL"\nflag=true\ncount=1\nitems=[{a=1,b=true}]\n[Case]\nkey=1'
    changes = [
        source.replace("SENSITIVE_ORIGINAL", "SENSITIVE_NEW"),
        source.replace("flag=true", 'flag="true"'),
        source.replace("count=1", "count=1.0"),
        source.replace("a=1,b=true", "a=1,b=false"),
        source.replace("[Case]", "[case]"),
        source.replace("count=1\n", ""),
    ]
    results = invoke(shell, tmp_path, [{"op": "test", "text": text, "requirements": requirements(source)} for text in changes])
    for result in results:
        assert not result["ok"] and result["code"] == "ACTIVE_DRIFT", result
        assert "SENSITIVE" not in result["message"]


INVALID = [
    'a=1\na=2', 'a=1\n"a"=2', 'a.b=1\n[a]\nb=2',
    '[features]\nhooks=true\n["features"]\nhooks=false',
    '"a\\u002eb"=1\n"a.b"=2', 'a=TRUE', 'a=', 'a="unterminated',
    'a=01', 'a=0x_1', 'a=[1,,2]', 'a={x=1,x=2}', 'a=1979-02-31',
    'a="\\q"',
    'a="\\uD800"', 'a="\\U00110000"', 'a="raw\x00control"',
    'managed=true\nmodel="SENSITIVE_UNTERMINATED',
    'managed=true\n[model_providers.p]\nkey="SENSITIVE"\nkey="OTHER"',
]


def test_entire_document_invalid_toml_is_rejected_without_source_leaks(shell, tmp_path):
    for text in INVALID:
        with pytest.raises(tomllib.TOMLDecodeError):
            tomllib.loads(text)
    items = []
    for text in INVALID:
        items.extend([{"op": "get", "text": text, "protected": []}, {"op": "test", "text": text, "requirements": requirements("managed=true")}])
    results = invoke(shell, tmp_path, items)
    for item, result in zip(items, results, strict=True):
        assert not result["ok"], (item, result)
        assert result["code"] == ("INVALID_PACKAGE" if item["op"] == "get" else "ACTIVE_DRIFT"), result
        assert "SENSITIVE" not in result["message"] and "OTHER" not in result["message"]


def test_contract_schema_rejects_ambiguous_or_untyped_entries(shell, tmp_path):
    good = requirements("a=1")[0]
    invalid = [[], [None], ["a"], [{**good, "extra": True}], [{**good, "path_segments": "a"}], [{**good, "path_segments": []}], [{**good, "path_segments": [1]}], [{**good, "kind": "INTEGER"}], [{**good, "kind": "null"}], [{**good, "value_sha256": "x" * 64}], [{**good, "value_sha256": "A" * 64}], [good, good], [{**good, "kind": "table"}]]
    results = invoke(shell, tmp_path, [{"op": "assert", "requirements": value} for value in invalid])
    assert all(not result["ok"] and result["code"] == "INVALID_PACKAGE" for result in results), results
    case_distinct = [good, {**good, "path_segments": ["A"]}]
    assert invoke(shell, tmp_path, [{"op": "assert", "requirements": case_distinct}])[0] == {"ok": True, "value": True}


def test_limits_and_invalid_protected_identities_fail_closed(shell, tmp_path):
    texts = ['a="' + 'x' * 1048576 + '"', 'a=' + '[' * 66 + '1' + ']' * 66, '\n'.join(f'k{i}=true' for i in range(10001)), 'a=9223372036854775808']
    items = [{"op": "get", "text": text, "protected": []} for text in texts]
    items += [{"op": "get", "text": "managed=true", "protected": [section]} for section in ['a]\n[b', 'a] # trailing header', 'a] value=1 #', 'a..b', 'a."unterminated']]
    results = invoke(shell, tmp_path, items)
    assert all(not result["ok"] and result["code"] == "INVALID_PACKAGE" for result in results), results


def test_tampered_dll_is_rejected_before_load(shell, tmp_path):
    copied = tmp_path / "adapter"
    (copied / "vendor" / "tomlyn").mkdir(parents=True)
    shutil.copy2(ADAPTER, copied / ADAPTER.name)
    (copied / "vendor" / "tomlyn" / DLL.name).write_bytes(b"THIS IS NOT AN ASSEMBLY")
    result = invoke(shell, tmp_path, [{"op": "get", "text": "managed=true", "protected": []}], adapter=copied / ADAPTER.name)[0]
    assert result["code"] == "INVALID_PACKAGE" and result["message"] == "Bundled TOML parser integrity check failed"


def test_integer_boundaries_cannot_wrap_or_alias_another_value(shell, tmp_path):
    valid = ['a=+9223372036854775807', 'a=-9223372036854775808', 'a=0x7fff_ffff_ffff_ffff', 'a=0o777777777777777777777', 'a=0b' + '1' * 63, 'a=0x0001']
    invalid = ['a=9223372036854775808', 'a=-9223372036854775809', 'a=18446744073709551616', 'a=0x8000000000000000', 'a=0xffffffffffffffff', 'a=0o1000000000000000000000', 'a=0b1' + '0' * 63, 'model_providers.p.x=9223372036854775808\nmanaged=true']
    items = [{"op": "get", "text": text, "protected": []} for text in valid + invalid]
    results = invoke(shell, tmp_path, items)
    for text, result in zip(valid, results[: len(valid)], strict=True):
        assert result["ok"] and result["value"] == requirements(text), result
    for result in results[len(valid):]:
        assert not result["ok"] and result["code"] == "INVALID_PACKAGE", result
        assert "9223372036854775808" not in result["message"]


def test_pinned_parser_representation_limit_is_explicit_and_fails_closed(shell, tmp_path):
    # Valid TOML with >16 hexadecimal digits, even leading zeroes, is a known
    # Tomlyn 0.19 lexical-size limitation. Do not normalize around the parser.
    text = "a=0x000000000000000001"
    assert tomllib.loads(text) == {"a": 1}
    result = invoke(shell, tmp_path, [{"op": "test", "text": text, "requirements": requirements("a=1")}])[0]
    assert not result["ok"] and result["code"] == "ACTIVE_DRIFT", result


def test_preloaded_same_identity_from_other_path_is_rejected(shell, tmp_path):
    other = tmp_path / DLL.name
    shutil.copy2(DLL, other)
    result = invoke(shell, tmp_path, [{"op": "get", "text": "managed=true", "protected": []}], preload=other)[0]
    assert result["code"] == "INVALID_PACKAGE" and result["message"] == "Loaded TOML parser identity check failed"
