# LLM Foundation Installer

Professional Windows installer and offline, current-user Foundation engine for
three independently versioned native bases:

- Codex;
- Claude Code;
- OpenCode.

The WPF application is DPI-aware, uses a branded multi-resolution executable
icon, detects installed clients, presents the exact plan before mutation,
shows live progress, and runs `doctor` after every install. A failed `doctor`
triggers automatic rollback. The application does not require administrator
rights.

## Installation lifecycle

1. Validate every embedded target package against release acceptance evidence.
2. Detect the exact installed client version.
3. Let the user select one or more ready targets.
4. Build and display a deterministic Foundation plan.
5. Create a complete backup of the managed surface.
6. Apply the target atomically.
7. Run `doctor`; rollback automatically if it fails.
8. Write a local report under `~/.llm-foundation/reports/`.

The engine implements `plan`, `install`, `doctor`, `inventory`, and `rollback`.
It rejects a client mismatch, downgrade, corrupt ZIP, extra file, path
traversal, protected-path overlap, incompatible engine, incomplete managed
surface, unsafe reparse point, or concurrent mutation before changing the
target home.

## Connection modes

Connection setup is shared by the GUI, release check, and `$sync-base`:

- **Direct** — clears inherited proxy variables.
- **VPN** — also requires no proxy; absence of a proxy is not a blocker.
- **Proxy** — HTTP, HTTPS, or SOCKS5 with either no authentication or
  username/password authentication.

Proxy credentials are encrypted with Windows DPAPI for the current user and
are never stored in the JSON profile or written to logs. A network probe runs
only when the user explicitly clicks the test button. Package installation
itself is offline.

See [the employee operator guide](docs/EMPLOYEE-OPERATOR-GUIDE.md) for the
complete workflow, preserved data, connection troubleshooting, and release
gates.

## Trust and distribution

A target package declares its exact client identity and accepted version,
managed and preserved paths, one-way sync policy, and every payload SHA-256.
An employee build additionally requires:

- accepted packages for all three targets;
- immutable stable release evidence and asset attestations;
- current, PII-free provider-eligibility evidence with all required controls;
- a current-user Authenticode code-signing certificate;
- a successful timestamped signature.

An `unsigned-preview` manifest is intentionally marked
`employee_distribution_allowed: false`. It is for local visual and synthetic
acceptance only.

## Development

```powershell
py -3.12 -m pytest -q
py -3.12 .\tools\run-acceptance.py

pwsh -NoProfile -File .\tools\build-gui.ps1 `
  -OutputRoot .\dist\gui-preview
```

An employee build is fail-closed:

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
  -ProviderEligibilityEvidence .\provider-eligibility-evidence.json `
  -EmployeeRelease `
  -SigningCertificateThumbprint <code-signing-thumbprint>
```

Provider eligibility evidence expires within seven days, contains no employee
identity, location, IP, or account data, is embedded into the signed
executable, and is SHA-256-bound into `bundle-manifest.json`. Any build that
contains an accepted Claude package requires it. Runtime catalog/preflight
rechecks the embedded or sidecar evidence hash and expiry and reports
`policy_blocked` instead of enabling Claude when it is invalid.

Acceptance refuses a dirty Git worktree and writes commit/tree-bound evidence.
`FOUNDATION_SYNTHETIC: PASS` covers fake homes only. It is not a client canary,
employee rollout, or full-program release.
