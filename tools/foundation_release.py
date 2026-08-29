from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERSION = (Path(__file__).resolve().parents[1] / "VERSION").read_text(
    encoding="utf-8"
).strip()
APP_VERSION = (
    Path(__file__).resolve().parents[1] / "APP_VERSION"
).read_text(encoding="utf-8").strip()
TAG = f"foundation-engine-v{VERSION}"
REPOSITORY_URL = (
    "https://github.com/K7-LS/llm-foundation-installer"
)
REPOSITORY = "K7-LS/llm-foundation-installer"
CORE_ENGINE_FILES = (
    "VERSION",
    "engine-manifest.json",
    "foundation.ps1",
)


@dataclass(frozen=True)
class FoundationRelease:
    root: Path
    asset_path: Path
    manifest_path: Path
    evidence_path: Path


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {"sha256": _sha256(payload), "bytes": len(payload)}


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _valid_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _engine_tree(engine_root: Path) -> dict[str, str]:
    if not engine_root.is_dir() or engine_root.is_symlink():
        raise ValueError("Foundation engine root is unsafe")
    paths = sorted(engine_root.rglob("*"), key=lambda item: item.as_posix())
    files = [path for path in paths if path.is_file()]
    if any(path.is_symlink() for path in paths) or not all(
        (engine_root / name).is_file() for name in CORE_ENGINE_FILES
    ):
        raise ValueError("Foundation engine inventory differs")
    return {
        path.relative_to(engine_root).as_posix(): _sha256(path.read_bytes())
        for path in files
    }


def _validate_engine_contract(engine_root: Path) -> None:
    if (engine_root / "VERSION").read_text(encoding="utf-8").strip() != VERSION:
        raise ValueError("Foundation engine version differs")
    manifest = _load_object(engine_root / "engine-manifest.json")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("protocol_version") != 1
        or manifest.get("engine_version") != VERSION
        or manifest.get("network") != "offline"
        or manifest.get("commands")
        != ["apply", "doctor", "install", "inventory", "plan", "rollback"]
        or manifest.get("supported_powershell") != ["5.1", "7"]
        or manifest.get("foundation_ps1_sha256")
        != _record(engine_root / "foundation.ps1")["sha256"]
    ):
        raise ValueError("Foundation engine contract differs")


def _validate_acceptance(
    evidence: dict[str, Any],
    actual_tree: dict[str, str],
) -> None:
    source = evidence.get("source")
    syntax = evidence.get("powershell_syntax")
    builds = evidence.get("engine_builds")
    pytest_record = evidence.get("pytest")
    counts = pytest_record.get("counts") if isinstance(pytest_record, dict) else None
    hashes = source.get("hashes") if isinstance(source, dict) else None
    valid = (
        evidence.get("schema_version") == 1
        and evidence.get("engine_version") == VERSION
        and evidence.get("installer_version") == APP_VERSION
        and evidence.get("FOUNDATION_SYNTHETIC") == "PASS"
        and evidence.get("deterministic_engine_bundle") == "PASS"
        and evidence.get("evidence_body_sha256")
        == evidence_body_sha256(evidence)
        and isinstance(source, dict)
        and source.get("repository") == REPOSITORY_URL
        and _valid_hex(source.get("commit"), 40)
        and _valid_hex(source.get("tree"), 40)
        and isinstance(hashes, dict)
        and set(hashes)
        == {
            "VERSION",
            "APP_VERSION",
            "client-sources.lock.json",
            "src",
            "tests",
            "tools",
        }
        and all(_valid_hex(value, 64) for value in hashes.values())
        and isinstance(syntax, dict)
        and set(syntax) == {"ps7", "ps51"}
        and all(
            isinstance(syntax[name], dict)
            and syntax[name].get("status") == "PASS"
            for name in ("ps7", "ps51")
        )
        and isinstance(builds, dict)
        and set(builds) == {"ps7", "ps51"}
        and all(
            isinstance(builds[name], dict)
            and builds[name].get("status") == "PASS"
            and builds[name].get("files") == actual_tree
            for name in ("ps7", "ps51")
        )
        and isinstance(pytest_record, dict)
        and pytest_record.get("status") == "PASS"
        and isinstance(counts, dict)
        and isinstance(counts.get("tests"), int)
        and not isinstance(counts.get("tests"), bool)
        and counts["tests"] > 0
        and counts.get("failures") == 0
        and counts.get("errors") == 0
        and counts.get("passed") == counts.get("tests") - counts.get("skipped")
        and isinstance(counts.get("ps7_cases"), int)
        and counts["ps7_cases"] > 0
        and isinstance(counts.get("ps51_cases"), int)
        and counts["ps51_cases"] > 0
    )
    if not valid:
        raise ValueError("Foundation synthetic acceptance is invalid")


def _zip_engine(engine_root: Path, destination: Path) -> None:
    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name in sorted(_engine_tree(engine_root)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (engine_root / name).read_bytes())


def prepare_foundation_release(
    *,
    engine_root: Path,
    acceptance_evidence_path: Path,
    output: Path,
) -> FoundationRelease:
    """Create exact pre-publication Foundation assets from accepted bytes."""

    engine_root = engine_root.resolve()
    evidence_source = acceptance_evidence_path.resolve()
    output = output.resolve()
    if output.exists():
        raise ValueError("Foundation release output must not exist")
    tree = _engine_tree(engine_root)
    evidence = _load_object(evidence_source)
    builds = evidence.get("engine_builds")
    if (
        not isinstance(builds, dict)
        or any(
            not isinstance(builds.get(name), dict)
            or builds[name].get("files") != tree
            for name in ("ps7", "ps51")
        )
    ):
        raise ValueError(
            "Foundation engine bytes differ from synthetic acceptance"
        )
    _validate_acceptance(evidence, tree)
    _validate_engine_contract(engine_root)
    output.mkdir(parents=True)

    asset = output / f"foundation-engine-{VERSION}.zip"
    _zip_engine(engine_root, asset)
    evidence_path = output / "acceptance-evidence.json"
    evidence_path.write_bytes(evidence_source.read_bytes())
    engine_records = {
        name: _record(engine_root / name)
        for name in CORE_ENGINE_FILES
    }
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "target": "foundation",
        "version": VERSION,
        "tag": TAG,
        "channel": "stable",
        "source": evidence["source"],
        "asset": {
            "name": asset.name,
            **_record(asset),
        },
        "engine_files": engine_records,
        "acceptance_evidence_sha256": _record(evidence_path)["sha256"],
        "requires": {
            "immutable_release": True,
            "release_attestation": True,
        },
    }
    manifest["evidence_body_sha256"] = evidence_body_sha256(manifest)
    manifest_path = output / "release-manifest.json"
    manifest_path.write_bytes(_json_bytes(manifest))
    return FoundationRelease(
        root=output,
        asset_path=asset,
        manifest_path=manifest_path,
        evidence_path=evidence_path,
    )


def _attestation_digest(payload: bytes, label: str) -> str:
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} did not return JSON") from error
    if not isinstance(parsed, (dict, list)):
        raise ValueError(f"{label} returned invalid JSON")
    return _sha256(payload)


def build_release_verification(
    *,
    manifest_path: Path,
    asset_path: Path,
    release_api: dict[str, Any],
    release_attestation_output: bytes,
    asset_attestation_output: bytes,
    gh_version: str,
) -> dict[str, object]:
    manifest = _load_object(manifest_path)
    asset = manifest.get("asset")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("target") != "foundation"
        or manifest.get("version") != VERSION
        or manifest.get("tag") != TAG
        or manifest.get("channel") != "stable"
        or manifest.get("evidence_body_sha256")
        != evidence_body_sha256(manifest)
        or not isinstance(asset, dict)
    ):
        raise ValueError("stable Foundation manifest is invalid")
    if (
        release_api.get("tag_name") != TAG
        or release_api.get("draft") is not False
        or release_api.get("prerelease") is not False
        or release_api.get("immutable") is not True
    ):
        raise ValueError("GitHub release is not immutable stable")
    if (
        not asset_path.is_file()
        or asset_path.name != asset.get("name")
        or _record(asset_path)
        != {
            "sha256": asset.get("sha256"),
            "bytes": asset.get("bytes"),
        }
    ):
        raise ValueError("local Foundation release asset binding differs")
    if not gh_version.startswith("gh version "):
        raise ValueError("GitHub CLI version evidence is invalid")
    evidence: dict[str, object] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "repository": REPOSITORY,
        "tag": TAG,
        "release_state": {
            "draft": False,
            "prerelease": False,
            "immutable": True,
        },
        "release_attestation": "PASS",
        "assets": [{**asset, "attestation": "PASS"}],
        "verification_commands": {
            "gh_version": gh_version.splitlines()[0],
            "release_output_sha256": _attestation_digest(
                release_attestation_output,
                "gh release verify",
            ),
            "asset_output_sha256": _attestation_digest(
                asset_attestation_output,
                "gh release verify-asset",
            ),
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


def create_package_acceptance(
    manifest_path: Path,
    evidence_path: Path,
    release_verification_path: Path,
    output_path: Path,
) -> dict[str, object]:
    manifest = _load_object(manifest_path)
    evidence = _load_object(evidence_path)
    verification = _load_object(release_verification_path)
    asset = manifest.get("asset")
    engine_files = manifest.get("engine_files")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("target") != "foundation"
        or manifest.get("version") != VERSION
        or manifest.get("tag") != TAG
        or manifest.get("channel") != "stable"
        or manifest.get("evidence_body_sha256")
        != evidence_body_sha256(manifest)
        or manifest.get("requires")
        != {"immutable_release": True, "release_attestation": True}
        or not isinstance(asset, dict)
        or not isinstance(engine_files, dict)
    ):
        raise ValueError("stable Foundation release manifest is invalid")
    asset_path = manifest_path.parent / str(asset.get("name") or "")
    if (
        not asset_path.is_file()
        or _record(asset_path)
        != {
            "sha256": asset.get("sha256"),
            "bytes": asset.get("bytes"),
        }
    ):
        raise ValueError("stable Foundation asset binding differs")
    if (
        _record(evidence_path)["sha256"]
        != manifest.get("acceptance_evidence_sha256")
    ):
        raise ValueError("Foundation acceptance evidence binding differs")
    builds = evidence.get("engine_builds")
    ps7_build = builds.get("ps7") if isinstance(builds, dict) else None
    accepted_tree = (
        ps7_build.get("files") if isinstance(ps7_build, dict) else None
    )
    manifest_core = {
        name: str(record.get("sha256"))
        for name, record in engine_files.items()
        if isinstance(record, dict)
    }
    if (
        not isinstance(accepted_tree, dict)
        or any(
            accepted_tree.get(name) != digest
            for name, digest in manifest_core.items()
        )
    ):
        raise ValueError("Foundation engine core differs from accepted tree")
    _validate_acceptance(evidence, accepted_tree)
    verified_assets = verification.get("assets")
    if (
        verification.get("schema_version") != 1
        or verification.get("repository") != REPOSITORY
        or verification.get("tag") != TAG
        or verification.get("release_state")
        != {"draft": False, "prerelease": False, "immutable": True}
        or verification.get("release_attestation") != "PASS"
        or verification.get("RELEASE_INTEGRITY") != "PASS"
        or verification.get("evidence_body_sha256")
        != evidence_body_sha256(verification)
        or verified_assets
        != [{**asset, "attestation": "PASS"}]
    ):
        raise ValueError("Foundation release verification is not PASS")
    result: dict[str, object] = {
        "schema_version": 1,
        "target": "foundation",
        "engine_version": VERSION,
        "package_acceptance": "PASS",
        "asset": asset,
        "engine_files": engine_files,
        "release_manifest": {
            "name": manifest_path.name,
            **_record(manifest_path),
        },
        "acceptance_evidence": {
            "name": evidence_path.name,
            **_record(evidence_path),
        },
        "release_verification": {
            "name": release_verification_path.name,
            **_record(release_verification_path),
        },
        "immutable_release": True,
        "release_attestation": True,
    }
    output_path.write_bytes(_json_bytes(result))
    return result
