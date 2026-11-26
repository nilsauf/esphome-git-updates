# 14: Declare the Platinum tier and guard it

**What to build:** The declaration itself, as the last step, once every rule is true. It has three parts:

1. `quality_scale: platinum` in the manifest.
2. A complete `quality_scale.yaml`: all 54 rules, each `done` or `exempt`. Every exemption carries its reason. The intended exemptions:
   - no actions, conditions or triggers: `action-setup`, `docs-actions`, `docs-conditions`, `docs-triggers`, `action-exceptions`;
   - no network discovery: `discovery`, `discovery-update-info`;
   - one entry per Configuration, one functional entity: `dynamic-devices`, `entity-category`, `entity-disabled-by-default`;
   - no external library: `async-dependency`;
   - `brands`: a custom integration, whose assets ship in its own `brand/` directory and are not submitted to the brands repository.
3. A repository test that keeps the claim honest, since hassfest validates quality scale files for core integrations only.

The test asserts: every rule is present exactly once and no unknown rule is present; every rule at or below the declared tier is `done` or `exempt`; every `exempt` has a non-empty reason; the manifest's tier matches the file. It pins the evidence each local rule needs, so removing the evidence fails the test: the diagnostics module exists; the config flow has a reconfigure step; the update platform declares its parallel update limit; the entity sets a translation key that the icon definitions contain; the standalone CLI type check is part of CI; `strings.json` and `translations/en.json` agree. It does not assert documentation headings — docs are reviewed by a human.

**Blocked by:** 01, 02, 03, 04, 05, 06, 07, 08, 09, 10, 11, 12, 13

**Status:** resolved

- [ ] The manifest declares the Platinum tier and keeps the code owner.
- [ ] `quality_scale.yaml` lists all 54 rules with a status, and a comment on every exemption.
- [ ] The tiers read in the file match what the code and docs actually do; no rule is `done` on aspiration.
- [ ] The guard test passes, and fails when a rule is removed, a `done` rule loses its evidence, an exemption loses its reason, or the manifest tier changes without the file.
- [ ] The full test suite, strict mypy (both invocations), ruff and the per-module coverage check are green.
- [ ] The version is bumped in the manifest and the project file, ready for the existing release steps.

## Comments

Implemented and merged into `quality-scale-platinum` (2026-11-26), followed by the code-review fixes merge.
