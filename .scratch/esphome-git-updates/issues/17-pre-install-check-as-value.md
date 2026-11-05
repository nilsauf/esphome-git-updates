# 17: Pre-install checks return a value instead of stashing one

**Status:** needs-triage

**What hurts** (architecture review, 2026-11-05):
- `update.py:221-240` calls `GitSourcesCoordinator.async_check_before_install`.
- That call runs a full poll: `ls-remote` for every Source, seeding and GitHub compares.
- As a side effect, the poll stores `_pre_install_check`, which is then read back (`git_coordinator.py:139-165`).
- The offline refusal (`linked_device.py:55`) and the YAML refusal (`pre_install.py`) are built in different modules.
- `PreInstallCheck.refusal` is pure but only tested through the harness.

**Direction:**
- Discovery returns `Discovery` and `PreInstallCheck` as a value.
- One place gathers every refusal. That could be `InstallRunner` from ticket 15, or the entity.
- The double-press guard must still mark the Install in progress before the first await (`test_pre_install_checks.py:209`).

**Blocked by:** 15.
