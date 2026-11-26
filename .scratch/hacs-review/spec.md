# HACS review: drop the core ESPHome fallback: spec

Status: ready-for-agent

Source: HACS default-repository submission [hacs/default#14478](https://github.com/hacs/default/pull/14478), review [6071861836](https://github.com/hacs/default/pull/14478#pullrequestreview-6071861836) by frenck, 2026-11-26, CHANGES_REQUESTED.

Terms are defined in `GLOSSARY.md`. Relevant decision: ADR-0002 (Device Builder websocket API, and how the Device Builder Location is reached).

## Problem Statement

When the add-on (auto) Device Builder Location cannot find the ESPHome add-on through the Supervisor, the integration falls back to the Device Builder URL that core's `esphome` integration discovered, read from `hass.data["esphome_dashboard_manager"]`. That is core ESPHome's internal data, not an API, and can change in any release. HACS will not include the integration in the default catalog while this fallback exists.

## Review points

1. **Blocking:** drop the `hass.data` fallback; the Device Builder URL comes from the config flow instead.
2. **Fine as is:** rewriting refs through the Device Builder's own `update_config` API on an explicit Install.
3. **Not blocking, not taken up:** the README install section leads with adding a custom repository. The maintainer chose to leave it as is.

## Decisions

- The add-on (auto) location stays. It finds the add-on through the Supervisor client (`aiohasupervisor`), which is a public API; the review objects only to the `hass.data` fallback. Users whose Device Builder is not the add-on under the given slug use the existing **Manual URL** location, which already asks for the URL in the config flow.
- README install section: out of scope (maintainer decision during triage, 2026-11-26).

## Follow-up (human)

After the ticket lands: cut a new release, then re-request review on hacs/default#14478.
