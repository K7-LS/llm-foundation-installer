"""Explicit engine-only acceptance. Never calls the GUI/full-installer runner."""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("historical_acceptance", ROOT / "tools/run-acceptance.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
sys.path.insert(0, str(ROOT / "tests"))
from test_engine_isolated_lifecycle import SCENARIOS, _real_environment

SELECTED_FILES = (
    "tests/test_foundation.py", "tests/test_foundation_shared_tools.py",
    "tests/test_foundation_release.py", "tests/test_acceptance_runner.py",
    "tests/test_professional_core_compat.py", "tests/test_engine_isolated_lifecycle.py",
    "tests/test_engine_acceptance_runner.py",
)
ENGINE_FILES = (
    "VERSION", "engine-manifest.json", "foundation.ps1", "shared-tools.lock.json",
    "shared-tools/officecli/officecli.exe", "shared-tools/officecli/officecli-shim.exe",
    "shared-tools/officecli/officecli-command-policy.json",
    "shared-tools/officecli/k7-officecli-pdf.exe",
    "shared-tools/officecli/officecli_csv_batch.py",
)
PROTOCOL = "foundation-engine-isolated-v1"
HISTORICAL_REASON = "The historical runner includes the full installer/GUI suite; this protocol is explicitly engine-only."


def _artifact(path, work):
    return {"path": path.relative_to(work).as_posix(), "sha256": legacy._sha256(path),
            "bytes": path.stat().st_size}


def _environment(work, officecli, compiler):
    environment = os.environ.copy()
    # Fixed selection cannot be broadened or filtered by caller pytest options.
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "PYTHONOPTIMIZE"):
        environment.pop(key, None)
    for key in list(environment):
        if key.startswith("FOUNDATION_"):
            del environment[key]
    directories = {name: work / name for name in ("temp", "home", "appdata", "localappdata", "cache")}
    for path in directories.values():
        path.mkdir()
    environment.update(
        K7_OFFICECLI_BINARY_PATH=str(officecli), K7_ROSLYN_COMPILER_PATH=str(compiler),
        K7_BUILD_CACHE=str(directories["cache"]), TEMP=str(directories["temp"]),
        TMP=str(directories["temp"]), USERPROFILE=str(directories["home"]),
        APPDATA=str(directories["appdata"]), LOCALAPPDATA=str(directories["localappdata"]),
        PSModuleAnalysisCachePath=str(directories["temp"] / "ps-cache"),
        PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        K7_ENGINE_ACCEPTANCE_WORKSPACE=str(work),
        K7_ENGINE_LIFECYCLE_RECEIPTS=str(work / "receipts"),
    )
    return environment


def _run(command, environment, source_root=ROOT):
    result = subprocess.run(command, cwd=source_root, env=environment, capture_output=True,
                            text=True, encoding="utf-8", timeout=1800)
    return {"status": "PASS" if result.returncode == 0 else "FAIL", "command": command,
            "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def _export_commit(work):
    """Build committed bytes, independent of working-tree line-end conversions."""
    payload = subprocess.run(['git', 'archive', '--format=zip', 'HEAD'], cwd=ROOT,
                             check=True, capture_output=True).stdout
    destination = work / 'source-export'
    destination.mkdir()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set()
        for entry in archive.infolist():
            path = destination / entry.filename
            if (entry.filename.casefold() in names or '\\' in entry.filename or
                not path.resolve().is_relative_to(destination) or
                (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('unsafe committed archive path')
            names.add(entry.filename.casefold())
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(archive.read(entry))
    return destination


def _receipt_rows(work, builds):
    shells = {}
    for shell in ("ps7", "ps51"):
        records = []
        for scenario in SCENARIOS:
            path = work / "receipts" / f"{shell}-{scenario}.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            assert value["status"] == "PASS" and value["scenario_id"] == scenario and value["shell"] == shell
            assert value["engine_sha256"] == builds[shell]["files"]["foundation.ps1"]
            assert value["engine_manifest_sha256"] == builds[shell]["files"]["engine-manifest.json"]
            assert value["commands"] and all(
                row["command"] and type(row["returncode"]) is int and
                isinstance(row["stdout"], str) and isinstance(row["stderr"], str)
                for row in value["commands"])
            assert value["real_user_environment_before"] == value["real_user_environment_after"]
            assert value["before"] == value["after"]
            records.append(_artifact(path, work))
        shells[shell] = {
            "status": "PASS", "engine_sha256": builds[shell]["files"]["foundation.ps1"],
            "engine_manifest_sha256": builds[shell]["files"]["engine-manifest.json"],
            "scenario_ids": list(SCENARIOS), "receipts": records,
            "user_environment_unchanged": True, "installed_files_verified": True,
            "rollback_byte_identical": True,
        }
    assert len(list((work / "receipts").glob("*.json"))) == 2 * len(SCENARIOS)
    return shells


def main(argv=None):
    if sys.flags.optimize != 0:
        raise RuntimeError('engine acceptance requires unoptimized Python')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True, help="existing authorized workspace")
    parser.add_argument("--work", type=Path, required=True, help="new non-existing child directory; never deleted")
    parser.add_argument("--officecli", type=Path, required=True, help="verified local dependency inside workspace")
    parser.add_argument("--compiler", type=Path, required=True, help="verified local csc.exe inside workspace")
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve(strict=True)
    work = args.work.resolve()
    if work == workspace or not work.is_relative_to(workspace) or work.exists():
        raise ValueError("work must be a new child of the authorized workspace")
    dependencies = [args.officecli.resolve(strict=True), args.compiler.resolve(strict=True)]
    if not all(path.is_file() and path.is_relative_to(workspace) for path in dependencies):
        raise ValueError("dependencies must be local files inside the authorized workspace")
    source = legacy._git_identity(ROOT)
    source["hashes"] = legacy._source_hashes(ROOT)
    work.mkdir()
    environment = _environment(work, *dependencies)
    source_root = _export_commit(work)
    before = _real_environment()
    evidence = {
        "schema_version": 1, "acceptance_protocol": PROTOCOL,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "engine_version": (ROOT / "VERSION").read_text().strip(),
        "installer_version": (ROOT / "APP_VERSION").read_text().strip(),
        "source": source, "model_requests": 0, "FOUNDATION_ENGINE_ACCEPTANCE": "FAIL",
        "build_dependencies": {name: {'sha256': legacy._sha256(path), 'bytes': path.stat().st_size}
                               for name, path in zip(('officecli', 'compiler'), dependencies)},
        "FOUNDATION_SYNTHETIC": "NOT_RUN", "INSTALLER_ACCEPTANCE": "NOT_RUN",
        "historical_not_run_reason": HISTORICAL_REASON,
        "scope": {"fake_homes_only": True, "real_consumer_executed": False,
                  "gui_executed": False, "model_requests": 0, "workspace": str(work)},
        "limitations": ["No GUI, real consumer, model execution, publication or release attestation.",
                        "PS5.1 synthetic homes use short paths; long-path support is not asserted.",
                        "Shared-tool locking coordinates this engine; older concurrent writers are not certified."],
    }
    try:
        builds, syntax = {}, {}
        for shell, name in (("ps7", "pwsh"), ("ps51", "powershell.exe")):
            executable = shutil.which(name)
            if not executable:
                raise RuntimeError(f"required shell is missing: {shell}")
            prefix = [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
            syntax[shell] = _run(prefix + [str(source_root / "tools/check-ps-syntax.ps1"), "-Root", str(source_root)], environment, source_root)
            out = work / f"engine-{shell}"
            builds[shell] = _run(prefix + [str(source_root / "tools/build-engine.ps1"), "-OutputRoot", str(out)], environment, source_root)
            builds[shell]["files"] = legacy._tree(out)
            assert set(builds[shell]["files"]) == set(ENGINE_FILES)
            assert syntax[shell]["status"] == builds[shell]["status"] == "PASS"
            environment[f"K7_ENGINE_{shell.upper()}_ROOT"] = str(out)
        evidence.update(powershell_syntax=syntax, engine_builds=builds)
        assert builds["ps7"]["files"] == builds["ps51"]["files"]
        evidence["deterministic_engine_bundle"] = "PASS"
        junit = work / "pytest.xml"
        collection = _run([sys.executable, '-X', 'utf8', '-m', 'pytest', '--collect-only', '-q',
                           *SELECTED_FILES], environment, source_root)
        assert collection['status'] == 'PASS'
        case_ids = [line.strip() for line in collection['stdout'].splitlines()
                    if line.startswith('tests/') and '::' in line]
        assert case_ids and len(case_ids) == len(set(case_ids))
        assert all(case.split('::', 1)[0] in SELECTED_FILES for case in case_ids)
        tests = _run([sys.executable, "-X", "utf8", "-m", "pytest", "-q", "--tb=short",
                      *SELECTED_FILES, f"--junitxml={junit}", f"--basetemp={work / 't'}"], environment, source_root)
        evidence["pytest"] = {
            **tests, "selected_files": list(SELECTED_FILES),
            "selected_files_sha256": {p: legacy._sha256(source_root / p) for p in SELECTED_FILES},
            "collection": collection, "collected_case_ids": case_ids,
            "junit_sha256": legacy._sha256(junit), "junit_artifact": _artifact(junit, work),
            "counts": legacy._junit_counts(junit),
        }
        assert tests["status"] == "PASS"
        counts = evidence["pytest"]["counts"]
        executed_ids = [c.attrib['classname'].replace('.', '/') + '.py::' + c.attrib['name']
                        for c in ET.parse(junit).iter('testcase')]
        assert len(case_ids) == counts['tests'] and set(case_ids) == set(executed_ids)
        assert counts["failures"] == counts["errors"] == counts['skipped'] == 0 and counts["ps7_cases"] > 0 and counts["ps51_cases"] > 0
        evidence["engine_lifecycle"] = {
            "status": "PASS", "evaluation_mode": "SYNTHETIC_HOME",
            "required_scenarios": list(SCENARIOS), "shells": _receipt_rows(work, builds),
        }
        assert legacy._git_identity(ROOT) == {k: v for k, v in source.items() if k != "hashes"}
        assert _real_environment() == before
        evidence["FOUNDATION_ENGINE_ACCEPTANCE"] = "PASS"
    except Exception as error:
        evidence["failure"] = f"{type(error).__name__}: {error}"
    finally:
        after = _real_environment()
        evidence["real_user_environment_before"] = before
        evidence["real_user_environment_after"] = after
        if before != after:
            evidence["FOUNDATION_ENGINE_ACCEPTANCE"] = "FAIL"
            evidence["failure"] = "Real User environment changed"
        evidence["evidence_body_sha256"] = legacy.evidence_body_sha256(evidence)
        (work / "foundation-engine-acceptance.json").write_bytes(legacy._json_bytes(evidence))
    print(json.dumps({"status": evidence["FOUNDATION_ENGINE_ACCEPTANCE"], "work": str(work),
                      "failure": evidence.get("failure")}))
    return 0 if evidence["FOUNDATION_ENGINE_ACCEPTANCE"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
