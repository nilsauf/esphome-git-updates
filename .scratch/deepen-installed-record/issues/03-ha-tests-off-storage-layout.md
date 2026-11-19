# 03: HA tests stop depending on the storage layout

Status: resolved

**What to build:** as specified in `../spec.md` under "Testing Decisions": one format test and "Full HA setup".

- Add a test helper that, given an entry id and `hass_storage`, builds a record through the Installed Record's own interface over the HA store adapter (such as recording an Install, or setting an active Install) and reads one back.
- Move every HA test that writes or reads raw `hass_storage[...]["data"][...]` keys over to that helper, or to assertions on entity state. Today these are in the update, Linked Device, git updates and pre-install check tests.
- Add one format test that writes the literal on-disk JSON, including a running Install stored before the upload address, Clean Build and Installed Tags were stored. It loads it through the HA store adapter and asserts what the record answers, then that a save writes the same layout.
- Delete HA tests that only repeat a record test, after checking them case by case.

**Blocked by:** 02

- [x] No HA test other than the format test names a storage field.
- [x] The kept HA tests cover the wiring:
  - a Linked Device's `sw_version` reaches the entity and a change of it updates the entity;
  - seeding at setup without a Linked Device, and none with one, present or gone;
  - a successful Install is recorded; a restart re-attaches;
  - a removed entry's record is deleted.
- [x] The full test suite passes, and `ruff` is clean.
