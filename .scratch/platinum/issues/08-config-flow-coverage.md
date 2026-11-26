# 08: Full config flow coverage

**What to build:** The Bronze `config-flow-test-coverage` rule. The flow is at 96%: the reauthentication's unsupported-version and cannot-connect branches, the empty-builder abort, and — once ticket 03 lands — the reconfigure errors are untested. Cover every branch of the flow, including recovery from errors, through the config-flow seam.

**Blocked by:** 03

**Status:** resolved

- [ ] The config flow module reports full coverage.
- [ ] Tests cover, through the flow: reauthentication succeeding, invalid auth, unsupported version and cannot-connect; setup aborting when the Device Builder lists no configurations; and every reconfigure error path from ticket 03 (each connection error and the wrong-configuration abort).
- [ ] No test reaches into flow internals; assertions are on flow results, entry data and entry state.

## Comments

Implemented and merged into `quality-scale-platinum` (2026-11-26), followed by the code-review fixes merge.
