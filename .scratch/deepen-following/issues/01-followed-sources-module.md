# 01: FollowedSources module with in-memory tests

**What to build:** `FollowedSources` in `following.py`, as specified in `../spec.md`. It is built with:

- a remote port (`resolve(url, ref, *, tags)`)
- a stateless Releases port (`releases(url)`)
- the installed-record protocol
- the entry title

It exposes `async_poll(discovery, files) -> Snapshot` and the four queries `shown`, `changes`, `install_plan` and `wrong_refresh_policy`. Move the follow-mode decision (`_ask_remote`), keep-last-known for remotes and Releases with warn-once/recovery logging, the key scheme, and seeding out of `git_coordinator.py` into it. `CommitFollowingSource` and `VersionFollowingSource` become private.

The poller is not rewired yet. If keeping both paths alive for one ticket costs more than it saves, merge this ticket with 02.

**Blocked by:** none

**Status:** ready-for-agent

- [ ] `tests/test_following.py` builds `FollowedSources` with in-memory remote, Releases and record adapters. It needs no HA, no Device Builder and no git.
- [ ] Each follow mode is covered: Floating Ref, Pinned Ref, Tag Ref with Releases, Tag Ref with Version Tags only, Tag Ref that is a branch, Unset Ref with Releases, with Version Tags only, and with neither, and an Unset Ref with no `written_at`.
- [ ] Unit tests cover:
  - a failing remote keeps the last state;
  - a Source switching between follow modes keeps its last state when its remote then fails (the key-scheme fallback);
  - GitHub never answered offers nothing newer;
  - GitHub failing keeps the last answer;
  - a first Version Tag switches an Unset Ref to versions without offering it.
- [ ] Unit tests cover seeding: new Sources are seeded, removed Sources are forgotten, and Tag Refs whose remote is failing are kept.
- [ ] Unit tests: `shown`, `changes` and `install_plan` reflect a record change made after the poll.
- [ ] Unit test: `wrong_refresh_policy` drops Sources following versions and keeps those following Latest Commit.
- [ ] `install_plan` uses the files of the same poll as the states it plans from.
