# 02: The record owns the Installed ESPHome Version

Status: resolved

**What to build:** as specified in `../spec.md` under "Construction" and the ESPHome-version parts of "Interface", "The update entity" and "The setup code".

- Construct the record with whether the entry has a Linked Device (from the entry's data, not from whether the device still exists) and with the entry title.
- Add "Installed ESPHome Version, given the raw `sw_version`": parse with the existing parser, fall back to the recorded version, and otherwise return nothing and warn once. Reset the warning once a version is known again.
- Add "seed the ESPHome version with the Available ESPHome Version". It does nothing with a Linked Device or with a recorded version. The setup code calls it after the version coordinator's first refresh, in place of its own seeding branch.
- The update entity asks the record, passing the Linked Device's raw `sw_version`. It asks when added and on each registry `sw_version` change, and drops its own resolution and "warned" state.
- The Linked Device helper drops its parsed-version property and keeps only the raw `sw_version`.
- The record no longer exposes the raw recorded ESPHome version to callers.

**Blocked by:** 01

- [x] New plain pytest record tests over in-memory storage:
  - reported wins; an unparseable report falls back to the recorded version; with neither it is unknown and warns once, and warns again only after being known in between; without a Linked Device the recorded version is used;
  - seeds without a Linked Device; never seeds with one; never replaces a recorded version.
- [x] The existing HA tests for a Linked Device's version, seeding and the unknown warning still pass. They may still use `hass_storage` until ticket 03.
- [x] The full test suite passes, and `ruff` is clean.
