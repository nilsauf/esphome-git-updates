# 03: Move follow-decision tests to the module, delete their HA versions

**What to build:** Go through `test_unset_refs.py`, `test_tag_install.py`, `test_github_releases.py` and `test_git_updates.py`. For each test that only checks a follow decision (what is followed, offered, seeded or kept), make sure `tests/test_following.py` covers it, then delete the HA-level test. Replace tests; don't keep both layers.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] These remain at HA level:
  - one end-to-end case per ref kind (Floating, Tag, Unset, Pinned);
  - YAML rewrites during an Install;
  - real `git ls-remote` via `git_repo`;
  - GitHub paging and the token option against `fake_github`;
  - Repair issues for the Refresh Policy.
- [ ] Every deleted test's scenario is named by a test in `tests/test_following.py`. List the mapping under `## Comments`.
- [ ] Coverage of `following.py` does not drop.
