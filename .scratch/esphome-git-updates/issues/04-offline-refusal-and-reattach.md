# 04: Device offline refusal + re-attach after restart

**What to build:** Two robustness behaviours for Install.
- **Device offline:** when the device is offline, the Device Builder would queue a Deferred Install. The integration never does one: it clears the queued update and fails the Install with "device offline".
- **HA restart:** if Home Assistant restarts mid-Install, the integration re-attaches to the still-running Device Builder jobs and finishes the Install normally, including recording the result. Jobs persist on the Device Builder across disconnects (ADR-0002).

**Context from 01** (see the `## Comments` on 01):
- The client's `install()` already refuses both deferral shapes (deferred at enqueue, and deferred mid-chain), by cancelling the deferred compile and clearing the queued update. This ticket shows the user "device offline" and covers the cases below.
- The add-on only marks devices OFFLINE while some client is subscribed (an open dashboard tab). With no subscriber, an offline device usually gives a plain failed upload instead of a deferral. The user should see a clear "device offline / unreachable" failure in that case too, not just a raw upload error.
- `follow_job` on an unknown or pruned job id gives `internal_error` "Command failed: firmware/follow_job". Treat that as a stale job id.

**Blocked by:** 03 (Install: compile + upload with notifications)

**Status:** closed

- [x] A `deferred` result calls `firmware/clear_queued_update` and fails the Install with a "device offline" notification.
- [x] A failed upload to an unreachable device is reported as "device offline / unreachable", not only as a raw upload error.
- [x] A failed upload is not retried and records nothing. If the device comes back online before the queued update is cleared and gets flashed unseen, that is accepted: nothing is recorded, and the next Install fixes it.
- [x] Active job ids are persisted in the entry's store while an Install runs, and cleared when it ends.
- [x] On setup with persisted job ids, the integration re-attaches with `firmware/follow_job`, shows `in_progress`, and finishes with the normal success or failure handling.
- [x] If a persisted job no longer exists on the Device Builder, the stale state is cleared and the user is told the Install's outcome is unknown.
- [x] Tests cover the deferred path and re-attachment after a simulated restart.

## Comments

### 2026-11-01: agent

**What exists:**

- **Client (`device_builder.py`):**
  - `install()` takes `on_jobs`, which is awaited with the jobs still to follow (`ActiveJob(job_id, stage)`) before each job is followed. Finished jobs therefore drop out of the store.
  - `resume_install(configuration, jobs)` follows those jobs again. If the Install had only got as far as its Clean Build, it then goes on to compile and upload.
  - `follow_job` turns `internal_error` into `JobNotFound`.
- **Store (`coordinator.py`):** `active_install` holds `{jobs, commits}`. The commits are the ones seen when Install was pressed, so a re-attached success records the same thing a normal one would. The store entry is cleared when the Install ends without success, and dropped when a success is recorded.
- **Entity (`update.py`):**
  - The Install runs as a background task of the entry, and `async_install` waits on it.
  - Unloading the entry, or stopping HA, cancels the task but leaves the stored jobs.
  - On setup, stored jobs are re-attached with `in_progress` shown, and finish with the normal success and failure handling.
  - The entity now declares `PROGRESS`, because a re-attached Install runs outside `async_install`. It has no `update_percentage` yet; that is ticket 14's.
- **Notifications:**
  - A Deferred Install gives "Install failed: device offline".
  - A failed upload whose output shows it couldn't connect gives "device offline / unreachable", with the log tail.
  - A re-attached job the Device Builder no longer knows gives "Install outcome unknown".
- **Fake Device Builder:** it now keeps `jobs` across connections, the way the real one does. A job with no result is still running.

**Decisions:**

- **"outcome unknown" only for a re-attached Install.** If a fresh Install's job vanishes, that is reported as a normal failure.
- **Unreachable detection** works on the upload's output: `connecting to … failed`, `connection refused`, `no route to host`, `host/network is unreachable`, `timed out`, and `error resolving ip address`. Other upload failures keep their raw error.
- **HA stop:** HA cancels background tasks before it fires STOP/CLOSE and closes the aiohttp session (`core.async_stop`). So a stop cancels the Install task, and does not first surface as `ConnectionLost`, which would clear the stored jobs.

**Open for later tickets:**

- A connection lost mid-Install, or a Device Builder that cannot be reached when re-attaching, still clears the stored jobs and reports a failure (ticket 03's behaviour). Keeping the jobs for a later re-attach would be more faithful to ADR-0002, but nothing would trigger that re-attach until the next reload.
- A restart between `firmware/install` and the first save of the job ids (one `get_jobs` round-trip) leaves nothing to re-attach. Nothing is recorded, and nothing tells the user either.
- Ticket 14: `PROGRESS` is already declared. Plug progress polling into `DeviceBuilderClient._follow`.
- Stage is still a bare string, now with more branches on it (`"clean"`, `"upload"`). The `Stage` enum suggested on 03 is worth doing with ticket 08.
