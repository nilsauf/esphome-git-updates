# 15: Deepen the Install run into one module

**Status:** done (uncommitted, awaiting review)

**What to build:** Move everything about running an Install out of the update entity into a new HA-free module, `install.py`. Its interface is `run(plan)` / `reattach(active)` → an ending. The entity keeps only these jobs:
- the pre-install checks,
- the background task,
- `in_progress`/percentage state,
- showing the ending as a notification.

While doing it, fix protocol drift during an Install: it currently raises no Repair issue.

**Why** (architecture review, 2026-11-05): an Install is spread across `update.py:185-386`, `device_builder.py` (install/resume/_follow), `install_log.py`, `progress.py`, `connection.py` and the `ActiveInstall` store methods. The entity knows about:
- `ActiveJob`, `JobNotFound`, `InstallOutcome.DEFERRED`, `stage == "upload"`,
- three callbacks and a `_StartJobs` lambda type,
- a five-branch failure ladder.

Every ending can only be tested through the full HA harness, plus a transcript for each branch.

**Bug:** `UnexpectedFrame` and `UnsupportedServerVersion` raised during an Install fall into `CONNECTION_ERRORS` (`update.py:358`). They become a plain "Install failed" with no Repair issue, which contradicts ADR-0002. No test covers this.

## Decisions (grilling, 2026-11-05)

1. **The seam is HA-free.** `InstallRunner` (in `install.py`) is built once per entity with five dependencies:
   - the configuration name,
   - a `connect` factory (an async context manager that yields a `DeviceBuilderClient`),
   - Install bookkeeping (the store: set active, clear active, record success),
   - the `Reauthentication`,
   - the `ProtocolIssue`.

   Tests use `FakeDeviceBuilder` with an in-memory bookkeeping fake and no `hass`.
2. **Pre-install checks stay in the entity:** the offline refusal, the double-press guard and `async_check_before_install`. Making them return a value is ticket 17.
3. **Drift is fixed here.** `UnexpectedFrame` or `UnsupportedServerVersion` during an Install reports drift and fails the Install. Drift is reported through `ProtocolIssue` under the name `"install"`. Write the regression test first.
4. **A lost connection keeps today's behaviour:** the active Install is cleared and the Install fails. Re-attaching instead is ticket 16.
5. **Stages become a `StrEnum` in `device_builder.py`.** Its values equal today's strings (`clean`, `compile`, `upload`), so the stored `active_install` format does not change. `connect` is not a job stage. It lives in `install.py` as the stage before the first job.
6. **The client takes one observer.** `install()` and `resume_install()` take one `InstallObserver` protocol object instead of `on_output`/`on_jobs`/`on_progress`, and `InstallRunner` implements it. Job sequencing stays in the client (ADR-0002).
7. **The runner returns an `InstallEnding`.** It never raises for an expected ending. Only `CancelledError` propagates. The endings are:
   - `Installed(warnings)`
   - `Failed(stage, error)`: compile or upload failed, the device was unreachable, the connection was lost, authentication failed, or the protocol drifted
   - `DeviceOffline`: a Deferred Install, refused
   - `OutcomeUnknown`: a re-attached Install whose jobs were pruned. A fresh Install that hits `JobNotFound` stays `Failed`.

   Each ending carries a `headline` and a `message` (built with `InstallLog`). The entity dismisses the notification for an `Installed` with no warnings, and otherwise notifies with the headline and message. It raises `HomeAssistantError` for anything other than `Installed`, unless the Install was re-attached.
8. **Interface:**
   - `InstallRunner.run(plan, on_percentage)` and `InstallRunner.reattach(active, on_percentage)`.
   - `InstallPlan` = commits + upload address + Clean Build. The entity builds it from `Options` and `latest_commits()`.
   - `on_percentage: Callable[[int], None]` is the only callback that crosses the seam.
   - The protocol-level `InstallOutcome`/`InstallResult` in `device_builder.py` keep their names.
9. **Reauth and drift are called by the runner itself.** An `AuthenticationFailed` calls `Reauthentication.async_require`. They are passed in as dependencies, and the "Device Builder session" refactor (architecture review, candidate 2; no ticket yet) would later absorb both.
10. **Replace tests, don't layer them.** The new `tests/test_install.py` drives `InstallRunner` straight against `FakeDeviceBuilder`, covering every ending, the drift fix, re-attach, active-Install bookkeeping and progress. The tests for each ending move out of `test_update.py`. That file keeps:
    - one test per ending → notification,
    - re-attach at setup,
    - unload/cancel keeps the active Install,
    - `in_progress`/percentage state.

    The pre-install tests stay unchanged.
11. **When the `"install"` drift problem clears.** Any Install that reaches the Device Builder without drift clears it: `Installed`, `DeviceOffline`, or `Failed` at compile or upload. A poll succeeding does not clear it.

## Acceptance

- [x] Drift during an Install raises the protocol-drift Repair issue and fails the Install. The regression test was written first.
- [x] `install.py` holds `InstallRunner`, `InstallPlan` and `InstallEnding`, and imports nothing from `homeassistant`.
- [x] `update.py` no longer imports `ActiveJob`, `JobNotFound`, `InstallOutcome`, `InstallResult`, `JobsCallback`, `ProgressCallback`, `InstallLog` or `InstallProgress`.
- [x] Stage strings are a `StrEnum`, and the stored `active_install` format is unchanged.
- [x] The client's install methods take one `InstallObserver`.
- [x] `tests/test_install.py` covers every ending without `hass`. The tests that moved are gone from `test_update.py`.
- [x] The full suite passes, and so do ruff and mypy as configured in `pyproject.toml`.

## Comments

**2026-11-05, implementation notes (Claude):**
- Built test-first. The drift regression test (`test_install.py`) was the first test written, and it went red because the module did not exist yet. The entity-level wiring test is `test_a_device_builder_that_answers_an_install_unexpectedly_raises_a_repair_issue`.
- **Small deviations and additions:**
  - `CONNECT` lives in `install_log.py` next to `InstallLog`, which starts in that stage, not in `install.py` as decision 5 says: `install.py` imports `install_log.py`, so the other way round would be an import cycle.
  - `ProtocolIssue.report_drift(name, err)` replaces the free function `report_protocol_drift`. Its callers are both pollers and the runner.
  - `CONNECTION_ERRORS` moved into `device_builder.py`, so `install.py` needs no HA import. Every caller imports it from there.
  - `RuntimeData` now carries the `ProtocolIssue`.
  - The `queue_install` test helper moved into `fake_device_builder.py`.
  - A connection lost before any output fails at the Install's first stage, as the old entity did. `connect` is only used when the connection never opens.
- **Tests:**
  - `test_install.py` has 22 HA-free tests.
  - `test_update.py` lost 12 tests, now covered there, and gained the drift wiring test.
  - The suite has 235 tests (224 before).
  - mypy passes for both the integration and the spike's strict config.

**2026-11-05, review fixes (Claude):**
- New tests in `test_install.py`:
  - `test_a_clean_build_whose_clean_fails_ends_at_clean_and_records_nothing` replaces the entity test that was dropped.
  - `test_an_install_that_never_reaches_the_jobs_leaves_earlier_drift_in_place` covers decision 11: a lost connection and a rejected login leave the `"install"` problem in place.
- Vocabulary: `InstallRunner.resume` is now `reattach`, and the entity's `resumed=` is now `reattached=`, matching ADR-0002. The tests use the same wording. The client's protocol-level `resume_install` keeps its name.
- `Installed` has a `summary` ("succeeded"), so every ending has one.
- `InstallStage = Stage | Literal["connect"]` sits next to `CONNECT` in `install_log.py`. `Failed.stage`, `InstallLog.stage` and `InstallLog.failure_text` use it. The CLI's `_PrintOutput.output` takes a `Stage`.
- `ProtocolIssue` and `DriftIssue` take `name` instead of `poller`: the Install reports under a name too.
- `connection.py` no longer re-exports `CONNECTION_ERRORS`. `config_flow.py`, `coordinator.py` and `git_coordinator.py` import it from `device_builder.py`.
- The suite has 237 tests.
