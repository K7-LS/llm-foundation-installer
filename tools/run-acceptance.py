from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _git_identity(root: Path) -> dict[str, str]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    if status.strip():
        raise RuntimeError(
            "acceptance requires a clean Git worktree; commit or remove all changes"
        )
    values = {}
    for key, revision in (("commit", "HEAD"), ("tree", "HEAD^{tree}")):
        values[key] = subprocess.run(
            ["git", "rev-parse", revision],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
    values["repository"] = (
        "https://github.com/daniileliseev1337/llm-foundation-installer"
    )
    return values


def _source_hashes(root: Path) -> dict[str, str]:
    tracked_result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    tracked = [
        value.decode("utf-8")
        for value in tracked_result.stdout.split(b"\0")
        if value
    ]
    result = {}
    for relative in (
        "VERSION",
        "APP_VERSION",
        "client-sources.lock.json",
        "src",
        "tests",
        "tools",
    ):
        selected = [
            value
            for value in tracked
            if value == relative or value.startswith(relative + "/")
        ]
        if not selected:
            raise RuntimeError(f"tracked source component is empty: {relative}")
        digest = hashlib.sha256()
        for file_path in selected:
            blob = subprocess.run(
                ["git", "show", f"HEAD:{file_path}"],
                cwd=root,
                check=True,
                capture_output=True,
            ).stdout
            file_hash = hashlib.sha256(blob).hexdigest()
            digest.update(file_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_hash.encode("ascii"))
            digest.update(b"\n")
        result[relative] = digest.hexdigest()
    return result


def _portable_command(command: list[str], root: Path) -> list[str]:
    root_text = str(root)
    portable = []
    for value in command:
        normalized = value.replace(root_text, ".").replace("\\", "/")
        candidate = Path(value)
        if candidate.is_absolute() and not normalized.startswith("./"):
            normalized = candidate.name
        portable.append(normalized)
    return portable


def _run(
    command: list[str],
    cwd: Path,
    environment: dict[str, str] | None = None,
) -> dict[str, object]:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        env=environment,
    )
    return {
        "command": _portable_command(command, cwd),
        "returncode": result.returncode,
        "stdout": result.stdout.decode("utf-8", errors="replace"),
        "stderr": result.stderr.decode("utf-8", errors="replace"),
    }


def _pytest_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _remove_tree(path: Path) -> None:
    def clear_read_only(function, child, _error) -> None:
        os.chmod(child, stat.S_IWRITE)
        function(child)

    if path.exists():
        try:
            shutil.rmtree(path, onexc=clear_read_only)
        except TypeError:
            shutil.rmtree(path, onerror=clear_read_only)


def _junit_counts(path: Path) -> dict[str, object]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    totals = {
        key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    totals["passed"] = (
        totals["tests"]
        - totals["failures"]
        - totals["errors"]
        - totals["skipped"]
    )
    cases = list(root.iter("testcase"))
    totals["ps7_cases"] = sum(
        "pwsh.exe" in case.attrib.get("name", "").lower()
        for case in cases
    )
    totals["ps51_cases"] = sum(
        "windowspowershell" in case.attrib.get("name", "").lower()
        for case in cases
    )
    totals["shared_cases"] = (
        totals["tests"] - totals["ps7_cases"] - totals["ps51_cases"]
    )
    return totals


def _pytest_command(
    work: Path,
    pytest_home: Path | None = None,
) -> list[str]:
    if pytest_home is None:
        pytest_home = work / "pytest-home"
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        f"--junitxml={work / 'pytest.xml'}",
        f"--basetemp={pytest_home}",
    ]


def _create_pytest_home() -> Path:
    return Path(tempfile.mkdtemp(prefix="k7-acceptance-"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path("dist/foundation-acceptance.json"),
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    source = _git_identity(root)
    source["hashes"] = _source_hashes(root)
    work = root / ".work" / "acceptance"
    _remove_tree(work)
    work.mkdir(parents=True)
    powershells = {
        "ps7": shutil.which("pwsh"),
        "ps51": shutil.which("powershell.exe"),
    }

    syntax: dict[str, object] = {}
    builds: dict[str, object] = {}
    for name, executable in powershells.items():
        if not executable:
            syntax[name] = {"status": "NOT_RUN", "reason": "executable missing"}
            builds[name] = {"status": "NOT_RUN", "reason": "executable missing"}
            continue
        syntax_result = _run(
            [
                executable,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "tools" / "check-ps-syntax.ps1"),
                "-Root",
                str(root),
            ],
            root,
        )
        syntax[name] = {
            "status": "PASS" if syntax_result["returncode"] == 0 else "NOT_PASS",
            **syntax_result,
        }
        output = work / f"engine-{name}"
        build_result = _run(
            [
                executable,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "tools" / "build-engine.ps1"),
                "-OutputRoot",
                str(output),
            ],
            root,
        )
        builds[name] = {
            "status": "PASS" if build_result["returncode"] == 0 else "NOT_PASS",
            "files": _tree(output) if output.is_dir() else {},
            **build_result,
        }

    junit = work / "pytest.xml"
    pytest_home = _create_pytest_home()
    try:
        tests = _run(
            _pytest_command(work, pytest_home),
            root,
            environment=_pytest_environment(),
        )
    finally:
        _remove_tree(pytest_home)
    counts = _junit_counts(junit) if junit.is_file() else {}
    deterministic = (
        bool(builds.get("ps7", {}).get("files"))
        and builds.get("ps7", {}).get("files")
        == builds.get("ps51", {}).get("files")
    )
    passed = (
        tests["returncode"] == 0
        and all(value.get("status") == "PASS" for value in syntax.values())
        and all(value.get("status") == "PASS" for value in builds.values())
        and deterministic
        and counts.get("ps7_cases", 0) > 0
        and counts.get("ps51_cases", 0) > 0
    )
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    installer_version = (
        (root / "APP_VERSION").read_text(encoding="utf-8").strip()
    )
    evidence = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "engine_version": version,
        "installer_version": installer_version,
        "source": source,
        "model_requests": 0,
        "FOUNDATION_SYNTHETIC": "PASS" if passed else "NOT_PASS",
        "powershell_syntax": syntax,
        "engine_builds": builds,
        "deterministic_engine_bundle": "PASS" if deterministic else "NOT_PASS",
        "pytest": {
            "status": "PASS" if tests["returncode"] == 0 else "NOT_PASS",
            "counts": counts,
            "stdout": tests["stdout"],
            "stderr": tests["stderr"],
        },
        "scope": [
            "fake-home only",
            "PowerShell 7 and Windows PowerShell 5.1",
            "offline engine with no network implementation",
            "plan/install/doctor/inventory/rollback",
            "corrupt ZIP, traversal, reparse, interruption and downgrade",
            "target traversal, managed-surface closure and engine compatibility",
            "hash-bound snapshot preflight and rollback crash recovery",
            "late rollback crash recovery driven by the original journal",
            "Windows case-folded preserved-path enforcement",
            "exclusive destructive-operation lock with stale-file recovery",
            "final lock-entry reparse rejection",
            "protected data preservation and exact rollback",
        ],
        "limitations": [
            "No live user home was modified.",
            "No target canary or stable release is authorized by this verdict.",
        ],
    }
    evidence["evidence_body_sha256"] = evidence_body_sha256(evidence)
    destination = (root / args.evidence).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_json_bytes(evidence))
    print(destination)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
