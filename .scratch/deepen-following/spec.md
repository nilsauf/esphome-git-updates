# Deepen the Followed Sources module: spec

Status: ready-for-agent

Terms are defined in `GLOSSARY.md`. Relevant decisions: ADR-0003 (installed record and seeding), ADR-0004 (Refresh Policy, exemption for sources following versions), ADR-0006 (raw YAML over `/ws`), ADR-0007 (Tag Refs follow Releases; the last GitHub answer is kept).

This is a refactor. Behaviour visible to users does not change.

## Problem

The GLOSSARY rule for what a Git Source follows is split between five places:

- `RefKind` and `written_at` clearing in `git_sources.py`
- `_ask_remote` in `git_coordinator.py`
- `VersionFollowingSource.offered` in `following.py`
- the "`None` = GitHub never answered" meaning, defined in `releases.get` and interpreted in `following`

The rule is: Tag Ref → Releases, else Version Tags; Unset Ref → Releases, else Version Tags, else Latest Commit; a Tag Ref that turns out to be a branch → Latest Commit.

The poller (`GitSourcesCoordinator`) also knows the key scheme. Commit-following sources are keyed `url@ref` and version-following ones `url`. The poller needs this for its keep-last-known fallback (`previous.get(source.key) or previous.get(source.url)`) and for the "present" sets it passes when seeding.

Seeding, the ADR-0004 filter and the Install plan are assembled in the poller from separate attributes (`data`, `_files`, `_pre_install_check`).

`following.py` has no tests of its own. Every follow scenario is tested through HA, the fake Device Builder and real git repositories.

## Solution

Make `following.py` a deep module: a stateful `FollowedSources` object, one per config entry, that owns everything about Followed Sources. The poller becomes a thin HA adapter.

### Interface

Construction:

- **Remote port**: `async resolve(url, ref, *, tags: bool) -> RemoteTags`; raises `GitRemoteError`. The production adapter is `git_remote`'s existing resolver.
- **Releases port**: stateless `async releases(url) -> tuple[Release, ...]`. It raises `GitHubError` on failure and returns `()` for repositories not on GitHub. The production adapter is `GitHubReleases`, reduced to paging, filtering out drafts and prereleases, the "no Release on the pages asked for" warning, and token/session injection.
- **Installed record**: `installed_commit(key)`, `installed_tag(url)`, `async_seed_commits(seeds, present)`, `async_seed_tags(seeds, present)`. `TrackedConfigStore` satisfies it, with methods renamed if needed.
- The entry title, used in log messages.

Poll:

- `await async_poll(discovery, files) -> Snapshot`. `Snapshot` is an opaque, immutable value. Callers use it only to tell that something changed.

Queries. Each one combines the last snapshot with the installed record as read at call time, so values stay correct after an Install with no poll in between:

- `shown(comparisons) -> tuple[ShownSource, ...]`
- `changes() -> list[SourceChange]`
- `install_plan(port, clean_build) -> InstallPlan`: built from the files of the same poll as the states it plans from.
- `wrong_refresh_policy() -> tuple[GitSource, ...]`: the Sources in `discovery.wrong_refresh_policy` that follow Latest Commit (ADR-0004).

### Behind the interface

- Which questions the remote and Releases ports are asked, chosen by follow mode.
- The follow-mode decision.
- The key scheme.
- Keeping the last known state of a remote that fails, and the last GitHub answer (ADR-0007), with warn-once and "reachable again" logging for both.
- The "GitHub has never answered" state.
- Seeding new Sources and forgetting removed ones against the installed record (ADR-0003).
- `CommitFollowingSource` and `VersionFollowingSource`, which become private.

### What stays in the poller

`GitSourcesCoordinator` becomes `DataUpdateCoordinator[Snapshot]`. It keeps:

- reading the YAML over `/ws` (`_reader`, `_ReadRefused` → `ProtocolProblem`, mapping errors to `UpdateFailed`), then calling `discover_git_sources`;
- logging untrackable Sources;
- building `PreInstallCheck` from `discovery.ota` and `followed.wrong_refresh_policy()`, and reporting Repair issues;
- `GitHubComparisons`, `async_compare_changed`, and passing comparisons to `shown`;
- `async_check_before_install` and `install_plan` as thin delegations, with the two-step protocol unchanged;
- poll interval handling.

## Out of scope

These are follow-ups. The design leaves room for each.

- Merging `async_check_before_install` and `install_plan` into one "prepare Install" operation.
- Moving the installed record (ADR-0003 policy, the duplicated seed-and-forget code in `TrackedConfigStore`) into its own module.
- Merging `GitHubReleases` and `GitHubComparisons` into one GitHub adapter that does not depend on HA.

## Testing

Replace tests rather than add a second layer of them.

- New `tests/test_following.py` exercises `FollowedSources` through its interface, using in-memory adapters:
  - a remote as a dict `{url: (refs, tags)}` that can be told to fail;
  - Releases as a dict, which can fail or be "never answered" by failing on the first ask;
  - an in-memory record.
- Follow-decision scenarios move there from `test_unset_refs.py`, `test_tag_install.py`, `test_github_releases.py` and `test_git_updates.py`. The HA-level versions are deleted.
- These stay at HA level:
  - one end-to-end case per ref kind;
  - YAML rewrites during an Install;
  - real `git ls-remote` via `git_repo`;
  - GitHub paging and the token option against `fake_github`;
  - Repair issues.
