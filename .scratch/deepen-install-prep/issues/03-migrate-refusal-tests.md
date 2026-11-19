# 03: Delete the HA versions of the refusal-rule tests

Status: ready-for-agent

**What to build:** as specified in `../spec.md` under "Testing Decisions". The unit tests from 01 now cover the OTA and Refresh Policy rules. Remove their HA duplicates from `tests/test_pre_install_checks.py`, and keep the HA tests that need HA.

**Blocked by:** 01, 02

- [ ] Deleted:
  - the Refresh Policy naming test;
  - the non-version-tag Refresh Policy test;
  - the version-tag needs-no-Refresh-Policy test.
- [ ] The missing-OTA HA test asserts only "refused with `HomeAssistantError`, starts no job".
- [ ] Kept at HA level:
  - an unreadable YAML is refused;
  - a fix counts without waiting for a poll;
  - a second press while checking is refused;
  - Repair issues are raised and cleared;
  - removing the entry drops its issues;
  - a config that passes both checks installs;
  - offline / available Linked Device (in `test_linked_device.py`).
- [ ] Each deleted HA test has a unit test in `tests/test_following.py` that covers its rule. Check this case by case before deleting.
- [ ] The full test suite passes, and `ruff` is clean.
