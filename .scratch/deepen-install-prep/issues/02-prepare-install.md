# 02: The poller prepares an Install; the entity is guard → prepare → run

Status: ready-for-agent

**What to build:** as specified in `../spec.md` under "The git poller prepares an Install" and "The update entity".

- In `GitSourcesCoordinator`, add one private "poll now" path that `_async_update_data` also uses. It reads the YAML, polls `FollowedSources`, reports `snapshot.check` to `PreInstallIssues`, updates comparisons, and returns the Snapshot or raises.
- Add `async_prepare_install(options, device_offline) -> InstallPlan`. In order:
  1. offline → refuse without reading;
  2. poll now, and refuse on failure with today's "cannot read its YAML" message;
  3. publish the Snapshot with `async_set_updated_data`;
  4. call `snapshot.install(...)`, turning a `Refusal` into `HomeAssistantError`.
- Delete `_pre_install_check`, `async_check_before_install`, `install_plan`, and every read of `last_update_success` and `last_exception`.
- In the update entity: compute `device_offline` from the Linked Device, mark the Install in progress, await `async_prepare_install` once, then start the runner. On error, clear in-progress and re-raise. Move the "what gets recorded is what the check just read" comment to `async_prepare_install`.

**Blocked by:** 01

- [ ] The entity's install path contains no refusal rule and makes a single call to the poller before starting the runner.
- [ ] No code reads `last_update_success` or `last_exception` to decide an Install.
- [ ] An offline Linked Device is refused without any `devices/get_config` request reaching the fake Device Builder.
- [ ] Every HA test in `test_pre_install_checks.py`, `test_linked_device.py`, `test_update.py`, `test_install.py`, `test_tag_install.py` and `test_ref_rewrite_install.py` passes unchanged. Messages and behaviour are the same.
- [ ] `ruff` is clean.
