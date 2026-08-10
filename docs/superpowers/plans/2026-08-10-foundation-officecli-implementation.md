# Foundation OfficeCLI and Managed Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Use superpowers:test-driven-development before every production change and superpowers:verification-before-completion before every commit.

**Goal:** выпустить Foundation `0.3.0`, который транзакционно устанавливает OfficeCLI `1.0.143`, безопасный shim/policy и compiled managed launchers для Claude, Codex и OpenCode.

**Architecture:** target ZIP приносит проверенные OfficeCLI bytes и session baseline; Foundation остаётся единственным runtime owner. Один global journal охватывает target files, shared tool, PATH/environment и launcher receipts. Compiled shim разрешает только документные команды. Compiled managed launcher выполняет session updater через fixed PowerShell `-File`, восстанавливает killed apply по durable journal и только затем запускает exact vendor executable.

**Tech Stack:** PowerShell 7/5.1, C#/.NET Framework compiler, Python 3.12 + pytest, Windows Job Objects, GitHub immutable releases and attestations.

## Global Constraints

- Source spec: `docs/superpowers/specs/2026-08-10-session-tools-officecli-design.md` at audited commit `394999c`.
- Preserve engine manifest schema `1`, protocol `1`, offline network contract and commands `plan/install/doctor/inventory/rollback`.
- Canonical OfficeCLI asset: version `1.0.143`, URL `https://github.com/iOfficeAI/OfficeCLI/releases/download/v1.0.143/officecli-win-x64.exe`, SHA-256 `d4d4c10fced307e209744cf98a56b003a6e613424fd651b08469274704afd2c6`.
- Keep upstream EXE private at `.llm-foundation/libexec/officecli/officecli.exe`; put only Foundation shim in PATH.
- Set current-user and child-process `OFFICECLI_NO_AUTO_INSTALL=1` and `OFFICECLI_SKIP_UPDATE=1`.
- Do not modify authentication, sessions, plugins, MCP configuration, projects or user-owned unknown skills.
- Use one durable global transaction for shared state across targets; target rollback cannot overwrite a later committed shared epoch.
- Do not publish automatically. Build and accept locally first; publication requires a separate reviewed release step.

## File Map

- `client-sources.lock.json` — canonical shared OfficeCLI source.
- `src/officecli-shim/Program.cs` — policy-enforcing public `officecli.exe`.
- `src/managed-launcher/Program.cs` — target-specific safe prelaunch and argv forwarding.
- `src/managed-launcher/SessionRecovery.cs` — launcher-side durable recovery.
- `support/officecli-command-policy.json` — exact allow/block policy.
- `tools/build-officecli-shim.ps1`, `tools/build-managed-launcher.ps1` — deterministic .NET Framework builds.
- `src/foundation.ps1` — schema validation, global transaction, shared install, granular ownership and receipts.
- `tools/build-engine.ps1`, `tools/foundation_release.py`, `tools/verify_foundation_release.py`, `tools/create_foundation_package_acceptance.py`, `tools/run-acceptance.py` — release chain.
- `src/gui/InstallerApp.cs`, `src/gui/ClientBootstrap.cs`, `src/gui/LaunchTarget.cs`, `src/gui/ClientLauncher.cs`, `tools/build-gui.ps1` — GUI and Launch Center integration.
- `tests/test_officecli_shim.py`, `tests/test_managed_launcher.py`, `tests/test_foundation_shared_tools.py`, `tests/test_foundation_transactions.py`, `tests/test_foundation_release.py`, `tests/test_gui.py`, `tests/test_launch_center.py`, `tests/test_launcher_runtime.py` — acceptance.

---

### Task 1: Canonical source, policy and OfficeCLI shim

**Files:**
- Modify: `client-sources.lock.json`
- Create: `support/officecli-command-policy.json`
- Create: `src/officecli-shim/Program.cs`
- Create: `tools/build-officecli-shim.ps1`
- Create: `tests/test_officecli_shim.py`

- [ ] Add RED tests for the exact source record, deterministic build, full-output version regex and compiled shim policy. Prove empty/bare, `install`, `skill`, `skills`, `mcp`, `mcp-serve`, `config`, update/self-update/internal aliases, leading options, `--`, `/`, `@` and unknown commands never launch a fake private EXE.
- [ ] Run `python -m pytest -q tests/test_officecli_shim.py` and record the expected missing-contract failures.
- [ ] Add the shared source record and strict policy. Allow only `open`, `close`, `watch`, `unwatch`, `mark`, `unmark`, `get-marks`, `goto`, `view`, `get`, `query`, `set`, `add`, `remove`, `move`, `swap`, `refresh`, `raw`, `raw-set`, `add-part`, `validate`, `save`, `batch`, `dump`, `import`, `create`, `merge`, `plugins`, `help`, `load_skill`, exact help forms and exact `--version`.
- [ ] Implement the compiled shim with `UseShellExecute=false`, exact private EXE path, strict policy load/hash check and `WindowsArgv.Serialize`. Cover empty, spaces, tabs, quotes, trailing backslashes, backslashes before quote and Cyrillic round-trip.
- [ ] Run the focused test to GREEN in PowerShell 7 and 5.1 build paths. Commit `feat: add managed OfficeCLI shim`.

### Task 2: Compiled managed launcher and killed-updater recovery

**Files:**
- Create: `src/managed-launcher/Program.cs`
- Create: `src/managed-launcher/SessionRecovery.cs`
- Create: `tools/build-managed-launcher.ps1`
- Create: `tests/test_managed_launcher.py`

- [ ] Add RED tests for target inference from `claude-managed.exe`, `codex-managed.exe`, `opencode-managed.exe`; committed receipt/hash checks; exact system PowerShell `-File` tokens; internally generated GUID/ticks; and no user argv reaching updater.
- [ ] Add RED argv tests for empty, spaces, tabs, quotes, trailing backslashes, `%`, `!`, `^`, `&`, `|`, `<`, `>` and Cyrillic reaching a fake vendor byte-for-byte.
- [ ] Add RED timeout tests for 22/25/30-second cutoffs, Job Object tree kill, phases `created`/`staged`, every journal `intent/applied` transition, idempotent recovery and `BLOCKED_SESSION_RECOVERY` without vendor launch.
- [ ] Implement deterministic .NET Framework launcher builds, shared Windows argv serializer, Job Object containment, strict receipt/journal parsing and launcher-side recovery.
- [ ] Run `python -m pytest -q tests/test_managed_launcher.py` to GREEN. Commit `feat: add safe managed client launchers`.

### Task 3: Backward-compatible package schema and granular ownership

**Files:**
- Modify: `tests/test_foundation.py`
- Create: `tests/test_foundation_shared_tools.py`
- Modify: `src/foundation.ps1`

- [ ] Add RED tests accepting legacy schema-1 manifests unchanged and new optional `retired_managed_paths`, `session_tools_baseline` and `shared_tools` fields only under the audited strict schemas.
- [ ] Add RED migration homes: clean; legacy broad skills ownership; legacy broad ownership plus an unmanaged local skill. Assert the local skill remains byte-identical through install/doctor/rollback.
- [ ] Implement strict shared payload rows and granular directory ownership. Exclude session-owned skill destinations from package-owned hashes and create baseline ownership state only after hash match.
- [ ] Run `python -m pytest -q tests/test_foundation.py tests/test_foundation_shared_tools.py` to GREEN in both PowerShell hosts. Commit `feat: accept session and shared tool package contracts`.

### Task 4: One global transaction for target, OfficeCLI and launchers

**Files:**
- Create: `tests/test_foundation_transactions.py`
- Modify: `tests/test_foundation.py`
- Modify: `src/foundation.ps1`

- [ ] Add RED tests for one global lock, durable active pointer/journal, per-operation `intent/applied`, write-through atomic state, cross-target concurrency, stale rollback epochs and crash injection at every target/shared/PATH/environment/launcher operation.
- [ ] Add RED OfficeCLI states `missing`, `exact`, `managed-older`, `compatible-newer`, `incompatible-newer`, `conflict`, including ownership-first classification and exact bundle identity: version/SHA, bundle version, epoch, shim, policy and environment.
- [ ] Implement global transaction primitives, shared receipt/provenance, version probe over full trimmed output, atomic private EXE/shim/policy replacement, idempotent PATH/environment changes and launcher/receipt installation.
- [ ] Make `install` execute `plan -> snapshot -> install -> doctor -> commit/rollback` under the same lock. Keep external post-commit doctor read-only.
- [ ] Run `python -m pytest -q tests/test_foundation.py tests/test_foundation_shared_tools.py tests/test_foundation_transactions.py` to GREEN in PowerShell 7 and 5.1. Commit `feat: transact shared OfficeCLI with target install`.

### Task 5: Foundation 0.3.0 release binding

**Files:**
- Modify: `VERSION`
- Modify: `tools/build-engine.ps1`
- Modify: `tools/foundation_release.py`
- Modify: `tools/verify_foundation_release.py`
- Modify: `tools/create_foundation_package_acceptance.py`
- Modify: `tools/run-acceptance.py`
- Modify: `tests/test_foundation_release.py`
- Modify: `tests/test_acceptance_runner.py`

- [ ] Add RED tests binding `shared-tools.lock.json`, OfficeCLI payload, shim, policy and three launcher bytes/hashes/sizes into deterministic engine/release manifests and package acceptance. Reject tamper, mutable release and missing attestation for every asset.
- [ ] Update `VERSION` from `0.2.2` to `0.3.0`. Keep engine protocol `1` and exclude the shared build-time OfficeCLI record from vendor client bootstrap lock processing.
- [ ] Download only in the release builder, verify exact SHA before use, derive size from bytes and build deterministic ZIP/evidence without network in unit tests.
- [ ] Run `python -m pytest -q tests/test_foundation_release.py tests/test_acceptance_runner.py`; run `pwsh` and `powershell.exe` syntax/build checks. Commit `release: prepare Foundation 0.3.0 shared tools`.

### Task 6: GUI installer and Launch Center use the same transaction/launcher

**Files:**
- Modify: `src/gui/InstallerApp.cs`
- Modify: `src/gui/ClientBootstrap.cs`
- Modify: `src/gui/LaunchTarget.cs`
- Modify: `src/gui/ClientLauncher.cs`
- Modify: `tools/build-gui.ps1`
- Modify: `tests/test_gui.py`
- Modify: `tests/test_launch_center.py`
- Modify: `tests/test_launcher_runtime.py`

- [ ] Add RED tests proving GUI passes the accepted target ZIP to one Foundation `install`, never installs OfficeCLI through `ClientBootstrap`, never performs a second rollback, and surfaces global transaction result codes.
- [ ] Add RED Launch Center tests proving CLI cards resolve only a committed target launcher/receipt and execute that exact launcher with the existing Direct/VPN/SingBox environment. Tampered/missing launcher must block.
- [ ] Implement GUI workflow and exact launcher resolution. Do not invoke `.cmd`, updater or vendor directly from Launch Center.
- [ ] Run `python -m pytest -q tests/test_gui.py tests/test_launch_center.py tests/test_launcher_runtime.py` to GREEN. Commit `feat: route installer and launch center through Foundation`.

### Task 7: Full verification and local Foundation candidate

**Files:**
- Read: complete branch diff and release outputs.
- Create only ignored outputs under `.work/officecli-0.3.0/`.

- [ ] Run `python -m pytest -q` with `LLM_FOUNDATION_CI_OFFLINE=1` and record exact pass/fail count.
- [ ] Run `pwsh -NoProfile -ExecutionPolicy Bypass -File tools/check-ps-syntax.ps1 -Root .` and the same with Windows PowerShell 5.1.
- [ ] Run `python tools/run-acceptance.py --evidence .work/officecli-0.3.0/evidence.json` from a clean committed worktree; require zero model requests and both PowerShell hosts.
- [ ] Build Foundation `0.3.0` candidate and independently verify every bound asset/hash, install/doctor/rollback, local unknown skill preservation and no skills/MCP/PATH changes from blocked OfficeCLI invocations.
- [ ] Run whole-branch independent review. Fix all Critical/Important findings, re-run affected tests and retain honest `candidate` status. Do not publish or mark stable in this task.

## Plan Self-Review

- Spec coverage: shim/policy Task 1; launcher/recovery Task 2; schema/ownership Task 3; global transaction Task 4; release binding Task 5; GUI Task 6; acceptance Task 7.
- Placeholder scan: none; versions, URL, hashes, paths, commands and result codes are explicit.
- Safety: upstream EXE stays private; blocked commands never reach it; unmanaged skills/auth/MCP/plugins remain outside managed writes; release publication is a separate gate.
