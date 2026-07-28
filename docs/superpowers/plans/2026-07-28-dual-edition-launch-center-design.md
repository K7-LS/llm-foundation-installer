# Dual-Edition Installer and Launch Center Design

**Status:** Approved by the owner on 2026-07-28.

**Visual references:**

- `K-7_prezentatsia_logo.pdf`, supplied by the owner;
- `Ryzhii_774.svg`, supplied by the owner;
- approved interactive mockup: Employee `K-7 Signal System` and Owner
  `Signal Routing Console`.

## Outcome

The Foundation product becomes one codebase that produces two explicitly
different internal editions:

| Contract | Employee | Owner |
|---|---|---|
| Intended operator | Employee | Owner/developer only |
| Installed clients | Codex, OpenCode | Codex, Claude, OpenCode |
| Applications | Installer, AI Launch Center | Installer, AI Launch Center |
| Distribution | Internal employee deployment | Owner-controlled, distribution false |
| Packaging | `InternalUnsigned` | `InternalUnsigned` |
| Claude | Absent | Present as owner candidate; provider gate remains visible |
| Visual system | K-7 Signal System | Signal Routing Console |

The two editions must never be inferred from missing files or mutable local
configuration. Edition is a build-time input, is embedded in the executable
and bundle manifest, and is verified again at runtime.

## Product boundaries

### Installer

The Installer is a finite, reviewable workflow:

1. platform and edition preflight;
2. connection profile;
3. exact client selection;
4. immutable installation plan;
5. backup and transactional apply;
6. doctor and package verification;
7. handoff to AI Launch Center.

It changes only current-user state, does not require elevation, and never
performs a model request. A failed or incomplete step remains visibly blocked;
the UI does not convert partial acceptance into readiness.

### AI Launch Center

AI Launch Center is a persistent post-install application. It launches the
exact verified Desktop or CLI target through one of four explicit routes:

- `Direct`;
- `VPN` (the existing system/user VPN, without creating a VPN);
- `SingBox HTTP`;
- `SingBox HTTPS`.

The selected route is per launch. The current OpenCode choice is CLI-only,
because the verified employee workflow opens a terminal rather than a desktop
application. The Center also shows the last doctor result, package identity,
rollback readiness, and privacy-safe session evidence.

The Center does not turn transport into provider eligibility. A route can be
technically ready while the provider target remains blocked by its account or
region gate.

## Edition contract

Create an immutable `EditionProfile` with these fields:

```text
EditionId: Employee | Owner
DisplayName
DistributionAllowed
IncludedTargetIds
RequiredTargetIds
ThemeId
OwnerControlled
```

Required values:

```text
Employee
  DistributionAllowed = true
  IncludedTargetIds = [codex, opencode]
  RequiredTargetIds = [codex, opencode]
  ThemeId = K7Signal
  OwnerControlled = false

Owner
  DistributionAllowed = false
  IncludedTargetIds = [codex, claude, opencode]
  RequiredTargetIds = [codex, opencode]
  ThemeId = SignalConsole
  OwnerControlled = true
```

Claude is included only in Owner. Its candidate may be installed when its
offline package acceptance is valid, but a failed or absent provider marker is
shown as `OWNER_CANDIDATE / PROVIDER_GATE_BLOCKED`; it is not relabelled as a
full provider PASS. Employee manifests, UI strings, payloads and evidence must
contain no Claude target.

## Visual systems

### Employee — K-7 Signal System

Employee follows the supplied K-7 identity:

- K-7 Ink `#071E22`;
- Signal Orange `#FC4912`;
- white/paper application canvas;
- Mint Status `#77CBB9`;
- Engineering Blue `#30BCED` only where a secondary technical state needs it.

The official logo geometry is preserved from `Ryzhii_774.svg`. It is stored as
a WPF vector resource rather than a raster screenshot. The large orange
cable-arc motif communicates progress and route state; it is not decorative
wallpaper. Active stage and primary action use orange, verified state uses
mint, and the dark sidebar anchors navigation.

Typography:

- Bahnschrift SemiCondensed for section titles;
- Segoe UI for interface copy;
- Cascadia Mono for hashes, ports and evidence.

The Employee Installer and Launch Center share tokens but remain visually
distinct applications: the Installer emphasizes sequential completion; the
Center emphasizes rapid client and route selection.

### Owner — Signal Routing Console

Owner is a separate hi-tech system based on engineering topology rather than
generic neon:

- K-7 Ink as the base;
- Engineering Blue for active signal paths;
- Mint Status for verified nodes;
- Signal Orange only for owner-only controls, Claude gates and destructive
  recovery warnings;
- fine technical grid and signal trace;
- explicit topology `selected client -> local relay -> upstream`.

The Owner Installer retains the seven-step workflow but uses the console token
set. The Owner Launch Center exposes route topology, exact client identity,
session state, evidence and rollback controls without revealing credentials.

Animation is restrained and functional: route activation, node readiness and
progress transitions. The interface remains usable when Windows animations are
disabled.

## Connection and launcher architecture

Reuse the current `ConnectionProfile` and its DPAPI-protected credential store.
Do not create a parallel launcher credential database.

One shared launcher engine serves both editions:

```text
EditionProfile
  -> LaunchTarget
  -> ConnectionRoute
  -> verified runtime/client resolution
  -> process launch
  -> cleanup verification
  -> redacted LauncherSessionResult
```

`Direct` and `VPN` do not start sing-box. `SingBox HTTP` and
`SingBox HTTPS` use one hash-locked per-user sing-box runtime. The launcher
generates a protected temporary configuration, routes only the reviewed AI
domains through the upstream, leaves unrelated traffic direct, and removes
temporary material after the owned process exits.

Prefer a process-local proxy environment. A temporary Windows proxy lease is
allowed only for a reviewed application that cannot inherit the process
environment. The lease snapshots and restores exact registry value
presence/type/data, uses a current-user mutex and recovery journal, and fails
closed if cleanup cannot be proved.

The owner-supplied PowerShell launchers and test harness are immutable behavior
references. Their proven behaviors are ported into the shared engine; the
release does not invoke the reference scripts as hidden production logic.

## Packaging

`tools/build-gui.ps1` accepts an explicit edition and produces edition-bound
artifacts:

```text
K7-AI-Foundation-Employee-InternalUnsigned.exe
K7-AI-Launch-Center-Employee-InternalUnsigned.exe
K7-AI-Foundation-Owner-InternalUnsigned.exe
K7-AI-Launch-Center-Owner-InternalUnsigned.exe
```

Each executable and bundle manifest records:

- edition id;
- product role (`Installer` or `LaunchCenter`);
- exact included and required targets;
- theme id;
- distribution flag;
- source-lock and package hashes;
- build version and build mode.

The build rejects:

- an unknown or omitted edition;
- Claude material in Employee;
- `DistributionAllowed=true` in Owner;
- edition/manifest/executable mismatch;
- signing material in `InternalUnsigned`;
- a mutable or unaccepted client payload.

## Error handling and evidence

Operator-visible states are limited to `READY`, `BLOCKED`, `FAILED` and
`OWNER_CANDIDATE`. Each state has a stable reason code and a plain-language
remediation. Logs and evidence contain hashes, versions, route types and
cleanup results, never proxy credentials, DPAPI bytes, usernames, account
identifiers or model responses.

Launcher success requires:

```text
PROFILE_VALIDATED
-> RUNTIME_VERIFIED (sing-box routes only)
-> CONFIG_CHECKED (sing-box routes only)
-> EXACT_CLIENT_STARTED
-> CLIENT_EXITED
-> RUNTIME_STOPPED
-> WINDOWS_RESTORED
-> TEMP_REMOVED
```

Any uncertain cleanup result is `FAILED`, not a warning.

## Test strategy

Implementation follows RED-GREEN-REFACTOR. Tests must prove:

1. edition schema and build-time fail-closed behavior;
2. Employee contains exactly Codex and OpenCode;
3. Owner contains Codex, Claude and OpenCode and remains distribution false;
4. official K-7 vector geometry and exact color tokens are embedded;
5. both applications build deterministically in PowerShell 7 and 5.1;
6. the visible OpenCode choice resolves only to the verified CLI target;
7. Direct/VPN avoid sing-box and proxy mutation;
8. HTTP/HTTPS use the verified shared runtime and restore state exactly;
9. no credential or PII sentinel appears in logs, manifests or binaries;
10. Installer can hand off to the matching edition of Launch Center;
11. synthetic failure injection covers bind, child, runtime and cleanup
    failures;
12. the final Employee and Owner bytes pass independent package and GUI
    review.

The existing 169-test suite is the starting baseline. On this Windows machine
it passed on 2026-07-28 in 683.55 seconds, so full-suite commands require a
timeout greater than five minutes.

## Delivery gates

Employee can be called distributable only after exact Codex and OpenCode
payload acceptance, dual-shell build, synthetic lifecycle PASS, clean-user
pilot, GUI visual review, secret scan and immutable release verification.

Owner can be built and used by the owner with
`DistributionAllowed=false`. Claude remains visibly provider-gated until a
valid provider marker exists; packaging it for owner control is not evidence
that the provider gate passed.

No push, tag, release or immutable publication is implied by design approval
or by a successful local build. Publication remains a separate explicit
owner-authorized action after review.
