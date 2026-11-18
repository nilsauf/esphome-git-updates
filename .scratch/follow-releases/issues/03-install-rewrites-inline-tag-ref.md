# 03: Install rewrites an inline Tag Ref

**What to build:** When a Tag Ref's `ref:` is written directly in the device YAML, Install moves it to the version the entity showed when Install was pressed. Before any Clean Build or compile, Install writes the new tag with the Device Builder's `devices/update_config`, passing as `expected` the file text that the pre-install check just read. Only the ref's text changes; comments and formatting stay as they are. It then compiles and uploads once, for all sources together, and records the Installed Tags on success, including when it re-attaches after a restart. The temporary Install guard from ticket 02 is removed. This follows ADR-0007.

**Blocked by:** 02

**Status:** done

- [x] The fake Device Builder answers `devices/update_config`, updating its file map, and refuses with `precondition_failed` when `expected` does not match.
- [x] After a successful Install the fake's YAML names the new tag, every other byte is unchanged, the entity shows no update, and the Installed Tag is recorded.
- [x] If the file was edited between the pre-install read and the write, the Install fails at an "update YAML" stage with a clear error, nothing is compiled, and nothing is recorded.
- [x] A failed compile or upload keeps the rewritten YAML, records nothing, and still offers the update; a retry succeeds without rewriting again.
- [x] A newer tag that appears while the Install runs is not what gets written.
- [x] A Tracked Config with both an outdated branch source and an outdated Tag Ref is rewritten and compiled in a single Install, and both records are written.
- [x] A re-attached Install records the planned Installed Tags on success.

## Comments

- Discovery: `GitSource.written_at: RefLocation | None` (not part of equality) says where the ref's text is written: `RefLocation(file, start, end)`, a span of that file's text, quotes excluded. `git_sources.rewritten(text, [(location, tag), ...])` replaces the spans from last to first, keeping every other byte. The loader (`_Loader(text, path)`) turns every plain or simply quoted string scalar into a `_Written` str subclass carrying its location; `_substitute` returns a string with no `$` as is, so the location survives. A ref that goes through a substitution, escapes or folding gets no location.
- Ticket 04: locations carry their file, so a literal `ref:` in an `!include`d or local-package file is probably located already (untested). Still to do: the shorthand's `@ref` (the scalar's span ends with `@<ref>`), single-use substitution values, the untrackable reasons, and the same repository at two refs. Note that `ref: 1.10` loads as the float `1.1` today.
- Ticket 06: an insertion is a `RefLocation` with `start == end`, but `rewritten` would also need the text to insert (`\n<indent>ref: <tag>` or `@<tag>`): give `RefLocation` a prefix, or let the edit carry the full text.
- Coordinator: `GitSourcesCoordinator.install_plan(port, clean_build)` replaces `latest_commits()`; update.py calls it right after `async_check_before_install`. It uses the data and the file texts (`_files`, kept by `_reader` during the same poll) of the pre-install refresh. Per `VersionedGitSource`, `_built(state)` is the version an Install builds: the one offered if an update is shown, else the YAML's ref, so a hand edit is never reverted. `_moves()` lists the sources whose ref differs from it. The plan holds one `YamlRewrite(file, content, expected)` per file, and `tags` = `_built` per repository URL. Ticket 06's Unset Refs need `_built` and `_moves` to cover them.
- The ticket-02 guard is gone. Install is still refused while an outdated Tag Ref has no location ("cannot rewrite a ref that is not written as is in the YAML (marsrelay v1.0.0 → v1.1.0)", tested in test_pre_install_checks.py). Ticket 04 should turn those cases into rewritable locations or untrackable reasons.
- Install: `InstallPlan.tags` and `.rewrites`, `ActiveInstall.tags` (stored as the NotRequired `tags`), `InstallBookkeeping.async_record_install(version, commits, tags)`. Rewrites are written inside the Install's connection, at stage `update YAML` (`install_log.UPDATE_YAML`), before any Clean Build. A re-attached Install has no rewrites.
- Device Builder client: `update_config(configuration, content, *, expected)`. A `precondition_failed` raises `PreconditionFailed` (a `CommandFailed`), whose message reads "<file> changed since it was read: …". The real server's success `result` is ignored.
- Fake Device Builder: it answers `devices/update_config` at any point, `writes` records the paths written, and `before_write(path)` runs first, so a test can edit a file or add a tag mid-Install. Integration tests are in tests/test_tag_install.py.
