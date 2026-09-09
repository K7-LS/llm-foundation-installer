"""Synthetic release envelopes and real ZIP I/O; no installer/client execution."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

APP_VERSION = (
    Path(__file__).resolve().parents[1] / "APP_VERSION"
).read_text(encoding="utf-8").strip()


ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
SPEC = importlib.util.spec_from_file_location(
    "foundation_release",
    ROOT / "tools" / "foundation_release.py",
)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path, version: str | None = None) -> tuple[Path, Path]:
    version = version or FOUNDATION_VERSION
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "foundation.ps1").write_text(
        "Write-Output 'foundation'\n",
        encoding="utf-8",
    )
    (engine / "VERSION").write_text(
        version + "\n",
        encoding="utf-8",
    )
    script_hash = _sha256(engine / "foundation.ps1")
    (engine / "engine-manifest.json").write_bytes(
        _json_bytes(
            {
                "schema_version": 1,
                "protocol_version": 1,
                "engine_version": version,
                "foundation_ps1_sha256": script_hash,
                "network": "offline",
                "commands": [
                    "apply",
                    "doctor",
                    "install",
                    "inventory",
                    "plan",
                    "rollback",
                ],
                "supported_powershell": ["5.1", "7"],
            }
        )
    )
    if version == '0.5.12':
        for name in ('foundation-toml.ps1', 'vendor/tomlyn/Tomlyn.dll',
                     'vendor/tomlyn/LICENSE.txt', 'vendor/tomlyn/provenance.json'):
            path = engine / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('Synthetic test-only payload: ' + name).encode())
    shared_records = {}
    for field, name in {
        'private_exe': 'officecli.exe', 'shim': 'officecli-shim.exe',
        'policy': 'officecli-command-policy.json', 'pdf_exporter': 'k7-officecli-pdf.exe',
        'csv_batch_adapter': 'officecli_csv_batch.py',
    }.items():
        path = engine / 'shared-tools' / 'officecli' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('Synthetic shared fixture: ' + name).encode())
        shared_records[field] = {'path': path.relative_to(engine).as_posix(),
                                 'sha256': _sha256(path), 'bytes': path.stat().st_size}
    (engine / 'shared-tools.lock.json').write_bytes(_json_bytes({
        'schema_version': 1, 'tools': [{'id': 'officecli', 'version': '1.0.143', **shared_records}],
    }))
    files = {
        path.relative_to(engine).as_posix(): _sha256(path)
        for path in sorted(engine.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file()
    }
    evidence = {
        "schema_version": 1,
        "generated_at_utc": "2026-07-27T12:00:00Z",
        "engine_version": version,
        "installer_version": APP_VERSION,
        "source": {
            "repository": (
                "https://github.com/K7-LS/"
                "llm-foundation-installer"
            ),
            "commit": "a" * 40,
            "tree": "b" * 40,
            "hashes": {
                "VERSION": "c" * 64,
                "APP_VERSION": "d" * 64,
                "client-sources.lock.json": "e" * 64,
                "src": "f" * 64,
                "tests": "1" * 64,
                "tools": "2" * 64,
            },
        },
        "FOUNDATION_SYNTHETIC": "PASS",
        "powershell_syntax": {
            "ps7": {"status": "PASS"},
            "ps51": {"status": "PASS"},
        },
        "engine_builds": {
            "ps7": {"status": "PASS", "files": files},
            "ps51": {"status": "PASS", "files": files},
        },
        "deterministic_engine_bundle": "PASS",
        "pytest": {
            "status": "PASS",
            "counts": {
                "tests": 143,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
                "passed": 143,
                "ps7_cases": 20,
                "ps51_cases": 20,
                "shared_cases": 103,
            },
        },
    }
    evidence["evidence_body_sha256"] = release.evidence_body_sha256(evidence)
    evidence_path = tmp_path / "foundation-acceptance.json"
    evidence_path.write_bytes(_json_bytes(evidence))
    return engine, evidence_path


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_prepare_foundation_release_is_deterministic_and_bound(tmp_path: Path):
    engine, evidence = _fixture(tmp_path)

    first = release.prepare_foundation_release(
        engine_root=engine,
        acceptance_evidence_path=evidence,
        output=tmp_path / "first",
    )
    second = release.prepare_foundation_release(
        engine_root=engine,
        acceptance_evidence_path=evidence,
        output=tmp_path / "second",
    )

    assert _tree(first.root) == _tree(second.root)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["tag"] == f"foundation-engine-v{FOUNDATION_VERSION}"
    assert manifest["channel"] == "stable"
    assert manifest["asset"]["name"] == (
        f"foundation-engine-{FOUNDATION_VERSION}.zip"
    )
    assert manifest["asset"]["sha256"] == _sha256(first.asset_path)
    assert manifest["engine_files"]["foundation.ps1"]["sha256"] == (
        _sha256(engine / "foundation.ps1")
    )


def test_prepare_foundation_release_rejects_changed_engine(tmp_path: Path):
    engine, evidence = _fixture(tmp_path)
    (engine / "foundation.ps1").write_text("changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="engine bytes"):
        release.prepare_foundation_release(
            engine_root=engine,
            acceptance_evidence_path=evidence,
            output=tmp_path / "out",
        )


def test_release_verification_and_package_acceptance_bind_exact_asset(
    tmp_path: Path,
):
    engine, evidence = _fixture(tmp_path)
    stable = release.prepare_foundation_release(
        engine_root=engine,
        acceptance_evidence_path=evidence,
        output=tmp_path / "stable",
    )
    manifest = json.loads(stable.manifest_path.read_text(encoding="utf-8"))
    verification = release.build_release_verification(
        manifest_path=stable.manifest_path,
        asset_path=stable.asset_path,
        release_api={
            "tag_name": manifest["tag"],
            "draft": False,
            "prerelease": False,
            "immutable": True,
        },
        release_attestation_output=b"{}",
        asset_attestation_output=b"{}",
        gh_version="gh version 2.96.0",
    )
    verification_path = stable.root / "release-verification.json"
    verification_path.write_bytes(_json_bytes(verification))
    output = stable.root / "package-acceptance.json"

    result = release.create_package_acceptance(
        stable.manifest_path,
        stable.evidence_path,
        verification_path,
        output,
    )

    assert result["package_acceptance"] == "PASS"
    assert result["target"] == "foundation"
    assert result["engine_version"] == FOUNDATION_VERSION
    assert result["asset"]["sha256"] == manifest["asset"]["sha256"]


def test_package_acceptance_rejects_mutable_release(tmp_path: Path):
    engine, evidence = _fixture(tmp_path)
    stable = release.prepare_foundation_release(
        engine_root=engine,
        acceptance_evidence_path=evidence,
        output=tmp_path / "stable",
    )
    manifest = json.loads(stable.manifest_path.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="immutable"):
        release.build_release_verification(
            manifest_path=stable.manifest_path,
            asset_path=stable.asset_path,
            release_api={
                "tag_name": manifest["tag"],
                "draft": False,
                "prerelease": False,
                "immutable": False,
            },
            release_attestation_output=b"{}",
            asset_attestation_output=b"{}",
            gh_version="gh version 2.96.0",
        )


POWERSHELLS = [path for name in ('pwsh', 'powershell.exe') if (path := shutil.which(name))]
DOCTOR_FILES = ('foundation-toml.ps1', 'vendor/tomlyn/Tomlyn.dll',
                'vendor/tomlyn/LICENSE.txt', 'vendor/tomlyn/provenance.json')
IMPORT_HARNESS = r'''
param([string]$Repository, [string]$Package, [string]$Destination,
      [string]$EngineVersion, [string]$InstallerVersion,
      [string]$Mode = 'read-export')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
foreach ($Relative in @('tools/_common.ps1', 'tools/build-gui.ps1')) {
    $Errors = $null; $Tokens = $null
    $Ast = [Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $Repository $Relative), [ref]$Tokens, [ref]$Errors)
    if ($Errors.Count -ne 0) { throw 'Source parse failed' }
    foreach ($Statement in $Ast.EndBlock.Statements) {
        if ($Statement -is [Management.Automation.Language.FunctionDefinitionAst]) {
            Invoke-Expression $Statement.Extent.Text
        }
    }
}
$FoundationEngineVersion = $EngineVersion; $Version = $InstallerVersion
try {
    if ($Mode -ceq 'export') {
        $Release = Get-Content -LiteralPath (Join-Path $Package 'release-manifest.json') -Raw |
            ConvertFrom-Json
        $Accepted = [pscustomobject]@{
            engine_files = $Release.engine_files
            asset_path = Join-Path $Package $Release.asset.name
        }
    } else {
        $Accepted = Read-AcceptedFoundation $Package
    }
    Export-AcceptedFoundationEngine $Accepted $Destination
    Write-Output 'IMPORTED'
} catch {
    Write-Output $_.Exception.Message
    exit 17
}
'''


def _unit_accepted_release(tmp_path, monkeypatch, version):
    # These PASS envelopes are synthetic unit fixtures, never release evidence.
    monkeypatch.setattr(release, 'VERSION', version)
    monkeypatch.setattr(release, 'TAG', 'foundation-engine-v' + version)
    engine, evidence = _fixture(tmp_path, version)
    prepared = release.prepare_foundation_release(engine_root=engine,
        acceptance_evidence_path=evidence, output=tmp_path / 'synthetic-release')
    _unit_seal_release(prepared)
    return engine, prepared


def _unit_seal_release(prepared):
    manifest = json.loads(prepared.manifest_path.read_bytes())
    manifest['asset'].update(sha256=_sha256(prepared.asset_path), bytes=prepared.asset_path.stat().st_size)
    manifest['evidence_body_sha256'] = release.evidence_body_sha256(manifest)
    prepared.manifest_path.write_bytes(_json_bytes(manifest))
    verification = release.build_release_verification(
        manifest_path=prepared.manifest_path, asset_path=prepared.asset_path,
        release_api={'tag_name': manifest['tag'], 'draft': False, 'prerelease': False, 'immutable': True},
        release_attestation_output=b'{}', asset_attestation_output=b'{}', gh_version='gh version 2.96.0 (synthetic unit fixture)')
    verification_path = prepared.root / 'release-verification.json'
    verification_path.write_bytes(_json_bytes(verification))
    release.create_package_acceptance(prepared.manifest_path, prepared.evidence_path,
        verification_path, prepared.root / 'package-acceptance.json')


def _import_release(tmp_path, executable, prepared, version, expected=None, mode='read-export'):
    harness = tmp_path / 'read-only-import.ps1'
    harness.write_text(IMPORT_HARNESS, encoding='utf-8-sig')
    destination = tmp_path / 'imported-engine'
    result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', str(harness), '-Repository', str(ROOT), '-Package', str(prepared.root),
        '-Destination', str(destination), '-EngineVersion', version, '-InstallerVersion', APP_VERSION,
        '-Mode', mode],
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True,
        text=True, encoding='utf-8', timeout=60)
    if expected:
        assert result.returncode == 17, result.stdout + result.stderr
        assert expected in result.stdout, result.stdout + result.stderr
    else:
        assert result.returncode == 0 and result.stdout.strip() == 'IMPORTED', result.stdout + result.stderr
    return destination


@pytest.mark.parametrize('executable', POWERSHELLS)
@pytest.mark.parametrize('version, core_count, total_count', [('0.5.11', 3, 9), ('0.5.12', 7, 13)])
def test_accepted_foundation_zip_imports_every_versioned_file(tmp_path, monkeypatch, executable, version, core_count, total_count):
    engine, prepared = _unit_accepted_release(tmp_path, monkeypatch, version)
    manifest = json.loads(prepared.manifest_path.read_bytes())
    assert len(manifest['engine_files']) == core_count
    destination = _import_release(tmp_path, executable, prepared, version)
    assert len(_tree(destination)) == total_count
    assert _tree(destination) == _tree(engine)


@pytest.mark.parametrize('executable', POWERSHELLS)
@pytest.mark.parametrize('name', DOCTOR_FILES)
@pytest.mark.parametrize('mutation', ['missing', 'tampered'])
def test_accepted_foundation_import_rejects_changed_doctor_payload(tmp_path, monkeypatch, executable, name, mutation):
    _, prepared = _unit_accepted_release(tmp_path, monkeypatch, '0.5.12')
    with zipfile.ZipFile(prepared.asset_path) as archive:
        entries = {path: archive.read(path) for path in archive.namelist()}
    if mutation == 'missing': entries.pop(name)
    else: entries[name] = bytes([entries[name][0] ^ 1]) + entries[name][1:]
    with zipfile.ZipFile(prepared.asset_path, 'w') as archive:
        for path, data in entries.items(): archive.writestr(path, data)
    # Rebind all outer artifacts so the production importer's inner check runs.
    _unit_seal_release(prepared)
    expected = 'archive inventory differs' if mutation == 'missing' else 'extracted bytes differ'
    _import_release(tmp_path, executable, prepared, '0.5.12', expected)


@pytest.mark.parametrize('name', DOCTOR_FILES)
@pytest.mark.parametrize('version', ['0.5.11', '0.5.12'])
def test_release_producer_requires_exact_versioned_engine_tree(tmp_path, monkeypatch, name, version):
    monkeypatch.setattr(release, 'VERSION', version)
    monkeypatch.setattr(release, 'TAG', 'foundation-engine-v' + version)
    engine, evidence = _fixture(tmp_path, version)
    path = engine / name
    if version == '0.5.12': path.unlink()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'Unexpected historical engine dependency')
    with pytest.raises(ValueError, match='engine inventory differs'):
        release.prepare_foundation_release(engine_root=engine,
            acceptance_evidence_path=evidence, output=tmp_path / 'must-not-exist')
    assert not (tmp_path / 'must-not-exist').exists()


@pytest.mark.parametrize('name', DOCTOR_FILES)
def test_package_acceptance_requires_each_doctor_manifest_record(tmp_path, monkeypatch, name):
    _, prepared = _unit_accepted_release(tmp_path, monkeypatch, '0.5.12')
    manifest = json.loads(prepared.manifest_path.read_bytes())
    manifest['engine_files'].pop(name)
    prepared.manifest_path.write_bytes(_json_bytes(manifest))
    with pytest.raises(ValueError, match='stable Foundation release manifest is invalid'):
        _unit_seal_release(prepared)


def _unit_rebind_colliding_inventory(prepared):
    # Adversarial synthetic envelopes only: the legitimate producer rejects this inventory.
    combined = 'vendor/tomlyn/LICENSE.txt,vendor/tomlyn/provenance.json'
    with zipfile.ZipFile(prepared.asset_path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries[combined] = entries.pop('vendor/tomlyn/LICENSE.txt') + entries.pop('vendor/tomlyn/provenance.json')
    assert len(entries) == 12
    with zipfile.ZipFile(prepared.asset_path, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    manifest = json.loads(prepared.manifest_path.read_bytes())
    expected_core = set(manifest['engine_files'])
    manifest['engine_files'].pop('vendor/tomlyn/LICENSE.txt')
    manifest['engine_files'].pop('vendor/tomlyn/provenance.json')
    manifest['engine_files'][combined] = {
        'sha256': hashlib.sha256(entries[combined]).hexdigest(), 'bytes': len(entries[combined])}
    # Reproduce the former PowerShell sorted comma-join collision, not a stale hash failure.
    assert len(manifest['engine_files']) == 6
    assert ','.join(sorted(manifest['engine_files'], key=str.casefold)) == ','.join(sorted(expected_core, key=str.casefold))
    evidence = json.loads(prepared.evidence_path.read_bytes())
    for build in evidence['engine_builds'].values():
        build['files'] = {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()}
    evidence['evidence_body_sha256'] = release.evidence_body_sha256(evidence)
    prepared.evidence_path.write_bytes(_json_bytes(evidence))
    manifest['acceptance_evidence_sha256'] = _sha256(prepared.evidence_path)
    manifest['asset'].update(sha256=_sha256(prepared.asset_path), bytes=prepared.asset_path.stat().st_size)
    manifest['evidence_body_sha256'] = release.evidence_body_sha256(manifest)
    prepared.manifest_path.write_bytes(_json_bytes(manifest))
    verification = release.build_release_verification(
        manifest_path=prepared.manifest_path, asset_path=prepared.asset_path,
        release_api={'tag_name': manifest['tag'], 'draft': False, 'prerelease': False, 'immutable': True},
        release_attestation_output=b'{}', asset_attestation_output=b'{}',
        gh_version='gh version 2.96.0 (synthetic adversarial fixture)')
    verification_path = prepared.root / 'release-verification.json'
    verification_path.write_bytes(_json_bytes(verification))
    acceptance_path = prepared.root / 'package-acceptance.json'
    with pytest.raises(ValueError, match='stable Foundation release manifest is invalid'):
        release.create_package_acceptance(prepared.manifest_path, prepared.evidence_path,
            verification_path, acceptance_path)
    acceptance = json.loads(acceptance_path.read_bytes())
    acceptance['engine_files'] = manifest['engine_files']
    acceptance['asset'] = manifest['asset']
    for field, path in [('release_manifest', prepared.manifest_path),
                        ('acceptance_evidence', prepared.evidence_path),
                        ('release_verification', verification_path)]:
        acceptance[field] = {'name': path.name, 'sha256': _sha256(path), 'bytes': path.stat().st_size}
    acceptance_path.write_bytes(_json_bytes(acceptance))


@pytest.mark.parametrize('executable', POWERSHELLS)
@pytest.mark.parametrize('mode, expected', [
    ('read-export', 'Foundation package release manifest is invalid'),
    ('export', 'Foundation engine archive core inventory differs'),
])
def test_accepted_foundation_rejects_rebound_comma_join_collision(tmp_path, monkeypatch, executable, mode, expected):
    engine, prepared = _unit_accepted_release(tmp_path, monkeypatch, '0.5.12')
    source_before = _tree(engine)
    _unit_rebind_colliding_inventory(prepared)
    destination = _import_release(tmp_path, executable, prepared, '0.5.12', expected, mode)
    assert not destination.exists() or not _tree(destination)
    assert _tree(engine) == source_before
