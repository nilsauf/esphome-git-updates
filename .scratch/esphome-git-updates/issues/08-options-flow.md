# 08: Options flow: Clean Build, poll interval, upload address

**What to build:** The entry's options let the user change how Installs and polling behave:
- a **Clean Build** toggle, default off. When on, Install first runs `firmware/clean` and follows it before compiling. This wipes the build directory but does not re-fetch Git Sources (ADR-0004).
- the **git poll interval**, default 30 min.
- the **upload address override**, default `OTA`, passed as the install `port`.

**Context from 01** (see the `## Comments` on 01): the real add-on compiles on remote build servers (`source: "remote"`), while clean and upload run locally. It is unverified whether `firmware/clean` gives a clean compile in that setup; each remote build appears to run in a fresh bundle directory. Check this, and record the answer on this ticket.

**Blocked by:** 05 (Git Sources: discover Floating Refs and track Latest Commit)

**Status:** closed

- [x] The options flow shows the three options with their defaults.
- [x] With Clean Build on, Install runs and follows `firmware/clean` before `firmware/install`; a clean failure is reported with stage "clean".
- [x] Changing the poll interval takes effect without restarting Home Assistant.
- [x] The upload address override is used as the install `port`.
- [x] Whether Clean Build is effective with remote builds is checked and recorded on this ticket.
- [x] Tests cover each option.

## Comments

### 2026-11-01: agent: Clean Build with remote builds

**Answer: Clean Build was not effective with remote builds, and is fixed now.**

From the Device Builder 1.26.0 source (`esphome_device_builder/controllers/firmware/clean.py`, `factories.py`, `helpers/remote_build_layout.py`):

- `firmware/clean` queues the local clean job, and also one remote clean job for each paired remote build server that is APPROVED and connected right now. It returns only the local job.
- A remote build server does not build in a fresh directory. The bundle is re-unpacked each time, but `ESPHOME_DATA_DIR` is one directory per offloading dashboard (`.remote_builds/<id>/.esphome`). It keeps `build/<device>` and the toolchain warm between builds, on purpose. So without a clean, a remote compile is incremental. A remote clean job runs `esphome clean` there and wipes `build/<device>`.
- `firmware/install` supersedes: it cancels every job still active for the configuration.

The real add-on capture `addon_success.json` shows the result. Its `get_jobs` answer lists two remote clean jobs (`evo`, `esphome_cloud.lan`), both `cancelled`, completed the moment `firmware/install` was sent. The client had followed only the local clean (about 2.7 s), while a remote clean takes about 8–14 s (the 10-29 jobs in the same answer). The remote compile on `evo` was therefore incremental.

**Fix:** after `firmware/clean`, the client asks `firmware/get_jobs` for the clean jobs created no earlier than the local one. Those are the ones this clean fanned out; the local clean's own supersede already cancelled any older active job. The client follows them after the local one, and only then sends `firmware/install` (`DeviceBuilderClient._clean_jobs`). A remote clean that fails ends the Install with stage "clean", even one that had already failed before `get_jobs` answered. They are stored like any other job, so an Install re-attached mid-clean follows them too.

**Caveats:**
- A remote build server that drops out during its clean fails the whole Install at "clean", even if the compile would have gone to another server. The Device Builder does not re-route a remote clean. This is deliberate: the remote compile cannot be known to be clean, so the Install stops.
- A remote build server that is disconnected or still pending when the clean is sent gets no clean, and nothing reports it. If the compile then goes to that server, it is incremental.
- Clean does not wipe PlatformIO or toolchain caches, on either side. Only `firmware/reset_build_env` does that.
- I could not trace how clean behaves when a remote server builds with a different ESPHome version (`env_provisioner.py`).

**For the human:** the remote clean follow is tested only against synthesized frames (`with_clean_jobs` in `tests/fake_device_builder.py`), built from the job shapes in the add-on's `get_jobs` answer. Confirm it once on the add-on: `python3 scripts/device_builder_cli.py --addon-port $PORT --record addon_clean_remote.json install b2500.yaml --clean`. The recording should show `get_jobs`, then a `follow_job` for each remote clean ending `completed`, before `firmware/install`.

**Options as built:** `clean_build` (default off), `git_poll_interval` (whole minutes, default 30) and `upload_address` (default `OTA`; a blank one is saved as `OTA`), in `options.py`. An options change does not reload the entry, because a reload would interrupt a running Install. The git poller takes the new interval and polls once right away. Clean Build and the upload address are read when Install is pressed. The address is stored with the active Install, so an Install re-attached after a restart still uploads where it was started for, even if the option changed meanwhile.
