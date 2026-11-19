# One GitHub adapter behind Followed Sources: spec

Status: resolved

Terms are defined in `GLOSSARY.md`. Relevant decisions: ADR-0003 (the integration records Installed Commits), ADR-0007 (an Install rewrites Tag Refs; GitHub's last answer about Releases is kept while it cannot be asked).

This is a refactor. Behaviour visible to users does not change. It follows `deepen-following` and `deepen-install-prep`.

## Problem Statement

GitHub is asked for two things: the Releases of a Git Source's repository, which decide what a Tag Ref or Unset Ref follows, and the commits between two commits, which release notes list. Each is wrapped in its own shallow module, and both reach into Home Assistant for things they could be handed:

- The Releases module takes the HA instance and the config entry only to get the shared HTTP session, the GitHub token option and the entry title. Its paging rule (the next page only while a full page holds no Release, at most five pages, one warning when it gives up) can only be tested by setting up the whole integration.
- The comparisons module takes the same HA instance and entry for the same reasons. It also owns its own cache (compare once per change, ask again after a failure) and its own "warn once, log on recovery" bookkeeping. `FollowedSources` already has that bookkeeping for remotes and Releases, so the rule is written twice.
- Comparisons are the only GitHub answers that are not behind `FollowedSources`. The git poller passes them back and forth: it asks `FollowedSources` for its changes, hands them to the comparisons module, then hands the comparisons module back to `FollowedSources` to show the sources. It also exposes the comparisons module as a public attribute.
- Every test of comparison caching and failure, and every test of Releases paging and the token, runs through a fully set-up HA instance with the fake Device Builder, real git repositories and the fake GitHub.

## Solution

One GitHub adapter that does not use Home Assistant. It fetches and nothing more: the Releases of a repository (with the paging rule) and the comparison between two commits, each asked by the Git Source's url. The session, a token getter and a title getter are handed to it.

All policy about GitHub's answers lives in `FollowedSources`: keeping the last answer about Releases (as today), caching comparisons, and warning once while GitHub cannot be asked. `FollowedSources` asks for the comparisons of its own changes as part of each poll and when release notes need a missing one, so the poller no longer passes comparisons around.

The two shallow modules are deleted.

## User Stories

1. As a user with a Git Source on GitHub, I want release notes to keep listing the commits each changed Git Source gained since its Installed Commit, so that the refactor changes nothing I see.
2. As a user with a Tag Ref or Unset Ref on GitHub, I want it to keep following Releases, else Version Tags, exactly as before, so that the version I am offered does not change.
3. As a user whose GitHub cannot be asked for a while, I want each Source to keep GitHub's last answer about Releases, so that it does not flip between following Releases and Version Tags (ADR-0007).
4. As a user whose GitHub cannot be asked for a while, I want release notes to fall back to the commit change, and a poll never to fail because of it, so that the update entity stays available.
5. As a user whose GitHub failed to compare a change, I want the next poll or the next opening of release notes to ask again, so that the commit list appears once GitHub answers.
6. As a user, I want a change compared only once while its two commits stay the same, so that my GitHub rate limit is not used up by repeated polls.
7. As a user, I want a comparison forgotten once its change is gone (installed, or the Source removed), so that stale commit lists never show.
8. As a user reading the log, I want one warning per Tracked Config while GitHub cannot compare, and one info line when it answers again, so that an outage does not flood the log.
9. As a user reading the log, I want one warning per repository while GitHub cannot list its Releases, and one info line when it can again, as today.
10. As a user with a repository whose newest releases are all drafts or prereleases, I want the next pages asked for only while a full page holds no Release, at most five, with one warning when the integration gives up and follows Version Tags, as today.
11. As a user with a Git Source not on GitHub, I want GitHub never to be asked about it, as today.
12. As a user who adds or changes the GitHub token option, I want the very next GitHub request to carry the new token without reloading the entry, so that a raised rate limit takes effect while an Install may be running.
13. As a user opening release notes while a comparison is missing, I want the integration to ask GitHub for it and refresh the entity when it arrives, as today.
14. As a maintainer, I want every rule about GitHub's answers (keep last, cache, warn once) in `FollowedSources`, so that a change to that rule is made in one place.
15. As a maintainer, I want the GitHub adapter to know nothing of Home Assistant, so that its paging, token and failure behaviour is tested against the fake GitHub alone.
16. As a maintainer, I want which urls are on GitHub to be decided inside the GitHub adapter, so that `FollowedSources` never parses a GitHub url.
17. As a maintainer, I want one port in `FollowedSources` for GitHub, so that its tests need one in-memory GitHub, not two fakes.
18. As a maintainer, I want the poller to stop holding and passing comparisons, so that it stays an adapter that reads the YAML, schedules polls and maps errors.
19. As a maintainer, I want the scenarios about comparison caching and logging in plain pytest, so that they run fast and say what they test.
20. As a maintainer, I want HA tests that only repeat a `FollowedSources` or adapter test deleted, so that each rule has one home in the tests.

## Implementation Decisions

- **The GitHub adapter** lives with the existing HA-free GitHub request code, which already owns GitHub's wire format and its single request path. It is constructed with:
  - the shared HTTP session;
  - a token getter, read at each request: an options change does not reload the entry, so a token set later must reach the next request;
  - a title getter, for the paging warning.
- It has two operations, both taking the Git Source's url:
  - **Releases of a url**: GitHub's Releases of that repository (neither draft nor prerelease, tag a Version Tag), newest first; empty when the repository is not on GitHub or has none. It keeps the paging rule and its once-per-repository warning, which are facts about GitHub, not about following.
  - **Compare a url from one commit to another**: the comparison GitHub lists; `None` when the repository is not on GitHub, without a request.
  - Both raise `GitHubError` when GitHub cannot be asked, refuses or answers unexpectedly. The adapter caches nothing and keeps no last answer.
- **One GitHub port in `FollowedSources`** replaces its separate Releases and Comparisons ports. The in-memory GitHub in its tests satisfies it.
- **`SourceChange`** (a Source whose Latest Commit differs from its Installed Commit) moves into the Followed Sources module, which produces it.
- **`FollowedSources` owns comparisons** as its own state, not as part of the `Snapshot`: install decisions do not depend on comparisons, and release notes can fill one in after a poll without a new `Snapshot`.
  - Each poll compares every change not compared yet, after asking remotes and Releases. A compare failure never fails the poll and never drops it.
  - A comparison is kept while its change's two commits stay the same; a failed one is asked for again next time; one whose change is gone is forgotten.
  - A new operation, "compare the missing changes", asks GitHub for every change still without a comparison and returns whether any arrived. Release notes use it.
  - Showing the sources reads the cache; it no longer takes comparisons as an argument.
  - Compare failures go through the existing outage bookkeeping under a single key for the whole Tracked Config: one warning ("release notes show only commit changes while GitHub cannot be asked"), one info line on recovery. Releases failures stay per repository.
- **The git poller** builds the GitHub adapter from the HA session, the entry's token option and its title, and hands it to `FollowedSources`. It no longer holds a comparisons object. Its "compare changed" operation calls "compare the missing changes" and notifies listeners when it returns true.
- **Deleted**: the Releases module and the comparisons module.
- No change to the store, the `Snapshot`, the Install plan, or the update entity's interface beyond what the poller change implies.

## Testing Decisions

- A good test drives a module only through its interface and asserts on what a caller sees: what is shown, which changes are listed, what is logged, which requests reach GitHub. No test reads private attributes or the cache's layout.
- **`FollowedSources` with an in-memory GitHub** (the main seam). The existing in-memory Releases and no-comparisons fakes merge into one in-memory GitHub that lists Releases and comparisons by url, records what was asked, and can be made to fail. New scenarios:
  - a change is compared during a poll and shown with its commits;
  - a change is compared once while its commits stay the same;
  - a failed comparison is asked for again on the next poll, and by "compare the missing changes";
  - "compare the missing changes" returns whether any arrived;
  - a comparison whose change is gone is forgotten;
  - a compare failure does not fail the poll;
  - one warning while comparisons fail, one info line on recovery.
  - Prior art: the existing remote and Releases outage scenarios in the Followed Sources tests.
- **The GitHub adapter over HTTP against the fake GitHub**, with a real HTTP session and no HA. Covers:
  - Releases paging: one page when it holds a Release; the next page while a full page holds none; at most five pages, then empty with one warning;
  - drafts, prereleases and non-version tags are not Releases;
  - the token header is sent, and a token changed between requests is sent on the next one;
  - a url not on GitHub sends no request;
  - a refusal (such as a rate limit) or an unreachable GitHub raises `GitHubError`.
  - Prior art: the fake GitHub server already used by the HA tests; the HA-free Device Builder client tests against the fake Device Builder.
- **Full HA setup**, kept to the wiring:
  - release notes render each newer Release, and the commits of a changed Git Source;
  - changing the token option without reloading the entry reaches GitHub's next request;
  - opening release notes with a missing comparison asks GitHub again and refreshes the entity.
  - The HA tests of paging, comparison caching and comparison failure, and the two separate token tests, are replaced by the tests above. Any remaining HA test that only repeats a Followed Sources or adapter case is deleted, after checking it case by case.

## Out of Scope

- Asking hosts other than GitHub for Releases or comparisons.
- Changing what release notes show, or the paging limits.
- Moving the installed record out of the version coordinator's module, or a Tracked Config module behind the update entity (separate architecture candidates).
- Any change to how remotes are asked.

## Further Notes

- Two adapters justify the new seam: the HTTP adapter in production, the in-memory GitHub in the Followed Sources tests.
- User story 12 already holds today, because both modules read the token option at each request. The token getter keeps it true once the adapter no longer sees the entry, and the HA test pins it.
