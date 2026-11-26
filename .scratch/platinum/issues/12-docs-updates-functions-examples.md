# 12: Document data updates, functionality, use cases and examples

**What to build:** Four Gold documentation rules.

- **How and how often data updates** (`docs-data-update`): the Available ESPHome Version is polled every five minutes; the YAML is read and each Git Source's remote is asked at the Git poll interval (default thirty minutes, configurable); GitHub is asked for Releases and release notes (rate-limited, raised by the optional token); an Install re-reads the YAML and plans before compiling; a Device Builder that cannot be reached makes the entity unavailable.
- **Supported functionality** (`docs-supported-functions`): the one update entity per Tracked Config, what its installed and latest versions show (a composite of the ESPHome version and every Git Source), the release notes and commit counts, Install with progress, and the Repair issues it can raise.
- **Use cases** (`docs-use-cases`): a shared component repository whose `main` is followed; a Configuration pinned to a Version Tag that should follow releases; a fleet of devices built from one Configuration; keeping ESPHome itself current.
- **Examples** (`docs-examples`): at least one automation that notifies when an update is available, and one that reports or acts on an Install's result. Examples use the entity's real attributes and the `update.install` action; no blueprint is required.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [ ] Polling intervals, what triggers each poll, and the effect of an unreachable Device Builder are documented.
- [ ] Every entity and action the integration provides is documented, with the attributes that matter (installed and latest version, release summary and notes, progress).
- [ ] At least three use cases are described in the integration's own terms.
- [ ] At least one automation example is valid YAML for the documented entity and actions, and one covers reacting to an Install.
- [ ] No documented behaviour contradicts the code.

## Comments

Implemented and merged into `quality-scale-platinum` (2026-11-26), followed by the code-review fixes merge.
