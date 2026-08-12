import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import textwrap

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = REPOSITORY_ROOT / "client-sources.lock.json"
POLICY = REPOSITORY_ROOT / "support" / "officecli-command-policy.json"
BUILD = REPOSITORY_ROOT / "tools" / "build-officecli-shim.ps1"

OFFICECLI_RECORD = {
    "id": "officecli",
    "target": "shared",
    "display_name": "OfficeCLI",
    "role": "shared-tool",
    "required_for_base": True,
    "required_for_employee": True,
    "version": "1.0.143",
    "source_kind": "download",
    "url": "https://github.com/iOfficeAI/OfficeCLI/releases/download/v1.0.143/officecli-win-x64.exe",
    "sha256": "d4d4c10fced307e209744cf98a56b003a6e613424fd651b08469274704afd2c6",
    "artifact_kind": "portable-exe",
    "archive_entry": None,
    "publisher": None,
    "signature_required": False,
    "install_mode": "foundation-shared",
    "detect_commands": ["officecli.exe"],
    "version_arguments": ["--version"],
    "store_identity": None,
    "store_publisher": None,
    "store_signature_kind": None,
    "version_pattern": r"\A(?:officecli[ \t]+)?v?(?<version>(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))\z",
    "license": "Apache-2.0",
}

ALLOWED_COMMANDS = [
    "open", "close", "watch", "unwatch", "mark", "unmark", "get-marks",
    "goto", "view", "get", "query", "set", "add", "remove", "move", "swap",
    "refresh", "raw", "raw-set", "add-part", "validate", "save", "batch", "dump",
    "import", "create", "merge", "plugins", "help", "load_skill",
]


def _powershells() -> list[str]:
    return [
        executable
        for executable in ("pwsh.exe", "powershell.exe")
        if shutil.which(executable)
    ]


def _find_csharp_compiler() -> Path | None:
    candidates: list[Path] = []
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(variable)
        if root:
            candidates.extend(
                Path(root).glob(
                    "Microsoft Visual Studio/*/*/MSBuild/Current/Bin/Roslyn/csc.exe"
                )
            )
    framework = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")
    if framework.is_file():
        candidates.append(framework)
    return sorted(candidates)[0] if candidates else None


def _build(host: str, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BUILD),
            "-OutputPath", str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _compile_fake_officecli(path: Path) -> None:
    compiler = _find_csharp_compiler()
    assert compiler is not None, "C# compiler is unavailable"
    source = path.with_suffix(".cs")
    source.write_text(
        textwrap.dedent(
            r'''
            using System;
            using System.IO;
            class FakeOfficeCli
            {
                static int Main(string[] args)
                {
                    File.WriteAllLines(
                        Environment.GetEnvironmentVariable("OFFICECLI_SHIM_TEST_OUTPUT"),
                        args
                    );
                    Console.OutputEncoding = System.Text.Encoding.UTF8;
                    Console.Error.WriteLine("officecli-stderr: проверка");
                    Console.WriteLine("officecli-stdout: документ");
                    return 23;
                }
            }
            '''
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(compiler), "/nologo", "/target:exe", f"/out:{path}", str(source)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


def _install_shim(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    host = _powershells()[0]
    shim = tmp_path / "build" / "officecli.exe"
    result = _build(host, shim)
    assert result.returncode == 0, result.stderr

    foundation = tmp_path / ".llm-foundation"
    public = foundation / "bin" / "officecli.exe"
    private = foundation / "libexec" / "officecli"
    private.mkdir(parents=True)
    public.parent.mkdir(parents=True)
    shutil.copy2(shim, public)
    shutil.copy2(POLICY, private / "officecli-command-policy.json")
    marker = tmp_path / "forwarded-arguments.txt"
    _compile_fake_officecli(private / "officecli.exe")
    environment = os.environ.copy()
    environment["OFFICECLI_SHIM_TEST_OUTPUT"] = str(marker)
    return public, marker, environment


def test_canonical_officecli_source_record_is_exact() -> None:
    """Changing the pinned asset or shared ownership must fail this contract."""
    source_lock = json.loads(SOURCE_LOCK.read_text(encoding="utf-8"))
    records = [row for row in source_lock["clients"] if row["id"] == "officecli"]
    assert records == [OFFICECLI_RECORD]


def test_policy_has_exact_document_command_allowlist() -> None:
    """Broadening the command policy must fail before a private executable is run."""
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    assert policy == {
        "schema_version": 1,
        "allowed_commands": ALLOWED_COMMANDS,
        "process_environment": {
            "OFFICECLI_NO_AUTO_INSTALL": "1",
            "OFFICECLI_SKIP_UPDATE": "1",
        },
    }


def test_version_pattern_matches_only_one_pinned_full_output_line() -> None:
    """Removing full anchoring would accept prerelease, build, or extra output."""
    pattern = OFFICECLI_RECORD["version_pattern"]
    python_pattern = pattern.replace(r"\A", "").replace(r"\z", "$")
    python_pattern = python_pattern.replace("(?<version>", "(?P<version>")
    version = re.compile(python_pattern)

    assert version.fullmatch("officecli 1.0.143") is not None
    for invalid in (
        "officecli 1.0.143.1",
        "officecli 1.0.143-preview",
        "officecli 1.0.143+build.1",
        "officecli 1.0.143\nextra",
    ):
        assert version.fullmatch(invalid) is None


def test_build_is_deterministic_in_powershell_7_and_51(tmp_path: Path) -> None:
    """Changing either host's compiler path or emitted bytes must fail this build contract."""
    hosts = _powershells()
    assert {Path(host).stem.lower() for host in hosts} >= {"pwsh", "powershell"}

    outputs: list[Path] = []
    for host in hosts:
        output = tmp_path / Path(host).stem / "officecli-shim.exe"
        result = _build(host, output)
        assert result.returncode == 0, result.stderr
        assert output.is_file()
        outputs.append(output)

    assert outputs[0].read_bytes() == outputs[1].read_bytes()


def test_shim_blocks_non_document_commands_and_round_trips_allowed_arguments(
    tmp_path: Path,
) -> None:
    """Removing the policy gate or Windows quoting serializer must fail this runtime contract."""
    public, marker, environment = _install_shim(tmp_path)

    blocked = [
        [], ["install"], ["skill"], ["skills"], ["mcp"], ["mcp-serve"],
        ["config"], ["update"], ["self-update"], ["__update-check__"],
        ["__resident-serve__"], ["_internal"], ["--trace"], ["--"], ["/help"],
        ["@response"], ["unknown"],
    ]
    for arguments in blocked:
        if marker.exists():
            marker.unlink()
        invocation = subprocess.run(
            [str(public), *arguments], check=False, capture_output=True, text=True,
            encoding="utf-8", env=environment,
        )
        assert invocation.returncode != 23
        assert not marker.exists(), arguments

    arguments = [
        "--json", "open", "two words", "tab\tvalue", 'quote"value', "trailing\\",
        'slashes\\\\"quote', "Привет, мир",
    ]
    invocation = subprocess.run(
        [str(public), *arguments], check=False, capture_output=True, text=True,
        encoding="utf-8", env=environment,
    )
    assert invocation.returncode == 23
    assert marker.read_text(encoding="utf-8").splitlines() == arguments


def test_shim_blocks_case_variants_without_launching_private_executable(
    tmp_path: Path,
) -> None:
    """Changing ordinal command matching to case-insensitive launches `Open`."""
    public, marker, environment = _install_shim(tmp_path)

    for arguments in (["Open"], ["OPEN"], ["oPeN"], ["--json", "Open"]):
        invocation = subprocess.run(
            [str(public), *arguments], check=False, capture_output=True, text=True,
            encoding="utf-8", env=environment,
        )
        assert invocation.returncode != 23
        assert not marker.exists(), arguments


def test_shim_forwards_only_exact_version_and_help_forms(tmp_path: Path) -> None:
    """Accepting non-exact version/help forms would launch the private executable."""
    public, marker, environment = _install_shim(tmp_path)

    for arguments in (["--version"], ["--help"], ["-h"], ["-?"]):
        invocation = subprocess.run(
            [str(public), *arguments], check=False, capture_output=True, text=True,
            encoding="utf-8", env=environment,
        )
        assert invocation.returncode == 23
        assert marker.read_text(encoding="utf-8").splitlines() == list(arguments)
        marker.unlink()

    for arguments in (
        ["--version", "extra"], ["--help", "extra"], ["-h", "extra"],
        ["-?", "extra"], ["--json", "--version"],
    ):
        invocation = subprocess.run(
            [str(public), *arguments], check=False, capture_output=True, text=True,
            encoding="utf-8", env=environment,
        )
        assert invocation.returncode != 23
        assert not marker.exists(), arguments


def test_shim_forwards_utf8_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    """Dropping redirected output must fail even when the child exit code survives."""
    public, marker, environment = _install_shim(tmp_path)

    invocation = subprocess.run(
        [str(public), "view", "документ.docx", "text"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )

    assert invocation.returncode == 23
    assert invocation.stdout.strip() == "officecli-stdout: документ"
    assert invocation.stderr.strip() == "officecli-stderr: проверка"
    assert marker.read_text(encoding="utf-8").splitlines() == [
        "view", "документ.docx", "text"
    ]
