# 01: Drop the core ESPHome Device Builder fallback

**Status:** ready-for-agent

**Blocked by:** None (can start immediately)

See `../spec.md` for the review this comes from.

## Agent Brief

**Category:** enhancement
**Summary:** The add-on (auto) Device Builder Location stops reading core ESPHome's dashboard manager out of `hass.data`; when the add-on is not found, the user is pointed to the Manual URL location.

**Current behavior:**
Resolving an add-on (auto) location (`async_resolve_location`) first asks the Supervisor for the add-on's ingress port by slug. If that fails, it reads `hass.data["esphome_dashboard_manager"]`, the Device Builder core's `esphome` integration discovered, and connects to its URL. Only when both fail does it raise `DeviceBuilderNotFound`, which the config flow reports as `addon_not_found` ("Neither the ESPHome add-on with this slug nor a Device Builder discovered by Home Assistant's ESPHome integration was found."). A HACS reviewer rejected this: `hass.data` is core ESPHome's internal data, not an API.

**Desired behavior:**
- Resolving an add-on (auto) location uses only the Supervisor. If the add-on with the stored slug is not found (no Supervisor, unknown slug, Supervisor error), it raises `DeviceBuilderNotFound` straight away. Nothing in the integration reads `hass.data` keys owned by core's `esphome` integration.
- The `addon_not_found` config-flow error no longer mentions core's ESPHome integration. It says the ESPHome add-on with this slug was not found and suggests checking the slug or choosing **Manual URL** instead. This applies to setup and reconfigure alike, in `strings.json` and in every translation (`en`, `de`).
- At runtime (setup of an existing add-on entry whose add-on has gone), behavior is otherwise unchanged: the entry fails to set up the same way it does today when neither path finds a Device Builder.
- Manual URL locations are untouched.

**Key interfaces:**
- `async_resolve_location(hass, location) -> ResolvedLocation`: the add-on branch loses its second source; the private helper that reads core ESPHome's dashboard manager, and its key constant, are deleted.
- `DeviceBuilderNotFound`: docstring and message describe only the add-on not being found.
- Config-flow error key `addon_not_found`: key unchanged, text changed.
- ADR-0002: its last sentence describes the fallback. Amend the ADR to say the add-on is found only through the Supervisor, and that the fallback was removed because it read core ESPHome's internal `hass.data` (HACS review, 2026-11-26).

**Acceptance criteria:**
- [ ] No code under `custom_components/` refers to `esphome_dashboard_manager` or otherwise reads core `esphome`'s `hass.data`.
- [ ] The test that sets up `hass.data["esphome_dashboard_manager"]` and expects the flow to reach `configuration` is removed, along with its helper. A test (new or existing) shows that with the add-on not found, the add-on step shows `addon_not_found`, even if `hass.data["esphome_dashboard_manager"]` holds a Device Builder.
- [ ] The add-on-found path, the manual path, and reconfigure tests still pass unchanged.
- [ ] The `addon_not_found` text in `strings.json`, `translations/en.json` and `translations/de.json` names the slug and the Manual URL alternative, and no longer mentions core's ESPHome integration; `strings.json` and `en.json` stay identical where the repo's tests require it.
- [ ] README sections describing **ESPHome add-on (auto)** (English and any translation) do not promise a fallback; if they do today, they are corrected. The README install section is not touched.
- [ ] ADR-0002 is amended as above; `GLOSSARY.md` needs no change unless it mentions the fallback.
- [ ] The full test suite, lint and type checks pass, and module coverage stays above the repo's floor.

**Out of scope:**
- The README "Install with HACS" section (maintainer declined that review point).
- The add-on (auto) location itself, its Supervisor lookup, or its default slug.
- The `update_config` ref rewriting on Install (the reviewer approved it).
- Historical spec and ticket files under `.scratch/` that mention the fallback.
- Cutting a release or commenting on hacs/default#14478 (the maintainer does that).
