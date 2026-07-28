# SingBox Launch Center Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the owner-proven HTTP/HTTPS SingBox launcher behavior into the
edition-bound AI Launch Center with exact client identity, direct fallback and
provable rollback.

**Architecture:** A hash-locked per-user sing-box runtime receives a protected
temporary config generated from the existing DPAPI connection profile. Direct
and VPN bypass the runtime; HTTP and HTTPS route reviewed AI domains through a
local relay while unrelated traffic remains direct. Process-local proxy state
is preferred, with a transactional Windows proxy lease only for a reviewed
client that cannot inherit process environment.

**Tech Stack:** C# 7.3/.NET Framework 4.8, WPF, sing-box 1.13.14,
PowerShell 7/5.1, Python 3.12/pytest.

## Global Constraints

- Reuse the approved edition and product contracts from
  `2026-07-28-dual-edition-wpf-implementation.md`.
- Reuse `%USERPROFILE%\.llm-foundation\connection.json` and DPAPI-protected
  `connection.cred`; do not add a second credential store.
- Never put credentials in arguments, URLs, logs, manifests or evidence.
- Never disable TLS verification, Defender, hash/signature checks or sandboxing.
- Do not create a service, TUN device, Proxifier dependency or admin requirement.
- Do not use transport to bypass provider supported-region, account or
  safeguard policy.
- Do not make model calls during implementation or offline acceptance.
- Manage only the exact process started by Launch Center.
- Cleanup uncertainty is `FAIL`, never `PASS_WITH_WARNING`.

## Accepted behavior references

| File | Bytes | SHA-256 |
|---|---:|---|
| `Start-AI-SingBox-HTTPS.ps1` | 36,565 | `5B5A10AE706E479F08C79377ABC204E682F73494EE83E98117AF7A7BA91F661D` |
| `Start-AI-SingBox-HTTP.ps1` | 36,413 | `4687C15CAFB749E8C9A25C93EE2FA7ED3FD9D27A6CC260D79F05CE040281E0D9` |
| `Test-AI-SingBox-Launchers.ps1` | 5,787 | `CEBA3CB018C937125573EAE8448A7639C6AFF5B90E42E19CCC4A6D599E116BF2` |
| `AI-SINGBOX-LAUNCHERS-HANDOFF.md` | 9,647 | `EE102E8FA61AD3840674BF8C440204550B917F0119B5FF408C33E5FF09B6D654` |

All three scripts parsed without PowerShell AST errors. The supplied synthetic
harness passed 56/56 without credentials, network, sing-box or a live user
home. This is input provenance, not release evidence.

## File Structure

- `docs/reference/ai-singbox-launchers/`: immutable owner-supplied references
  and privacy-safe source manifest.
- `runtime-sources.lock.json`: official immutable sing-box asset lock.
- `src/gui/RuntimeBootstrap.cs`: atomic per-user runtime verification/install.
- `src/gui/SingBoxConfig.cs`: deterministic redacted config generation.
- `src/gui/SingBoxSession.cs`: runtime lifecycle and bounded listener ownership.
- `src/gui/WindowsProxyLease.cs`: exact snapshot, recovery and restore.
- `src/gui/ClientLauncher.cs`: route dispatch for exact owned client process.
- `tests/fixtures/fake-sing-box.ps1`: deterministic failure-injection runtime.
- `tests/test_launcher_reference.py`: reference hash and secret scan.
- `tests/test_launcher_runtime.py`: runtime, config, lease and lifecycle tests.
- `tools/launcher_canary.py`: default-offline exact-byte canary evidence.

---

### Task 1: Intake immutable launcher references

**Files:**

- Create: `docs/reference/ai-singbox-launchers/SOURCE-MANIFEST.json`
- Create: `docs/reference/ai-singbox-launchers/Start-AI-SingBox-HTTPS.ps1`
- Create: `docs/reference/ai-singbox-launchers/Start-AI-SingBox-HTTP.ps1`
- Create: `docs/reference/ai-singbox-launchers/Test-AI-SingBox-Launchers.ps1`
- Create: `docs/reference/ai-singbox-launchers/AI-SINGBOX-LAUNCHERS-HANDOFF.md`
- Create: `tests/test_launcher_reference.py`

**Interfaces:**

- Consumes: exact supplied files from the recorded Downloads paths.
- Produces: hash-bound, non-executed behavior reference.

- [ ] **Step 1: Write the failing integrity test**

```python
def test_launcher_reference_manifest_is_exact_and_private():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert set(manifest["files"]) == EXPECTED_HASHES.keys()
    for name, expected in EXPECTED_HASHES.items():
        path = MANIFEST.parent / name
        assert path.stat().st_size == expected["bytes"]
        assert sha256(path) == expected["sha256"]
    serialized = json.dumps(manifest).lower()
    for forbidden in ("c:\\\\users", "password", "username", "proxy_host"):
        assert forbidden not in serialized
```

- [ ] **Step 2: Run the test and confirm RED**

Run:

```powershell
py -3.12 -m pytest tests/test_launcher_reference.py -vv
```

Expected: the reference directory is absent.

- [ ] **Step 3: Copy exact bytes and write provenance**

Copy only the five owner-supplied files after independently recomputing SHA-256.
The manifest records filename, byte length, SHA-256, received date and
`contains_credentials=false`; it records no absolute source path.

- [ ] **Step 4: Run integrity and PowerShell AST checks**

Run:

```powershell
py -3.12 -m pytest tests/test_launcher_reference.py -vv
pwsh -NoProfile -File tools/check-ps-syntax.ps1
powershell.exe -NoProfile -File tools/check-ps-syntax.ps1
```

Expected: exact hashes pass and both parsers report zero errors.

- [ ] **Step 5: Commit**

```powershell
git add docs/reference/ai-singbox-launchers tests/test_launcher_reference.py
git commit -m "test: preserve approved SingBox launcher behavior"
```

### Task 2: Lock and atomically install the sing-box runtime

**Files:**

- Create: `runtime-sources.lock.json`
- Create: `src/gui/RuntimeBootstrap.cs`
- Modify: `tools/build-gui.ps1`
- Modify: `tools/build-edition.ps1`
- Test: `tests/test_launcher_runtime.py`

**Interfaces:**

- Consumes: official versioned sing-box 1.13.14 Windows AMD64 asset.
- Produces:
  `RuntimeBootstrap.Plan(home)`,
  `RuntimeBootstrap.Install(home, downloader)`,
  `RuntimeBootstrap.Verify(home) -> VerifiedRuntime`.

- [ ] **Step 1: Add RED source-lock and atomic-install tests**

Reject mutable/latest URLs, HTTP URLs, wrong SHA-256, unsafe ZIP entries,
reparse ancestors, downgrade, unexpected installed files and execution before
verification. Assert final path:

```text
%USERPROFILE%\.llm-foundation\runtimes\sing-box\1.13.14\sing-box.exe
```

- [ ] **Step 2: Run runtime tests and confirm RED**

Run:

```powershell
py -3.12 -m pytest tests/test_launcher_runtime.py `
  -k "runtime_lock or runtime_install" -vv
```

Expected: source lock and `RuntimeBootstrap` are absent.

- [ ] **Step 3: Implement verified per-user runtime bootstrap**

Use the same download-to-random-partial, hash-before-extract, safe-entry and
atomic-directory-swap pattern as `ClientBootstrap`. `Verify` checks exact
version directory, executable byte length and SHA-256 and returns no executable
until every check passes.

- [ ] **Step 4: Embed the source lock and rerun tests**

Run:

```powershell
py -3.12 -m pytest tests/test_launcher_runtime.py `
  -k "runtime_lock or runtime_install" -vv
```

Expected: all runtime bootstrap tests pass without network by using a local
locked fixture source.

- [ ] **Step 5: Commit**

```powershell
git add runtime-sources.lock.json src/gui/RuntimeBootstrap.cs `
  tools/build-gui.ps1 tools/build-edition.ps1 `
  tests/test_launcher_runtime.py
git commit -m "feat: add verified SingBox runtime bootstrap"
```

### Task 3: Generate protected deterministic sing-box sessions

**Files:**

- Create: `src/gui/SingBoxConfig.cs`
- Create: `src/gui/SingBoxSession.cs`
- Create: `src/gui/launcher-routing-domains.json`
- Modify: `src/gui/ConnectionProfile.cs`
- Test: `tests/test_launcher_runtime.py`

**Interfaces:**

```text
SingBoxSession.Start(
  VerifiedRuntime runtime,
  ConnectionProfile profile,
  LaunchTarget target,
  string sessionRoot
) -> RunningSingBoxSession
```

- [ ] **Step 1: Add RED config and listener-ownership tests**

Cover HTTP and HTTPS upstreams, authenticated and anonymous profiles, reviewed
AI suffix routing, unrelated direct routing, missing/unknown domain keys,
occupied ports, failed `sing-box check`, runtime exit before readiness and
foreign listeners.

Assert generated JSON has:

```python
assert config["route"]["final"] == "direct"
assert config["outbounds"][0]["tag"] == "upstream"
assert config["outbounds"][1] == {"tag": "direct", "type": "direct"}
assert sentinel_password not in command_line
assert sentinel_password not in redacted_evidence
```

- [ ] **Step 2: Run session tests and confirm RED**

Run:

```powershell
py -3.12 -m pytest tests/test_launcher_runtime.py `
  -k "config or listener or session_start" -vv
```

Expected: config/session classes are absent.

- [ ] **Step 3: Implement config generation and protected lifetime**

Decrypt DPAPI only in memory. Create a unique session directory under
`%USERPROFILE%\.llm-foundation\launcher-state\sessions`, apply a current-user
ACL before writing, write the config without logging it, run exact
`sing-box.exe check -c <config>`, and start the verified runtime.

Use bounded bind/start/retry and require a session nonce in the owned runtime
state. Never accept a listener solely because a port is open.

- [ ] **Step 4: Implement quiet upstream preflight**

Probe the explicitly selected upstream type with no verbose output. Map `407`,
TLS validation failure, timeout and scheme mismatch to stable redacted reason
codes. A failed preflight changes neither Windows proxy nor last-known-good
connection profile.

- [ ] **Step 5: Run config/session tests**

Run:

```powershell
py -3.12 -m pytest tests/test_launcher_runtime.py `
  -k "config or listener or session_start or upstream_preflight" -vv
```

Expected: all focused tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/gui/SingBoxConfig.cs src/gui/SingBoxSession.cs `
  src/gui/ConnectionProfile.cs src/gui/launcher-routing-domains.json `
  tests/test_launcher_runtime.py
git commit -m "feat: create protected SingBox launch sessions"
```

### Task 4: Add an exact transactional Windows proxy lease

**Files:**

- Create: `src/gui/WindowsProxyLease.cs`
- Modify: `src/gui/SingBoxSession.cs`
- Test: `tests/test_launcher_runtime.py`

**Interfaces:**

- `WindowsProxySnapshot.Capture()` preserves value existence, type and bytes for
  `ProxyEnable`, `ProxyServer`, `ProxyOverride`, `AutoConfigURL`.
- `WindowsProxyLease.Acquire(localProxy, journalRoot)` returns an owned lease.
- `DisposeVerified() -> ProxyCleanupResult`.

- [ ] **Step 1: Add RED state-machine tests**

Cover absent values, custom values, PAC URL, Ctrl+C, child crash, runtime crash,
launcher crash simulation, mutex contention, failed restore, failed temp
deletion and stale recovery journal. Run only against an isolated registry
adapter fixture, never live HKCU.

- [ ] **Step 2: Run lease tests and confirm RED**

Run:

```powershell
py -3.12 -m pytest tests/test_launcher_runtime.py -k proxy_lease -vv
```

Expected: `WindowsProxyLease` is absent.

- [ ] **Step 3: Implement exact snapshot, mutex and journal**

Inject an `IUserProxyStore` so tests use an in-memory store. Production uses
HKCU Internet Settings. Acquire a current-user named mutex before mutation,
write a credential-free recovery journal, apply local proxy, refresh WinINet,
then restore exact original presence/type/data and verify a second snapshot
matches byte-for-byte.

- [ ] **Step 4: Implement explicit stale recovery**

At Launch Center start, expose a redacted delta and require operator
confirmation before restoring. Refuse automatic restore if current values no
longer equal the lease-written values.

- [ ] **Step 5: Run lease tests**

Run:

```powershell
py -3.12 -m pytest tests/test_launcher_runtime.py -k proxy_lease -vv
```

Expected: all normal, failure-injection and concurrency cases pass.

- [ ] **Step 6: Commit**

```powershell
git add src/gui/WindowsProxyLease.cs src/gui/SingBoxSession.cs `
  tests/test_launcher_runtime.py
git commit -m "feat: add recoverable Windows proxy lease"
```

### Task 5: Route exact Launch Center targets through SingBox

**Files:**

- Modify: `src/gui/ClientLauncher.cs`
- Modify: `src/gui/InstallerApp.cs`
- Modify: `src/gui/LaunchCenterEmployeeView.xaml`
- Modify: `src/gui/LaunchCenterOwnerView.xaml`
- Test: `tests/test_launcher_runtime.py`
- Test: `tests/test_launch_center.py`

**Interfaces:**

- Consumes: `Direct|VPN|SingBoxHttp|SingBoxHttps`.
- Produces:
  `ClientLauncher.Start(target, route) -> LauncherSessionResult`.

- [ ] **Step 1: Add RED full-route matrix tests**

For each edition and exact launch target, assert Direct/VPN avoid sing-box;
HTTP/HTTPS start the fake verified runtime; process-local environment is used
when supported; a system proxy lease is acquired only by an explicit adapter.
Assert OpenCode Desktop starts the managed Desktop EXE in every route.

- [ ] **Step 2: Run route matrix and confirm RED**

Run:

```powershell
py -3.12 -m pytest tests/test_launcher_runtime.py `
  tests/test_launch_center.py -k route_matrix -vv
```

Expected: HTTP/HTTPS dispatch is absent.

- [ ] **Step 3: Implement route dispatch and visible lifecycle**

Bind four route buttons to a typed enum. Disable launch until profile, runtime
and exact client are ready. Stream only redacted lifecycle events:

```text
PROFILE_VALIDATED
RUNTIME_VERIFIED
CONFIG_CHECKED
LOCAL_PROXY_READY
EXACT_CLIENT_STARTED
CLIENT_EXITED
RUNTIME_STOPPED
WINDOWS_RESTORED
TEMP_REMOVED
```

On any exception, unwind in reverse order and return `FAILED` unless every
cleanup proof succeeds.

- [ ] **Step 4: Run route and UI-state tests**

Run:

```powershell
py -3.12 -m pytest tests/test_launcher_runtime.py `
  tests/test_launch_center.py -k "route_matrix or lifecycle or cleanup" -vv
```

Expected: all route/lifecycle tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/gui/ClientLauncher.cs src/gui/InstallerApp.cs `
  src/gui/LaunchCenter*View.xaml tests/test_launcher_runtime.py `
  tests/test_launch_center.py
git commit -m "feat: route Launch Center through SingBox"
```

### Task 6: Add deterministic fake runtime and privacy scans

**Files:**

- Create: `tests/fixtures/fake-sing-box.ps1`
- Modify: `tests/test_launcher_runtime.py`
- Modify: `tools/check-ps-syntax.ps1`
- Modify: `tools/run-acceptance.py`

**Interfaces:**

- Fake runtime supports only `check` and `run`, accepts a fixture-only config,
  emits stable event names, and injects bind/start/stop failures.
- Acceptance produces credential-free JSON with exact artifact hashes.

- [ ] **Step 1: Write the fake-runtime contract test**

Assert unknown commands fail, `check` rejects malformed config, `run` owns its
listener, and the sentinel credential never reaches arguments or event output.

- [ ] **Step 2: Implement the minimal fake runtime**

Use `TcpListener` only on loopback. Accept failure modes through a fixture
field inside the isolated test config, not environment variables shared with
production.

- [ ] **Step 3: Add complete lifecycle and secret scans**

Run normal close plus injected client/runtime/cleanup failure. Recursively scan
source, logs, evidence, bundle and temp fixtures for the sentinel password and
credential-like JSON keys. One unexpected hit fails acceptance.

- [ ] **Step 4: Run focused and acceptance suites**

Run:

```powershell
py -3.12 -m pytest tests/test_launcher_runtime.py -q
py -3.12 tools/run-acceptance.py
```

Expected: zero failures, zero external network requests and zero live-user
profile or proxy mutations.

- [ ] **Step 5: Commit**

```powershell
git add tests/fixtures/fake-sing-box.ps1 `
  tests/test_launcher_runtime.py tools/check-ps-syntax.ps1 `
  tools/run-acceptance.py
git commit -m "test: verify SingBox lifecycle and privacy"
```

### Task 7: Bind launcher evidence to the two release gates

**Files:**

- Create: `tools/launcher_canary.py`
- Create: `tests/test_launcher_canary.py`
- Modify: `tools/installer_release.py`
- Modify: `tests/test_installer_release.py`
- Modify: `docs/ИНСТРУКЦИЯ-СОТРУДНИКУ.md`
- Modify: `docs/ИНСТРУКЦИЯ-ВЛАДЕЛЬЦУ.md`
- Modify: `README.md`

**Interfaces:**

- Default canary is offline and records zero model requests.
- Live mode requires a separate explicit flag and authorization and is not run
  by this implementation plan.

- [ ] **Step 1: Add RED canary-schema tests**

Require:

```text
schema_version
edition_id
installer_sha256
launch_center_sha256
runtime_version
runtime_sha256
client_id
upstream_type
proxy_scope
direct_route_probe
upstream_route_probe
cleanup_verified
model_requests
operator_visual_confirmation
evidence_body_sha256
```

Reject PII keys, credential-like fields, mismatched artifact hashes,
`model_requests != 0`, false cleanup and missing OpenCode Desktop visual
confirmation.

- [ ] **Step 2: Implement default-offline canary generation**

Without `--execute-live`, validate only schema, source locks and exact local
candidate bytes. Do not open an app, runtime, registry, account or endpoint.

- [ ] **Step 3: Add edition-specific release rules**

Employee promotion requires accepted Codex/OpenCode, synthetic launcher PASS,
exact cleanup, clean-user pilot and GUI review. Owner candidate requires all
offline package checks, remains `distribution_allowed=false`, and records
Claude provider state without converting a blocked gate to PASS.

- [ ] **Step 4: Run focused and full suites**

Run:

```powershell
py -3.12 -m pytest tests/test_launcher_canary.py `
  tests/test_installer_release.py -q
py -3.12 -m pytest -q
```

Expected: zero failures; allow at least 15 minutes for the full suite.

- [ ] **Step 5: Commit**

```powershell
git add tools/launcher_canary.py tools/installer_release.py `
  tests/test_launcher_canary.py tests/test_installer_release.py `
  README.md docs
git commit -m "feat: gate releases on launcher evidence"
```
