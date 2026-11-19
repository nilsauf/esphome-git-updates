# 03: Keep the HA GitHub tests to the wiring

Status: ready-for-agent

**What to build:** as specified in `../spec.md` under "Testing Decisions" → "Full HA setup". The tests from 01 and 02 now cover paging, the token, comparison caching and failure. Trim `tests/test_github_releases.py` and `tests/test_release_notes.py` (and any other HA test that only repeats them) to the wiring.

**Blocked by:** 01, 02

- [ ] Kept or added at HA level:
  - release notes render each newer Release, and the commits of a changed Git Source;
  - changing the GitHub token option without reloading the entry reaches GitHub's next request (replaces the two separate token tests);
  - opening release notes with a missing comparison asks GitHub again and refreshes the entity.
- [ ] Deleted: the HA tests of paging, comparison caching and comparison failure, and the two separate token tests. Before deleting each, check case by case that a test in `tests/test_github.py` or `tests/test_following.py` covers its rule; add one there if not.
- [ ] Any other HA test that only repeats a Followed Sources or adapter case is deleted after the same check. Note in this ticket's Comments which tests were deleted and where each rule is now covered.
- [ ] The full test suite passes, and `ruff` is clean.
