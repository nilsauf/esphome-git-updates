# 02: Reduce the poller to an HA adapter

**What to build:** Rewire `GitSourcesCoordinator` onto `FollowedSources`, as specified in `../spec.md`. It becomes `DataUpdateCoordinator[Snapshot]`. It keeps:

- YAML reading over `/ws` and error mapping (ADR-0006)
- untrackable logging
- `PreInstallCheck` and Repair issues, built from `followed.wrong_refresh_policy()`
- comparisons
- poll interval handling

`shown_sources`, `install_plan` and `async_check_before_install` delegate to `FollowedSources`. The entity's two-step Install protocol stays as it is.

Reduce `GitHubReleases` to the stateless Releases port. It keeps paging, release filtering, the unpaged warning and token/session. The keep-last-answer and failure logging now live in `FollowedSources`. Expose `git_remote`'s resolver as the remote port adapter. Make `TrackedConfigStore` satisfy the record protocol.

**Blocked by:** 01

**Status:** resolved

- [ ] `git_coordinator.py` contains no follow-mode decision, no key-scheme fallback and no seeding calls.
- [ ] `GitHubReleases` holds no answers between polls.
- [ ] The full existing test suite passes unchanged, except the one test that reaches into `runtime_data.git_coordinator` (`test_git_updates.py`), which is rewritten against the queries.
- [ ] `ruff` passes.

## Comments

Implemented together with 01 in 777fdb1, because making the follow classes private broke the old poller. The `test_git_updates.py` test that reaches into `runtime_data.git_coordinator` only reads `last_update_success` and `last_exception`, so it was left unchanged rather than rewritten against the queries.
