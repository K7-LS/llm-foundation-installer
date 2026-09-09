"""Read-only production validators, synthetic ZIPs; no CLI/build/install entrypoints.

Fixtures labelled PASS model signed-producer envelopes only. They are not release
attestations, behavioral observations, or permission to deliver an installer.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import warnings
import zipfile
import xml.etree.ElementTree as ET

import pytest

from test_foundation import POWERSHELLS, _package
from test_foundation_shared_tools import _write_release_manifest
from test_gui import _accepted_package
from test_engine_isolated_lifecycle import SCENARIOS


ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ".codex/base/core-eval-contract.json"
CORE_BYTES = (ROOT / "tests/fixtures/professional-core/contract.json").read_bytes()
REFERENCE = {
    "id": "k7-professional-core-v1",
    "sha256": "028ba6363bff000b4aa8551ca27a66a69631cb29dacdc5319a4ce0b696b3a184",
    "suite_sha256": "62b45686075d01277f9c924ca245f105dc5c54ba385f74084b7bdd379218d49c",
}
REASON = "Historical startup-token benchmark is separate from professional-core conformance."
BINDING_KEYS = (
    "target", "version", "tag", "client", "asset", "package_manifest_sha256",
    "components_lock_sha256", "source", "foundation_engine_version",
    "foundation_engine_manifest_sha256", "core_behavior_contract", "session_tools_asset",
)

HARNESS = r'''
param([string]$Repository, [string]$Mode, [string]$Fixture)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
foreach ($Relative in @('src/foundation.ps1', 'tools/_common.ps1', 'tools/build-gui.ps1')) {
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
$script:EngineVersion = [IO.File]::ReadAllText((Join-Path $Repository 'VERSION')).Trim()
try {
    if ($Mode -in @('foundation', 'foundation-bound')) {
        if ($Mode -ceq 'foundation-bound') {
            $ReleasePath = Join-Path (Split-Path -Parent $Fixture) 'release-manifest.json'
            $Opened = Open-ValidatedPackage $Fixture -ReleaseManifestPath $ReleasePath `
                -ExpectedReleaseManifestSha256 (Get-Sha256 $ReleasePath)
        } else { $Opened = Open-ValidatedPackage $Fixture }
        try { $null = $Opened.manifest } finally { Close-ValidatedPackage $Opened }
    } elseif ($Mode -ceq 'builder') {
        $Rows = @(Read-AcceptedPackages $Fixture 'Preview')
        if ($Rows.Count -ne 1 -or $Rows[0].trust_level -cne 'accepted') {
            throw 'Expected one accepted fixture row'
        }
    } elseif ($Mode -ceq 'client') {
        $InputValue = Get-Content -LiteralPath $Fixture -Raw | ConvertFrom-Json
        Assert-ClientContract $InputValue.expected $InputValue.id $InputValue.version
    } else { throw 'Unknown fixture mode' }
    Write-Output 'VALIDATED'
} catch {
    Write-Output $_.Exception.Message
    exit 17
}
'''


def _bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _read(path):
    return json.loads(path.read_bytes())


def _write(path, value):
    path.write_bytes(_bytes(value))


def _selfhash(value):
    value["evidence_body_sha256"] = _sha(_bytes({
        key: item for key, item in value.items() if key != "evidence_body_sha256"
    }))


def _isolated_engine_fixture(version=None):
    version = version or (ROOT / 'VERSION').read_text().strip()
    names = ('VERSION', 'foundation.ps1', 'shared-tools.lock.json',
             'shared-tools/officecli/officecli.exe', 'shared-tools/officecli/officecli-shim.exe',
             'shared-tools/officecli/officecli-command-policy.json',
             'shared-tools/officecli/k7-officecli-pdf.exe', 'shared-tools/officecli/officecli_csv_batch.py')
    assert version in ('0.5.11', '0.5.12')
    if version == '0.5.12':
        names += ('foundation-toml.ps1', 'vendor/tomlyn/Tomlyn.dll',
                  'vendor/tomlyn/LICENSE.txt', 'vendor/tomlyn/provenance.json')
    payloads = {name: (f'fixture {name}\n').encode() for name in names}
    payloads['VERSION'] = (version + '\n').encode()
    payloads['engine-manifest.json'] = _bytes({'schema_version': 1, 'protocol_version': 1,
        'engine_version': version, 'foundation_ps1_sha256': _sha(payloads['foundation.ps1']),
        'network': 'offline', 'supported_powershell': ['5.1', '7'],
        'commands': ['apply', 'doctor', 'install', 'inventory', 'plan', 'rollback']})
    files = {name: _sha(data) for name, data in payloads.items()}
    evidence = {
        'schema_version': 1, 'acceptance_protocol': 'foundation-engine-isolated-v1',
        'engine_version': version, 'installer_version': '0.4.5', 'model_requests': 0,
        'FOUNDATION_ENGINE_ACCEPTANCE': 'PASS', 'FOUNDATION_SYNTHETIC': 'NOT_RUN',
        'INSTALLER_ACCEPTANCE': 'NOT_RUN', 'deterministic_engine_bundle': 'PASS',
        'historical_not_run_reason': 'The historical runner includes the full installer/GUI suite; this protocol is explicitly engine-only.',
        'source': {'repository': 'https://github.com/K7-LS/llm-foundation-installer',
                   'commit': 'a' * 40, 'tree': 'b' * 40,
                   'hashes': {name: 'c' * 64 for name in ('VERSION', 'APP_VERSION', 'client-sources.lock.json', 'src', 'tests', 'tools')}},
        'scope': {'fake_homes_only': True, 'real_consumer_executed': False, 'gui_executed': False, 'model_requests': 0},
        'powershell_syntax': {shell: {'status': 'PASS', 'returncode': 0} for shell in ('ps7', 'ps51')},
        'engine_builds': {shell: {'status': 'PASS', 'returncode': 0, 'files': copy.deepcopy(files)} for shell in ('ps7', 'ps51')},
        'engine_lifecycle': {'status': 'PASS', 'evaluation_mode': 'SYNTHETIC_HOME',
            'required_scenarios': list(SCENARIOS), 'shells': {shell: {
                'status': 'PASS', 'engine_sha256': files['foundation.ps1'],
                'engine_manifest_sha256': files['engine-manifest.json'], 'scenario_ids': list(SCENARIOS),
                'user_environment_unchanged': True, 'installed_files_verified': True, 'rollback_byte_identical': True,
                'receipts': [{'path': f'receipts/{shell}-{scenario}.json', 'sha256': 'd' * 64, 'bytes': 123} for scenario in SCENARIOS],
            } for shell in ('ps7', 'ps51')}},
        'pytest': {'status': 'PASS', 'returncode': 0, 'counts': {'tests': 14, 'failures': 0, 'errors': 0, 'skipped': 0},
                   'junit_sha256': 'e' * 64, 'junit_artifact': {'path': 'pytest.xml', 'sha256': 'e' * 64, 'bytes': 123},
                   'selected_files': ['tests/test_engine_isolated_lifecycle.py'],
                   'collected_case_ids': [f'{shell}-{scenario}' for shell in ('ps7', 'ps51') for scenario in SCENARIOS]},
    }
    selected = ['tests/test_foundation.py', 'tests/test_foundation_shared_tools.py',
                'tests/test_foundation_release.py', 'tests/test_acceptance_runner.py',
                'tests/test_professional_core_compat.py', 'tests/test_engine_isolated_lifecycle.py',
                'tests/test_engine_acceptance_runner.py']
    if version == '0.5.12':
        selected += ['tests/test_doctor_state.py', 'tests/test_doctor_toml.py']
    environment = {name: 'a' * 64 for name in ('PATH', 'OFFICECLI_NO_AUTO_INSTALL', 'OFFICECLI_SKIP_UPDATE')}
    evidence['real_user_environment_before'] = copy.deepcopy(environment)
    evidence['real_user_environment_after'] = copy.deepcopy(environment)
    artifacts = {}
    def artifact(path, text):
        raw = text.encode('utf-8'); sha = _sha(raw)
        artifacts[sha] = {'sha256': sha, 'bytes': len(raw), 'text': text}
        return {'path': path, 'sha256': sha, 'bytes': len(raw)}
    for shell in ('ps7', 'ps51'):
        rows = []
        for scenario in SCENARIOS:
            receipt = {'scenario_id': scenario, 'shell': shell, 'status': 'PASS',
                'engine_sha256': files['foundation.ps1'], 'engine_manifest_sha256': files['engine-manifest.json'],
                'commands': [{'command': ['synthetic-envelope-fixture', 'install'], 'returncode': 0,
                              'stdout': '{"status":"CANONICAL"}', 'stderr': ''}],
                'real_user_environment_before': environment, 'real_user_environment_after': environment,
                'before': {'files': {}, 'fake_environment': {}}, 'after': {'files': {}, 'fake_environment': {}}}
            rows.append(artifact(f'receipts/{shell}-{scenario}.json', _bytes(receipt).decode()))
        evidence['engine_lifecycle']['shells'][shell]['receipts'] = rows
    count = 2 * len(SCENARIOS) + len(selected) - 1
    suite = ET.Element('testsuite', tests=str(count), failures='0', errors='0', skipped='0')
    ids = []
    for shell in ('pwsh.exe', 'powershell.exe'):
        for scenario in SCENARIOS:
            name = f'test_actual_built_engine_lifecycle[{scenario}-C:\\fixture\\{shell}]'
            ET.SubElement(suite, 'testcase', classname='tests.test_engine_isolated_lifecycle', name=name)
            ids.append('tests/test_engine_isolated_lifecycle.py::' + name)
    for path in selected:
        if path.endswith('test_engine_isolated_lifecycle.py'): continue
        ET.SubElement(suite, 'testcase', classname=path[:-3].replace('/', '.'), name='test_fixture_regression')
        ids.append(path + '::test_fixture_regression')
    junit = artifact('pytest.xml', ET.tostring(suite, encoding='unicode'))
    evidence['pytest'].update(counts={'tests': count, 'failures': 0, 'errors': 0, 'skipped': 0},
        junit_sha256=junit['sha256'], junit_artifact=junit, collected_case_ids=ids,
        selected_files=selected, selected_files_sha256={p: 'c' * 64 for p in selected})
    evidence['pytest']['collection'] = {'status': 'PASS', 'returncode': 0,
        'command': ['synthetic-envelope-fixture', 'pytest', '--collect-only'],
        'stdout': '\n'.join(ids) + '\n', 'stderr': ''}
    _selfhash(evidence)
    return payloads, evidence, artifacts


def _edit_zip(package, edit):
    with zipfile.ZipFile(package) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries.pop("package-manifest.json"))
    edit(manifest, entries)
    manifest["files"] = [
        {"path": name, "sha256": _sha(payload), "bytes": len(payload)}
        for name, payload in sorted(entries.items())
    ]
    # Keep declared replace rows coherent so a mutant reaches the core guard.
    manifest["managed_surface"]["replace_files"] = sorted(
        name for name in manifest["managed_surface"]["replace_files"] if name in entries
    )
    with zipfile.ZipFile(package, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
        archive.writestr("package-manifest.json", _bytes(manifest))


def _rebind(directory, *, evidence_edit=None):
    """Recompute the complete outer chain; failures must reach semantic guards."""
    package = directory / "codex-base-1.0.0.zip"
    release = _read(directory / "release-manifest.json")
    evidence = _read(directory / "acceptance-evidence.json")
    with zipfile.ZipFile(package) as archive:
        package_bytes = archive.read("package-manifest.json")
    release["asset"] = {"name": package.name, "sha256": _sha(package.read_bytes()), "bytes": package.stat().st_size}
    release["package_manifest_sha256"] = _sha(package_bytes)
    binding = {key: release[key] for key in BINDING_KEYS if key in release}
    # Match the actual current producer. Historical GUI fixtures may include
    # client explicitly; current client identity is bound through ZIP bytes.
    if "acceptance_protocol" in evidence or "core_behavior_evidence" in evidence:
        binding.pop("client", None)
    evidence["release_binding"] = copy.deepcopy(binding)
    if isinstance(evidence.get("core_behavior_evidence"), dict):
        evidence["core_behavior_evidence"]["release_binding"] = copy.deepcopy(binding)
    if evidence_edit:
        evidence_edit(evidence)
    if isinstance(evidence.get("core_behavior_evidence"), dict):
        _selfhash(evidence["core_behavior_evidence"])
    if isinstance(evidence.get("foundation"), dict):
        _selfhash(evidence["foundation"])
    _selfhash(evidence)
    _write(directory / "acceptance-evidence.json", evidence)
    release["acceptance_evidence_sha256"] = _sha((directory / "acceptance-evidence.json").read_bytes())
    _write(directory / "release-manifest.json", release)
    verification = _read(directory / "release-verification.json")
    verification["assets"] = [{**release["asset"], "attestation": "PASS"}]
    _selfhash(verification)
    _write(directory / "release-verification.json", verification)
    acceptance = _read(directory / "package-acceptance.json")
    acceptance["client"] = release["client"]
    acceptance["asset"] = release["asset"]
    for key in ("release_manifest", "acceptance_evidence", "release_verification"):
        path = directory / acceptance[key]["name"]
        acceptance[key].update(sha256=_sha(path.read_bytes()), bytes=path.stat().st_size)
    _write(directory / "package-acceptance.json", acceptance)


def _bundle(root, *, current=True, engine_version=None):
    directory = _accepted_package(root)
    package = _package(directory / "codex-base-1.0.0.zip")
    if current:
        engine_payloads, engine_evidence, engine_artifacts = _isolated_engine_fixture(engine_version)
        def add(manifest, entries):
            manifest["core_behavior_contract"] = copy.deepcopy(REFERENCE)
            entries[CORE_PATH] = CORE_BYTES
            manifest["managed_surface"]["replace_files"].append(CORE_PATH)
            manifest["foundation_engine_version"] = engine_evidence['engine_version']
            for name in list(entries):
                if name.startswith('.codex/base/foundation/'):
                    entries.pop(name)
            prefix = f'.codex/base/foundation/{manifest["foundation_engine_version"]}/'
            entries.update({prefix + name: data for name, data in engine_payloads.items()})
        _edit_zip(package, add)
    _write_release_manifest(package, mutate=lambda release: release.update(
        **({"core_behavior_contract": copy.deepcopy(REFERENCE),
            "foundation_engine_manifest_sha256": _sha(engine_payloads['engine-manifest.json'])} if current else {})
    ))
    evidence = _read(directory / "acceptance-evidence.json")
    if current:
        evidence.update(
            acceptance_protocol="professional-core-v1", CORE_BEHAVIOR="PASS",
            CODEX_CANARY="PASS", MATCHED_AB="NOT_REQUIRED", matched_ab_not_required_reason=REASON,
            FOUNDATION_ENGINE_ACCEPTANCE="PASS", FOUNDATION_SYNTHETIC="NOT_RUN", foundation=engine_evidence,
            foundation_artifacts=engine_artifacts,
            core_behavior_evidence={"schema_version": 1, "kind": "core_behavior_evidence",
                           "evaluation_mode": "RELEASE_PACKAGE",
                           "protocol": copy.deepcopy(REFERENCE), "CORE_BEHAVIOR": "PASS",
                           "claim": "SINGLE_RUN_CONFORMANCE_NOT_RELIABILITY"},
        )
    _write(directory / "acceptance-evidence.json", evidence)
    _rebind(directory)
    return directory


def _run(tmp_path, executable, mode, fixture, error=None):
    script = tmp_path / "read-only-contract-harness.ps1"
    script.write_text(HARNESS, encoding="utf-8")
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script), str(ROOT), mode, str(fixture)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    if error is None:
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "VALIDATED"
    else:
        assert result.returncode == 17, result.stdout + result.stderr
        assert result.stdout.strip() == error
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize('executable', POWERSHELLS)
@pytest.mark.parametrize('version', ['0.5.11', '0.5.12'])
def test_current_builder_accepts_exact_historical_and_doctor_engine_contracts(tmp_path, executable, version):
    directory = _bundle(tmp_path / 'packages', engine_version=version)
    evidence = _read(directory / 'acceptance-evidence.json')['foundation']
    assert len(evidence['engine_builds']['ps7']['files']) == (9 if version == '0.5.11' else 13)
    assert len(evidence['pytest']['selected_files']) == (7 if version == '0.5.11' else 9)
    _run(tmp_path, executable, 'builder', directory.parent)


@pytest.mark.parametrize('executable', POWERSHELLS)
@pytest.mark.parametrize('name', ['foundation-toml.ps1', 'vendor/tomlyn/Tomlyn.dll',
                                 'vendor/tomlyn/LICENSE.txt', 'vendor/tomlyn/provenance.json'])
@pytest.mark.parametrize('mutation', ['missing', 'tampered'])
def test_current_builder_rejects_changed_doctor_payload_with_rebound_outer_hashes(tmp_path, executable, name, mutation):
    directory = _bundle(tmp_path / 'packages', engine_version='0.5.12')
    def edit(manifest, entries):
        path = '.codex/base/foundation/0.5.12/' + name
        if mutation == 'missing': entries.pop(path)
        else: entries[path] = b'Changed doctor payload'
    _edit_zip(directory / 'codex-base-1.0.0.zip', edit)
    _rebind(directory)
    expected = 'ZIP inventory' if mutation == 'missing' else 'ZIP binding'
    _run(tmp_path, executable, 'builder', directory.parent,
         'Accepted isolated engine ' + expected + ' differs')


@pytest.mark.parametrize('executable', POWERSHELLS)
@pytest.mark.parametrize('version', ['0.5.11', '0.5.12'])
@pytest.mark.parametrize('test_file', ['tests/test_doctor_state.py', 'tests/test_doctor_toml.py'])
def test_current_builder_rejects_selected_tests_from_another_engine_contract(tmp_path, executable, version, test_file):
    directory = _bundle(tmp_path / 'packages', engine_version=version)
    def edit(evidence):
        tests = evidence['foundation']['pytest']
        if version == '0.5.12':
            tests['selected_files'].remove(test_file)
            tests['selected_files_sha256'].pop(test_file)
        else:
            tests['selected_files'].append(test_file)
            tests['selected_files_sha256'][test_file] = 'f' * 64
    _rebind(directory, evidence_edit=edit)
    _run(tmp_path, executable, 'builder', directory.parent,
         'Accepted isolated engine artifact coverage differs')


@pytest.mark.parametrize('executable', POWERSHELLS)
def test_current_builder_rejects_rebound_extra_file_in_historical_engine(tmp_path, executable):
    directory = _bundle(tmp_path / 'packages', engine_version='0.5.11')
    payload = b'Unexpected historical helper'
    _edit_zip(directory / 'codex-base-1.0.0.zip', lambda manifest, entries: entries.update({
        '.codex/base/foundation/0.5.11/foundation-toml.ps1': payload}))
    def edit(evidence):
        for row in evidence['foundation']['engine_builds'].values():
            row['files']['foundation-toml.ps1'] = _sha(payload)
    _rebind(directory, evidence_edit=edit)
    _run(tmp_path, executable, 'builder', directory.parent,
         'Accepted isolated engine ZIP binding differs')


@pytest.mark.parametrize('executable', POWERSHELLS)
@pytest.mark.parametrize('mutation,expected', [
    ('protocol', 'protocol or scope'), ('missing', 'protocol or scope'),
    ('full_claim', 'protocol or scope'), ('gui', 'protocol or scope'),
    ('model_bool', 'protocol or scope'), ('source', 'source'),
    ('shell', 'matrix'), ('scenario', 'matrix'), ('receipt', 'matrix'),
    ('not_exercised', 'matrix'), ('boolean_code', 'matrix'),
    ('other_engine', 'ZIP binding'), ('missing_file', 'ZIP inventory'),
    ('failure', 'test evidence'), ('skip', 'test evidence'), ('junit', 'test evidence'),
    ('version_bytes', 'ZIP binding'),
])
def test_current_builder_rejects_isolated_engine_mutants_with_rebound_outer_hashes(tmp_path, executable, mutation, expected):
    directory = _bundle(tmp_path / 'packages')
    evidence = _read(directory / 'acceptance-evidence.json')
    engine = evidence['foundation']
    if mutation == 'protocol': engine['acceptance_protocol'] = 'unknown'
    elif mutation == 'missing': evidence.pop('foundation')
    elif mutation == 'full_claim': engine['FOUNDATION_SYNTHETIC'] = 'PASS'
    elif mutation == 'gui': engine['scope']['gui_executed'] = True
    elif mutation == 'model_bool': engine['model_requests'] = False
    elif mutation == 'source': engine['source']['hashes'].pop('tools')
    elif mutation == 'shell': engine['engine_builds'].pop('ps51')
    elif mutation == 'scenario': engine['engine_lifecycle']['shells']['ps51']['scenario_ids'][0] = 'unrelated'
    elif mutation == 'receipt': engine['engine_lifecycle']['shells']['ps7']['receipts'] = []
    elif mutation == 'not_exercised': engine['engine_lifecycle']['shells']['ps7']['status'] = 'NOT_EXERCISED'
    elif mutation == 'boolean_code': engine['engine_builds']['ps51']['returncode'] = False
    elif mutation == 'other_engine': engine['engine_builds']['ps7']['files']['foundation.ps1'] = 'f' * 64
    elif mutation == 'missing_file':
        _edit_zip(directory / 'codex-base-1.0.0.zip', lambda manifest, entries: entries.pop(
            f'.codex/base/foundation/{manifest["foundation_engine_version"]}/shared-tools/officecli/officecli-shim.exe'))
    elif mutation == 'failure': engine['pytest']['counts']['failures'] = 1
    elif mutation == 'skip': engine['pytest']['counts']['skipped'] = 1
    elif mutation == 'junit': engine['pytest']['junit_artifact']['sha256'] = 'f' * 64
    elif mutation == 'version_bytes':
        changed = b'0.0.1\n'
        _edit_zip(directory / 'codex-base-1.0.0.zip', lambda manifest, entries: entries.update({
            f'.codex/base/foundation/{manifest["foundation_engine_version"]}/VERSION': changed}))
        for shell in ('ps7', 'ps51'): engine['engine_builds'][shell]['files']['VERSION'] = _sha(changed)
    _write(directory / 'acceptance-evidence.json', evidence)
    _rebind(directory)
    _run(tmp_path, executable, 'builder', directory.parent,
         f'Accepted isolated engine {expected} differs')


@pytest.mark.parametrize('executable', POWERSHELLS)
@pytest.mark.parametrize('mutation,expected', [
    ('absent', 'artifact coverage differs'), ('stale_text', 'artifact bytes differ'),
    ('wrong_scenario', 'receipt differs'), ('rollback', 'rollback bytes differ'),
    ('userenv', 'User environment differs'), ('selected_subset', 'artifact coverage differs'),
    ('junit_count', 'JUnit differs'), ('junit_failures', 'JUnit differs'), ('junit_parent_failures', 'JUnit differs'),
    ('unrelated_names', 'JUnit lifecycle coverage differs'), ('collection', 'JUnit identity differs'),
    ('junit_missing_modules', 'JUnit module coverage differs'),
])
def test_current_builder_rechecks_inline_results_after_rehash(tmp_path, executable, mutation, expected):
    directory = _bundle(tmp_path / 'packages')
    evidence = _read(directory / 'acceptance-evidence.json')
    foundation = evidence['foundation']
    if mutation == 'absent': evidence.pop('foundation_artifacts')
    elif mutation == 'userenv': foundation['real_user_environment_after']['PATH'] = 'b' * 64
    elif mutation == 'selected_subset': foundation['pytest']['selected_files'] = ['tests/test_engine_isolated_lifecycle.py']
    elif mutation == 'collection': foundation['pytest']['collection']['stdout'] = 'tests/other.py::unrelated\n'
    else:
        junit = mutation.startswith('junit') or mutation == 'unrelated_names'
        record = foundation['pytest']['junit_artifact'] if junit else foundation['engine_lifecycle']['shells']['ps7']['receipts'][0]
        artifact = evidence['foundation_artifacts'][record['sha256']]
        if mutation == 'stale_text': artifact['text'] += ' '
        else:
            if junit:
                suite = ET.fromstring(artifact['text'])
                if mutation == 'junit_count': suite.remove(list(suite)[0])
                elif mutation == 'junit_failures': suite.set('failures', '1')
                elif mutation == 'junit_parent_failures':
                    wrapper = ET.Element('testsuite', tests='20', failures='1', errors='0', skipped='0')
                    wrapper.append(suite)
                    suite = wrapper
                elif mutation == 'junit_missing_modules':
                    for case in list(suite):
                        if case.attrib['classname'] != 'tests.test_engine_isolated_lifecycle': suite.remove(case)
                    suite.set('tests', str(len(suite)))
                    ids = [case.attrib['classname'].replace('.', '/') + '.py::' + case.attrib['name'] for case in suite]
                    foundation['pytest']['counts']['tests'] = len(suite)
                    foundation['pytest']['collected_case_ids'] = ids
                    foundation['pytest']['collection']['stdout'] = '\n'.join(ids) + '\n'
                else:
                    ids = []
                    for index, case in enumerate(suite):
                        case.set('name', f'unrelated_{index}')
                        ids.append(case.attrib['classname'].replace('.', '/') + '.py::' + case.attrib['name'])
                    foundation['pytest']['collected_case_ids'] = ids
                    foundation['pytest']['collection']['stdout'] = '\n'.join(ids) + '\n'
                text = ET.tostring(suite, encoding='unicode')
            else:
                receipt = json.loads(artifact['text'])
                if mutation == 'wrong_scenario': receipt['scenario_id'] = 'unrelated'
                else: receipt['after']['files']['.codex/AGENTS.md'] = 'b' * 64
                text = _bytes(receipt).decode()
            evidence['foundation_artifacts'].pop(record['sha256'])
            raw = text.encode(); digest = _sha(raw)
            record.update(sha256=digest, bytes=len(raw))
            evidence['foundation_artifacts'][digest] = {'sha256': digest, 'bytes': len(raw), 'text': text}
            if junit: foundation['pytest']['junit_sha256'] = digest
    _write(directory / 'acceptance-evidence.json', evidence)
    _rebind(directory)
    _run(tmp_path, executable, 'builder', directory.parent, 'Accepted isolated engine ' + expected)


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("mode", ["foundation", "foundation-bound", "builder"])
@pytest.mark.parametrize("current", [False, True])
def test_real_validators_accept_historical_and_current_fixtures(tmp_path, executable, mode, current):
    assert _sha(CORE_BYTES) == REFERENCE["sha256"]
    directory = _bundle(tmp_path / "packages", current=current)
    fixture = directory / "codex-base-1.0.0.zip" if mode.startswith("foundation") else directory.parent
    _run(tmp_path, executable, mode, fixture)


FOUNDATION_ERRORS = {
    "unknown": "Core behavior contract is unknown or changed",
    "changed_digest": "Core behavior contract is unknown or changed",
    "changed_suite": "Core behavior contract is unknown or changed",
    "case_property": "Core behavior contract is unknown or changed",
    "extra": "core behavior contract properties differ",
    "null": "core behavior contract properties differ",
    "missing_payload": "Core contract reference and payload must occur together",
    "missing_reference": "Core contract reference and payload must occur together",
    "tampered_payload": "Embedded core behavior contract bytes differ",
    "duplicate": f"Duplicate ZIP path: {CORE_PATH}",
    "case_duplicate": f"Duplicate ZIP path: {CORE_PATH.upper()}",
    "missing_release_reference": "Release core behavior contract binding differs",
    "unknown_release": "Core behavior contract is unknown or changed",
    "missing_release": "Release manifest path is invalid",
    "legacy_with_core_release": "Release core behavior contract binding differs",
}


def _mutate_package(directory, mutant):
    package = directory / "codex-base-1.0.0.zip"
    def edit(manifest, entries):
        if mutant == "unknown": manifest["core_behavior_contract"]["id"] = "unknown-v9"
        elif mutant == "changed_digest": manifest["core_behavior_contract"]["sha256"] = "a" * 64
        elif mutant == "changed_suite": manifest["core_behavior_contract"]["suite_sha256"] = "a" * 64
        elif mutant == "case_property": manifest["core_behavior_contract"]["ID"] = manifest["core_behavior_contract"].pop("id")
        elif mutant == "extra": manifest["core_behavior_contract"]["unexpected"] = True
        elif mutant == "null": manifest["core_behavior_contract"] = None
        elif mutant == "missing_payload": entries.pop(CORE_PATH)
        elif mutant == "missing_reference": manifest.pop("core_behavior_contract")
        elif mutant == "tampered_payload": entries[CORE_PATH] += b" "
        elif mutant == "changed_client": manifest["client"]["supported_version"] = "9.9.9"
    _edit_zip(package, edit)
    if mutant in ("duplicate", "case_duplicate"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(package, "a") as archive:
                archive.writestr(CORE_PATH if mutant == "duplicate" else CORE_PATH.upper(), CORE_BYTES)
    release = _read(directory / "release-manifest.json")
    if mutant == "missing_release_reference": release.pop("core_behavior_contract")
    elif mutant == "unknown_release": release["core_behavior_contract"]["id"] = "unknown-v9"
    elif mutant == "legacy_with_core_release": release["core_behavior_contract"] = copy.deepcopy(REFERENCE)
    _write(directory / "release-manifest.json", release)
    _rebind(directory)


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("mutant,error", FOUNDATION_ERRORS.items())
def test_foundation_rejects_rebound_zip_mutants(tmp_path, executable, mutant, error):
    directory = _bundle(tmp_path / "packages", current=mutant != "legacy_with_core_release")
    _mutate_package(directory, mutant)
    if mutant == "missing_release":
        # Core-only package has neither baseline nor shared_tools to trigger the old gate.
        (directory / "release-manifest.json").unlink()
    mode = "foundation-bound" if mutant == "legacy_with_core_release" else "foundation"
    _run(tmp_path, executable, mode, directory / "codex-base-1.0.0.zip", error)


BUILDER_ERRORS = {
    "unknown": "Accepted core behavior contract is unknown or changed",
    "changed_digest": "Accepted core behavior contract is unknown or changed",
    "changed_suite": "Accepted core behavior contract is unknown or changed",
    "case_property": "Accepted core behavior contract is unknown or changed",
    "extra": "Accepted core behavior contract structure differs",
    "null": "Accepted core behavior contract structure differs",
    "missing_payload": f"Accepted package entry is missing, duplicated or outside limits: {CORE_PATH}",
    "missing_reference": "Accepted core behavior contract structure differs",
    "tampered_payload": "Accepted embedded core behavior contract bytes differ",
    "duplicate": "Accepted package contains duplicate ZIP paths",
    "case_duplicate": "Accepted package contains duplicate ZIP paths",
    "missing_release_reference": "Accepted core behavior contract structure differs",
    "unknown_release": "Accepted core behavior contract is unknown or changed",
    "changed_client": "Accepted core package target, version or client differs",
}


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("mutant,error", BUILDER_ERRORS.items())
def test_builder_rejects_rebound_zip_mutants(tmp_path, executable, mutant, error):
    directory = _bundle(tmp_path / "packages")
    _mutate_package(directory, mutant)
    _run(tmp_path, executable, "builder", directory.parent, error)


EVIDENCE_MUTANTS = [
    ("CORE_BEHAVIOR", None), ("CORE_BEHAVIOR", "FAIL"), ("CORE_BEHAVIOR", "NOT_EXERCISED"),
    ("acceptance_protocol", None), ("acceptance_protocol", "unknown-v9"),
    ("MATCHED_AB", "PASS"), ("matched_ab_not_required_reason", ""),
    ("matched_ab_not_required_reason", "invented"), ("CODEX_CANARY", "FAIL"),
]


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("field,value", EVIDENCE_MUTANTS)
def test_builder_rejects_rehashed_protocol_or_verdict_mutants(tmp_path, executable, field, value):
    directory = _bundle(tmp_path / "packages")
    def edit(evidence):
        if value is None: evidence.pop(field)
        else: evidence[field] = value
    _rebind(directory, evidence_edit=edit)
    _run(tmp_path, executable, "builder", directory.parent, "Accepted professional core protocol or verdict differs")


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("strip_package_reference", [False, True])
def test_builder_cannot_downgrade_current_zip_by_stripping_outer_markers(tmp_path, executable, strip_package_reference):
    directory = _bundle(tmp_path / "packages")
    if strip_package_reference:
        _edit_zip(directory / "codex-base-1.0.0.zip", lambda manifest, entries: manifest.pop("core_behavior_contract"))
    release = _read(directory / "release-manifest.json")
    release.pop("core_behavior_contract")
    _write(directory / "release-manifest.json", release)
    def strip(evidence):
        for key in ("acceptance_protocol", "CORE_BEHAVIOR", "core_behavior_evidence", "matched_ab_not_required_reason"):
            evidence.pop(key)
    _rebind(directory, evidence_edit=strip)
    _run(tmp_path, executable, "builder", directory.parent, "Accepted core behavior contract structure differs")


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("binding_name", ["release_binding", "core_behavior_evidence"])
def test_builder_rejects_rehashed_binding_changes(tmp_path, executable, binding_name):
    directory = _bundle(tmp_path / "packages")
    def edit(evidence):
        binding = evidence["release_binding"] if binding_name == "release_binding" else evidence["core_behavior_evidence"]["release_binding"]
        binding["asset"]["sha256"] = "9" * 64
    _rebind(directory, evidence_edit=edit)
    _run(tmp_path, executable, "builder", directory.parent, "Acceptance release binding differs: asset")


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("mutant,error", [
    ("missing_nested", "Accepted core behavior evidence envelope differs"),
    ("nested_fail", "Accepted core behavior evidence envelope differs"),
    ("unknown_claim", "Accepted core behavior evidence envelope differs"),
    ("missing_mode", "Accepted core behavior evidence envelope differs"),
    ("snapshot_mode", "Accepted core behavior evidence envelope differs"),
    ("unknown_mode", "Accepted core behavior evidence envelope differs"),
    ("unknown_protocol", "Accepted core behavior contract is unknown or changed"),
    ("old_colliding_wire", "Package acceptance evidence JSON is invalid for target: codex"),
])
def test_current_envelope_and_noncolliding_wire_are_required(tmp_path, executable, mutant, error):
    directory = _bundle(tmp_path / "packages")
    def edit(evidence):
        if mutant == "missing_nested": evidence.pop("core_behavior_evidence")
        elif mutant == "nested_fail": evidence["core_behavior_evidence"]["CORE_BEHAVIOR"] = "FAIL"
        elif mutant == "unknown_claim": evidence["core_behavior_evidence"]["claim"] = "RELIABILITY"
        elif mutant == "missing_mode": evidence["core_behavior_evidence"].pop("evaluation_mode")
        elif mutant == "snapshot_mode": evidence["core_behavior_evidence"]["evaluation_mode"] = "SOURCE_SNAPSHOT"
        elif mutant == "unknown_mode": evidence["core_behavior_evidence"]["evaluation_mode"] = "UNKNOWN"
        elif mutant == "unknown_protocol": evidence["core_behavior_evidence"]["protocol"]["id"] = "future-v2"
        elif mutant == "old_colliding_wire": evidence["core_behavior"] = evidence.pop("core_behavior_evidence")
    _rebind(directory, evidence_edit=edit)
    _run(tmp_path, executable, "builder", directory.parent, error)


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("change,error", [
    ("none", None),
    ("missing", "Current acceptance release binding properties differ"),
    ("extra", "Current acceptance release binding properties differ"),
    ("changed", "Acceptance release binding differs: session_tools_asset"),
])
def test_current_session_tools_binding_is_exact(tmp_path, executable, change, error):
    directory = _bundle(tmp_path / "packages")
    release = _read(directory / "release-manifest.json")
    release["session_tools_asset"] = {
        "name": "session-tools-1.0.0.zip", "sha256": "a" * 64, "bytes": 123,
        "manifest_sha256": "b" * 64, "tool_count": 1, "file_count": 1,
    }
    _write(directory / "release-manifest.json", release)
    def edit(evidence):
        binding = evidence["core_behavior_evidence"]["release_binding"]
        if change == "missing": binding.pop("session_tools_asset")
        elif change == "extra": binding["unknown"] = True
        elif change == "changed": binding["session_tools_asset"]["sha256"] = "c" * 64
    _rebind(directory, evidence_edit=edit)
    _run(tmp_path, executable, "builder", directory.parent, error)


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("target", ["claude", "opencode"])
def test_other_historical_targets_keep_their_accepted_route(tmp_path, executable, target):
    directory = _accepted_package(tmp_path / "packages", target)
    _run(tmp_path, executable, "builder", directory.parent)


@pytest.mark.parametrize("executable", POWERSHELLS)
@pytest.mark.parametrize("client_id,version,error", [
    ("codex-cli", "0.153.1", None), ("codex-cli", "2.0.0", None),
    ("other-client", "0.153.1", "Supported client is codex-cli 0.146.0-alpha.3.1"),
    ("codex-cli", "0.153", "Client version evidence is invalid"),
])
def test_external_client_policy_is_preserved(tmp_path, executable, client_id, version, error):
    fixture = tmp_path / "client.json"
    _write(fixture, {"expected": {"id": "codex-cli", "supported_version": "0.146.0-alpha.3.1"},
                     "id": client_id, "version": version})
    _run(tmp_path, executable, "client", fixture, error)
