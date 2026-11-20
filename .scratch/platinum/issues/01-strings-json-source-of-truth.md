# 01: Make strings.json the source of truth

**What to build:** Add `strings.json` with the current content of `translations/en.json`. It becomes the file the integration's translation strings are written in; `translations/en.json` stays the runtime copy Home Assistant reads for a custom integration. A test pins the two together, so a key added to one and not the other fails CI. Every later ticket that adds a string adds it to both.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] `strings.json` exists and its content equals `translations/en.json`.
- [ ] A packaging-style test loads both files and asserts they parse to the same object, so formatting differences do not matter and a missing key does.
- [ ] The test fails when a key is added to one file only (checked by running it against a deliberately diverged copy, then reverting).
- [ ] No change to any string's text, and no change to how Home Assistant reads translations at runtime.
