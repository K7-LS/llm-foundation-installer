# Dual-Edition WPF Products Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build edition-bound Employee and Owner versions of the Foundation
Installer and persistent AI Launch Center from one C# WPF codebase.

**Architecture:** `build-gui.ps1` generates and embeds a canonical edition
contract plus product-role contract before compiling. Runtime code validates
those resources, filters the target catalog by edition, and loads one of four
edition/product XAML views. A separate build orchestrator produces the two
executables and one manifest for each edition.

**Tech Stack:** C# 7.3, WPF on .NET Framework 4.8, PowerShell 7/5.1, Roslyn
`csc.exe`, Python 3.12/pytest.

## Global Constraints

- Work directly on `main` as explicitly requested by the owner.
- Keep `InternalUnsigned`; do not use a signing certificate.
- Employee includes exactly `codex` and `opencode`.
- Owner includes `codex`, `claude`, and `opencode` and always records
  `distribution_allowed=false`.
- Claude is never embedded, named, rendered or evidenced in Employee.
- Owner Claude may be `OWNER_CANDIDATE / PROVIDER_GATE_BLOCKED`; do not turn a
  failed provider marker into `PASS`.
- Product roles are `Installer` and `LaunchCenter`.
- The official K-7 vector geometry and exact brand colors are build resources.
- No model request, live employee-home write, push, tag or release is part of
  these implementation tasks.
- Preserve Windows x64 build `19041+`, current-user-only operation, no
  telemetry and user-initiated network.

## File Structure

- `src/gui/EditionProfile.cs`: canonical runtime edition and product contracts.
- `src/gui/EditionTheme.cs`: exact theme tokens and view-resource selection.
- `src/gui/LaunchTarget.cs`: Desktop/CLI target identities shown by Launch
  Center.
- `src/gui/ClientLauncher.cs`: exact verified-process resolution and Direct/VPN
  launch.
- `src/gui/InstallerApp.cs`: shared entry point and Installer workflow host.
- `src/gui/InstallerEmployeeView.xaml`: K-7 Employee Installer.
- `src/gui/InstallerOwnerView.xaml`: Owner hi-tech Installer.
- `src/gui/LaunchCenterEmployeeView.xaml`: K-7 Employee Launch Center.
- `src/gui/LaunchCenterOwnerView.xaml`: Owner Signal Routing Console.
- `tools/build-gui.ps1`: compile one explicit edition/product pair.
- `tools/build-edition.ps1`: assemble both products for one edition.
- `tests/test_editions.py`: focused edition, view and package contract tests.
- `tests/test_launch_center.py`: focused exact-target and handoff tests.

---

### Task 1: Embed and validate the immutable edition contract

**Files:**

- Create: `src/gui/EditionProfile.cs`
- Modify: `tools/build-gui.ps1`
- Modify: `src/gui/InstallerApp.cs`
- Test: `tests/test_editions.py`

**Interfaces:**

- Consumes: build arguments `-Edition Employee|Owner` and
  `-ProductRole Installer|LaunchCenter`.
- Produces:
  `EditionProfile.LoadEmbedded() -> EditionProfile`,
  `EditionProfile.Includes(string) -> bool`,
  `EditionProfile.Requires(string) -> bool`.

- [ ] **Step 1: Write the failing edition-resource tests**

Create `tests/test_editions.py` with a preview-build helper and assertions:

```python
@pytest.mark.parametrize(
    ("edition", "included", "required", "distribution_allowed"),
    [
        ("Employee", ["codex", "opencode"], ["codex", "opencode"], True),
        ("Owner", ["claude", "codex", "opencode"], ["codex", "opencode"], False),
    ],
)
def test_embedded_edition_contract(
    tmp_path, edition, included, required, distribution_allowed
):
    bundle = build_preview(tmp_path, edition, "Installer")
    result = subprocess.run(
        [str(next(bundle.glob("*.exe"))), "--describe-edition"],
        text=True, capture_output=True, check=True, timeout=30,
    )
    value = json.loads(result.stdout)
    assert value["edition_id"] == edition
    assert value["included_target_ids"] == included
    assert value["required_target_ids"] == required
    assert value["distribution_allowed"] is distribution_allowed
```

Also assert that omitted/unknown edition and omitted/unknown product role make
`build-gui.ps1` exit non-zero before creating an EXE.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
py -3.12 -m pytest tests/test_editions.py -k edition_contract -vv
```

Expected: failure because the build parameters and
`--describe-edition` do not exist.

- [ ] **Step 3: Implement the minimal immutable contract**

`EditionProfile.cs` defines:

```csharp
internal sealed class EditionProfile
{
    public string edition_id { get; set; }
    public string display_name { get; set; }
    public bool distribution_allowed { get; set; }
    public string[] included_target_ids { get; set; }
    public string[] required_target_ids { get; set; }
    public string theme_id { get; set; }
    public bool owner_controlled { get; set; }

    public static EditionProfile LoadEmbedded();
    public bool Includes(string targetId);
    public bool Requires(string targetId);
    public void Validate();
}
```

`build-gui.ps1` makes both arguments mandatory, writes canonical UTF-8 JSON,
embeds it as `EditionProfile.json`, and deletes the temporary resource after
compilation. `Validate()` requires exact property sets and exact values for the
two known profiles. `Program.Main` implements `--describe-edition`.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run:

```powershell
py -3.12 -m pytest tests/test_editions.py -k edition_contract -vv
```

Expected: all edition-contract tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/gui/EditionProfile.cs src/gui/InstallerApp.cs `
  tools/build-gui.ps1 tests/test_editions.py
git commit -m "feat: bind builds to explicit editions"
```

### Task 2: Make target acceptance edition-aware

**Files:**

- Modify: `tools/build-gui.ps1`
- Modify: `src/gui/InstallerApp.cs`
- Test: `tests/test_editions.py`

**Interfaces:**

- Consumes: `EditionProfile.IncludedTargetIds` and accepted package records.
- Produces:
  `ProductCatalog.Load(bundleRoot, edition) -> ProductCatalog`,
  edition-bound `bundle-manifest.json`.

- [ ] **Step 1: Add RED target-matrix tests**

Add tests that build accepted fixtures and assert:

```python
assert employee_manifest["targets"] == ["codex", "opencode"]
assert employee_manifest["verdicts"]["EMPLOYEE_INSTALLER_INTERNAL"] == "PASS"
assert "claude" not in json.dumps(employee_manifest).lower()

assert owner_manifest["targets"] == ["claude", "codex", "opencode"]
assert owner_manifest["employee_distribution_allowed"] is False
assert owner_manifest["owner_controlled"] is True
```

Also prove Employee rejects a package root containing Claude, Employee rejects
a missing Codex/OpenCode acceptance, Owner rejects an unknown fourth target,
and Owner can carry Claude with a blocked provider gate without marking
`FULL_RELEASE_CLAUDE=PASS`.

- [ ] **Step 2: Run the target tests and confirm RED**

Run:

```powershell
py -3.12 -m pytest tests/test_editions.py -k target_matrix -vv
```

Expected: current hard-coded `claude,codex,opencode` checks fail Employee.

- [ ] **Step 3: Implement edition-specific package validation**

Replace the hard-coded three-target gate with exact set comparison against the
embedded edition profile. In Employee, fail before compilation if any Claude
file, accepted record or provider-evidence resource is present. In Owner,
preserve the provider record but compute:

```text
FULL_RELEASE_CLAUDE = PASS only for valid provider evidence
FULL_RELEASE_CLAUDE = NOT_PASS otherwise
OWNER_CLAUDE_STATE = OWNER_CANDIDATE or PROVIDER_READY
employee_distribution_allowed = false
```

Pass `EditionProfile` into `ProductCatalog.Load` and filter catalog rows before
any UI binding.

- [ ] **Step 4: Run target and legacy release-gate tests**

Run:

```powershell
py -3.12 -m pytest tests/test_editions.py -k target_matrix -vv
py -3.12 -m pytest tests/test_gui.py `
  -k "employee_distribution or provider_eligibility or accepted_claude" -vv
```

Expected: focused new tests pass; every legacy three-target test calls the
explicit Owner profile, while Employee-gate tests call Employee.

- [ ] **Step 5: Commit**

```powershell
git add tools/build-gui.ps1 src/gui/InstallerApp.cs `
  tests/test_editions.py tests/test_gui.py
git commit -m "feat: enforce edition target matrices"
```

### Task 3: Add exact K-7 and Signal Console view resources

**Files:**

- Create: `src/gui/EditionTheme.cs`
- Create: `src/gui/InstallerEmployeeView.xaml`
- Create: `src/gui/InstallerOwnerView.xaml`
- Create: `src/gui/LaunchCenterEmployeeView.xaml`
- Create: `src/gui/LaunchCenterOwnerView.xaml`
- Modify: `tools/build-gui.ps1`
- Modify: `src/gui/InstallerApp.cs`
- Test: `tests/test_editions.py`

**Interfaces:**

- Consumes: `EditionProfile.theme_id` and embedded product role.
- Produces:
  `EditionTheme.ViewResource(profile, productRole) -> string`,
  `InstallerView.Create(bundleRoot, profile, productRole) -> UserControl`.

- [ ] **Step 1: Add RED resource and token tests**

Assert all four views are embedded and the Employee views contain:

```text
#071E22
#FC4912
#77CBB9
Bahnschrift SemiCondensed
Segoe UI
Cascadia Mono
M144.91,200h-50l-32.38-48.57
7.62-7.06
```

Assert the Owner views contain `#30BCED`, `OWNER CONTROLLED`,
`Selected client`, `Local relay`, `Upstream`, and do not contain generic
`Neon`, `Cyberpunk` or a second logo geometry.

- [ ] **Step 2: Run view tests and confirm RED**

Run:

```powershell
py -3.12 -m pytest tests/test_editions.py -k "view_resource or brand_token" -vv
```

Expected: the four XAML files and selector do not exist.

- [ ] **Step 3: Implement the four approved views**

Port the approved mockups to WPF using vector `Path.Data`, `Grid`,
`Border`, `ItemsControl` and named controls. Keep all workflow control names
required by `InstallerActions`. Give Launch Center its own named controls:

```text
LaunchTargetList
RouteDirect
RouteVpn
RouteHttp
RouteHttps
LaunchSelected
RouteStatus
EvidenceStatus
RollbackStatus
```

Use static vector resources only; do not add an SVG rendering dependency.
`EditionTheme` maps the exact profile/product pair to one resource name and
throws on every unknown combination.

- [ ] **Step 4: Render all four previews**

Run:

```powershell
py -3.12 -m pytest tests/test_editions.py -k render_preview -vv
```

Expected: four 1440x900 PNG previews are created, are non-empty, and contain
the expected edition-specific dominant colors.

- [ ] **Step 5: Commit**

```powershell
git add src/gui/EditionTheme.cs src/gui/*View.xaml `
  src/gui/InstallerApp.cs tools/build-gui.ps1 tests/test_editions.py
git commit -m "feat: add K7 and owner console WPF views"
```

### Task 4: Split Installer and Launch Center runtime behavior

**Files:**

- Create: `src/gui/LaunchTarget.cs`
- Modify: `src/gui/InstallerApp.cs`
- Modify: `src/gui/ClientBootstrap.cs`
- Test: `tests/test_launch_center.py`

**Interfaces:**

- Consumes: embedded product role and edition-filtered catalog.
- Produces:
  `LaunchTargetCatalog.ForEdition(profile, bundleRoot)`,
  `ClientBootstrap.ResolveLaunchTarget(home, clientId)`.

- [ ] **Step 1: Add RED product-role and exact-target tests**

Build both product roles and assert:

```python
assert installer["app_id"] == "k7-ai-foundation-installer"
assert center["app_id"] == "k7-ai-launch-center"
assert installer["product_role"] == "Installer"
assert center["product_role"] == "LaunchCenter"
assert employee_center["targets"] == ["codex-desktop", "codex-cli",
                                      "opencode-desktop", "opencode-cli"]
assert "claude-code" not in employee_center["targets"]
assert "claude-code" in owner_center["targets"]
```

The internal catalog may retain both exact adapters for compatibility tests,
but the visible Employee and Owner choice resolves OpenCode only to
`opencode-cli`. Assert that the UI never substitutes the dormant desktop
adapter or an unverified `opencode` from `PATH`.

- [ ] **Step 2: Run product-role tests and confirm RED**

Run:

```powershell
py -3.12 -m pytest tests/test_launch_center.py `
  -k "product_role or exact_target" -vv
```

Expected: Launch Center and exact launch-target resolution do not exist.

- [ ] **Step 3: Implement product-role dispatch and resolution**

`Program.Main` loads both contracts before parsing operational commands.
Interactive `Installer` binds `InstallerActions`; interactive `LaunchCenter`
binds `LaunchCenterActions`. Add:

```csharp
internal sealed class LaunchTarget
{
    public string target_id { get; set; }
    public string client_id { get; set; }
    public string role { get; set; }
    public string display_name { get; set; }
}
```

`ClientBootstrap.ResolveLaunchTarget` validates the stored managed-client
record, file length and SHA-256 immediately before returning the executable.
Missing Desktop returns `BLOCKED_DESKTOP_NOT_FOUND`; it cannot substitute CLI.

- [ ] **Step 4: Run exact-target and existing bootstrap tests**

Run:

```powershell
py -3.12 -m pytest tests/test_launch_center.py -k exact_target -vv
py -3.12 -m pytest tests/test_gui.py `
  -k "managed_desktop or client_plan_and_install" -vv
```

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/gui/LaunchTarget.cs src/gui/InstallerApp.cs `
  src/gui/ClientBootstrap.cs tests/test_launch_center.py
git commit -m "feat: add edition-bound launch target catalog"
```

### Task 5: Implement Direct/VPN launch and Installer handoff

**Files:**

- Create: `src/gui/ClientLauncher.cs`
- Modify: `src/gui/InstallerApp.cs`
- Modify: `src/gui/ConnectionProfile.cs`
- Test: `tests/test_launch_center.py`

**Interfaces:**

- Consumes:
  `ClientBootstrap.ResolveLaunchTarget`,
  `ConnectionProfileStore.Load`,
  route `Direct|VPN`.
- Produces:
  `ClientLauncher.Start(target, route) -> LauncherSessionResult`,
  `InstallerActions.OpenMatchingLaunchCenter()`.

- [ ] **Step 1: Add RED process-environment and handoff tests**

Use an echo-process fixture to prove Direct and VPN:

```python
assert result["status"] == "PASS"
assert result["transport"] in {"Direct", "VPN"}
assert result["uses_proxy"] is False
assert result["cleanup_verified"] is True
assert "HTTP_PROXY" not in child_environment
assert launched_path == expected_verified_executable
```

Build mismatched Installer/Center editions and assert handoff rejects them.
Build matching products and assert Installer resolves the sibling Center by
manifest hash and `edition_id`.

- [ ] **Step 2: Run launch/handoff tests and confirm RED**

Run:

```powershell
py -3.12 -m pytest tests/test_launch_center.py `
  -k "direct_vpn or installer_handoff" -vv
```

Expected: launcher and matching-product handoff do not exist.

- [ ] **Step 3: Implement owned-process launch**

`ClientLauncher` starts only the exact resolved executable, stores the returned
`Process`, waits asynchronously, and reports only stable reason codes. It does
not search for or kill processes by short name. Installer handoff validates
the sibling bundle manifest and executable hash before starting it.

- [ ] **Step 4: Run focused launch tests**

Run:

```powershell
py -3.12 -m pytest tests/test_launch_center.py `
  -k "direct_vpn or installer_handoff or no_cli_fallback" -vv
```

Expected: all focused tests pass with isolated test homes.

- [ ] **Step 5: Commit**

```powershell
git add src/gui/ClientLauncher.cs src/gui/InstallerApp.cs `
  src/gui/ConnectionProfile.cs tests/test_launch_center.py
git commit -m "feat: launch verified clients from Launch Center"
```

### Task 6: Assemble deterministic two-product edition bundles

**Files:**

- Create: `tools/build-edition.ps1`
- Modify: `tools/build-gui.ps1`
- Modify: `tests/test_editions.py`
- Modify: `tests/test_gui.py`

**Interfaces:**

- Consumes: one edition, accepted package root, foundation package root and
  optional Owner provider evidence.
- Produces: two edition-named EXEs and one `bundle-manifest.json`.

- [ ] **Step 1: Add RED bundle-name and determinism tests**

Assert exact artifact names:

```text
K7-AI-Foundation-Employee-InternalUnsigned.exe
K7-AI-Launch-Center-Employee-InternalUnsigned.exe
K7-AI-Foundation-Owner-InternalUnsigned.exe
K7-AI-Launch-Center-Owner-InternalUnsigned.exe
```

Build each edition twice and compare SHA-256 for matching inputs. Assert the
manifest binds both EXE hashes, roles, edition, target matrix and theme.

- [ ] **Step 2: Run bundle tests and confirm RED**

Run:

```powershell
py -3.12 -m pytest tests/test_editions.py `
  -k "artifact_names or deterministic_edition_bundle" -vv
```

Expected: `build-edition.ps1` does not exist.

- [ ] **Step 3: Implement the orchestrator**

`build-edition.ps1` creates two temporary output roots, calls
`build-gui.ps1` once per product role with the same edition inputs, verifies
each child manifest, copies only exact expected artifacts into a fresh final
root, writes one canonical manifest, and removes temporary roots. A partial
bundle is deleted and the command exits non-zero.

- [ ] **Step 4: Run dual-shell and deterministic build tests**

Run:

```powershell
py -3.12 -m pytest tests/test_editions.py -vv
py -3.12 -m pytest tests/test_gui.py `
  -k "builder_supports_powershell or byte_deterministic" -vv
```

Expected: both PowerShell 7 and Windows PowerShell 5.1 pass.

- [ ] **Step 5: Commit**

```powershell
git add tools/build-edition.ps1 tools/build-gui.ps1 `
  tests/test_editions.py tests/test_gui.py
git commit -m "feat: build deterministic dual-product editions"
```

### Task 7: Update operator documentation and run the edition gate

**Files:**

- Modify: `README.md`
- Modify: `docs/ИНСТРУКЦИЯ-СОТРУДНИКУ.md`
- Create: `docs/ИНСТРУКЦИЯ-ВЛАДЕЛЬЦУ.md`
- Modify: `tests/test_editions.py`
- Modify: `tests/test_gui.py`

**Interfaces:**

- Consumes: completed dual-product build and exact commands.
- Produces: role-specific installation, launch, rollback and known-gate
  guidance.

- [ ] **Step 1: Add RED documentation assertions**

Assert Employee guide names only Codex/OpenCode, explains Direct/VPN/HTTP/HTTPS,
InternalUnsigned and SmartScreen, and states transport is not provider bypass.
Assert Owner guide says `distribution_allowed=false`, documents Claude
provider-gate states, and forbids redistribution.

- [ ] **Step 2: Write the role-specific guides**

Document exact build, preview, installation, Launch Center, doctor, rollback
and evidence paths. Include the exact warning that `InternalUnsigned` may show
Windows Unknown Publisher and does not weaken hash/package verification.

- [ ] **Step 3: Run focused and full tests**

Run:

```powershell
py -3.12 -m pytest tests/test_editions.py tests/test_launch_center.py -q
py -3.12 -m pytest -q
```

Expected: focused tests pass; full suite finishes with zero failures. Allow at
least 15 minutes for the full suite on the current machine.

- [ ] **Step 4: Render and inspect four final previews**

Generate Installer and Launch Center PNGs for both editions at 1440x900. Check
for clipped text, hidden controls, wrong logo geometry, incorrect target list
and color-token drift. Record the four SHA-256 values in the session report.

- [ ] **Step 5: Commit**

```powershell
git add README.md docs tests
git commit -m "docs: publish dual-edition operator workflow"
```
