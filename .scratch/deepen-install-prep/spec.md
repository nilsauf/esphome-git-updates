# Prepare an Install in one step: spec

Status: ready-for-agent

Terms are defined in `GLOSSARY.md`. Relevant decisions: ADR-0003 (record what was shown when Install was pressed), ADR-0004 (Refresh Policy, exemption for Sources following versions), ADR-0006 (raw YAML over `/ws`), ADR-0007 (an Install rewrites Tag Refs).

This is a refactor. Behaviour visible to users does not change. It follows `deepen-following`, which left this as its first follow-up.

## Problem Statement

The update entity has to run a protocol to prepare an Install, and the protocol's ordering rule is written only in a docstring:

1. The entity asks the Linked Device whether it looks offline, and refuses if it does.
2. It marks the Install in progress, which refuses a second press.
3. It asks the git poller to "check before install". That runs a coordinator refresh, which swallows failures. The poller then reads three things after the fact: the pre-install check it stashed during the poll, `last_update_success` and `last_exception`. On a failed refresh, the coordinator's data still holds the *previous* poll.
4. It asks the poller for the Install plan, built from `FollowedSources`' last poll. The docstring says "call it right after the check".

ADR-0003/0007 ("record what was shown when Install was pressed") holds only because nothing awaits between steps 3 and 4.

The plan and the check must come from the same poll, but they are two separate pieces of state: the stashed `PreInstallCheck` on the poller, and the last poll inside `FollowedSources`.

The refusal rules are spread over three modules:

- offline in the entity;
- missing OTA and wrong Refresh Policy in `PreInstallCheck`;
- "cannot read its YAML" in the poller.

The only tests of the OTA and Refresh Policy rules press Install on a fully set-up HA instance, with the fake Device Builder and real git repositories.

## Solution

A poll produces one immutable `Snapshot` that can decide an Install on its own. It answers either "this Install is refused, and here is why" or "here is the plan", from a single poll.

The git poller gets one operation, "prepare an Install of what is shown now", which:

1. refuses an offline device without reading anything;
2. reads the YAML again through a direct poll that either returns a fresh Snapshot or fails with the reason;
3. asks that Snapshot to decide.

The entity then marks the Install in progress, calls prepare once, and hands the plan to the runner. There is no ordering rule left for it to know.

## User Stories

1. As a Home Assistant user, I want pressing Install on an offline device to be refused at once, so that the Device Builder never defers an Install (Deferred Install).
2. As a Home Assistant user, I want an offline refusal to cost no Device Builder round trip, so that pressing Install on an offline device is instant.
3. As a Home Assistant user, I want an Install of a Tracked Config with no `ota:` platform to be refused with a message naming the fix, so that I know to flash once over USB.
4. As a Home Assistant user, I want an Install to be refused when a Git Source following Latest Commit lacks `refresh: always`, naming those Sources, so that ESPHome never builds a stale cached clone (ADR-0004).
5. As a Home Assistant user, I want a Tag Ref or an Unset Ref following versions to need no Refresh Policy, so that I am not asked to change YAML that is already safe.
6. As a Home Assistant user, I want a Git Source on a tag that is not a Version Tag to need `refresh: always` like a branch, so that a moved tag is never missed.
7. As a Home Assistant user, I want all failed checks named in one refusal message, so that I can fix everything in one pass.
8. As a Home Assistant user, I want an Install to read the YAML again before deciding, so that a fix I just made counts without waiting for the next poll.
9. As a Home Assistant user, I want an Install to be refused with the reason when the YAML cannot be read, so that I know why nothing happened.
10. As a Home Assistant user, I want the Repair issues to update on the read an Install makes, as on any poll, so that a fixed YAML clears its issue when I press Install.
11. As a Home Assistant user, I want an Install to record exactly the commits and versions that the read just before it showed (ADR-0003), so that a push during the build still shows as an update afterwards.
12. As a Home Assistant user, I want an Install to rewrite each Tag Ref in the files as that same read saw them (ADR-0007), so that the rewrite never targets stale text.
13. As a Home Assistant user, I want a second press while the first Install is still being checked to be refused, so that two Installs never run for one Tracked Config.
14. As a Home Assistant user, I want a refused Install to start no Device Builder job and leave the entity not in progress, so that nothing half-starts.
15. As a Home Assistant user, I want the entity to show what the Install's read saw once it returns, so that state and plan agree.
16. As a maintainer, I want every refusal rule reachable from one poller operation, so that I can find all the reasons an Install is refused in one place.
17. As a maintainer, I want the OTA and Refresh Policy rules tested in plain pytest against a Snapshot, so that a rule change needs no HA, no Device Builder and no git.
18. As a maintainer, I want the entity's install path to be guard → prepare → run, so that it cannot be wired in the wrong order.
19. As a maintainer, I want no code to read a coordinator's `last_update_success` or `last_exception` to learn whether a read worked, so that failure is a result, not a side channel.
20. As a maintainer, I want the plan and the check to come from one immutable value, so that they cannot come from different polls.

## Implementation Decisions

### `Snapshot` decides an Install

- `Snapshot` stops being opaque. It is the one immutable result of a poll. It holds:
  - the Followed Sources;
  - the text of each file read;
  - the `PreInstallCheck`: OTA status from discovery, and the Sources whose Refresh Policy is wrong and that follow Latest Commit (ADR-0004).
- `FollowedSources.async_poll` builds the `PreInstallCheck` itself from the discovery it is given. The poller no longer builds it.
- `Snapshot` exposes:
  - `check`, the `PreInstallCheck`, for reporting Repair issues;
  - `install(port, clean_build, title) -> InstallPlan | Refusal`. It returns a `Refusal` when the check fails, and otherwise the plan of what this poll showed (ADR-0003, ADR-0007).
- The plan combines the Snapshot's poll with the installed record as read when `install` is called, as `install_plan` does today. The Snapshot therefore keeps a reference to the record. A plan made after an Install with no poll in between must still rewrite nothing.
- `Refusal` is a frozen value holding the user-facing message. `PreInstallCheck` still builds the joined message ("…; and …. See Repairs.").
- `FollowedSources.install_plan` and `FollowedSources.wrong_refresh_policy` are removed from its interface. The Snapshot answers both. `shown` and `changes` stay on `FollowedSources`.
- `PreInstallCheck` stays a pure value. `PreInstallIssues` stays the HA adapter that raises and clears the Repair issues.

### The git poller prepares an Install

- `GitSourcesCoordinator` gets one private "poll now" path. It:
  1. reads the YAML over `/ws` and discovers its Sources;
  2. polls `FollowedSources`;
  3. reports `snapshot.check` to `PreInstallIssues`;
  4. updates GitHub comparisons;
  5. returns the Snapshot, or raises the failure.

  `_async_update_data` uses it, mapping failures to `UpdateFailed` as today.
- New `async_prepare_install(options, device_offline) -> InstallPlan`. It raises `HomeAssistantError` on refusal, in this order:
  1. When `device_offline` is true, it refuses with today's offline message. It reads nothing.
  2. It runs "poll now". On failure it refuses with today's "cannot read its YAML to check it before installing: …" message, carrying the failure's text. A failed read leaves the last good poll in place, as a failed scheduled poll does.
  3. It publishes the fresh Snapshot as the coordinator's data (`async_set_updated_data`), so listeners, Repair issues and the poll schedule behave as after a scheduled poll.
  4. It calls `snapshot.install(...)`. A `Refusal` becomes `HomeAssistantError` with its message; otherwise it returns the plan.
- Removed from the poller: the stashed `_pre_install_check`, `async_check_before_install`, `install_plan`, and every read of `last_update_success` and `last_exception`.
- The offline rule stays an adapter-level `if`. It needs no Snapshot, and its input comes from HA's registries.

### The update entity

- The entity keeps the in-progress guard. It computes `device_offline` from the Linked Device (false without one), marks the Install in progress, awaits `async_prepare_install` once, and starts the runner with the returned plan. On any error from prepare it clears in-progress and re-raises.
- The entity no longer holds any refusal rule.

### Unchanged

- Refusal messages, Repair issue ids and translations, and the order of checks as the user sees them.
- `InstallRunner`, `InstallPlan`, `TrackedConfigStore` and the version coordinator.
- No GLOSSARY or ADR changes. `Snapshot` and `Refusal` are code names, not domain terms.

## Testing Decisions

- Test external behaviour through the highest seam that covers it. Replace tests; do not add a second layer of them. Do not assert on private attributes or the shape of coordinator internals.
- Two seams, both existing:
  - **`FollowedSources` / `Snapshot`, with in-memory adapters**, as in `tests/test_following.py`. New unit tests:
    - no OTA → `Refusal` naming the `ota:` platforms;
    - unknown OTA (a remote package may add it) → not refused;
    - a Floating Ref without `refresh: always` → `Refusal` naming the Source;
    - a non-version tag without it → refused, like a branch;
    - a Tag Ref, and an Unset Ref following versions, without it → not refused;
    - several failed checks → one message joining every reason;
    - checks pass → the same `InstallPlan` as today;
    - `check` reflects the poll it came from;
    - the plan reflects a record change made after the poll.

    The existing `install_plan` tests move to `snapshot.install`.
  - **The integration in HA, with the fake Device Builder and real git repositories**, as in `tests/test_pre_install_checks.py` and `tests/test_linked_device.py`. These keep only what needs HA:
    - a refusal becomes `HomeAssistantError` and starts no job;
    - an unreadable YAML is refused with its reason;
    - a fix counts without waiting for a poll;
    - a second press while checking is refused;
    - Repair issues are raised by a poll and cleared once the YAML is fixed;
    - removing the entry drops its issues;
    - an offline Linked Device is refused, and one with an available entity installs;
    - one config that passes both checks installs.
- These HA tests are deleted, because their rule moves to the unit tests:
  - the Refresh Policy naming test;
  - the non-version-tag Refresh Policy test;
  - the version-tag needs-no-Refresh-Policy test.

  The missing-OTA HA test is cut down to "refused, starts no job".
- Prior art: the `deepen-following` migration (`InMemoryRemotes`, `InMemoryReleases`, the in-memory record in `tests/test_following.py`), and the `tracked_config` helpers (`set_up`, `press_install`, `install`).

## Out of Scope

- Moving the installed record (ADR-0003 policy, the duplicated seed-and-forget code in `TrackedConfigStore`, Installed ESPHome Version resolution) into its own module.
- A Tracked Config module behind a thin update entity, and folding the version coordinator into it.
- Merging `GitHubReleases` and `GitHubComparisons` into one GitHub adapter that does not depend on HA.
- Any change to refusal wording, Repair issue text, or which conditions refuse an Install.

## Further Notes

- The comment "what gets recorded is what the check just read, not what the remote holds once the build is done" moves from the entity to `async_prepare_install`. There the rule is now enforced by construction rather than by call order.
- Because prepare publishes the Snapshot it read, the entity's displayed versions and the plan come from the same poll.
