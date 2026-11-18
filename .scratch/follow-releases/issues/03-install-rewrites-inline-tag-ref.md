# 03: Install rewrites an inline Tag Ref

**What to build:** When a Tag Ref's `ref:` is written directly in the device YAML, Install moves it to the version the entity showed when Install was pressed. Before any Clean Build or compile, Install writes the new tag with the Device Builder's `devices/update_config`, passing as `expected` the file text that the pre-install check just read. Only the ref's text changes; comments and formatting stay as they are. It then compiles and uploads once, for all sources together, and records the Installed Tags on success, including when it re-attaches after a restart. The temporary Install guard from ticket 02 is removed. This follows ADR-0007.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] The fake Device Builder answers `devices/update_config`, updating its file map, and refuses with `precondition_failed` when `expected` does not match.
- [ ] After a successful Install the fake's YAML names the new tag, every other byte is unchanged, the entity shows no update, and the Installed Tag is recorded.
- [ ] If the file was edited between the pre-install read and the write, the Install fails at an "update YAML" stage with a clear error, nothing is compiled, and nothing is recorded.
- [ ] A failed compile or upload keeps the rewritten YAML, records nothing, and still offers the update; a retry succeeds without rewriting again.
- [ ] A newer tag that appears while the Install runs is not what gets written.
- [ ] A Tracked Config with both an outdated branch source and an outdated Tag Ref is rewritten and compiled in a single Install, and both records are written.
- [ ] A re-attached Install records the planned Installed Tags on success.
