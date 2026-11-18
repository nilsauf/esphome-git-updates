# 01: Non-version tags follow their commit

**What to build:** A Git Source whose ref names a tag that is not a Version Tag (such as `stable` or `nightly`) is now a Floating Ref. When the maintainer moves that tag to another commit, the update entity offers an update, just as for a branch, and Install builds the same ref, which is never rewritten. A Pinned Ref now means a commit only. This ticket also introduces the Version Tag rule from the spec (`.scratch/follow-releases/spec.md`): an optional `v`/`V`, then one to four dot-separated integers, ordered numerically with zero padding. A tag that is a Version Tag is still ignored at this stage, as before.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] A Tracked Config with `ref: stable` (a tag on the test remote) shows no update after setup, shows an update once `stable` is moved to a new commit, and records the new commit after a successful Install.
- [x] Refresh Policy is required for such a source, as for any Floating Ref.
- [x] A ref naming a Version Tag (`v1.2.3`, `1.2`) and a ref naming a commit still never produce an update.
- [x] Discovery tests cover how refs are classified: `v1.2.3`, `V1.2`, `1.2.3.4` count as Version Tags; `v1.2.0-rc1`, `test-foo`, `firmware-2024-05`, `v1.2.3.4.5` do not.
- [x] `GLOSSARY.md` terms are used throughout; no ADR is contradicted.

## Comments

- `git_sources.version_of(tag)` implements the Version Tag rule: the version as an int tuple zero-padded to 4 parts (so `v1.2 == 1.2.0`, and tuples order numerically), or None. Later tickets should use it for ordering.
- `git_sources.RefKind` (FLOATING / TAG / UNSET) is exposed as `GitSource.kind`, classified from the ref's name only. Pinned Refs (commits) still never become Discovery sources. Only the remote can tell a branch from a tag, so a ref classified TAG that resolves to a branch is treated as Floating (`git_coordinator._is_floating`).
- To avoid a name clash, `git_remote.RefKind` was renamed `RemoteRefKind` (BRANCH / TAG: what `ls-remote` found).
- Tag Refs are still skipped by the poller (absent from the coordinator's data), so they get no update and are exempt from the Refresh Policy check. Non-version tags are tracked exactly like branches (keyed `url@ref`), so seeding, recording and the Refresh Policy check needed no change.
- Test helper `GitRepo.tag()` now passes `--force`, so tagging again moves the tag.
- README's sentence on what is tracked was updated.
