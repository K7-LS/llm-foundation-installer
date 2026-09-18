"""Execute the actual C# target-engine reader against embedded hostile fixtures.

No Foundation command, client process, installer or user-profile write is run.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import warnings
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMON = [
    "VERSION", "engine-manifest.json", "foundation.ps1", "shared-tools.lock.json",
    "shared-tools/officecli/k7-officecli-pdf.exe",
    "shared-tools/officecli/officecli-command-policy.json",
    "shared-tools/officecli/officecli-shim.exe", "shared-tools/officecli/officecli.exe",
    "shared-tools/officecli/officecli_csv_batch.py",
]
NEW = ["foundation-toml.ps1", "vendor/tomlyn/LICENSE.txt", "vendor/tomlyn/Tomlyn.dll", "vendor/tomlyn/provenance.json"]
PREFIXES = {"codex": ".codex", "claude": ".claude", "opencode": ".config/opencode"}
BAD_CASES = [
    "resource_hash", "resource_size", "release_hash", "release_size", "release_missing",
    "release_invalid_json", "target_mismatch", "unknown_engine", "release_asset_mismatch",
    "missing_manifest", "manifest_hash", "manifest_invalid_json", "manifest_target",
    "manifest_engine", "manifest_version", "manifest_duplicate", "engine_missing",
    "engine_extra", "engine_hash", "engine_size", "engine_manifest_hash", "engine_version",
    "engine_script", "engine_protocol", "archive_duplicate", "archive_case_collision",
    "directory_case_collision", "archive_traversal", "archive_absolute", "archive_backslash",
    "archive_symlink", "archive_reparse", "archive_device", "archive_file_directory",
    "unexpected_engine_version", "archive_truncated",
]


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: object) -> bytes:
    return json.dumps(data, sort_keys=True).encode("utf-8")


def _fixture(target: str, defect: str | None = None) -> tuple[bytes, bytes]:
    version = "0.5.12" if target == "codex" else "0.5.10"
    prefix = f"{PREFIXES[target]}/base/foundation/{version}/"
    files = {prefix + name: ("fixture " + name).encode() for name in COMMON + (NEW if version == "0.5.12" else [])}
    files[prefix + "VERSION"] = (version + "\n").encode()
    engine = {"schema_version": 1, "protocol_version": 1, "engine_version": version,
              "network": "offline", "foundation_ps1_sha256": _hash(files[prefix + "foundation.ps1"])}
    if defect == "engine_script":
        engine["foundation_ps1_sha256"] = "0" * 64
    if defect == "engine_protocol":
        engine["protocol_version"] = 2
    files[prefix + "engine-manifest.json"] = _json(engine)
    engine_hash = _hash(files[prefix + "engine-manifest.json"])
    if defect == "engine_version":
        files[prefix + "VERSION"] = b"0.5.99\n"
    manifest = {"schema_version": 1, "target": target, "version": "1.0.0", "foundation_engine_version": version,
                "files": [{"path": path, "bytes": len(data), "sha256": _hash(data)} for path, data in files.items()]}
    if defect == "manifest_target":
        manifest["target"] = "other"
    if defect == "manifest_engine":
        manifest["foundation_engine_version"] = "0.5.10"
    if defect == "manifest_version":
        manifest["version"] = "2.0.0"
    if defect == "manifest_duplicate":
        manifest["files"].append(manifest["files"][0])
    if defect == "engine_size":
        manifest["files"][0]["bytes"] += 1
    manifest_bytes = _json(manifest)
    if defect == "manifest_invalid_json":
        manifest_bytes = b"{ invalid"
    if defect == "engine_missing":
        del files[prefix + "shared-tools/officecli/officecli.exe"]
    if defect == "engine_extra":
        files[prefix + "extra.ps1"] = b"unapproved"
    if defect == "engine_hash":
        files[prefix + "foundation.ps1"] = b"altered code"
    if defect == "unexpected_engine_version":
        files[f"{PREFIXES[target]}/base/foundation/0.5.99/extra.ps1"] = b"other engine"
    extras: list[tuple[str | zipfile.ZipInfo, bytes]] = []
    if defect == "archive_duplicate":
        extras.append((prefix + "VERSION", b"duplicate"))
    if defect == "archive_case_collision":
        extras.append((prefix + "version", b"duplicate"))
    if defect == "directory_case_collision":
        extras.extend([("a/file-one", b"1"), ("A/file-two", b"2")])
    if defect in {"archive_traversal", "archive_absolute", "archive_backslash", "archive_device"}:
        path = {"archive_traversal": "../escape.ps1", "archive_absolute": "/escape.ps1",
                "archive_backslash": "a\\escape.ps1", "archive_device": "NUL.txt"}[defect]
        extras.append((path, b"unsafe"))
    if defect in {"archive_symlink", "archive_reparse"}:
        info = zipfile.ZipInfo("unrelated-link")
        info.create_system = 3
        info.external_attr = (0o120777 << 16) if defect == "archive_symlink" else 0x400
        extras.append((info, b"outside"))
    if defect == "archive_file_directory":
        extras.extend([("data", b"file"), ("data/item", b"child")])
    stream = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        warnings.simplefilter("ignore", UserWarning)
        for path, data in files.items():
            archive.writestr(path, data)
        if defect != "missing_manifest":
            archive.writestr("package-manifest.json", manifest_bytes)
        for path, data in extras:
            archive.writestr(path, data)
    package = stream.getvalue()
    if defect == "archive_backslash":
        # ZipInfo on Windows normalizes backslashes while writing; put the
        # hostile spelling in both local and central directory records.
        package = package.replace(b"a/escape.ps1", b"a\\escape.ps1")
    if defect == "archive_truncated":
        package = package[:80]
    release = {"schema_version": 1, "target": target, "version": "1.0.0", "foundation_engine_version": version,
               "foundation_engine_manifest_sha256": engine_hash, "package_manifest_sha256": _hash(manifest_bytes),
               "asset": {"name": "base.zip", "bytes": len(package), "sha256": _hash(package)}}
    if defect == "manifest_hash":
        release["package_manifest_sha256"] = "0" * 64
    if defect == "engine_manifest_hash":
        release["foundation_engine_manifest_sha256"] = "0" * 64
    if defect == "unknown_engine":
        release["foundation_engine_version"] = "0.5.99"
    if defect == "target_mismatch":
        release["target"] = "claude"
    if defect == "release_asset_mismatch":
        release["asset"]["sha256"] = "0" * 64
    return package, (b"{ invalid" if defect == "release_invalid_json" else _json(release))


@pytest.fixture(scope="module")
def engine_harness(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Path]]:
    compiler = Path(os.environ.get("K7_TEST_CSHARP_COMPILER", "C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe"))
    if not compiler.is_file():
        pytest.skip("Windows C# compiler is unavailable")
    root = tmp_path_factory.mktemp("target-engine")
    harness = root / "Harness.cs"
    harness.write_text(r'''
using System;
using System.IO;
using System.Web.Script.Serialization;
namespace LlmFoundationInstaller {
internal sealed class TrustedFile { public string relative_path {get;set;} public string resource_name {get;set;} public string sha256 {get;set;} public long bytes {get;set;} }
internal sealed class TrustedPackage { public string target {get;set;} public TrustedFile asset {get;set;} public TrustedFile release_manifest {get;set;} }
public static class Harness {
public static int Main(string[] args) {
 try {
  var package = new JavaScriptSerializer().Deserialize<TrustedPackage>(File.ReadAllText(args[0]));
  TargetEngineBinding result;
  if(args[1] == "extract") result=TargetFoundationEngine.Extract(package,args[2]);
  else if(args[1] == "contract") result=TargetFoundationEngine.ReadContract(package);
  else result=TargetFoundationEngine.Validate(package);
  Console.WriteLine(new JavaScriptSerializer().Serialize(result)); return 0;
 } catch(InvalidOperationException error) { Console.WriteLine(error.Message); return 2; }
}
}}
''', encoding="utf-8")
    resources = []
    metadata = {}
    cases = [(target, None) for target in PREFIXES] + [("codex", defect) for defect in BAD_CASES]
    for target, defect in cases:
        name = defect or target
        package, release = _fixture(target, defect)
        package_path = root / f"{name}.zip"
        release_path = root / f"{name}.release.json"
        package_path.write_bytes(package)
        release_path.write_bytes(release)
        resources.append(f"/resource:{package_path},{name}.zip")
        if defect != "release_missing":
            resources.append(f"/resource:{release_path},{name}.release")
        record = {"target": target,
                  "asset": {"relative_path": "base.zip", "resource_name": f"{name}.zip", "sha256": _hash(package), "bytes": len(package)},
                  "release_manifest": {"relative_path": "release-manifest.json", "resource_name": f"{name}.release", "sha256": _hash(release), "bytes": len(release)}}
        if defect == "resource_hash":
            record["asset"]["sha256"] = "0" * 64
        if defect == "resource_size":
            record["asset"]["bytes"] += 1
        if defect == "release_hash":
            record["release_manifest"]["sha256"] = "0" * 64
        if defect == "release_size":
            record["release_manifest"]["bytes"] += 1
        metadata[name] = root / f"{name}.json"
        metadata[name].write_bytes(_json(record))
    executable = root / "TargetEngineHarness.exe"
    response = root / "compile.rsp"
    arguments = ["/nologo", "/target:exe", f"/out:{executable}", "/r:System.IO.Compression.dll",
                 "/r:System.Web.Extensions.dll", str(harness), str(ROOT / "src/gui/TargetFoundationEngine.cs"), *resources]
    response.write_text("\n".join('"' + value + '"' for value in arguments), encoding="utf-8")
    result = subprocess.run([str(compiler), "@" + str(response)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return executable, metadata


def _run(engine_harness: tuple[Path, dict[str, Path]], name: str, mode: str = "validate", root: Path | None = None) -> subprocess.CompletedProcess[str]:
    executable, metadata = engine_harness
    return subprocess.run([str(executable), str(metadata[name]), mode, str(root or "")], capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("target,version,count", [("codex", "0.5.12", 13), ("claude", "0.5.10", 9), ("opencode", "0.5.10", 9)])
def test_target_engine_selection_and_complete_extraction(engine_harness, tmp_path, target, version, count):
    result = _run(engine_harness, target, "extract", tmp_path / "engine")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["engine_version"] == version
    assert json.loads(result.stdout)["target"] == target
    files = [path for path in (tmp_path / "engine").rglob("*") if path.is_file()]
    assert len(files) == count
    assert (tmp_path / "engine/VERSION").read_text().strip() == version
    assert (tmp_path / "engine/vendor/tomlyn/Tomlyn.dll").exists() == (target == "codex")


@pytest.mark.parametrize("defect", BAD_CASES)
def test_invalid_embedded_engine_fails_closed_without_writing(engine_harness, tmp_path, defect):
    result = _run(engine_harness, defect, "extract", tmp_path / "engine")
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (tmp_path / "engine").exists()


def test_read_contract_does_not_need_to_open_archive(engine_harness):
    result = _run(engine_harness, "archive_truncated", "contract")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["engine_version"] == "0.5.12"


def test_extraction_refuses_existing_files(engine_harness, tmp_path):
    root = tmp_path / "engine"
    root.mkdir()
    sentinel = root / "keep.txt"
    sentinel.write_text("keep")
    result = _run(engine_harness, "codex", "extract", root)
    assert result.returncode == 2, result.stdout + result.stderr
    assert sentinel.read_text() == "keep"
    assert list(root.iterdir()) == [sentinel]
