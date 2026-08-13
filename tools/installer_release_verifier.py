from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import installer_release  # noqa: E402
import pilot_release  # noqa: E402


REPOSITORY = "daniileliseev1337/llm-foundation-installer"
TAG = installer_release.TAG
EXPECTED_VERDICTS = pilot_release.EXPECTED_STABLE_VERDICTS


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{path.name} is missing or unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _record(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _attestation_digest(payload: bytes, label: str) -> str:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} did not return JSON") from error
    if not isinstance(value, (dict, list)):
        raise ValueError(f"{label} returned an invalid JSON value")
    return hashlib.sha256(payload).hexdigest()


def _local_assets(stable_root: Path) -> list[Path]:
    if not stable_root.is_dir() or stable_root.is_symlink():
        raise ValueError("stable release root must be a real directory")
    children = sorted(stable_root.iterdir(), key=lambda item: item.name)
    if any(not child.is_file() or child.is_symlink() for child in children):
        raise ValueError("stable release must contain regular top-level files")
    if not children:
        raise ValueError("stable release is empty")
    return children


def _expected_sums(paths: list[Path]) -> str:
    lines = [
        f"{_record(path)['sha256']}  {path.name}"
        for path in paths
        if path.name != "SHA256SUMS"
    ]
    return "\n".join(lines) + "\n"


def _validate_stable_root(
    stable_root: Path,
) -> tuple[dict[str, Any], list[Path]]:
    paths = _local_assets(stable_root)
    by_name = {path.name: path for path in paths}
    required = {
        *installer_release.PRODUCT_FILES.values(),
        installer_release.RUNTIME_FILE,
        "release-manifest.json",
        "acceptance-evidence.json",
        "pilot-acceptance.json",
        "hub-canary-evidence.json",
        "bundle-manifest.json",
        "components.lock.json",
        "ИНСТРУКЦИЯ-СОТРУДНИКУ.md",
        "SHA256SUMS",
    }
    missing = sorted(required.difference(by_name))
    if missing:
        raise ValueError(
            "stable release artifact inventory is incomplete: "
            + ", ".join(missing)
        )
    manifest = _load_object(by_name["release-manifest.json"])
    artifacts = manifest.get("artifacts")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("app_id") != "k7-ai-employee-edition"
        or manifest.get("edition_id") != "Employee"
        or manifest.get("version") != installer_release.VERSION
        or manifest.get("tag") != TAG
        or manifest.get("channel") != "stable"
        or manifest.get("distribution_mode") != "InternalUnsigned"
        or manifest.get("verdicts") != EXPECTED_VERDICTS
        or manifest.get("evidence_body_sha256")
        != evidence_body_sha256(manifest)
        or not isinstance(artifacts, dict)
    ):
        raise ValueError("stable Employee release manifest is invalid")
    expected_artifact_names = set(by_name).difference(
        {"release-manifest.json", "SHA256SUMS"}
    )
    if set(artifacts) != expected_artifact_names:
        raise ValueError("stable release artifact inventory differs")
    for name, record in artifacts.items():
        if not isinstance(record, dict) or _record(by_name[name]) != record:
            raise ValueError(f"stable release artifact differs: {name}")
    expected_products = {
        product: _record(by_name[filename])
        for product, filename in installer_release.PRODUCT_FILES.items()
    }
    if manifest.get("products") != expected_products:
        raise ValueError("stable release product binding differs")
    if manifest.get("runtime") != _record(
        by_name[installer_release.RUNTIME_FILE]
    ):
        raise ValueError("stable release runtime binding differs")
    if by_name["SHA256SUMS"].read_text(
        encoding="utf-8"
    ) != _expected_sums(paths):
        raise ValueError("stable release SHA256SUMS differs")
    return manifest, paths


def _validate_remote_assets(
    release_api: dict[str, Any],
    local_paths: list[Path],
) -> None:
    rows = release_api.get("assets")
    if not isinstance(rows, list):
        raise ValueError("GitHub release asset inventory is missing")
    remote: dict[str, tuple[int, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("GitHub release asset inventory is invalid")
        name = row.get("name")
        size = row.get("size")
        digest = row.get("digest")
        if (
            not isinstance(name, str)
            or not name
            or name in remote
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not isinstance(digest, str)
            or not digest.startswith("sha256:")
        ):
            raise ValueError("GitHub release asset inventory is invalid")
        remote[name] = (size, digest.removeprefix("sha256:").lower())
    local = {
        path.name: (
            path.stat().st_size,
            str(_record(path)["sha256"]).lower(),
        )
        for path in local_paths
    }
    if remote != local:
        raise ValueError("GitHub release asset inventory differs")


def build_release_verification(
    *,
    stable_root: Path,
    release_api: dict[str, Any],
    release_attestation_output: bytes,
    asset_attestation_outputs: dict[str, bytes],
    gh_version: str,
) -> dict[str, Any]:
    """Bind one immutable GitHub release to every accepted local asset."""

    stable_root = stable_root.resolve()
    manifest, local_paths = _validate_stable_root(stable_root)
    if (
        release_api.get("tag_name") != TAG
        or release_api.get("draft") is not False
        or release_api.get("prerelease") is not False
        or release_api.get("immutable") is not True
    ):
        raise ValueError("GitHub release state is not immutable stable")
    _validate_remote_assets(release_api, local_paths)
    expected_names = {path.name for path in local_paths}
    if set(asset_attestation_outputs) != expected_names:
        raise ValueError("asset attestation inventory differs")
    if not gh_version.startswith("gh version "):
        raise ValueError("GitHub CLI version evidence is invalid")
    assets = []
    output_digests: dict[str, str] = {}
    for path in local_paths:
        payload = asset_attestation_outputs[path.name]
        output_digests[path.name] = _attestation_digest(
            payload,
            f"gh release verify-asset {path.name}",
        )
        assets.append(
            {
                "name": path.name,
                **_record(path),
                "attestation": "PASS",
            }
        )
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "repository": REPOSITORY,
        "tag": manifest["tag"],
        "release_state": {
            "draft": False,
            "prerelease": False,
            "immutable": True,
        },
        "release_attestation": "PASS",
        "assets": assets,
        "verification_commands": {
            "gh_version": gh_version.splitlines()[0],
            "release_output_sha256": _attestation_digest(
                release_attestation_output,
                "gh release verify",
            ),
            "asset_output_sha256": output_digests,
        },
        "privacy": {
            "raw_attestation_output_included": False,
            "credentials_included": False,
            "personal_data_included": False,
        },
        "RELEASE_INTEGRITY": "PASS",
    }
    evidence["evidence_body_sha256"] = evidence_body_sha256(evidence)
    return evidence


def _run(command: list[str]) -> bytes:
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace")[-2000:]
        )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify immutable employee-v0.4.0 and every published asset."
        )
    )
    parser.add_argument("--stable-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--gh", default="gh")
    arguments = parser.parse_args()
    stable_root = arguments.stable_root.resolve()
    output = arguments.output.resolve()
    if output.exists():
        raise SystemExit("release verification exists; refusing overwrite")
    try:
        output.relative_to(stable_root)
    except ValueError:
        pass
    else:
        raise SystemExit(
            "release verification output must be outside stable release root"
        )
    _, paths = _validate_stable_root(stable_root)
    gh = arguments.gh
    gh_version = _run([gh, "--version"]).decode(
        "utf-8", errors="replace"
    ).strip()
    release_api = json.loads(
        _run(
            [
                gh,
                "api",
                f"repos/{REPOSITORY}/releases/tags/{TAG}",
            ]
        )
    )
    release_output = _run(
        [
            gh,
            "release",
            "verify",
            TAG,
            "-R",
            REPOSITORY,
            "--format",
            "json",
        ]
    )
    asset_outputs = {
        path.name: _run(
            [
                gh,
                "release",
                "verify-asset",
                TAG,
                str(path),
                "-R",
                REPOSITORY,
                "--format",
                "json",
            ]
        )
        for path in paths
    }
    evidence = build_release_verification(
        stable_root=stable_root,
        release_api=release_api,
        release_attestation_output=release_output,
        asset_attestation_outputs=asset_outputs,
        gh_version=gh_version,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_bytes(_json_bytes(evidence))
    os.replace(temporary, output)
    print(
        json.dumps(
            {
                "RELEASE_INTEGRITY": "PASS",
                "asset_count": len(evidence["assets"]),
                "output": str(output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
