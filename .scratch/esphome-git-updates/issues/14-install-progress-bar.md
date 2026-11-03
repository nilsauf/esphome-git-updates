# 14: Install progress bar (`update_percentage`)

**What to build:** While an Install runs, the update entity shows one 0–100 progress bar over the whole Install: Clean Build if it is on, then compile, then upload. The bar also works for an Install re-attached after an HA restart. Progress is cosmetic: losing it never fails an Install.

**Context** (see the `## Comments` on 01 and 03, and the spec's Entity section):
- `firmware/follow_job` frames carry no progress. The Device Builder keeps `FirmwareJob.progress` (0–100 or `null`) for each job, and `firmware/get_job` returns it.
- **Remote compiles report progress (checked against the 1.25.0/1.26.0 wheel source):**
  - The add-on parses the remote build server's forwarded output lines with the same parser as local jobs (`controllers/firmware/remote_runner.py` `_on_output` → `helpers.py` `_ingest_output_line`).
  - Replaying `tests/fixtures/addon_success.json` through those patterns gives these results:

    | Job | Progress |
    |---|---|
    | Remote compile | 0→99 (ninja `[N/M]`; it never reaches 100) |
    | Local OTA upload | 1→100 (`Uploading: … NN%`) |
    | Local clean | stays `null` |
  - Within a job, progress only goes up. The add-on resets it to `null` only when the job is requeued, for example when the Device Builder restarts or a compile moves to another build server.
  - No `job_progress` frame was ever captured on the wire, because the captures only used `follow_job`.
- **Why polling and not `firmware/follow_jobs`:** `follow_jobs` has no job filter. It streams every job's output a second time and silently drops output and progress frames when its 4000-frame queue is full.
- **The client cannot poll alongside `follow_job` today.** In `device_builder.py`, `_frames()` raises `UnexpectedFrame` on any frame for another `message_id`, and `_command()` holds `_lock` for the whole exchange. Polling on the same connection needs the client to route incoming frames by `message_id`, so that a `get_job` request can run while a `follow_job` stream is open. That routing is still protocol knowledge, so it stays inside the client module (ADR-0002).

**Decisions (grilling, 2026-11-01):**
- Poll `firmware/get_job` for the active job about every 2 s, on the connection that runs `follow_job`.
- **Fixed bands:**
  - with Clean Build: clean 0–5, compile 5–90, upload 90–100
  - without it: compile 0–90, upload 90–100

  The band edges are constants in one place, not options.
- **The bar never goes backwards:**
  - While a job reports `null`, the bar holds its last value.
  - A completed job moves the bar to the end of its band. The bar is 0 until the first progress arrives.
  - If the Device Builder resets a requeued job's progress, the bar holds its last value until the job passes it again.
- A failed poll or a poll with an unexpected shape stops the bar from updating and is logged at debug level. It never fails the Install and raises no Repair issue.

**Blocked by:** 04 (Device offline refusal + re-attach after restart). Ticket 04 moves Install into a background task and adds re-attach. This ticket plugs into that final job-following loop.

**Status:** closed

- [x] The entity declares `UpdateEntityFeature.PROGRESS` and sets `update_percentage` while an Install runs. Once the Install ends, it is `None`.
- [x] The client can run `firmware/get_job` on a connection while a `follow_job` stream is open on it. Frames are routed by `message_id`, and a frame for an unknown id is still an `UnexpectedFrame`.
- [x] Progress for the active job is polled about every 2 s and mapped onto the fixed bands. With Clean Build: 0–5, 5–90, 90–100. Without it: 0–90, 90–100.
- [x] The bar never decreases. `null` holds the last value, and a completed job moves the bar to the end of its band.
- [x] A re-attached Install (ticket 04) shows the bar from the job's current progress.
- [x] A failed or malformed poll is logged at debug level and does not affect the Install's outcome, notifications or recorded state.
- [x] Tests against the fake Device Builder cover: bands with and without Clean Build; a remote compile whose progress tops out at 99, followed by an upload to 100; a `null` clean; a poll error mid-compile with the Install still succeeding; and re-attach showing progress. The fake server answers `get_job` with progress taken from the replayed output, or from a scripted sequence.

**Implementation notes:**

- `device_builder.py` routes frames by `message_id` on one reader task, so `firmware/get_job` can run while `follow_job` is open. Late frames are discarded only for requests the client abandoned: a command's id is dropped when its single late answer arrives, a `follow_job` id stays suppressed for the connection's life because a cancelled stream can keep sending.
- Bands and the monotonic mapping live in `progress.py` (`InstallProgress`); the entity maps the result onto `_attr_update_percentage`. `ActiveInstall` stores `clean_build` so a re-attached Install uses the right bands.
- The first poll runs inline before the follow's output is replayed, so a re-attach shows progress immediately.
- Progress is cosmetic by design: `_poll_progress` and `_notify_progress` catch every exception and log at debug. ADR-0002 records this as its one deliberate exception to "unexpected frames raise a Repair issue".
- Only a job that `succeeded` sends the terminal `finished` update, so a failed compile leaves the bar below its band end instead of filling it.
