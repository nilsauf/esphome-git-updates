# 09: Every module above 95% coverage, enforced in CI

**What to build:** The Silver `test-coverage` rule, and the machine that keeps it true. Two modules are below the floor: the Device Builder module (93%) and the git remote module (91%). Add tests for the uncovered statements — the error and timeout branches, the protocol replies the fake server can already produce, and the classify paths — without changing production behaviour.

Then enforce the rule rather than reporting it: run the tests with coverage in CI, and fail the job if **any module** of the integration is at or below 95%. A global average can hide a weak module, so read the per-module numbers from the coverage report.

The coverage reporter is not currently installed in CI; add it to the development dependencies and to the CI install.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Every module of the integration is above 95% coverage; no production code is deleted merely to raise the number.
- [ ] The Device Builder module's uncovered error, timeout and unexpected-reply branches are covered by tests that drive the fake server into them.
- [ ] The git remote module's uncovered timeout and failure paths are covered, including the no-output and error-exit cases.
- [ ] CI runs the tests with a machine-readable coverage report and fails when any module of the integration is at or below 95%.
- [ ] A deliberately low-coverage module makes the CI check fail (verified locally, then reverted).
