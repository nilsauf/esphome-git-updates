# 01: Non-version tags follow their commit

**What to build:** A Git Source whose ref names a tag that is not a Version Tag (such as `stable` or `nightly`) is now a Floating Ref. When the maintainer moves that tag to another commit, the update entity offers an update, just as for a branch, and Install builds the same ref, which is never rewritten. A Pinned Ref now means a commit only. This ticket also introduces the Version Tag rule from the spec (`.scratch/follow-releases/spec.md`): an optional `v`/`V`, then one to four dot-separated integers, ordered numerically with zero padding. A tag that is a Version Tag is still ignored at this stage, as before.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] A Tracked Config with `ref: stable` (a tag on the test remote) shows no update after setup, shows an update once `stable` is moved to a new commit, and records the new commit after a successful Install.
- [ ] Refresh Policy is required for such a source, as for any Floating Ref.
- [ ] A ref naming a Version Tag (`v1.2.3`, `1.2`) and a ref naming a commit still never produce an update.
- [ ] Discovery tests cover how refs are classified: `v1.2.3`, `V1.2`, `1.2.3.4` count as Version Tags; `v1.2.0-rc1`, `test-foo`, `firmware-2024-05`, `v1.2.3.4.5` do not.
- [ ] `GLOSSARY.md` terms are used throughout; no ADR is contradicted.
