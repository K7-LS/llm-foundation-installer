# LLM Foundation Installer

Windows 10/11 current-user installer for three independently versioned native
bases:

- Codex Desktop + Codex CLI;
- Claude Code;
- OpenCode Desktop + CLI.

The WPF application is DPI-aware, has a seven-stage employee workflow, does
not require administrator rights, and never collects LLM credentials. It
downloads clients only from the hash-locked sources in
`client-sources.lock.json`, verifies SHA-256 and Authenticode publisher before
execution, then installs each base through the pinned Foundation engine.

## Employee workflow

1. Check Windows, accepted packages, and installed clients.
2. Select `Direct`, `VPN`, or `Proxy`.
3. Download and verify missing official clients.
4. Show the deterministic base plan.
5. Backup, install, run `doctor`, and rollback automatically on failure.
6. Open the installed clients for interactive authorization.
7. Write a local result and show `$sync-base` / rollback guidance.

Exact client versions are left unchanged. Older versions can be upgraded.
Newer or different versions are never downgraded automatically; only the
affected base is blocked. Codex Desktop uses the exact Microsoft Store Product
ID and validates package name, publisher, architecture, and `SignatureKind`.
No ambiguous Store/winget search is used.

Codex CLI `0.146.0-alpha.3.1` is installed by the `install.ps1` asset attached
to the same official OpenAI release tag. The root installer currently published
at `chatgpt.com/codex/install.ps1` rejects the two-part alpha suffix, so it is
not used for this pinned version. The release-specific script is downloaded to
staging, checked against SHA-256
`397cad1d3091728fc59531018c4b2cd99b49b51b36c6ad42f7ec304d8da8ba4f`,
AST-checked, and only then executed with
`-Release 0.146.0-alpha.3.1`.

Foundation provides `plan`, `install`, `doctor`, `inventory`, and `rollback`.
It rejects corrupt ZIPs, extra files, path traversal, reparse-point paths,
protected-path overlap, incompatible engines, concurrent mutations, and
unsupported clients before changing the target home.

## Connection and credentials

- **Direct** — inherited proxy variables are cleared.
- **VPN** — uses system routing; absence of proxy is expected and never a
  blocker.
- **Proxy** — HTTP, HTTPS, or SOCKS5, with optional username/password.

Proxy credentials are protected with Windows DPAPI for the current user. They
are passed to child processes only through temporary environment variables and
never written to argv, manifests, evidence, or logs. Verbose `curl` is not
used.

LLM authorization remains inside Codex, Claude, and OpenCode. Existing auth,
sessions, memories, state, projects, and external workspaces are outside the
managed surface and remain untouched. Consumer devices never upload feedback,
telemetry, session reports, or local changes.

## Distribution modes

```text
-DistributionMode Preview | InternalUnsigned | PublicSigned
```

- `Preview` — development and synthetic validation only.
- `InternalUnsigned` — employee distribution after all target/provider/client
  evidence passes. Windows may show `Unknown Publisher` or SmartScreen.
- `PublicSigned` — also requires a valid timestamped Authenticode signature.

The internal unsigned manifest explicitly records:

```json
{
  "distribution_mode": "internal_unsigned",
  "signature": "unsigned-internal",
  "employee_release": true,
  "employee_distribution_allowed": true,
  "public_distribution_allowed": false,
  "windows_warning_expected": true
}
```

`PublicSigned` is implemented but currently `DEFERRED_BY_OWNER`; lack of a
certificate does not block `InternalUnsigned`.

## Build and test

The release build host needs Python 3.12, PowerShell 7, Windows PowerShell
5.1, and Microsoft Visual Studio Build Tools with the Roslyn/MSBuild
component. Roslyn deterministic compilation is mandatory; the legacy
Framework `csc.exe` is rejected rather than producing unstable EXE bytes.

```powershell
py -3.12 -m pytest -q
py -3.12 .\tools\run-acceptance.py

pwsh -NoProfile -File .\tools\build-gui.ps1 `
  -OutputRoot .\dist\gui-preview `
  -DistributionMode Preview
```

`run-acceptance.py` refuses a dirty Git worktree. Its evidence binds the
commit, tree, tracked source groups, both PowerShell builds, test counts and
the exact three engine files.

### Foundation 0.2.1 release

The employee build never rebuilds Foundation from the current worktree. First
prepare the exact synthetic-accepted engine bytes:

```powershell
py -3.12 .\tools\promote_foundation.py `
  --engine-root .\.work\acceptance\engine-ps7 `
  --acceptance-evidence .\dist\foundation-acceptance.json `
  --output .\dist\foundation-stable-0.2.1
```

Publish those assets under `foundation-engine-v0.2.1`, then run:

```powershell
py -3.12 .\tools\verify_foundation_release.py `
  --manifest .\dist\foundation-stable-0.2.1\release-manifest.json `
  --asset .\dist\foundation-stable-0.2.1\foundation-engine-0.2.1.zip `
  --output .\dist\foundation-stable-0.2.1\release-verification.json

py -3.12 .\tools\create_foundation_package_acceptance.py `
  --manifest .\dist\foundation-stable-0.2.1\release-manifest.json `
  --evidence .\dist\foundation-stable-0.2.1\acceptance-evidence.json `
  --release-verification `
    .\dist\foundation-stable-0.2.1\release-verification.json `
  --output .\dist\foundation-stable-0.2.1\package-acceptance.json
```

### Internal employee candidate

After all three target releases have their own post-publication
`package-acceptance.json`, create current PII-free provider evidence:

```powershell
pwsh -NoProfile -File .\tools\new-provider-eligibility-evidence.ps1 `
  -OutputPath .\provider-eligibility-evidence.json `
  -ConfirmEmployeeLocationEligibility `
  -ConfirmOrganizationEligibility `
  -ConfirmIndividualAccounts `
  -ConfirmNoRegionOrBanBypass `
  -ConfirmNoUnattendedConsumerAutomation

pwsh -NoProfile -File .\tools\build-gui.ps1 `
  -OutputRoot .\dist\employee `
  -PackageRoot <accepted-packages-root> `
  -FoundationPackageRoot .\dist\foundation-stable-0.2.1 `
  -ProviderEligibilityEvidence .\provider-eligibility-evidence.json `
  -DistributionMode InternalUnsigned
```

Provider evidence is PII-free, expires within seven days, and is hash-bound to
the bundle. Each target manifest must reference the same accepted Foundation
`engine-manifest.json`; a mismatch blocks the build.

### Hub canary, draft, pilot, publication

```powershell
py -3.12 .\tools\hub_canary.py `
  --execute-approved-hub-canary `
  --bundle .\dist\employee `
  --home <isolated-canary-home> `
  --output .\dist\hub-canary.json

py -3.12 .\tools\installer_release.py `
  --bundle .\dist\employee `
  --hub-canary .\dist\hub-canary.json `
  --output .\dist\installer-draft-0.3.0
```

The clean-PC pilot uses the exact draft EXE. Every pilot check is explicitly
confirmed in a PII-free record made by `pilot_evidence.py`; no machine name,
account, IP address, token or credential is stored. After a passing pilot:

```powershell
py -3.12 .\tools\pilot_release.py `
  --draft .\dist\installer-draft-0.3.0 `
  --pilot-evidence <pilot-evidence.json> `
  --output .\dist\installer-stable-0.3.0
```

Final metadata and evidence are necessarily produced after the pilot, while
`LLMFoundationInstaller.exe` remains byte-for-byte identical to the draft
canary and pilot EXE. After immutable publication, verify the release and
every asset:

```powershell
py -3.12 .\tools\installer_release_verifier.py `
  --stable-root .\dist\installer-stable-0.3.0 `
  --output .\dist\installer-v0.3.0-release-verification.json
```

`FOUNDATION_SYNTHETIC: PASS` covers fake homes only. It is not a provider
canary, clean-PC pilot, or final employee release.

See [the employee operator guide](docs/EMPLOYEE-OPERATOR-GUIDE.md) for the
complete operating and release procedure.
