import hashlib
import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPOSITORY / "tools" / "build-gui.ps1"
EDITION_BUILD_SCRIPT = REPOSITORY / "tools" / "build-edition.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe")


def _run_build(
    tmp_path: Path,
    *,
    edition: str | None,
    product_role: str | None,
) -> subprocess.CompletedProcess[str]:
    output = tmp_path / (
        f"{edition or 'missing'}-{product_role or 'missing'}"
    )
    command = [
        str(POWERSHELL),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(BUILD_SCRIPT),
        "-OutputRoot",
        str(output),
    ]
    if edition is not None:
        command.extend(["-Edition", edition])
    if product_role is not None:
        command.extend(["-ProductRole", product_role])
    return subprocess.run(
        command,
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        timeout=90,
    )


def _describe_edition(
    tmp_path: Path,
    edition: str,
    product_role: str,
) -> dict[str, object]:
    built = _run_build(
        tmp_path,
        edition=edition,
        product_role=product_role,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    output = tmp_path / f"{edition}-{product_role}"
    executables = sorted(output.glob("*.exe"))
    assert len(executables) == 1
    result = subprocess.run(
        [str(executables[0]), "--describe-edition"],
        cwd=output,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _render_preview(
    tmp_path: Path,
    edition: str,
    product_role: str,
) -> Path:
    built = _run_build(
        tmp_path,
        edition=edition,
        product_role=product_role,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    output = tmp_path / f"{edition}-{product_role}"
    executable = output / "LLMFoundationInstaller.exe"
    preview = output / "preview.png"
    result = subprocess.run(
        [str(executable), "--render-preview", str(preview)],
        cwd=output,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return preview


@pytest.mark.parametrize(
    (
        "edition",
        "product_role",
        "included",
        "required",
        "distribution_allowed",
        "theme_id",
        "owner_controlled",
    ),
    [
        (
            "Employee",
            "Installer",
            ["codex", "opencode"],
            ["codex", "opencode"],
            True,
            "K7Signal",
            False,
        ),
        (
            "Owner",
            "LaunchCenter",
            ["claude", "codex", "opencode"],
            ["codex", "opencode"],
            False,
            "SignalConsole",
            True,
        ),
    ],
)
def test_embedded_edition_contract(
    tmp_path: Path,
    edition: str,
    product_role: str,
    included: list[str],
    required: list[str],
    distribution_allowed: bool,
    theme_id: str,
    owner_controlled: bool,
) -> None:
    value = _describe_edition(tmp_path, edition, product_role)
    assert value == {
        "edition_id": edition,
        "display_name": (
            "K-7 AI Foundation Employee"
            if edition == "Employee"
            else "K-7 AI Foundation Owner"
        ),
        "distribution_allowed": distribution_allowed,
        "included_target_ids": included,
        "required_target_ids": required,
        "theme_id": theme_id,
        "owner_controlled": owner_controlled,
        "product_role": product_role,
    }


@pytest.mark.parametrize(
    ("edition", "product_role"),
    [
        (None, "Installer"),
        ("Employee", None),
        ("Unknown", "Installer"),
        ("Employee", "Unknown"),
    ],
)
def test_build_rejects_missing_or_unknown_contract(
    tmp_path: Path,
    edition: str | None,
    product_role: str | None,
) -> None:
    result = _run_build(
        tmp_path,
        edition=edition,
        product_role=product_role,
    )
    assert result.returncode != 0
    output = tmp_path / (
        f"{edition or 'missing'}-{product_role or 'missing'}"
    )
    assert not list(output.glob("*.exe"))


@pytest.mark.parametrize(
    "view_name",
    [
        "InstallerEmployeeView.xaml",
        "LaunchCenterEmployeeView.xaml",
    ],
)
def test_employee_views_use_exact_k7_visual_contract(view_name: str) -> None:
    value = (REPOSITORY / "src" / "gui" / view_name).read_text(
        encoding="utf-8"
    )
    for token in (
        "#071E22",
        "#FC4912",
        "#77CBB9",
        "#30BCED",
        "Bahnschrift SemiCondensed",
        "Segoe UI",
        "Cascadia Mono",
        "M144.91,200h-50l-32.38-48.57",
        "7.62-7.06",
    ):
        assert token in value
    assert "Claude" not in value


@pytest.mark.parametrize(
    "view_name",
    [
        "InstallerOwnerView.xaml",
        "LaunchCenterOwnerView.xaml",
    ],
)
def test_owner_views_use_signal_console_contract(view_name: str) -> None:
    value = (REPOSITORY / "src" / "gui" / view_name).read_text(
        encoding="utf-8"
    )
    for token in (
        "#071E22",
        "#FC4912",
        "#77CBB9",
        "#30BCED",
        "OWNER CONTROLLED",
        "Selected client",
        "Local relay",
        "Upstream",
        "Claude",
    ):
        assert token in value
    assert "Neon" not in value
    assert "Cyberpunk" not in value


@pytest.mark.parametrize(
    ("edition", "product_role"),
    [
        ("Employee", "Installer"),
        ("Employee", "LaunchCenter"),
        ("Owner", "Installer"),
        ("Owner", "LaunchCenter"),
    ],
)
def test_each_edition_product_renders_its_own_preview(
    tmp_path: Path,
    edition: str,
    product_role: str,
) -> None:
    preview = _render_preview(tmp_path, edition, product_role)
    payload = preview.read_bytes()
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", payload[16:24]) == (1440, 900)


def _build_edition(
    output: Path,
    edition: str,
    *,
    runtime_lock: Path | None = None,
    runtime_archive: Path | None = None,
) -> Path:
    command = [
            str(POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(EDITION_BUILD_SCRIPT),
            "-OutputRoot",
            str(output),
            "-Edition",
            edition,
            "-DistributionMode",
            "Preview",
    ]
    if runtime_lock is not None:
        command.extend(
            [
                "-RuntimeSourcesLock",
                str(runtime_lock),
                "-AllowLocalTestSources",
            ]
        )
    if runtime_archive is not None:
        command.extend(["-RuntimeArchive", str(runtime_archive)])
    result = subprocess.run(
        command,
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def test_edition_builder_declares_exact_internal_artifact_names() -> None:
    source = EDITION_BUILD_SCRIPT.read_text(encoding="utf-8")
    for name in (
        "K7-AI-Foundation-Employee-InternalUnsigned.exe",
        "K7-AI-Launch-Center-Employee-InternalUnsigned.exe",
        "K7-AI-Foundation-Owner-InternalUnsigned.exe",
        "K7-AI-Launch-Center-Owner-InternalUnsigned.exe",
    ):
        assert name in source


def test_deterministic_edition_bundle_binds_both_products(
    tmp_path: Path,
) -> None:
    first = _build_edition(tmp_path / "first", "Employee")
    second = _build_edition(tmp_path / "second", "Employee")
    expected = {
        "installer": "K7-AI-Foundation-Employee-Preview.exe",
        "launch_center": "K7-AI-Launch-Center-Employee-Preview.exe",
    }
    manifest = json.loads(
        (first / "bundle-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert manifest["edition_id"] == "Employee"
    assert manifest["theme_id"] == "K7Signal"
    assert manifest["distribution_mode"] == "Preview"
    assert manifest["targets"] == ["codex", "opencode"]
    assert {
        role: value["file"]
        for role, value in manifest["products"].items()
    } == expected
    for role, name in expected.items():
        first_executable = first / name
        second_executable = second / name
        assert first_executable.read_bytes() == second_executable.read_bytes()
        assert manifest["products"][role]["sha256"] == hashlib.sha256(
            first_executable.read_bytes()
        ).hexdigest()
    assert (first / "bundle-manifest.json").read_bytes() == (
        second / "bundle-manifest.json"
    ).read_bytes()
    handoff = subprocess.run(
        [
            str(first / expected["installer"]),
            "--resolve-sibling-json",
            str(first),
        ],
        cwd=first,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert handoff.returncode == 0, handoff.stdout + handoff.stderr
    handoff_value = json.loads(handoff.stdout)
    assert handoff_value["status"] == "RESOLVED"
    assert handoff_value["executable_path"] == str(
        (first / expected["launch_center"]).resolve()
    )


def test_edition_bundle_carries_hash_bound_runtime_sidecar(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "sing-box-fixture.zip"
    archive.write_bytes(b"PK\x03\x04runtime-fixture")
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    lock = tmp_path / "runtime.lock.json"
    lock.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "test_only": True,
                "runtime": {
                    "id": "sing-box",
                    "version": "1.13.14",
                    "url": "http://127.0.0.1:43118/" + archive.name,
                    "sha256": archive_hash,
                    "archive_kind": "zip",
                    "archive_entry": (
                        "sing-box-1.13.14-windows-amd64/sing-box.exe"
                    ),
                    "executable_name": "sing-box.exe",
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    bundle = _build_edition(
        tmp_path / "bundle",
        "Employee",
        runtime_lock=lock,
        runtime_archive=archive,
    )
    copied = bundle / archive.name
    manifest = json.loads(
        (bundle / "bundle-manifest.json").read_text(encoding="utf-8")
    )

    assert copied.read_bytes() == archive.read_bytes()
    assert manifest["runtime"] == {
        "id": "sing-box",
        "version": "1.13.14",
        "file": archive.name,
        "sha256": archive_hash,
        "bytes": archive.stat().st_size,
    }
