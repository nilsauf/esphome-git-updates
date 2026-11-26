# 07: Set the update platform's parallel update limit

**What to build:** The Silver `parallel-updates` rule. The update platform sets no limit, so Home Assistant applies its default to entity updates that are not serialized anyway. Set it explicitly to zero: polling belongs to the coordinators, and Installs are serialized by the Install runner, so a per-entity limit does not apply.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [ ] The update platform declares a parallel update limit of zero, with a comment saying why.
- [ ] A test asserts the platform's declared limit, so it cannot be dropped silently.
- [ ] No change to polling behaviour or to Install serialization.

## Comments

Implemented and merged into `quality-scale-platinum` (2026-11-26), followed by the code-review fixes merge.
