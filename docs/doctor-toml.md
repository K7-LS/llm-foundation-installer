# Doctor: typed TOML state in engine 0.5.12

For files declared in `managed_surface.merge_toml_files`, a successful install
records `toml_contracts` in active state. Each contract binds its relative path,
schema version, original package file SHA-256, and typed requirements. The
requirements come from validated canonical ZIP bytes before installation writes.
Other managed files retain full byte/hash checks.

Doctor checks the complete current TOML document for valid syntax, then compares
the canonical keys by type and value. Reordering keys, comments, whitespace, and
equivalent TOML spellings do not change a requirement. A boolean does not equal
a string or integer. Arrays are ordered, atomic values; table keys are ordinal
and case-sensitive. An empty-table requirement permits additional local keys.

Top-level `model`, `model_reasoning_effort`, `model_provider`, `model_providers`,
`providers`, and `model_verbosity` are user-owned. Desired-state protected table
prefixes are also excluded. This does not disable the existing inventory and
KEEP checks: an unapproved registration or a missing saved exception still fails.
Invalid syntax anywhere in the document is rejected, including user-owned sections.

The requirements store hashes of typed values, not configuration values.
Diagnostics identify keys without quoting source snippets or current values.
Without `-Package`, doctor trusts the contract in local active state. The state
file is not a cryptographic trust root; package verification supplies an
independent canonical binding when that assurance is required.
Canonical UTF-8 BOM is removed for parsing, while source SHA-256 binds the raw
package bytes, including the BOM.

## Historical state

An absent `toml_contracts` field means a historical receipt. An empty, partial,
malformed, or duplicate contract is invalid and cannot fall back to that path.

An unchanged historical TOML file can still pass the old whole-file hash check.
If it changed, doctor requires the original ZIP through `-Package`. Without that
file it returns `LEGACY_TOML_BASELINE_REQUIRED`. Doctor does not fetch packages,
search Git, adopt the current config as a baseline, or rewrite active state.

With the original package, doctor binds its SHA-256, version, client, target,
managed surface, desired-state rules, and environment to the installed receipt.
It validates the hash-bound pre-install snapshot without staging a restoration.
The historical merge, including saved exceptions and newline handling, must
reproduce the recorded post-install SHA-256 and byte count exactly. Only then may
canonical typed requirements replace the whole-file comparison for this check.
The pre-install backup is never mistaken for the post-install baseline.

`-Package` may refer to an older engine only in this doctor path. Install and plan
retain the current engine-version requirement. Other health checks, including
shared-tool integrity, remain active. A successful TOML reconstruction alone
does not certify the whole historical installation.

## Parser and delivery limits

The offline bundle carries Tomlyn 0.19.0, netstandard2.0, its BSD-2-Clause licence,
and [provenance](../src/vendor/tomlyn/provenance.json). Build and runtime check the
DLL pin; runtime also rejects a conflicting loaded assembly. No package manager,
download, or external parser process runs during doctor.

This pin has known limits. Its integer model can wrap overflow, so the adapter
checks parsed integer tokens against the signed 64-bit range before conversion.
It rejects hexadecimal spellings longer than sixteen digits even when leading
zeros would make the value fit. That is a reported failure, never a match to a
different value. Documents are capped at 1 MiB; model depth and node limits are
checked after parsing. The parser itself has no separately enforced depth cap.

The 0.5.12 delivery matrix requires all thirteen bundle files and both doctor test
modules. The historical 0.5.11 matrix remains nine files and seven test modules.
Old package evidence does not certify the new bytes. Full installer GUI, model
behavior, publication, and real-profile installation require their own evidence.
