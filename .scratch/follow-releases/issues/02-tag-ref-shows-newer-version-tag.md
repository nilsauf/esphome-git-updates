# 02: A Tag Ref shows a newer Version Tag

**What to build:** A Git Source with a Tag Ref (for example `ref: v1.4.0`) follows the remote's Version Tags on any git host. When a higher Version Tag appears, the update entity shows `name v1.4.0 → v1.5.0`. On GitHub, the release notes list the commits between the two tags. The integration records an Installed Tag per followed source. It is seeded from the tag the YAML names, so an existing entry that is already behind is offered the update right away. The record is keyed by the repository, not its ref, so it will survive the rewrite added in ticket 03. Tag Refs no longer need `refresh: always` (ADR-0004 note). Until ticket 03 lands, Install is refused with a clear message while any Tag Ref has an update, because it would otherwise build the old tag and record the new one.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] With `ref: v1.4.0` and a remote that also has `v1.5.0` and `v1.10.0`, the entity shows `v1.4.0 → v1.10.0`, ordered by version, not by date.
- [ ] Tags with a prerelease suffix or a non-version name are ignored; a version equal to or older than the Installed Tag (e.g. installed `v2.0.0`, newest `v1.9.0`) offers no update.
- [ ] Changing the tag in the YAML by hand without installing leaves the Installed Tag unchanged.
- [ ] The composite version and release summary show versions for Tag Refs and short shas for branch sources; GitHub release notes list the commits between the two tags' commits.
- [ ] A Tag Ref without `refresh: always` raises no Refresh Policy Repair issue; a Floating Ref still does.
- [ ] Install is refused with a clear error while a Tag Ref has an update, and allowed otherwise.
