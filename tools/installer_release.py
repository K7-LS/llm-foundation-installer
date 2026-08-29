from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Единственный источник версии продукта — файл APP_VERSION в корне
# (раньше значение дублировалось здесь, в foundation_release и в тестах).
VERSION = (
    Path(__file__).resolve().parents[1] / "APP_VERSION"
).read_text(encoding="utf-8").strip()
TAG = f"employee-v{VERSION}"
TARGETS = ("claude", "codex", "opencode")
SELF_TEST_TARGETS = ("codex", "claude", "opencode")
PRODUCT_FILES = {
    "installer": "K7-AI-Foundation-Employee-PublicUnsigned.exe",
}
FALLBACK_FILE = "K7-AI-Launch-Center-Employee-PublicUnsigned.cmd"
PRODUCT_ROLES = {
    "installer": "Installer",
}
SINGBOX_VERSION = json.loads(
    (Path(__file__).resolve().parents[1] / "src" / "gui" / "product-config.json")
    .read_text(encoding="utf-8")
)["singbox_version"]
RUNTIME_FILE = f"sing-box-{SINGBOX_VERSION}-windows-amd64.zip"
EXPECTED_TARGET_LIFECYCLE = {
    "status": "PASS",
    "plan": "READY",
    "install": "CANONICAL",
    "doctor": "CANONICAL",
    "inventory": "INSTALLED",
    "rollback": "ROLLED_BACK",
    "preserved_data": "PASS",
}
EXPECTED_PRODUCT_CANARY = {
    "installer": {
        "product": "PASS",
        "self_test": "PASS",
        "catalog": "PASS",
        "launch_center_fallback": "PASS",
    },
}
EXPECTED_RUNTIME_CANARY = {
    "id": "sing-box",
    "version": SINGBOX_VERSION,
    "status": "VERIFIED",
}
EXPECTED_BUNDLE_VERDICTS = {
    "FULL_RELEASE_CLAUDE": "PASS",
    "FULL_RELEASE_CODEX": "PASS",
    "FULL_RELEASE_OPENCODE": "PASS",
    "TECHNICAL_READY": "PASS",
    "PROVIDER_LIVE": "PASS",
    "PROGRAM_RELEASE": "3/3",
    "INTERNAL_UNSIGNED_RELEASE": "NOT_PASS",
    "PUBLIC_UNSIGNED_RELEASE": "PASS",
    "PUBLIC_SIGNED_RELEASE": "DEFERRED_UNSIGNED",
}
EXPECTED_DRAFT_VERDICTS = {
    **EXPECTED_BUNDLE_VERDICTS,
    "INSTALLER_HUB_CANARY": "PASS",
    "HOME_PC_CANARY": "PENDING",
    "EMPLOYEE_INSTALLER_PUBLIC_UNSIGNED": "PENDING_PILOT",
}
INSTALL_GUIDE = f"""# K-7 для сотрудников, версия {VERSION}

В комплекте пять связанных позиций:

- `K7-AI-Foundation-Employee-PublicUnsigned.exe` — единый EXE: установка,
  обслуживание и Launch Center;
- `K7-AI-Launch-Center-Employee-PublicUnsigned.cmd` — ярлык ежедневного
  запуска Launch Center через тот же EXE;
- архив `sing-box-*-windows-amd64.zip` — среда маршрутов с проверкой хеша;
- `bundle-manifest.json` — проверяемые SHA-256 и состав комплекта.

Перед запуском сверить каждый файл с `SHA256SUMS`. Публичный выпуск остаётся
без цифровой подписи, поэтому Windows может показать «Неизвестный издатель»
или SmartScreen. Администратор не требуется. Версия для сотрудников включает
Codex, Claude Code и OpenCode. Авторизация Claude выполняется интерактивно в
официальном клиенте; выбор proxy-маршрута не подменяет право доступа к
провайдеру.
"""


@dataclass(frozen=True)
class DraftRelease:
    root: Path
    product_paths: dict[str, Path]
    runtime_path: Path
    release_manifest_path: Path
    components_lock_path: Path
    sha256sums_path: Path

    @property
    def installer_path(self) -> Path:
        return self.product_paths["installer"]

def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_record(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {"sha256": _sha256(payload), "bytes": len(payload)}


def evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return _sha256(_json_bytes(body))


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{path.name} is missing or unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _validate_record(path: Path, value: object, label: str) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"sha256", "bytes"}
        or value != _file_record(path)
    ):
        raise ValueError(f"Employee bundle {label} binding differs")


def validate_bundle(bundle: Path) -> dict[str, Any]:
    """Validate the exact five-file Employee edition release input."""

    bundle = bundle.resolve()
    if not bundle.is_dir() or bundle.is_symlink():
        raise ValueError("Employee bundle root is missing or unsafe")
    manifest = _load_json(bundle / "bundle-manifest.json")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("app_id") != "k7-ai-edition-bundle"
        or manifest.get("edition_id") != "Employee"
        or manifest.get("version") != VERSION
        or manifest.get("theme_id") != "K7Signal"
        or manifest.get("owner_controlled") is not False
        or manifest.get("distribution_allowed") is not True
        or manifest.get("distribution_mode") != "PublicUnsigned"
        or manifest.get("targets") != list(TARGETS)
        or manifest.get("verdicts") != EXPECTED_BUNDLE_VERDICTS
    ):
        raise ValueError("Employee edition bundle manifest is not accepted")
    expected_names = {
        "bundle-manifest.json",
        FALLBACK_FILE,
        RUNTIME_FILE,
        *PRODUCT_FILES.values(),
    }
    children = sorted(bundle.iterdir(), key=lambda item: item.name)
    if (
        {path.name for path in children} != expected_names
        or any(not path.is_file() or path.is_symlink() for path in children)
    ):
        raise ValueError("Employee bundle file inventory differs")
    products = manifest.get("products")
    if not isinstance(products, dict) or set(products) != set(PRODUCT_FILES):
        raise ValueError("Employee bundle product inventory differs")
    fallback = manifest.get("launch_center_fallback")
    fallback_path = bundle / FALLBACK_FILE
    fallback_record = _file_record(fallback_path)
    if (
        not isinstance(fallback, dict)
        or fallback.get("product_role") != "LaunchCenter"
        or fallback.get("file") != FALLBACK_FILE
        or fallback.get("arguments") != "--launch-center-ui"
        or fallback.get("sha256") != fallback_record["sha256"]
        or fallback.get("bytes") != fallback_record["bytes"]
    ):
        raise ValueError("Employee launch-center fallback binding differs")
    for product, filename in PRODUCT_FILES.items():
        row = products.get(product)
        path = bundle / filename
        if (
            not isinstance(row, dict)
            or row.get("product_role") != PRODUCT_ROLES[product]
            or row.get("file") != filename
        ):
            raise ValueError(f"Employee bundle {product} metadata differs")
        _validate_record(
            path,
            {"sha256": row.get("sha256"), "bytes": row.get("bytes")},
            product,
        )
    runtime = manifest.get("runtime")
    if (
        not isinstance(runtime, dict)
        or runtime.get("id") != "sing-box"
        or runtime.get("version") != SINGBOX_VERSION
        or runtime.get("file") != RUNTIME_FILE
    ):
        raise ValueError("Employee bundle runtime metadata differs")
    _validate_record(
        bundle / RUNTIME_FILE,
        {
            "sha256": runtime.get("sha256"),
            "bytes": runtime.get("bytes"),
        },
        "runtime",
    )
    return manifest


def _validate_hub_canary(path: Path, bundle: Path) -> dict[str, Any]:
    evidence = _load_json(path)
    binding = evidence.get("bundle")
    expected_binding = {
        "manifest_sha256": _file_record(
            bundle / "bundle-manifest.json"
        )["sha256"],
        "installer_sha256": _file_record(
            bundle / PRODUCT_FILES["installer"]
        )["sha256"],
        "runtime_sha256": _file_record(
            bundle / RUNTIME_FILE
        )["sha256"],
    }
    if (
        evidence.get("schema_version") != 1
        or evidence.get("target") != "employee_edition"
        or evidence.get("version") != VERSION
        or evidence.get("INSTALLER_HUB_CANARY") != "PASS"
        or evidence.get("model_requests") != 0
        or evidence.get("unexpected_network") != 0
        or binding != expected_binding
        or evidence.get("products")
        != EXPECTED_PRODUCT_CANARY
        or evidence.get("runtime") != EXPECTED_RUNTIME_CANARY
        or evidence.get("targets")
        != {
            target: EXPECTED_TARGET_LIFECYCLE
            for target in TARGETS
        }
        or evidence.get("credentials_included") is not False
        or evidence.get("personal_data_included") is not False
        or evidence.get("evidence_body_sha256")
        != evidence_body_sha256(evidence)
    ):
        raise ValueError("Employee edition hub canary is invalid or unbound")
    return evidence


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite release file: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _copy_exact(source: Path, destination: Path) -> None:
    payload = source.read_bytes()
    _write_new(destination, payload)
    if destination.read_bytes() != payload:
        raise AssertionError(f"exact-byte copy failed: {source.name}")


def _sha256sums(root: Path) -> bytes:
    lines = [
        f"{_file_record(path)['sha256']}  {path.name}"
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _components(manifest: dict[str, Any]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "edition_id": "Employee",
        "version": VERSION,
        "products": {
            product: {
                "file": row["file"],
                "product_role": row["product_role"],
                "sha256": row["sha256"],
                "bytes": row["bytes"],
            }
            for product, row in manifest["products"].items()
        },
        "runtime": manifest["runtime"],
        "targets": list(TARGETS),
        "sync_direction": "hub-to-consumer",
        "consumer_upload": False,
    }


def prepare_draft_release(
    *,
    bundle: Path,
    hub_canary_path: Path,
    output: Path,
) -> DraftRelease:
    """Prepare draft assets without rebuilding any edition binary."""

    bundle = bundle.resolve()
    output = output.resolve()
    manifest = validate_bundle(bundle)
    _validate_hub_canary(hub_canary_path.resolve(), bundle)
    if output.exists():
        raise ValueError("draft release output must not exist")
    output.mkdir(parents=True)

    for filename in (
        "bundle-manifest.json",
        FALLBACK_FILE,
        RUNTIME_FILE,
        *PRODUCT_FILES.values(),
    ):
        _copy_exact(bundle / filename, output / filename)
    _copy_exact(
        hub_canary_path.resolve(),
        output / "hub-canary-evidence.json",
    )
    components_path = output / "components.lock.json"
    _write_new(components_path, _json_bytes(_components(manifest)))
    _write_new(
        output / "ИНСТРУКЦИЯ-СОТРУДНИКУ.md",
        INSTALL_GUIDE.encode("utf-8"),
    )

    artifacts = {
        path.name: _file_record(path)
        for path in sorted(output.iterdir(), key=lambda item: item.name)
        if path.is_file()
    }
    release_manifest: dict[str, Any] = {
        "schema_version": 1,
        "app_id": "k7-ai-employee-edition",
        "edition_id": "Employee",
        "version": VERSION,
        "tag": TAG,
        "channel": "draft",
        "distribution_mode": "PublicUnsigned",
        "products": {
            product: artifacts[filename]
            for product, filename in PRODUCT_FILES.items()
        },
        "launch_center_fallback": artifacts[FALLBACK_FILE],
        "runtime": artifacts[RUNTIME_FILE],
        "bundle_manifest_sha256": artifacts[
            "bundle-manifest.json"
        ]["sha256"],
        "hub_canary_sha256": artifacts[
            "hub-canary-evidence.json"
        ]["sha256"],
        "artifacts": artifacts,
        "verdicts": EXPECTED_DRAFT_VERDICTS,
        "requires": {
            "owner_attested_home_pc_canary": True,
            "same_product_and_runtime_bytes": True,
            "immutable_release": True,
            "release_attestation": True,
        },
    }
    release_manifest["evidence_body_sha256"] = evidence_body_sha256(
        release_manifest
    )
    release_manifest_path = output / "release-manifest.json"
    _write_new(release_manifest_path, _json_bytes(release_manifest))
    sums = output / "SHA256SUMS"
    _write_new(sums, _sha256sums(output))
    product_paths = {
        product: output / filename
        for product, filename in PRODUCT_FILES.items()
    }
    return DraftRelease(
        root=output,
        product_paths=product_paths,
        runtime_path=output / RUNTIME_FILE,
        release_manifest_path=release_manifest_path,
        components_lock_path=components_path,
        sha256sums_path=sums,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare deterministic employee-v0.4.0 draft assets from the "
            "accepted Employee edition and no-model hub canary."
        )
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--hub-canary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    result = prepare_draft_release(
        bundle=arguments.bundle,
        hub_canary_path=arguments.hub_canary,
        output=arguments.output,
    )
    print(
        json.dumps(
            {
                "status": "DRAFT_ASSETS_PREPARED",
                "tag": TAG,
                "products": {
                    product: _file_record(path)["sha256"]
                    for product, path in result.product_paths.items()
                },
                "runtime_sha256": _file_record(
                    result.runtime_path
                )["sha256"],
                "pilot": "PENDING",
                "output": str(result.root),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
