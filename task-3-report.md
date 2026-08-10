# Task 3 report

## Scope

- Preserve package schema `1` compatibility.
- Accept only the audited optional `retired_managed_paths`,
  `session_tools_baseline`, and `shared_tools` contracts.
- Move skills ownership from a broad root to granular package and session
  ownership without changing unmanaged local skills.
- Bind a session baseline to the immutable release manifest and the already
  opened package bytes.

## RED evidence

- Added the optional-contract, migration-home, release-binding, baseline
  adoption, state-preservation, package-swap, and retirement tests in
  `tests/test_foundation_shared_tools.py` before the corresponding engine
  behavior.
- Confirmed the retirement RED with
  `python -m pytest -q tests/test_foundation_shared_tools.py -k retired_path -x`:
  the prior-owned unchanged skill remained present.
- The first full legacy rerun exposed a security regression:
  `test_foundation_engine_has_no_network_or_secret_material` failed because
  target names were embedded in the generic engine (`69 passed, 1 failed`).
  Kept the legacy assertion unchanged and derived target roots from the
  verified managed surface instead.

## GREEN implementation

- Validate legacy schema `1` unchanged and reject every undeclared optional
  property.
- Validate supplemental session/shared payload rows, hashes, sizes, ordering,
  destinations, environment, shim, and compatibility fields.
- Exclude session payload bytes from package-owned installed file rows.
- Adopt a pre-existing session tool only after an exact file/hash/size match;
  preserve a valid newer session state; block an unmanaged collision.
- Create the common session state and runtime recovery manifest inside the
  Foundation transaction.
- Delete a retired path only when the prior active state proves ownership and
  every current byte still matches; snapshot it for rollback.
- Preserve local or modified paths byte-for-byte through install, doctor, and
  rollback.
- Accept `-ReleaseManifest` and `-ReleaseManifestSha256` only as a pair. Use
  the exact sibling `release-manifest.json` for legacy bootstrap fallback.
- Hold the release manifest buffer and one exclusive package stream/ZipArchive
  through validation, snapshot, payload reads, install, and rollback.

## Verification

- `python -m pytest -q tests/test_foundation_shared_tools.py -k retired_path -x`
  -> `8 passed`.
- `python -m pytest -q tests/test_foundation_shared_tools.py`
  -> `54 passed`.
- `python -m pytest -q tests/test_foundation.py::test_foundation_engine_has_no_network_or_secret_material tests/test_foundation_shared_tools.py -k "baseline or migration" -x`
  -> `36 passed, 19 deselected`.
- `python -m pytest -q tests/test_foundation.py tests/test_foundation_shared_tools.py`
  -> `124 passed in 444.09s`.
- `pwsh -NoProfile -File tools/check-ps-syntax.ps1 -Root .`
  -> `PowerShell syntax PASS: 12`.
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/check-ps-syntax.ps1 -Root .`
  -> `PowerShell syntax PASS: 12`.
- `git diff --cached --check`
  -> no findings.

The pytest suite parameterizes its Foundation operations across PowerShell 7
and Windows PowerShell 5.1.

## Boundaries and limitations

- Performed no network access and no live-home install or mutation.
- Validated the `shared_tools` package contract only; shared tool installation
  and runtime policy remain assigned to the later shared-tool task.
- Treated the exact sibling manifest path as the documented same-user legacy
  bootstrap boundary, then held an exclusive handle after opening it.
- Did not modify or stage other agents' files or the untracked PowerShell
  analysis cache.
