# 02: FollowedSources owns every rule about GitHub's answers

Status: ready-for-agent

**What to build:** as specified in `../spec.md` under "One GitHub port", "`SourceChange`", "`FollowedSources` owns comparisons" and "The git poller".

- In `following.py`, replace the `Releases` and `Comparisons` ports with one `GitHub` port (`releases(url)`, `compare(url, base, head)`), which the adapter from 01 satisfies. Move `SourceChange` into `following.py`.
- `FollowedSources` keeps the comparisons as its own state, not in the `Snapshot`:
  - each poll, after remotes and Releases, compares every change not compared yet; a `GitHubError` never fails or drops the poll;
  - a comparison is kept while its change's two commits stay the same, asked again after a failure, and forgotten once its change is gone;
  - new `async_compare_missing() -> bool`: asks for every change still without a comparison, returns whether any arrived;
  - `shown()` takes no argument and reads the cache;
  - compare failures go through `_Outages` under one key for the Tracked Config: today's warning ("release notes show only commit changes while GitHub cannot be asked: …") once, and an info line when GitHub answers again. Releases outages stay per repository.
- In `git_coordinator.py`, build `GitHub(async_get_clientsession(hass), lambda: Options.of(entry.options).github_token, lambda: entry.title)` and hand it to `FollowedSources`. Drop the `comparisons` attribute; `async_compare_changed` calls `async_compare_missing` and notifies listeners when it returns true. Check `update.py` and any test for uses of `coordinator.comparisons` and adapt them.
- Delete `releases.py` and `comparisons.py`, and any `github.py` helper only they used.

**Blocked by:** 01

- [ ] In `tests/test_following.py`, `InMemoryReleases` and `NoComparisons` merge into one in-memory GitHub that lists Releases and comparisons by url, records what was asked, and can be made to fail. New scenarios, asserting only through `FollowedSources`' interface and the log:
  - a change is compared during a poll and shown with its commits;
  - a change is compared once while its commits stay the same;
  - a failed comparison is asked for again on the next poll, and by `async_compare_missing`;
  - `async_compare_missing` returns whether any arrived;
  - a comparison whose change is gone is forgotten;
  - a compare failure does not fail the poll;
  - one warning while comparisons fail, one info line on recovery.
- [ ] `following.py` never parses a GitHub url, and imports nothing from Home Assistant.
- [ ] The poller holds no comparisons object.
- [ ] Every HA test still passes, with behaviour and log messages unchanged. The full suite passes, and `ruff` is clean.
