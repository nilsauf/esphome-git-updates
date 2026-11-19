# 01: One HA-free GitHub adapter

Status: resolved

**What to build:** as specified in `../spec.md` under "The GitHub adapter".

- In `github.py`, add a `GitHub` class constructed with the shared `aiohttp.ClientSession`, a token getter (`Callable[[], str | None]`, read at each request) and a title getter (`Callable[[], str]`, for the paging warning).
- `async releases(url) -> tuple[Release, ...]`: the Releases of the repository at *url* (neither draft nor prerelease, tag a Version Tag), newest first; `()` without a request when *url* is not on GitHub. Move the paging rule and its once-per-repository warning here from `releases.py` (`GitHubReleases._releases`, `_is_release`, `_MAX_PAGES`).
- `async compare(url, base, head) -> Comparison | None`: `None` without a request when *url* is not on GitHub.
- Both raise `GitHubError` as today. The adapter caches nothing and keeps no last answer.
- Leave `releases.py`, `comparisons.py`, `FollowedSources` and the poller untouched; ticket 02 switches them over and deletes the old modules. Module-level helpers that only the old modules use may stay until then.

**Blocked by:** none

- [ ] A new `tests/test_github.py` drives `GitHub` over a real `aiohttp.ClientSession` against `tests/fake_github.py`, with no HA fixture (prior art: `tests/test_device_builder.py` against the fake Device Builder). It covers:
  - Releases: one page when it holds a Release; the next page while a full page holds none; at most five pages, then `()` with one warning (not repeated on the next call);
  - drafts, prereleases and non-version tags are not Releases;
  - compare lists the commits, oldest first, with the total;
  - the token header is sent, and a token changed between two requests is sent on the next one; no token, no header;
  - a url not on GitHub sends no request, for both operations;
  - a refusal (such as a rate limit) and an unreachable GitHub raise `GitHubError`, for both operations.
- [ ] `github.py` still imports nothing from Home Assistant.
- [ ] The full test suite passes, and `ruff` is clean.
