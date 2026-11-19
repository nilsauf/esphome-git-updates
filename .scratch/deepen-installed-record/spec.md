# One Installed Record module: spec

Status: ready-for-agent

Terms are defined in `GLOSSARY.md`. Relevant decisions: ADR-0002 (an Install is followed through the Device Builder, and re-attached after a restart), ADR-0003 (the integration records Installed Commits and the Installed ESPHome Version, and seeds them), ADR-0005 (one combined entity on the Linked Device), ADR-0007 (Installed Tags, keyed by repository so they survive a rewritten ref).

This is a refactor. Behaviour visible to users does not change. It follows `deepen-following`, `deepen-install-prep` and `deepen-github`.

## Problem Statement

ADR-0003 says what the integration remembers about a Tracked Config and how it seeds that memory. That rule has no single home:

- The store sits in the version coordinator's module, next to the runtime data. It does not depend on the version coordinator at all.
- The glossary's Installed ESPHome Version has three parts, and each lives somewhere else:
  - the setup code seeds it, and only when the entry has no Linked Device;
  - the update entity resolves it ("what the Linked Device reports, else what the last Install recorded");
  - the update entity also warns once while it is unknown;
  - the Linked Device helper parses `sw_version`.
- The store seeds Installed Commits and Installed Tags with two near-identical methods. `FollowedSources` always calls both, one after the other, and each saves separately.
- The Followed Sources tests use their own in-memory record. That fake re-implements the seed-and-forget rule, so the rule is written twice and the copy in the fake is never checked against the real one.
- The store takes the HA instance directly. The only way to test seeding, forgetting, fallback or the Linked Device exception is to set up the whole integration.
- Four HA test files read and write the store's raw JSON layout through `hass_storage`. The storage schema has become a test interface.

## Solution

One Installed Record module, which does not use Home Assistant, owns everything ADR-0003 says the integration remembers:

- resolving the Installed ESPHome Version from the Linked Device's raw `sw_version`, with its fallback;
- seeding, with the Linked Device exception;
- seeding and forgetting Installed Commits and Installed Tags;
- recording a successful Install;
- the Install still running.

Its storage sits behind a small seam with two adapters: HA's store in production and an in-memory one in tests. The on-disk format does not change. The update entity, the setup code and the Followed Sources tests use the real record. The old store and the fake record are deleted.

## User Stories

1. As a user with a Linked Device, I want the Installed ESPHome Version to stay the one the device reports in `sw_version`, so that the refactor changes nothing I see.
2. As a user whose Linked Device reports an `sw_version` the integration cannot parse, I want the version recorded at the last successful Install used instead, as today.
3. As a user whose Linked Device reports nothing parseable and who has had no Install yet, I want no update shown and one warning in the log, as today (ADR-0003).
4. As a user, I want that warning repeated only after the version became known and then unknown again, so that the log is not flooded.
5. As a user whose Linked Device starts reporting a parseable `sw_version`, I want the entity to show it right away, as today.
6. As a user adding a Tracked Config without a Linked Device, I want its Installed ESPHome Version seeded with the Available ESPHome Version, so that a new entry shows no update until the Device Builder's ESPHome changes (ADR-0003).
7. As a user adding a Tracked Config with a Linked Device, I want its Installed ESPHome Version never seeded, so that a guess never hides a real update (ADR-0003).
8. As a user whose Linked Device has since been deleted, I want the entry still never seeded, as today.
9. As a user restarting Home Assistant, I want an entry's recorded versions kept, and an existing seed never replaced, so that a restart does not hide or invent an update.
10. As a user adding a Git Source, I want its Installed Commit or Installed Tag seeded at first discovery, so that it shows no update until the source moves (ADR-0003).
11. As a user removing a Git Source and adding it back later, I want it seeded again, as today.
12. As a user whose Install succeeds, I want the ESPHome version it compiled with, and the commits and versions shown when I pressed Install, recorded (ADR-0003, ADR-0007).
13. As a user restarting Home Assistant during an Install, I want the integration to re-attach to the running jobs, with the same upload address, Clean Build choice and versions to record (ADR-0002).
14. As a user upgrading from an older version of the integration, I want my stored record read exactly as before, including records of Installs written before the upload address, Clean Build and Installed Tags were stored, so that no update appears or disappears.
15. As a user removing a Tracked Config, I want its record deleted, as today.
16. As a maintainer, I want every rule of ADR-0003 in one module, so that a change to how the integration remembers or seeds is made in one place.
17. As a maintainer, I want the glossary's definition of the Installed ESPHome Version (reported, else recorded, else seeded, else unknown and warned about) in one module, so that a reader finds it without opening the entity, the setup code and the Linked Device helper.
18. As a maintainer, I want seed-and-forget written once for commits and tags, with one save per poll, so that the two copies cannot drift apart.
19. As a maintainer, I want the record to know nothing of Home Assistant, so that its rules are tested in plain pytest against in-memory storage.
20. As a maintainer, I want the Followed Sources tests to use the real record, so that no test fake re-implements the record's rules.
21. As a maintainer, I want the update entity to stop resolving and warning about versions itself, so that it stays an HA adapter for state, progress and notifications.
22. As a maintainer, I want HA tests to build and inspect the record through its interface, with one test pinning the on-disk format, so that a change of the storage layout is caught by one test and breaks no others.
23. As a maintainer, I want the version coordinator's module left with only the version coordinator and the runtime data, so that its name says what it holds.

## Implementation Decisions

- **The Installed Record** is a new module and class, named after the glossary term. It imports nothing from Home Assistant. It replaces the store in the version coordinator's module.
- **The storage seam.** The record is constructed with a storage object that has three async operations: load (returns the stored data or nothing), save (the data) and remove. HA's `Store` already has this shape and is the production adapter. Tests use an in-memory adapter.
  - The record module owns the storage key (per config entry) and the storage version as constants. The setup code builds the HA store from them, both on setup and when an entry is removed.
  - The on-disk format does not change: same key, storage version 1, same fields, same defaults for records written before the upload address, Clean Build and Installed Tags of a running Install were stored. No migration.
- **Construction.** Besides the storage, the record is given:
  - whether the entry has a Linked Device, taken from the entry's data, not from whether the device still exists, because ADR-0003 never seeds an entry with a Linked Device, even after the device is gone;
  - the entry title, for the log line about an unknown version.
- **Interface:**
  - **Installed ESPHome Version, given the Linked Device's raw `sw_version`** (or nothing without a Linked Device). The record parses `sw_version` with the existing parser and falls back to the recorded version. When both give nothing, it returns nothing and warns once. It forgets that it warned once a version is known again, so a later unknown is warned about again.
  - **Seed the ESPHome version with the Available ESPHome Version.** Called at setup after the version coordinator's first refresh. Does nothing when the entry has a Linked Device or a version is already recorded.
  - **Installed Commit of a Source key** and **Installed Tag of a url**, unchanged.
  - **Seed commits and tags in one call.** For each kind it takes the seeds and the keys still present. It forgets recorded entries no longer present, records seeds where nothing is recorded yet, and saves once, only when something changed. One implementation serves both kinds.
  - **The active Install**, **set the active Install**, **clear the active Install** and **record a successful Install**, unchanged. Recording an Install writes the versions and clears the active Install in one save. The record keeps satisfying the Install runner's bookkeeping protocol, so the Install runner does not change.
- **Followed Sources** drops its own record protocol and takes the Installed Record. The narrow read-only protocol for Installed Commits and Installed Tags, which shown sources, changes and moves take, stays. Its seeding step makes the single seed call.
- **The update entity** asks the record for the Installed ESPHome Version, passing the Linked Device's raw `sw_version`. It asks when it is added and whenever the device registry reports a changed `sw_version`, as today. It no longer resolves the version or keeps "warned" state. The Linked Device helper no longer parses `sw_version`; it only exposes the raw value.
- **The setup code** builds the record, loads it and calls the ESPHome seeding after the first version refresh. It no longer holds the seeding rule. The runtime data holds the record in place of the store.
- **Deleted:** the old store class, the Followed Sources record protocol, the Linked Device helper's parsed-version property, the entity's version resolution and warning, and the setup code's seeding branch.

## Testing Decisions

- A good test drives a module only through its interface and asserts on what a caller sees: versions returned, what is logged, what an entity shows. No test reads private attributes. Only the one format test reads the stored JSON layout.
- **The Installed Record over in-memory storage** (the main seam), in plain pytest:
  - Installed ESPHome Version: reported wins; an unparseable report falls back to the recorded version; with neither it is unknown and warns once; it warns again only after being known in between; without a Linked Device the recorded version is used.
  - ESPHome seeding: seeds without a Linked Device; never seeds with one; never replaces a recorded version.
  - Commit and tag seeding: seeds only where nothing is recorded; forgets what is no longer present; a removed Source that returns is seeded afresh; saves only when something changed; one save for both kinds.
  - Recording an Install merges the versions and clears the active Install.
  - The active Install round-trips through storage, and survives a reload from the same storage.
  - Prior art: the Followed Sources tests with in-memory ports; the Install runner tests with their honest bookkeeping fake.
- **Followed Sources** tests switch from their own fake record to the real record over in-memory storage. Their scenarios do not change.
- **One format test** writes the literal on-disk JSON, including a running Install stored before the upload address, Clean Build and Installed Tags were stored. It loads the record through the HA store adapter and asserts what the record answers, then that a save writes the same layout.
- **Full HA setup**, kept to the wiring:
  - a Linked Device's `sw_version` reaches the entity, and a change of it updates the entity;
  - an entry without a Linked Device is seeded at setup; one with a Linked Device, present or gone, is not;
  - a successful Install is recorded; a restart re-attaches to a running Install;
  - a removed entry's record is deleted.
  - A test that needs an existing record writes it through a test helper that drives the record's own interface against `hass_storage`. A test that asserts on what was recorded reads entity state, or reads back through that helper. No HA test names a storage field.
  - HA tests that only repeat a record test are deleted, after checking them case by case.

## Out of Scope

- A Tracked Config module behind the update entity, or merging the two pollers (a separate architecture candidate).
- Any change to the on-disk format, or a storage migration.
- Any change to how `sw_version` is parsed, or to what counts as a Linked Device being offline.
- Any change to the Install runner beyond the type its bookkeeping is satisfied by.

## Further Notes

- Two adapters justify the storage seam: HA's store in production, the in-memory storage in the record and Followed Sources tests.
- HA tests write `hass_storage` under two keys today, one per entry id the fixtures use. The test helper takes the entry id, so this needs no special case.
