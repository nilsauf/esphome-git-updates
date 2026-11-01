# The integration remembers Installed Commits itself

Nothing a flashed ESPHome device reports identifies which Git Source commit it was built from:
- `project_version` is a static YAML string.
- `config_hash` covers the `url@ref` text, not the commit.
- `compilation_time` is only a date.

The integration therefore records each Git Source's Installed Commit in its own store when an Install succeeds. It uses the commit seen by the poller when Install was pressed, which accepts a rare race with pushes made during the build. The ESPHome version, by contrast, is read from the Linked Device's registry `sw_version`. That field has the same format whether the device came in through the native API or MQTT discovery. The integration falls back to its own record only when no device is linked or the field can't be parsed.

## Seeding

A new entry has no record yet. Each Git Source's Installed Commit is seeded with its Latest Commit at first discovery, and with no Linked Device the Installed ESPHome Version is seeded with the Available ESPHome Version, so a new entry starts without an update. With a Linked Device the ESPHome version is never seeded: as a fallback for an unparseable `sw_version`, a seed would claim the device runs a version it may not, and hide a real update. In that case the version is unknown, and the entity shows no update and logs a warning until the device reports a parseable version or an Install succeeds.
