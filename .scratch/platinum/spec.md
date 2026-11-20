# Quality scale platinum: spec

Status: ready-for-agent

Terms are defined in `GLOSSARY.md`. Relevant decisions: ADR-0001 (one config entry per Tracked Config), ADR-0002 (an Install is followed through the Device Builder and re-attached after a restart), ADR-0003 (the Installed Record owns what the integration remembers), ADR-0005 (one combined entity, on the Linked Device when there is one), ADR-0006 (raw YAML, read through the Device Builder), ADR-0007 (Install rewrites Tag Refs).

The rule set is the Home Assistant integration quality scale, all 54 rules across Bronze, Silver, Gold and Platinum. This is not a refactor: several user-visible capabilities are added.

## Problem Statement

The integration is a HACS custom integration with no declared quality tier. Nobody — user, reviewer or maintainer — can tell which of the quality scale rules it meets, and some of them it plainly does not:

- When the Device Builder moves, the user must remove the config entry and add it again, losing the entity, its history and the automations built on it.
- A user reporting a problem has nothing to attach: there is no diagnostics download, and the data a maintainer would need (polled versions, Git Sources, Installed Record, location) is only visible in logs.
- Errors from a refused or failed Install are English prose, so a user running Home Assistant in another language gets an untranslated message at the moment something went wrong.
- The update entity has no icon of its own (its `icons.json` entry is never looked up), and the device page of an entry without a Linked Device shows only a name and a model.
- The documentation does not say how often data is polled, what the options do, what the limitations are, how to troubleshoot a Repair, or how to remove the integration.
- Two modules are below the 95% test coverage floor, and the config flow's error paths are not fully covered.
- The translation strings have no source of truth, and the declaration of a tier would have nothing keeping it honest: hassfest skips `quality_scale.yaml` for custom integrations, so a rule marked done can silently rot.

## Solution

Declare the Platinum tier and make every rule true where it applies:

- A complete `quality_scale.yaml` with every rule's status and, for every exemption, the reason, plus `quality_scale: platinum` in the manifest, guarded by a repository test so the claim cannot rot.
- A reconfigure flow for the Device Builder Location, keeping the Tracked Config, its entity and its Installed Record.
- Diagnostics for the config entry, with credentials redacted and the YAML excluded.
- Translated exception messages for every Install refusal and failure.
- An entity translation key that activates the existing icon definitions, and complete device information for the device the integration creates itself.
- The missing documentation, the missing test coverage, and the typed-entry annotations that strict typing asks for.

## User Stories

1. As a user whose Device Builder moved to another host, I want to change its address without removing the entry, so that I keep my entity, its history and my automations.
2. As a user moving from a Supervisor install to a standalone Device Builder, I want to switch the entry from the add-on to a URL, so that I do not have to set the Tracked Config up again.
3. As a user who mistyped the Device Builder Location during setup, I want to correct it from the reconfigure flow, so that the entry works without a re-add.
4. As a user reconfiguring, I want the form pre-filled with the current location, so that I change only what moved.
5. As a user reconfiguring, I want the new location tested before it is saved, so that a typo does not leave me with a broken entry.
6. As a user reconfiguring to a Device Builder that does not list my Configuration, I want the flow to refuse with a clear reason, so that I do not silently lose my Tracked Config.
7. As a user reconfiguring while an Install runs on a builder that keeps its jobs, I want that Install to be followed through after the reload, so that it is not lost.
8. As a user reconfiguring while an Install runs on a builder that is being replaced, I want to be told the Install's outcome is unknown, so that nothing is silently reported as successful.
9. As a user whose stored credentials were rejected, I want reconfiguring to clear the pause and try the new credentials, so that polling resumes without a restart.
10. As a user, I want the Tracked Config and its Installed Record to survive a location change, so that no update appears or disappears.
11. As a user, I want a non-English Home Assistant to keep showing the update entity's name in my language, so that the entity is not the one English word on my dashboard.
12. As a user, I want the update entity to show the integration's own icon, and a different one when an update is available, so that I can spot it at a glance.
13. As a user whose Install is refused because the YAML has no `ota:` platform, I want that message translated, so that I understand what to add.
14. As a user whose Install is refused because a Git Source lacks `refresh: always`, I want the message translated and to name the Git Sources, so that I know what to fix.
15. As a user whose Install is refused because the Linked Device is offline, I want that message translated, so that I know why pressing Install did nothing.
16. As a user whose Install fails at compile or upload, I want the failure message translated, with the stage and the error kept, so that I know where it went wrong.
17. As a user whose Install is interrupted by a reload or a restart, I want that message translated, so that I know it continues after setup.
18. As a user whose re-attached Install could not be found on the Device Builder, I want that message translated, so that I know the outcome is unknown and why.
19. As a user, I want the device page of an entry without a Linked Device to show its manufacturer, its model, the Device Builder address and the ESPHome version, so that I can tell what it is and what it runs.
20. As a user who installs a newer ESPHome version, I want that device page's software version to follow the last Install, so that it is not stale until a restart.
21. As a user with a Linked Device, I want the integration to leave that device's information alone, as today, so that ESPHome or MQTT stays its owner.
22. As a user, I want to download diagnostics for the entry, so that I can attach them to an issue.
23. As a user sharing diagnostics, I want my username, password, GitHub token and any URL credentials left out, so that I do not leak them.
24. As a user sharing diagnostics, I want my YAML left out, so that a large or private configuration is not in the file.
25. As a user, I want diagnostics to work while the Device Builder is unreachable, so that I can report the problem that made it unreachable.
26. As a user, I want diagnostics to include the polled versions, the Git Sources and what the Installed Record remembers, so that a maintainer sees what the integration sees.
27. As a user, I want the docs to explain what the update entity shows and does, so that I understand the versions, the release notes and the progress.
28. As a user, I want the docs to explain how often the YAML, the Git remotes and GitHub are polled, so that I know when to expect an update.
29. As a user, I want the docs to list the limitations (OTA required, `refresh: always`, untrackable refs, refused Deferred Installs), so that I can tell whether my Configuration can work.
30. As a user, I want the docs to say which Configurations are supported, so that I do not try one that cannot work.
31. As a user, I want the docs to describe each setup field and each option, so that I know what I am choosing.
32. As a user, I want the docs to describe the Repair issues and what to do about them, so that I can fix a problem myself.
33. As a user, I want an automation example that notifies me when an update is available, so that I can use the entity without writing it from scratch.
34. As a user, I want a use-cases section, so that I can tell whether this integration fits my setup.
35. As a user, I want the docs to tell me how to remove the integration, and what happens to the entry's data, so that I can clean up.
36. As a maintainer, I want every rule's status and every exemption's reason in one file, so that a reviewer can check the claim without reading the whole codebase.
37. As a maintainer, I want a test that fails when the declared tier is not backed by the rules, so that the claim cannot rot silently.
38. As a maintainer, I want `strings.json` to be the source of truth with `translations/en.json` kept in step, so that a future move to core needs no rework.
39. As a maintainer, I want the options flow typed with the integration's config entry, so that strict typing holds at every entry point.
40. As a maintainer, I want the standalone CLI checked with the same strict mypy settings in CI, so that the shared module stays typed outside the integration.
41. As a maintainer, I want every module above 95% coverage, enforced in CI, so that the Silver rule is not a rounding error.
42. As a maintainer, I want the config flow's error paths covered, so that reauth, an empty builder and a wrong reconfigure target cannot regress.
43. As a maintainer, I want `Refusal` to carry a translation key and placeholders instead of prose, so that `following` stays free of Home Assistant and the text is translated where it is raised.
44. As a maintainer, I want the reconfigure flow to share the setup flow's location testing, so that the two cannot drift.
45. As a maintainer, I want the Device Builder Location in the glossary, so that "location" is never confused with the Tracked Config.

## Implementation Decisions

### The declaration

- **The tier is declared** as `quality_scale: platinum` in the manifest, with a `quality_scale.yaml` listing all 54 rules. hassfest accepts the manifest key for custom integrations but skips the YAML file entirely (it validates quality scale files for core integrations only), so the file is documentation plus our own guard.
- **Status policy.** A rule is `done` when its substance is met in this repository, even where the rule's wording names a core-only artifact: strict typing is enforced by the project's mypy configuration and CI rather than core's `.strict-typing` list, and the docs rules are met by the README, which is this integration's documentation page.
- **Exemptions** are used where the rule's own exceptions apply, plus one documented custom-integration case:
  - no actions, conditions or triggers: `action-setup`, `docs-actions`, `docs-conditions`, `docs-triggers`, `action-exceptions`;
  - no network discovery: `discovery`, `discovery-update-info`;
  - one entry per Configuration, one functional entity: `dynamic-devices`, `entity-category`, `entity-disabled-by-default`;
  - no external library: `async-dependency`;
  - `brands`: a custom integration, whose assets ship in its own `brand/` directory and are not submitted to the brands repository, which takes core integrations.
- `entity-translations` is `done` without the integration defining a name: the name is the translated device-class name the update component provides, so it is already translated for every language Home Assistant ships. The integration hardcodes no name.

### Translations

- **`strings.json` becomes the source of truth**, with `translations/en.json` its runtime copy. A test asserts the two parse to the same object. Every later ticket adds a key to both.
- **The entity gets a translation key** (the icon definitions already use it) so the icon lookup finds the integration's own icons. The key deliberately has no name in `strings.json`: naming it would replace the update component's translated device-class name with an English-only string. Entity ids already end in the device-class name, so nothing changes for existing entities.
- **Every user-facing exception becomes translatable**: the refusal shown before an Install, the device-offline refusal, the unreadable-YAML refusal, the interrupted-Install error, and the failed or unknown Install outcome. The keys live under `exceptions` in the strings files, with placeholders for the details that stay data.
- **`Refusal` carries a translation key and placeholders**, not prose. It is built by the pre-install check in the HA-free `following` module, which keeps no Home Assistant import. The coordinator that raises it chooses the translated message.
- **An Install ending is identified by its type, not by a sentence.** The prose `summary` on the ending types is removed; the raise site picks the key from the ending's type (`failed` with the stage and error, `device offline`, `unknown outcome`). Notification headlines and messages stay as they are: they are notifications, not exceptions.

### Reconfiguration

- **`async_step_reconfigure`** shows the same add-on / manual menu as setup, pre-filled from the entry. The user may switch modes; the saved entry data is a full replacement containing only the chosen mode's keys plus the Configuration and, if set, the Linked Device, so no stale key survives.
- **The location is tested and then verified**: connecting and listing configurations reuses the setup flow's step, and a builder that does not list the Tracked Config's Configuration aborts the flow with a dedicated reason rather than leaving a half-working entry.
- **The change is applied with a reload.** The reload clears the entry's reauthentication pause, which is the only way the new credentials are ever tried after the Device Builder rejected the old ones. A running Install is re-attached after the reload; it either continues against the same builder or ends as an unknown outcome when the builder was replaced, which is the truthful report.
- **Nothing about the Tracked Config changes**: the Configuration remains the unique id, the Installed Record keeps its per-entry storage, and the entity keeps its registry entry.

### Diagnostics

- **A diagnostics module** returns the entry's title and unique id, its data and options with credentials redacted, the stored Device Builder Location, the version coordinator's data and success state, the Git coordinator's success state and shown sources, the Installed Record's snapshot, and the Linked Device's id.
- **Redaction**: username, password and GitHub token become a redaction marker; a manual URL is reduced to scheme, host and port. The YAML text is never included.
- **No I/O**: diagnostics read in-memory state only. It does not resolve an add-on ingress port, contact the Device Builder, or ask GitHub, so it works while the Device Builder is down.
- **The Installed Record exposes a read-only snapshot** for diagnostics (the Installed ESPHome Version, Installed Commits, Installed Tags and the active Install). Diagnostics must not read its storage layout directly.

### Device information and the entity

- **The device the integration creates for an entry without a Linked Device** carries manufacturer `ESPHome`, the Configuration as its model, the Device Builder address as its configuration URL (manual locations only; an add-on's ingress address is internal), and the Installed ESPHome Version as its software version.
- **That software version is kept current**: when the Installed ESPHome Version changes, the entity updates the device registry entry, so the device page follows the last Install instead of the last setup.
- **A Linked Device is not touched.** Its information belongs to ESPHome or MQTT; the entity only pins itself to it.
- **`PARALLEL_UPDATES` is set** on the update platform. Zero: the coordinator owns polling, and Installs are serialized by the Install runner, so per-entity limits do not apply.

### Strict typing

- **The options flow is typed with the integration's config entry**, and `async_get_options_flow` is annotated with it, so every entry point strict typing checks uses the typed entry.
- **The standalone CLI is checked with the same strict settings** in a separate mypy invocation, as the project's mypy configuration already documents, because the shared Device Builder module has two module names.

### Coverage

- **Every module is above 95%**, enforced in CI from the coverage JSON rather than by a global average that can hide a weak module.
- **The config flow is fully covered**, including the reauth error paths, the empty-builder abort and every reconfigure error.

### Documentation

- **The README is the integration's documentation page.** It gains: setup fields and the reconfigure flow, every option, removal, how and how often data is updated, the supported functionality, use cases, automation examples, known limitations, supported Configurations and troubleshooting. The action, condition and trigger sections remain absent because the integration has none.

## Testing Decisions

- **A good test drives the integration the way Home Assistant does** and asserts what a user or a caller can see: entity state and attributes, registry entries, config flow results, a raised exception's translation key, the diagnostics payload, log lines. No test reads a private attribute or the Installed Record's storage layout; the one existing on-disk format test stays the only exception.
- **Seams, all existing:**
  - **The config-entry seam** — add a `MockConfigEntry`, set the entry up, assert states, attributes and registries, call the install action. It is how the entity, device info, translations, exception keys and parallel updates are tested. Prior art: `test_update.py`.
  - **The config-flow seam** — start a flow with a source and entry id, walk its steps, assert results and entry data. Prior art: `test_config_flow.py`, `test_reauth.py`.
  - **The diagnostics helper seam** — the test plugin's config-entry diagnostics helper with the authenticated client, asserting the downloaded JSON. No other diagnostics test touches the payload.
  - **The file seam** — the packaging tests' style, for the quality scale file, the manifest tier, the strings parity and the icon key.
  - **The `following` unit seam** — construct the pre-install check and assert the `Refusal` it returns. Prior art: `test_pre_install_checks.py`.
- **No new production seam is introduced.** The only new interface is the Installed Record's diagnostics snapshot, which exists so diagnostics does not need one.
- **The fake Device Builder** serves the scenarios (login refusal, unreachable, compile failure, install progress); **the fake GitHub** and the local git remote serve the Git Source scenarios. No test talks to the network.
- **The tier is guarded twice**: the repository test checks the YAML's completeness, the declared tier, every exemption's reason, and the presence of the evidence each done rule needs; CI checks per-module coverage from the coverage report.

## Out of Scope

- A core submission, core's `.strict-typing` file, a brands-repository pull request, or a logo.
- Translations into any language other than English.
- Making the Linked Device reconfigurable, or changing the tracked Configuration in place (a different Configuration is a different entry, ADR-0001).
- Translating notification headlines or messages.
- Network discovery, actions, conditions, triggers, or any device the integration does not create.
- Any change to Install semantics, the stored format, the ref rules or the Install runner.

## Further Notes

- The claim is guarded by this repository, not by the Home Assistant tooling: hassfest validates quality scale files for core integrations only, and validates only `translations/en.json` when it runs against a custom integration. The per-module coverage check therefore has to live in CI.
- `brands` is the one exemption outside a rule's own exception text. The local brand assets HACS uses are kept.
- The per-module coverage check needs a coverage reporter in CI; the project's dev dependencies gain it.
- A possible follow-up ADR, not part of this spec: "reconfiguring changes only the Device Builder Location". The reasoning — the location is not part of a Tracked Config's identity, and a different Configuration is a different entry — is recorded in this spec for now.
- The manifest and project version are bumped and released after the last ticket, following the existing release steps.
