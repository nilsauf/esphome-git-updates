# 10: Strict typing at every entry point

**What to build:** The Platinum `strict-typing` evidence. The project already checks the integration and its tests with strict mypy, and CI runs it. Two gaps remain:

- the options flow is not typed with the integration's config entry, and `async_get_options_flow` takes a bare config entry, which is what the rule's automated check looks for;
- the standalone CLI and the tests that drive the shared Device Builder module are not checked in CI. The project's mypy configuration documents why they need a separate invocation (the module has two names), and that invocation is not part of CI.

Annotate the options flow and its entry point with the integration's typed config entry. Add the separate mypy run for the CLI and its tests to CI, with the same strict settings.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [ ] The options flow and `async_get_options_flow` use the integration's typed config entry.
- [ ] The main mypy run stays green, and includes the diagnostics module added in ticket 02.
- [ ] CI runs the separate strict mypy invocation over the standalone CLI and the tests that import the shared Device Builder module, and it is green.
- [ ] No `Any` is introduced to make either check pass; the configuration is the same strictness the project already uses.

## Comments

Implemented and merged into `quality-scale-platinum` (2026-11-26), followed by the code-review fixes merge.
