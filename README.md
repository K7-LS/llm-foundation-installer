# LLM Foundation Installer

Offline, current-user, target-neutral installer engine for independently
versioned LLM-base packages.

The engine implements five commands:

```text
plan
install
doctor
inventory
rollback
```

It does not contain target names, provider rules, model defaults, network
clients, release discovery, authentication, telemetry, feedback upload, or
session upload. A target package declares:

- its exact client identity and accepted version;
- replace-only files and exact discovery directories;
- preserved paths that must never overlap the managed surface;
- a strictly one-way sync policy;
- every payload file, byte length, and SHA-256.

The target-native wrapper obtains trustworthy client-version evidence and
passes `-ClientId` and `-ClientVersion` to the engine. The engine rejects a
mismatch, downgrade, corrupt ZIP, extra file, path traversal, protected-path
overlap, or reparse point before target-home mutation.

## Development

```powershell
py -3.12 -m pytest -q
py -3.12 .\tools\run-acceptance.py
pwsh -NoProfile -File .\tools\build-engine.ps1 -OutputRoot .\dist\engine
```

`FOUNDATION_SYNTHETIC: PASS` covers fake homes only. It is not a Codex canary,
employee rollout, or full-program release.
