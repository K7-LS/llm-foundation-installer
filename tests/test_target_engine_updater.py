"""Run the real updater's manifest gate without networking or installation.

The complete production BaseReleaseUpdater.cs is compiled unchanged. Only its
external dependencies are inert stubs; the tests invoke private ValidateManifest
and GetSource through reflection. Target-engine extraction has separate tests.
"""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "gui" / "BaseReleaseUpdater.cs"
TARGETS = ("codex", "claude", "opencode")
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native .NET Framework test")


HARNESS = r'''
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    // Any accidental call outside the pure manifest gate fails before I/O.
    internal sealed class TrustedPackage { }
    internal sealed class TargetEngineBinding
    {
        public string target;
        public string engine_version;
        public string engine_manifest_sha256;
    }
    internal static class ProductCatalog
    {
        public static bool TryGetAcceptedPackage(string root, string target,
                                                 out TrustedPackage package)
        { throw new InvalidOperationException("Unexpected package lookup"); }
    }
    internal static class TargetFoundationEngine
    {
        public static TargetEngineBinding ReadContract(TrustedPackage package)
        { throw new InvalidOperationException("Unexpected engine extraction"); }
    }
    internal sealed class ClientSourceLock { public bool test_only; }
    internal static class ClientBootstrap
    {
        public static ClientSourceLock Load(string root)
        { throw new InvalidOperationException("Unexpected client state access"); }
    }
    internal static class BundleIntegrity
    {
        public static string Sha256(string path)
        { throw new InvalidOperationException("Unexpected bundle file access"); }
    }
    internal sealed class ConnectionProcessState { }
    internal static class ConnectionStore
    {
        public static ConnectionProcessState ConfigureProcessEnvironment(
            string home, ProcessStartInfo start)
        { throw new InvalidOperationException("Unexpected connection state access"); }
    }
    internal sealed class BoundedProcessResult
    {
        public bool started;
        public bool timed_out;
        public int exit_code;
        public string standard_error;
    }
    internal static class BoundedProcess
    {
        public static BoundedProcessResult Run(ProcessStartInfo start, int timeout)
        { throw new InvalidOperationException("Unexpected process or network request"); }
    }

    internal static class ManifestGateHarness
    {
        private const string NewEngineHash =
            "2d412a3b116fcb636b5f04387ba643edd88a194d1d41121e9f56d95ed3808144";
        private const string OldEngineHash =
            "555f4010448204be88a48a430a61a756db73b5f9cd03015675c0e167c024fb45";

        public static int Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            if (args.Length < 2 || args.Length > 3) return 2;
            string target = args[0];
            string mutation = args[1];
            string version = target == "codex" ? "0.2.0" :
                target == "claude" ? "0.1.25" : "0.1.14";
            string repository = target == "codex" ? "K7-LS/codex-base" :
                target == "claude" ? "K7-LS/claude-base-v2" : "K7-LS/opencode-base";
            string engineVersion = target == "codex" ? "0.5.12" : "0.5.10";
            string engineHash = target == "codex" ? NewEngineHash : OldEngineHash;
            string assetHash = new string('a', 64);
            string tag = target + "-v" + version;
            GitHubReleaseAsset asset = new GitHubReleaseAsset
            {
                name = target + "-base-" + version + ".zip",
                size = 1024,
                digest = "sha256:" + assetHash,
                browser_download_url = "https://github.com/" + repository +
                    "/releases/download/" + tag + "/package.zip"
            };
            GitHubLatestRelease release = new GitHubLatestRelease
            {
                tag_name = tag, draft = false, prerelease = false, immutable = true,
                assets = new List<GitHubReleaseAsset> { asset }
            };
            StableBaseManifest manifest = new StableBaseManifest
            {
                schema_version = 1, target = target, version = version,
                tag = tag, channel = "stable",
                foundation_engine_version = engineVersion,
                foundation_engine_manifest_sha256 = engineHash,
                source = new StableBaseSource
                {
                    repository = "https://github.com/" + repository
                },
                asset = new StableBaseAsset
                {
                    name = asset.name, sha256 = assetHash, bytes = asset.size
                },
                requires = new StableBaseRequirements
                {
                    immutable_release = true, release_attestation = true
                }
            };
            TargetEngineBinding binding = new TargetEngineBinding
            {
                target = target, engine_version = engineVersion,
                engine_manifest_sha256 = engineHash
            };
            switch (mutation)
            {
                case "compatible": break;
                case "cross-target":
                    // Keep the matching version and hash: target identity alone
                    // must reject the binding, including Claude/OpenCode 0.5.10.
                    if (args.Length != 3) return 2;
                    binding.target = args[2]; break;
                case "engine-version":
                    manifest.foundation_engine_version =
                        engineVersion == "0.5.12" ? "0.5.10" : "0.5.12"; break;
                case "engine-hash":
                    manifest.foundation_engine_manifest_sha256 = new string('f', 64); break;
                case "engine-hash-uppercase":
                    manifest.foundation_engine_manifest_sha256 = engineHash.ToUpperInvariant(); break;
                case "missing-binding": binding = null; break;
                case "missing-manifest": manifest = null; break;
                case "schema": manifest.schema_version = 2; break;
                case "target": manifest.target = "unexpected"; break;
                case "version": manifest.version = "9.9.9"; break;
                case "tag": manifest.tag = "other-v" + version; break;
                case "channel": manifest.channel = "candidate"; break;
                case "missing-engine-version": manifest.foundation_engine_version = null; break;
                case "missing-engine-hash": manifest.foundation_engine_manifest_sha256 = null; break;
                case "missing-source": manifest.source = null; break;
                case "repository": manifest.source.repository = "https://github.com/other/repo"; break;
                case "missing-asset": manifest.asset = null; break;
                case "asset-name": manifest.asset.name = "other.zip"; break;
                case "asset-hash": manifest.asset.sha256 = new string('b', 64); break;
                case "asset-size": manifest.asset.bytes++; break;
                case "missing-requirements": manifest.requires = null; break;
                case "mutable": manifest.requires.immutable_release = false; break;
                case "unattested": manifest.requires.release_attestation = false; break;
                default: return 2;
            }

            Type updater = typeof(BaseReleaseUpdater);
            MethodInfo getSource = updater.GetMethod("GetSource",
                BindingFlags.NonPublic | BindingFlags.Static);
            MethodInfo validate = updater.GetMethod("ValidateManifest",
                BindingFlags.NonPublic | BindingFlags.Static);
            object source = getSource.Invoke(null, new object[] { target });
            try
            {
                validate.Invoke(null, new object[] { source, release, manifest, asset, binding });
                WriteResult(true, null);
                return 0;
            }
            catch (TargetInvocationException exception)
            {
                WriteResult(false, exception.InnerException);
                return exception.InnerException is InvalidOperationException ? 0 : 3;
            }
        }

        private static void WriteResult(bool accepted, Exception error)
        {
            Console.WriteLine(new JavaScriptSerializer().Serialize(
                new Dictionary<string, object>
                {
                    { "accepted", accepted },
                    { "exception_type", error == null ? null : error.GetType().FullName },
                    { "message", error == null ? null : error.Message }
                }));
        }
    }
}
'''


def _compiler() -> Path:
    windows = Path(os.environ.get("WINDIR", os.environ.get("SystemRoot", "C:/Windows")))
    candidates = [
        windows / "Microsoft.NET" / kind / "v4.0.30319" / "csc.exe"
        for kind in ("Framework64", "Framework")
    ]
    on_path = shutil.which("csc.exe")
    if on_path:
        candidates.append(Path(on_path))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    pytest.skip("A local .NET Framework C# compiler is required; no downloads attempted")


@pytest.fixture(scope="module")
def manifest_gate(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("target-engine-updater")
    harness = work / "ManifestGateHarness.cs"
    harness.write_text(HARNESS, encoding="utf-8")
    executable = work / "ManifestGateHarness.exe"
    result = subprocess.run(
        [
            str(_compiler()), "/nologo", "/target:exe", "/r:System.Web.Extensions.dll",
            f"/out:{executable}", str(SOURCE), str(harness),
        ],
        cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return executable


def _validate(executable: Path, target: str, mutation: str, *arguments: str) -> dict:
    result = subprocess.run(
        [str(executable), target, mutation, *arguments],
        cwd=executable.parent, capture_output=True, text=True, encoding="utf-8-sig",
        errors="replace", timeout=15, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _assert_rejected(result: dict) -> None:
    assert result == {
        "accepted": False,
        "exception_type": "System.InvalidOperationException",
        "message": "Latest base release contract is incompatible",
    }


@pytest.mark.parametrize("target", TARGETS)
def test_compatible_engine_is_selected_per_target(manifest_gate: Path, target: str) -> None:
    assert _validate(manifest_gate, target, "compatible") == {
        "accepted": True, "exception_type": None, "message": None,
    }


@pytest.mark.parametrize("target,foreign_target", tuple(itertools.permutations(TARGETS, 2)))
def test_foreign_target_binding_fails_even_with_matching_hash_and_version(
    manifest_gate: Path, target: str, foreign_target: str,
) -> None:
    _assert_rejected(_validate(manifest_gate, target, "cross-target", foreign_target))


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("mutation", ("engine-version", "engine-hash"))
def test_engine_version_and_manifest_hash_are_bound(
    manifest_gate: Path, target: str, mutation: str,
) -> None:
    _assert_rejected(_validate(manifest_gate, target, mutation))


@pytest.mark.parametrize("target", TARGETS)
def test_engine_hash_accepts_uppercase_hex(manifest_gate: Path, target: str) -> None:
    assert _validate(manifest_gate, target, "engine-hash-uppercase")["accepted"] is True


@pytest.mark.parametrize("mutation", (
    "missing-binding", "missing-manifest", "schema", "target", "version", "tag",
    "channel", "missing-engine-version", "missing-engine-hash", "missing-source",
    "repository", "missing-asset", "asset-name", "asset-hash", "asset-size",
    "missing-requirements", "mutable", "unattested",
))
def test_stable_release_contract_remains_fail_closed(manifest_gate: Path, mutation: str) -> None:
    _assert_rejected(_validate(manifest_gate, "codex", mutation))
