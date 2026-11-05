# 18: One Device Builder session per config entry

**Status:** done

**What to build:** Add a `DeviceBuilderSession` to `connection.py`, created once per config entry. It owns these jobs for the entry's three callers:
- connecting,
- the reauth gate,
- the reaction to a rejected login,
- protocol-drift Repair issue reporting.

The three callers are:
- the ESPHome version poller,
- the Git Sources poller,
- the Install run.

Each caller then catches a single `DeviceBuilderUnavailable` in place of its error ladder.

**Why** (architecture review, 2026-11-05, candidate 2): after ticket 15, the same ladder is still copied into each caller:
- the ESPHome version poller: `coordinator.py`, `EsphomeVersionCoordinator._async_update_data`,
- the Git Sources poller: `git_coordinator.py`, `_async_update_data` (gate) and `_discover` (ladder),
- the Install run: `install.py`, `InstallRunner._follow`.

The ladder is:
- `if reauth.required: raise UpdateFailed`,
- `AuthenticationFailed` → `reauth.async_require`,
- `UnsupportedServerVersion`/`UnexpectedFrame` → `issue.report_drift(name, err)`,
- `CONNECTION_ERRORS`, which must be caught last (a rule written down only in a comment),
- `issue.clear(name)` on success.

The Install run has **no reauth gate**. An Install pressed while reauth is pending is refused today only because the pre-install poll fails first.

`connection.py` is shallow: `connect_to_device_builder` only resolves the location and calls `connect`. `InstallRunner` takes three dependencies, `Connect`, `Reauth` and `DriftIssue` (`install.py`), that together already describe a session.

## Decisions (grilling, 2026-11-05)

1. **Scope: only callers scoped to a config entry.** Those are the two pollers and `InstallRunner`.
   - The config flow keeps its own short `except` → form-error-key mapping (`config_flow.py`: `async_step_reauth_confirm`, `_async_step_location`).
   - It runs before an entry exists, or tests credentials that are not yet saved, and it touches neither reauth nor the Repair issue.
   - It keeps calling `connect_to_device_builder` directly. That function can stay, as the session's default connect factory.
2. **The interface is a context manager.** `async with session.connect(name, long_running=False) as client:`
   - `name` is the caller's name for the Repair issue: `"version"`, `_POLLER = "git"` and `DRIFT_NAME = "install"` today.
   - `long_running` passes through to `connect_to_device_builder` (the Install run uses `True`).
   - Every failure the session owns comes out as one `DeviceBuilderUnavailable(message)`.
   - Messages keep today's wording ("cannot reach the Device Builder: …", "the Device Builder refused the login: …", "waiting for new Device Builder credentials", and the drift `str(err)`), so the pollers become `except DeviceBuilderUnavailable as err: raise UpdateFailed(str(err)) from err`.
   - The session chains the original error with `raise … from err`.
   - A callback form (`session.run(name, fn)`) was rejected: the Install run holds the client for 10–45 minutes and reads `client.esphome_version` afterwards.
3. **Which errors the session handles, and which pass through:**
   - **Handled, raised as `DeviceBuilderUnavailable`:** `AuthenticationFailed`, `UnsupportedServerVersion`, `UnexpectedFrame`, `ConnectionLost`, `DeviceBuilderNotFound`, `aiohttp.ClientError`, `OSError`, `TimeoutError`.
   - **Passed through untouched:** everything else, notably `CommandFailed` (the Git Sources poller's "cannot read config") and `JobNotFound` (the Install run's `OutcomeUnknown` on re-attach). `CancelledError` always propagates.
   - **Consequences:**
     - `CONNECTION_ERRORS` is no longer something callers use. It becomes private to the session, unless the config flow still needs it.
     - `InstallRunner` must now catch `CommandFailed` explicitly and keep today's ending for it: `Failed` at `log.stage`, with no drift clear. Today it lands in `CONNECTION_ERRORS`.
4. **The reauth gate lives in the session and applies to every caller,** the Install run included. While reauth is required, `connect` raises `DeviceBuilderUnavailable("waiting for new Device Builder credentials")` without trying to log in, so a login never triggers `rate_limited` (ticket 13). The Install run turns that into `Failed` at the connect stage. The entity is unchanged.
5. **The session owns `Reauthentication` and `ProtocolIssue`:**
   - `RuntimeData.reauth` and `RuntimeData.issue` are replaced by `RuntimeData.session`, built in `async_setup_entry` before the coordinators.
   - `ProtocolIssue` (and `protocol_issue_id`) move out of `coordinator.py` into `connection.py`. `async_remove_entry` imports `protocol_issue_id` from there.
   - `Reauthentication` stays in `reauth.py` as a part of the session; the add-on-entry rule (log once, never latch) is unchanged.
   - `InstallRunner`'s `connect`, `reauth` and `issue` parameters collapse into one session parameter. Define a small `Protocol` for it in `install.py` so the module stays HA-free (ticket 15 acceptance).
   - **The Git Sources poller's `include_refused` report** (ADR-0006) is not drift. It reaches the session as an error raised inside the `with` block, not as an extra method:
     - add an error such as `ProtocolProblem(translation_key, placeholders)` in `connection.py`,
     - the poller raises it for `_ReadRefused`,
     - the session reports it under the caller's name and re-raises it as `DeviceBuilderUnavailable`.

     The session's interface stays one `connect(name)` method.
6. **When a caller's drift problem clears.** Drift is a reply the integration doesn't understand (ADR-0002). Each caller reports it under its own name, and the Repair issue stays up until every name has cleared.
   - The session clears the caller's name **only when the `with` block exits without an exception**. That is today's behaviour for all three callers.
   - A pass-through error such as `CommandFailed` or `JobNotFound` clears nothing.
   - A clean handshake alone does not clear it, because `UnexpectedFrame` can arrive mid-command.
   - Ticket 15 decision 11 still holds: an Install that returns a result (`Installed`, `DeviceOffline`, `Failed` at compile or upload) exits the block cleanly and clears `"install"`.
7. **`DeviceBuilderUnavailable` carries only a message.** No caller branches on the reason, so none is added. Add a reason when a caller needs one.
   - Ticket 16 (re-attach after connection loss) may be that caller: it has to tell `ConnectionLost` apart.
   - Until then it can read `err.__cause__`.
8. **No `GLOSSARY.md` entry.** "Device Builder session" is a name for the module, not a domain term.
9. **Replace tests, don't layer them:**
   - **New `tests/test_connection.py`** drives `DeviceBuilderSession` directly. It uses an injected fake connect factory and the real `Reauthentication`/`ProtocolIssue` on the `hass` fixture. It covers:
     - the gate blocks without connecting,
     - a rejected login starts reauth once and latches for a manual entry,
     - an add-on entry logs once and does not latch,
     - drift is reported per name and cleared on a clean exit only,
     - another name's problem stays visible when one name clears,
     - `ProtocolProblem` is reported,
     - each handled error becomes `DeviceBuilderUnavailable` with today's message,
     - `CommandFailed`/`JobNotFound`/`CancelledError` pass through.
   - **Existing tests shrink:**
     - in `test_reauth.py`, `test_install.py`, `test_git_updates.py` and `test_update.py`, the per-caller cases for the reauth gate, rejected login and drift shrink to one "unavailable → `UpdateFailed` / `Failed`" check per caller,
     - `test_install.py` swaps its `Reauth`/`DriftIssue` fakes for a session fake,
     - one end-to-end reauth test through the HA harness stays as a smoke test,
     - the entity-level drift wiring test from ticket 15 stays.

## Acceptance

- [x] `connection.py` holds `DeviceBuilderSession`, `DeviceBuilderUnavailable`, `ProtocolProblem` and `ProtocolIssue`.
- [x] `coordinator.py`, `git_coordinator.py` and `install.py` no longer import `AuthenticationFailed`, `UnsupportedServerVersion`, `UnexpectedFrame`, `CONNECTION_ERRORS` or `Reauthentication`, and none of them checks `reauth.required`.
- [x] Pressing Install while reauth is pending fails at once, with no login attempt. A test was written first.
- [x] `RuntimeData` has `session` and no longer has `reauth` or `issue`.
- [x] `install.py` still imports nothing from `homeassistant`.
- [x] `tests/test_connection.py` covers the session's behaviour, and the per-caller duplicates are gone.
- [x] The full suite passes, and so do ruff and mypy as configured in `pyproject.toml`.

## Comments

**2026-11-05, implementation notes (Claude):**
- Built test-first. The acceptance test `test_connection.py::test_an_install_pressed_while_reauth_is_pending_fails_at_connect_without_logging_in` drives `InstallRunner` with the real session. It went red because the session did not exist yet and `InstallRunner` had no gate.
- **Deviations:**
  - `DeviceBuilderUnavailable` is *defined* in `device_builder.py` and re-exported from `connection.py`. `install.py` must catch it, and importing `connection.py` would pull in Home Assistant. It is not a `DeviceBuilderError`, because the client never raises it.
  - `ProtocolProblem(message, translation_key, placeholders)` takes a message as well, so the Git Sources poller keeps "the Device Builder refused to read …".
  - The Install's failure text now carries the session's wording, e.g. "cannot reach the Device Builder: connection refused" instead of the bare "connection refused".
  - `ConnectFactory` is a small `Protocol` whose `long_running` is keyword-only.
  - `CONNECTION_ERRORS` stays in `device_builder.py`, for the config flow and the CLI. The session has its own private `_UNREACHABLE`, which excludes `CommandFailed` and `JobNotFound`.
- **Tests:**
  - `test_install.py` uses a `FakeSession`. The fake copies the session's error sorting, because the real session needs `hass`.
  - The version poller's parametrized drift test, `test_reauth.py`'s Install case and the Install drift/reauth cases in `test_install.py` were removed; `test_connection.py` covers them.
  - New per-caller checks: `test_git_updates.py::test_a_device_builder_that_cannot_be_reached_fails_the_git_poll_without_a_repair_issue`, and `test_install.py::test_a_refused_install_command_fails_at_its_stage_and_clears_no_drift` for `CommandFailed`.
- **Review follow-ups not done (judgement calls):**
  - `(translation_key, placeholders)` still travels as a pair through `ProtocolIssue`.
  - There are two "unreachable" error lists, `CONNECTION_ERRORS` and `_UNREACHABLE`.
- Ruff has no config in `pyproject.toml`. Run with its defaults, it finds no new errors; the 54 it reports are older.
