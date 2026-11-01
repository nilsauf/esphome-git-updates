# 03: Install: compile + upload with notifications

**What to build:** Pressing Install on the update entity runs an Install through the Device Builder: `firmware/install` with the Tracked Config and the `OTA` port, then the compile job, then the upload job. The entity shows the Install as in progress while it runs. On success the Installed ESPHome Version is recorded and the update disappears. On failure the user gets a persistent notification with the stage, the error and the last 30 log lines. A successful Install that produced warnings also posts a notification. No log file is written to `/config`.

**Context from 01/02** (see the `## Comments` on 01):
- `firmware/follow_job` frames carry no progress. Progress (0–100 or `null`) is only available from `firmware/follow_jobs` (`job_progress` events) or by polling `firmware/get_job`. Pick one for `update_percentage`.
- Output lines carry ANSI colour codes as literal text (`\033[32m…`). Strip them before putting log lines in a notification.
- The client's `install()` already runs the stages in order and refuses Deferred Installs. Ticket 04 covers what the user sees for those.

**Blocked by:** 02 (Tracer bullet: manual-URL entry → update entity showing ESPHome version)

**Status:** closed

- [x] Install runs `firmware/install {configuration, port: OTA}` and follows the compile job, then the upload job.
- [x] `in_progress` is true while jobs run. `update_percentage` is set only if the spike (01) found progress in job frames.
- [x] On success the Installed ESPHome Version is recorded in the entry's store and the entity no longer offers an update.
- [x] On failure a persistent notification names the stage, the error and the last 30 log lines, and nothing is recorded.
- [x] On success with warnings a persistent notification lists them. On a clean success there is no notification.
- [x] Tests cover success, compile failure and upload failure against replayed frames.

## Comments

### 2026-11-01: agent

**What exists:**

- `TrackedConfigUpdate.async_install` in `update.py` runs the Install over its own connection. It uses `connect_to_device_builder(..., long_running=True)`: only connecting and logging in are timed, and the websocket heartbeat notices a Device Builder that goes away.
- `install_log.py` collects the output. It keeps the last 30 lines with ANSI codes stripped (both the ESC byte and the literal `\033`), the warnings, and the stage reached. It also writes the notification text.
- Tests in `tests/test_update.py` run `update.install` against the fake Device Builder, replaying the captured fixtures. They cover success, compile failure, upload failure, success with warnings, the add-on's real capture as a clean success, a stale failure notification being dismissed, and a connection lost mid-compile. The captures' configuration, port and Clean Build steps are rewritten to what the entity sends.

**Decisions:**

- **No `update_percentage`.** `follow_job` frames carry no progress (ticket 01), so the entity does not declare `PROGRESS`. Home Assistant's own `in_progress` handling around `async_install` covers "in progress while jobs run".
- **What counts as a warning:** an ESPHome log line at level `WARNING`. Two things are excluded:
  - Compiler `warning:` lines. Every ESP-IDF build prints dozens of them from upstream code; `addon_success.json` has them.
  - `WARNING Bundle contains secrets…`, which every remote build prints.

  Without those exclusions, every Install on the real add-on would post a notification. The list of routine warnings is `_ROUTINE_WARNINGS` in `install_log.py`.
- **A failed Install** posts the notification and also fails the action with `HomeAssistantError`, so automations and the UI see the failure. A lost connection or a protocol error notifies the same way, with the stage it had reached.
- **A clean success dismisses** an earlier failure notification for the entry (the same notification id), so a stale failure does not outlive the fix.
- **The recorded version** is the `esphome_version` from the Install connection's handshake, which is what was actually compiled with.
- **A Deferred Install** currently gets the generic failure notification ("device offline"). Ticket 04 owns how it should look.

**Open for later tickets:**

- ADR-0002 says Installs run as background tasks with persisted job ids. Here the Install runs inline in the service call, and nothing is persisted yet. Re-attach after a restart is ticket 04's.
- Stages are bare strings (`"clean"`, `"compile"`, `"upload"`, plus the entity's `"connect"`). Before a lost connection produces any output, the stage is assumed to be `"compile"`. That assumption breaks once ticket 08 adds Clean Build. Consider a `Stage` enum in `device_builder.py` then.
