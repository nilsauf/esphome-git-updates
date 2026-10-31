# 01: Device Builder client + CLI spike

**What to build:** Turn the `DeviceBuilder` class from the original build script into a standalone Device Builder client module with a CLI (`list`, `version`, `get-config`, `install`). The client has to work against the real ESPHome add-on before any integration code is written (ADR-0002), because the original script was never run. Run it against the real add-on, optionally against a local Docker Device Builder first, and capture the `/ws` frames as test fixtures.

The spike must answer and record (in a `## Comments` note on this ticket):
- Do compile/upload job frames carry progress? This decides whether `update_percentage` is set.
- What does a `deferred` install result look like, and does `firmware/clear_queued_update` clear it?
- Does `firmware/follow_job` re-attach to a running job after a disconnect?
- Which `server_version` values the client accepts.

**Blocked by:** None (can start immediately)

**Status:** ready-for-human

- [x] The client is a single module that holds all knowledge of the `/ws` protocol.
- [x] It connects either through the add-on's ingress port on 127.0.0.1 with no auth, or through a manual URL with optional username and password via `auth/login`.
- [x] It checks `server_version` on connect and raises a typed error on an unexpected version or frame.
- [x] `list` prints the Device Builder's configurations, `version` prints the Available ESPHome Version, and `get-config` prints a configuration's raw YAML.
- [x] `install` runs an optional Clean Build (`firmware/clean`), then `firmware/install {configuration, port}`, follows the compile and upload jobs, and reports a deferred result distinctly.
- [ ] Frames from a successful Install, a failed compile and a deferred install are captured from the real add-on and stored as fixtures.
- [x] A fake `/ws` server replays the fixtures, and the client's tests pass against it.
- [x] The answers to the questions above are recorded on this ticket.

## Comments

### 2026-10-31: agent spike (local Device Builder, not yet the add-on)

**What exists:**

- Client: `custom_components/esphome_git_updates/device_builder.py`. It has no HA imports.
- CLI: `scripts/device_builder_cli.py`, with `list`, `version`, `get-config` and `install [--clean] [--port]`, plus `--record FILE` to capture a transcript.
- Fake server: `tests/fake_device_builder.py`.
- Tests: `tests/`, run with `.venv/bin/pytest`.

**Where the facts come from:** I read the protocol from the `esphome-device-builder` 1.26.0 wheel and checked it against a real 1.26.0 / ESPHome 2026.10.1 running locally from pip. A `host`-platform config gave a real successful OTA Install. The captures are in `tests/fixtures/` and each one says what it recorded in its `source` field.

**Still open (the unticked box):** a capture from the real add-on. See "For the human" below.

**Do compile/upload job frames carry progress?**

`firmware/follow_job` does not. Its frames are only `event: "output"` (one line of text per frame), then `event: "result"` with `{status, exit_code, error, queued_update_armed}`, then a closing `{"result": null}`.

The Device Builder does compute progress, though:
- `FirmwareJob.progress` is 0–100 or `null`. It is parsed from ninja `[N/M]`, PlatformIO and esptool output.
- `firmware/get_job` returns it.
- `firmware/follow_jobs`, which streams every job, emits `job_progress {job_id, progress}`.

So `update_percentage` is possible, but only through `follow_jobs` running alongside, or by polling `get_job`. The client does not do either yet; that decision belongs to ticket 03.

**What does a Deferred Install look like, and does `firmware/clear_queued_update` clear it?**

There are two shapes, and `clear_queued_update` alone is not enough for the first one.

1. **Deferred at enqueue.** This happens when the device is OFFLINE and `port` is `OTA`. `firmware/install` returns a single `compile` job with `is_deferred_install: true` and `port: ""`, and no upload job exists.
   - At that moment nothing is armed yet, so `clear_queued_update` does nothing.
   - The compile keeps running. When it completes, its result has `queued_update_armed: true` and the device's `queued_update` becomes `true` (captured in `deferred_compile_armed_then_cleared.json`).
   - The way to refuse it is `firmware/cancel {job_id}` (captured: following the job afterwards shows `cancelled`, not armed). The client then calls `clear_queued_update` anyway, because a cached compile can finish before the cancel arrives. In that case the cancel is refused with `invalid_args` "Cannot cancel a completed job", and the flag is already armed.
2. **Deferred mid-chain.**
   - Either the compile completes while the device is OFFLINE: the compile result has `queued_update_armed: true`, and the held upload is cancelled with "device is offline; update queued for when it reconnects".
   - Or an OTA upload fails while the device is OFFLINE: the upload result is `failed` with `queued_update_armed: true`.
   - Neither mid-chain shape is captured; both come from the source (`lifecycle._defer_install_if_target_offline`). The flag they arm is the same `queued_update` flag, and `clear_queued_update` was seen to clear it (`true` → `false`) after a deferred compile completed, in `deferred_compile_armed_then_cleared.json`.
   - Race for ticket 03: the flag is armed when the compile completes. If the device comes back ONLINE before the client clears it, the Device Builder's wake dispatch can start an upload that the client does not see.

`InstallOutcome.DEFERRED` covers both shapes, and `install()` refuses both.

**Spec step 4 needs amending:** "call `clear_queued_update`" has to become "cancel the deferred compile job, and clear the queued update".

A caveat that matters for the integration: only the state monitors set OFFLINE, and the ping sweep only runs while some client is subscribed through `subscribe_events`, for example an open dashboard tab. Locally, with no subscriber, the devices stayed `unknown` and nothing deferred. With a subscriber they went `offline` within about 20 s. So with the dashboard closed, an offline device usually gives a plain failed upload rather than a deferral, and the integration must handle both.

**Does `firmware/follow_job` re-attach after a disconnect?**

Yes (captured in `follow_job_reattach.json`). I killed the CLI mid-compile, and the job kept running on the server. `follow_job` from a new connection replayed the 56 lines of history, then streamed live output, then sent the result.

For a job that has already finished, it replays the history and the result, then ends. From the source: an unknown or pruned job id gives `internal_error` "Command failed: firmware/follow_job".

Because `firmware/install` replaces any active jobs for the same configuration, the stages have to run one after another. `install()` does that.

**Which `server_version` values does the client accept?**

`>= 1.25.0` and `< 2.0.0`, including pre-releases such as `1.26.1b5` (`MIN_SERVER_VERSION`, `MAX_SERVER_MAJOR`). 1.26.0 is the version I read and ran. 1.25.0 was checked by diffing its wheel against 1.26.0's: the `/ws` code is identical, and only the MQTT monitor differs.

The Device Builder has been 1.x since 2026-06-15 and ships a new minor almost weekly, so pinning a minor would break all the time. The client therefore treats a new major as the breaking signal. If the add-on reports something below 1.25.0, check it and lower the floor.

**Other protocol facts:**

- Output lines carry ANSI colours as literal text (`\033[32m…\033[0m`), not as ESC bytes. Strip them before showing a log tail.
- The ingress site is a `trusted_site`, so it answers `requires_auth: false` (from the source; still needs confirming on the add-on).
- A manual URL with a password answers `requires_auth: true`, and `auth/login {username, password}` returns `{token, expires_at}`. A wrong password gives `not_authenticated` "Invalid credentials", and repeated failures give `rate_limited`.
- `devices/get_config` on an unknown file gives `not_found`.
- The `esphome_version` in the handshake is the Available ESPHome Version. `config/version` returns the same value.

**Open points from the code review:**

- ADR-0006 says raw YAML is read from `/config/esphome`, but this client reads it over `/ws` with `devices/get_config`. Decide which one the integration uses, and amend ADR-0006 if it is `/ws`.
- `Configuration`, meaning any YAML file the Device Builder lists, is not a glossary term, and it is wider than a Tracked Config. Note it for `/domain-modeling`.
- Spec step 4 is not amended yet. It should become "cancel the deferred compile job, and clear the queued update".

**For the human: capture from the real add-on.**

This captures a successful Install, a failed compile and a Deferred Install. Run it from the HA host or the `homeassistant` container, which already has `aiohttp` and host networking:

```sh
PORT=$(ha addons info 5c53de3b_esphome --raw-json | jq .data.ingress_port)
python3 scripts/device_builder_cli.py --addon-port $PORT version   # note server_version on stderr
python3 scripts/device_builder_cli.py --addon-port $PORT --record addon_success.json install b2500.yaml --clean
# a config with a deliberate compile error:
python3 scripts/device_builder_cli.py --addon-port $PORT --record addon_compile_failed.json install <broken>.yaml
# an unplugged device, with the ESPHome dashboard open in a browser so it shows OFFLINE:
python3 scripts/device_builder_cli.py --addon-port $PORT --record addon_deferred.json install <offline>.yaml
```

Copy the JSON files into `tests/fixtures/`, add a `source` line, and add a replay test for each, following the `test_captured_*` tests. If the add-on's frames differ from the local ones, record the difference here.

### 2026-10-31: captures from the real add-on

Captured by the human through the add-on's ingress port. Stored as `tests/fixtures/addon_success.json` and `tests/fixtures/addon_deferred.json`, each with a replay test. `/home/<user>/` paths from the remote receiver's output are scrubbed to `/remote/`.

**Still missing:** a failed compile from the add-on. The local capture `install_compile_failed.json` stands in for it until one exists, so the capture box stays unticked.

**What the add-on showed:**

- **Version:** `server_version` 1.25.0, ESPHome 2026.10.1. The client's floor was lowered to 1.25.0 in the amended spike commit, after diffing the 1.25.0 and 1.26.0 wheels.
- **Ingress needs no auth (confirmed):** the handshake on the ingress port says `requires_auth: false`, `ha_addon: true`, `ha_ingress: false`. `ha_ingress` is only `true` behind the Supervisor proxy, not on the port itself.
- **Successful Install:** the frames match the local capture exactly. `firmware/clean` and `follow_job`, then `firmware/install`, then `get_jobs`, which finds the upload through `depends_on`, then `follow_job` for the compile and for the upload. All three stages ended `completed`, exit 0.
- **Deferred Install:** the same shape as locally. `is_deferred_install: true`, `port: ""`, no upload job. Cancel and clear worked, and following the job shows `cancelled`, `queued_update_armed: false`.
- **Remote builds:** this add-on is paired with remote build servers (`evo`, `esphome_cloud.lan`). The compile jobs have `source: "remote"`, while clean and upload ran locally (`source: "local"`). The client needs no change for this, because jobs are followed the same way either way.

**Open for ticket 03:** with a remote compile, does `firmware/clean` (which wipes the add-on's local build directory) still give a clean compile? Each remote build runs in a fresh `.remote_builds/<id>/` bundle directory, which suggests yes, but that is unverified.
