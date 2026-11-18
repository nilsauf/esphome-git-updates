# 02: A Tag Ref shows a newer Version Tag

**What to build:** A Git Source with a Tag Ref (for example `ref: v1.4.0`) follows the remote's Version Tags on any git host. When a higher Version Tag appears, the update entity shows `name v1.4.0 → v1.5.0`. On GitHub, the release notes list the commits between the two tags. The integration records an Installed Tag per followed source. It is seeded from the tag the YAML names, so an existing entry that is already behind is offered the update right away. The record is keyed by the repository, not its ref, so it will survive the rewrite added in ticket 03. Tag Refs no longer need `refresh: always` (ADR-0004 note). Until ticket 03 lands, Install is refused with a clear message while any Tag Ref has an update, because it would otherwise build the old tag and record the new one.

**Blocked by:** 01

**Status:** done

- [x] With `ref: v1.4.0` and a remote that also has `v1.5.0` and `v1.10.0`, the entity shows `v1.4.0 → v1.10.0`, ordered by version, not by date.
- [x] Tags with a prerelease suffix or a non-version name are ignored; a version equal to or older than the Installed Tag (e.g. installed `v2.0.0`, newest `v1.9.0`) offers no update.
- [x] Changing the tag in the YAML by hand without installing leaves the Installed Tag unchanged.
- [x] The composite version and release summary show versions for Tag Refs and short shas for branch sources; GitHub release notes list the commits between the two tags' commits.
- [x] A Tag Ref without `refresh: always` raises no Refresh Policy Repair issue; a Floating Ref still does.
- [x] Install is refused with a clear error while a Tag Ref has an update, and allowed otherwise.

## Comments

- Coordinator data is now `dict[str, GitSourceState]`, where `GitSourceState = TrackedGitSource | VersionedGitSource` (git_coordinator.py). `TrackedGitSource` follows Latest Commit and is keyed `source.key` (`url@ref`). `VersionedGitSource(source, version_tags: {tag: commit})` is keyed by `source.url` (its `.key`). `VersionedGitSource.offered(installed)` gives the newest Version Tag if strictly newer, else `installed`: that is the whole update condition. Ticket 05 can add the Releases answer to `VersionedGitSource`; ticket 06 can return one for Unset Refs from `_ask_remote`.
- `git_remote.resolve_ref_and_tags(url, ref) -> RemoteTags(ref: RemoteRef | None, tags: {name: peeled commit})`: one `ls-remote url refs/heads/<ref> refs/tags/*`. A Tag Ref that is a branch on the remote is still a `TrackedGitSource`. One whose own tag is gone still follows the other Version Tags.
- Store: `installed_tags` (url -> tag), `TrackedConfigStore.installed_tag(url)` and `async_seed_installed_tags(tags, present)` (setdefault; forgets urls no longer present). Nothing records an Installed Tag yet: ticket 03 must add the tags to `InstallPlan` / `ActiveInstall` / `_StoredInstall` / `async_record_install`, and to `FakeBookkeeping`.
- Display moved into the coordinator: `GitSourcesCoordinator.shown_sources()` builds the `SourceCommits` rows the entity shows; update.py no longer reads the store or data for them. `SourceCommits.versions=True` shows installed/latest in full (`label()`), so the composite reads `marsrelay v1.4.0` and the summary `marsrelay v1.4.0 → v1.5.0`. As for branches, the commit count is appended once the GitHub compare (between the two tags' commits) is known: `marsrelay v1.4.0 → v1.5.0 (1 commit)`.
- `latest_commits()` (the Install plan's commits) covers only Sources following Latest Commit; so does the Refresh Policy check.
- The temporary Install guard is the `outdated` block at the end of `GitSourcesCoordinator.async_check_before_install` ("moving a Tag Ref to a newer Version Tag is not supported yet (marsrelay v1.0.0 → v1.1.0)"). Ticket 03 removes it and `test_an_install_is_refused_while_a_tag_ref_has_a_newer_version_tag`. The rewrites can be taken from the same `shown_sources()` / data at the point update.py builds the plan.
- Left for later: the same repository at two Tag Refs in one config silently collapses to one data entry (last wins) until ticket 04 reports it untrackable. A failed `ls-remote` on a Tag Ref still logs "cannot get the Latest Commit of …".
- README updated: Tag Refs need no Refresh Policy, and their Install is refused for now.
