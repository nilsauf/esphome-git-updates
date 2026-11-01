# 07: Linked Device + Installed ESPHome Version from `sw_version`

**What to build:** The user can attach the update entity to the device the ESPHome device already has in Home Assistant (ADR-0005), and the Installed ESPHome Version then comes from what that device reports (ADR-0003).

The config flow offers an optional device picker limited to the `esphome` and `mqtt` integrations, pre-selected by matching the YAML's `esphome: name:`. With a Linked Device, the entity is explicitly attached to it. The device lookup sets `device_entry`, since HA no longer merges devices across config entries. With no device picked, the integration keeps its own device.

**Blocked by:** 02 (Tracer bullet: manual-URL entry → update entity showing ESPHome version)

**Status:** closed

- [x] The device picker lists only `esphome` and `mqtt` devices and pre-selects the one matching `esphome: name:`.
- [x] With a Linked Device, the update entity appears on that device; without one, it is on the integration's own device.
- [x] The Installed ESPHome Version is parsed from the Linked Device's `sw_version` in both formats: `"<ver> (config hash 0x…)"` / `"<ver> (<compile time>)"`, and `"<project_ver> (ESPHome <ver>)"`.
- [x] When `sw_version` can't be parsed, the version stored by the last successful Install is used. With a Linked Device the version is never seeded: if neither exists, the installed version is unknown, no update is shown, and a warning is logged (ADR-0003, Seeding).
- [x] With no Linked Device, a new entry seeds the Installed ESPHome Version with the Available ESPHome Version (the current behaviour from 02).
- [x] With a Linked Device whose entities are all unavailable, Install refuses with "device offline" before any Device Builder job starts.
- [x] Unit tests cover the `sw_version` parsing, including unparseable values.

## Comments

- Pre-selection matches the Device Builder list's `name` (its reading of `esphome: name:`) and `friendly_name`, slugified, against the registry device name. The YAML itself is not read. A device built with `name_add_mac_suffix` has a registry name ending in the MAC suffix, so it is not pre-selected; the user picks it by hand.
- If the Linked Device has been removed from the registry, the entity falls back to a device of its own. The version is still never seeded (ADR-0003).
- "All entities unavailable" ignores disabled entities and the update entity itself. A device with no other entity is not treated as offline, because that gives no sign either way.
