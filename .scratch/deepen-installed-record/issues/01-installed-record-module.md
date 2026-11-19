# 01: The Installed Record module, behind a storage seam

Status: ready-for-agent

**What to build:** as specified in `../spec.md` under "The Installed Record", "The storage seam" and the commit, tag and Install parts of "Interface".

- Add an HA-free `InstalledRecord` module. Move the store out of the version coordinator's module into it, along with its stored-data types, storage version and storage key.
- Construct it with a storage object (async load, save, remove). HA's `Store` is the production adapter; add an in-memory adapter for tests. Keep the on-disk format exactly as it is.
- Replace the two seeding methods with one call that seeds and forgets commits and tags together, with a single save only when something changed.
- Keep the Installed Commit and Installed Tag reads, the active-Install operations and recording an Install, so the Install runner's bookkeeping protocol is still satisfied unchanged.
- Followed Sources: drop its own record protocol, keep the read-only Installed Commits and Tags protocol, and make the single seed call.
- Rewire the setup code (build the HA store from the record module's constants, on setup and on entry removal), the runtime data, the git poller and the update entity to the new record. The ESPHome-version parts stay where they are for now; ticket 02 moves them.
- Delete the old store class.

**Blocked by:** none

- [ ] New plain pytest tests drive the record over in-memory storage, with no HA fixture:
  - seeding only where nothing is recorded; forgetting what is no longer present; a returning Source seeded afresh;
  - one save for both kinds, and no save when nothing changed;
  - recording an Install merges the versions and clears the active Install;
  - the active Install round-trips and survives a reload from the same storage.
- [ ] The Followed Sources tests use the real record over in-memory storage. Their own fake record is deleted, and their scenarios are unchanged.
- [ ] The record module imports nothing from Home Assistant.
- [ ] The full test suite passes, and `ruff` is clean.
