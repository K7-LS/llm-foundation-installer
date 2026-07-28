import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPOSITORY / "tools" / "build-gui.ps1"
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
