# 01: A Snapshot decides an Install

Status: ready-for-agent

**What to build:** as specified in `../spec.md` under "`Snapshot` decides an Install".

- `Snapshot` becomes the immutable result of a poll. It holds the Followed Sources, the files read, the `PreInstallCheck` and a reference to the installed record.
- `FollowedSources.async_poll` builds the `PreInstallCheck` from discovery's OTA status and the wrong-Refresh-Policy Sources that follow Latest Commit.
- `Snapshot` exposes:
  - `check`;
  - `install(port, clean_build, title) -> InstallPlan | Refusal`.
- Add a frozen `Refusal(message)`. `PreInstallCheck` still builds the joined message.
- Remove `FollowedSources.install_plan` and `FollowedSources.wrong_refresh_policy`.
- Keep the poller working by having it use `snapshot.check` and `snapshot.install` behind its existing two-step methods for now. Ticket 02 replaces those.

**Blocked by:** none

- [ ] `tests/test_following.py` covers `snapshot.install` and `snapshot.check` with the in-memory adapters, and needs no HA. The cases:
  - no OTA → `Refusal`;
  - unknown OTA → allowed;
  - a Floating Ref without `refresh: always` → `Refusal` naming it;
  - a non-version tag without it → refused;
  - a Tag Ref, and an Unset Ref following versions, without it → allowed;
  - several failed checks → one joined message;
  - checks pass → the `InstallPlan`.
- [ ] The existing `install_plan` unit tests now call `snapshot.install`. That includes "after an install with no poll rewrites nothing" and "rewrites the files as its poll read them".
- [ ] `FollowedSources` no longer exposes `install_plan` or `wrong_refresh_policy`.
- [ ] The full test suite passes, and `ruff` is clean.
