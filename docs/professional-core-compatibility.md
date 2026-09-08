# Professional core delivery compatibility (source change)

This change supports the unpublished `professional-core-v1` acceptance protocol
and the exact `k7-professional-core-v1` contract. It is not an engine release,
installer build, signature, or consumer installation. The local engine candidate
is 0.5.11; APP_VERSION remains unchanged. The separately verified bootstrap
lock now selects official Codex CLI 0.153.1 for a new installation.

Foundation validates the known contract reference, the exact embedded bytes,
and the matching release reference. A core package requires its release
manifest even without session-tools/shared-tools. Historical packages retain
their existing optional/explicit release-manifest rules and external-client
version policy.

The installer builder reads the ZIP even when external current-protocol markers
are absent. Any current marker requires the complete protocol, matching package,
release and evidence bindings, `CORE_BEHAVIOR=PASS`, `CODEX_CANARY=PASS`, and the
specified `MATCHED_AB=NOT_REQUIRED` reason. The nested evidence must
declare `evaluation_mode=RELEASE_PACKAGE`; source snapshots are not
accepted package evidence. The nested wire field is
`core_behavior_evidence`; the old `core_behavior` spelling collided with the
`CORE_BEHAVIOR` verdict in PowerShell and is not accepted for this protocol.

The builder consumes a trusted producer verdict. It does not repeat the full
Python criterion/event/source validator, and a signature or self-hash does not
prove that behavioral observations or an independent review actually happened.
Historical evidence retains its own meaning. InternalUnsigned remains a
separate technical route, not accepted release evidence.

Current core packages also require `FOUNDATION_ENGINE_ACCEPTANCE=PASS` under
`foundation-engine-isolated-v1`. `FOUNDATION_SYNTHETIC` and
`INSTALLER_ACCEPTANCE` remain `NOT_RUN`: the historical full installer/GUI suite
was not run by this protocol. The builder checks the protocol envelope, two
PowerShell build inventories and lifecycle matrices against all nine actual
files under `.codex/base/foundation/<engine-version>/` in the ZIP. The producer
rechecks the JUnit and per-scenario receipt text; the builder remains a consumer
of that trusted verdict, not a second behavioral evaluator.

Engine 0.5.11 snapshots the five bundled OfficeCLI files, its receipt, and the
three User environment values PATH/OFFICECLI_NO_AUTO_INSTALL/OFFICECLI_SKIP_UPDATE.
Acceptance mode uses a fixture environment file; it does not write User registry
values. Snapshot schema 5 validates complete backup bytes before restoration;
schemas 3/4 remain readable. A shared lock and receipt generation prevent this
engine from rolling back a later target's shared tools. Older concurrent engine
writers do not honor this lock; cross-version concurrent installation is not
certified.

`tools/run-engine-acceptance.py` requires a clean committed source, explicit local
OfficeCLI/compiler dependencies, and a new child of an authorized workspace. It
builds in PowerShell 7 and 5.1, compares all nine files, then runs an explicit
engine-only test list. The seven actual built-engine scenarios run in each shell:
fresh/existing install and rollback, late failure, interrupted recovery, snapshot
tampering, receipt drift, and foreign-generation refusal. JUnit and UTF-8 receipts
are hashed and retained. A source-level test subset is not labelled full
installer acceptance. PS5.1 tests use short fixture homes because .NET Framework
atomic writes remain sensitive to MAX_PATH; this candidate does not claim
general long-path support.

`tests/test_professional_core_compat.py` loads only PowerShell function ASTs and
calls read-only production validators on synthetic temporary ZIP bundles. It
does not execute the Foundation CLI, build scripts, GUI, clients, or models.
The fixed fixture is copied verbatim from codex-base `evals/core/contract.json`;
its SHA-256 is `028ba6363bff000b4aa8551ca27a66a69631cb29dacdc5319a4ce0b696b3a184`.
All mutant ZIP and outer artifact hashes are recomputed before validation.
Synthetic PASS fixtures are not release attestations.

Before delivery: accept the new engine artifact; build the final base package;
run package-bound lifecycle and core evaluation against its exact bytes and
observed client; then obtain the separately authorized publication/installer
acceptance. Existing consumers require a trusted transition delivering the new
sync script and policy together. A policy-only bridge is not supported.
The external, separately observed Codex CLI 0.153.1 may be retained: Foundation's
external-client check preserves its historical ID/version-syntax policy. The
official-download lock now points to 0.153.1, with release metadata, checksum
list and Windows x64 package hashes bound to that version. The official
`install.ps1` bytes are unchanged from 0.153.0. The old 0.153.0 metadata fixtures
remain as historical files; current offline bundle tests use new 0.153.1 fixtures.
Official sources are the [tagged release](https://github.com/openai/codex/releases/tag/rust-v0.153.1)
and its [release metadata](https://releases.openai.com/codex/releases/0.153.1/release.json).

This bootstrap lock update does not change the nine engine payload files or
relabel acceptance evidence produced from installer commit
`f3e5757fed87b8d1154d82d0de87ffbfc25fc6e5`. Existing exact-package evidence stays
bound to its original bytes. Offline metadata and bundle checks do not certify
the full GUI installer, a real-profile installation, or an automatic migration
of the download path; those require their own observed acceptance.
