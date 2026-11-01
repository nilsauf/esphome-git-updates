# ESPHome Git Updates: spec

Status: ready-for-human

A HACS custom integration (`esphome_git_updates`, "ESPHome Git Updates") that replaces `packages/marsrelay_update.yaml` + `scripts/marsrelay_build.py`. It offers one update entity per Tracked Config. The entity shows an update when the Available ESPHome Version or the Latest Commit of any Floating Ref differs from what is installed. Install runs through the Device Builder. Terms are defined in `GLOSSARY.md`; the reasoning behind the bigger decisions is in `docs/adr/0001`–`0006`.

## Setup

- **Config entry:** one per Tracked Config, chosen from the Device Builder's `devices/list` (ADR-0001).
- **Device Builder location:** either
  - "add-on (auto)": find the ESPHome add-on through the Supervisor `hassio` helpers, overridable slug, and use its ingress port on 127.0.0.1 with no auth. Fallback: the URL discovered by core's `esphome.dashboard`.
  - "manual": a URL plus optional username and password, using `auth/login` (ADR-0002).
- **Linked Device:** an optional device picker limited to the `esphome` and `mqtt` integrations, pre-selected by matching the YAML's `esphome: name:`. With no device picked, the integration creates its own device (ADR-0005).
- **Options flow:**
  - Clean Build toggle, default off
  - git poll interval, default 30 min
  - GitHub token, optional and used only for release notes
  - upload address override, default `OTA`

## Sources and versions

- **Git Sources** come from the raw YAML, read over `/ws` with `devices/get_config` (the Configuration and every file it includes): `substitutions` are applied, and `!include` and local `packages:` files are followed (ADR-0006). `secrets.yaml` is never read; a Git Source that depends on `!secret` is reported as untrackable. A refused include read raises the protocol-drift Repair issue.
- **Tracked refs:** only Floating Refs. Pinned Refs (tag or sha) and local or path sources are ignored.
- **Remote `packages:`** are tracked as Git Sources, but the integration does not recurse into them.
- **Latest Commit:** found with `git ls-remote` for every remote. For GitHub remotes the GitHub API is used as well, but only to enrich release notes.
- **Available ESPHome Version:** the Device Builder's `esphome_version`, read on connect and every 5 min.
- **Installed ESPHome Version:** parsed from the Linked Device's registry `sw_version`, which has one of two formats:
  - `"<ver> (config hash 0x…)"` or `"<ver> (<compile time>)"`
  - `"<project_ver> (ESPHome <ver>)"`

  It falls back to the value stored at the last successful Install. With no Linked Device, a new entry seeds it with the Available ESPHome Version; with a Linked Device it is never seeded, and stays unknown (no update shown, warning logged) until the device reports a parseable version or an Install succeeds (ADR-0003).
- **Installed Commits:** kept in the entry's `Store`. Each is the commit observed when Install was pressed, and is committed only on success (ADR-0003).
- **Seeding:** a Git Source with no Installed Commit is seeded with its Latest Commit at first discovery, so a new entry shows no Git Source update until the next push. There is no field to enter an initial commit.
- **Version display:** the version string is a composite of the ESPHome version and the short sha of each source. "Newer" means "differs", via a `version_is_newer` override.

## Install

1. **Check before compiling:** refuse with a clear error if the YAML has no `ota:` platform `esphome`/`web_server` (Repair issue), or if a tracked Git Source lacks `refresh: always`/`0s` (Repair issue, ADR-0004).
2. If Clean Build is on, run `firmware/clean` and follow it.
3. Run `firmware/install {configuration, port}`, then follow the compile job and then the upload job.
4. **Device offline:** the integration never does a Deferred Install.
   - **Before compiling:** with a Linked Device whose entities are all unavailable, refuse with "device offline" before starting any job.
   - **Deferred by the Device Builder:** cancel the deferred compile job, clear the queued update, and fail with "device offline".
   - **Failed upload:** the Device Builder only marks devices offline while a dashboard is open, so an offline device usually compiles and then fails at upload. Report that as a failed Install at stage "upload", worded as "device unreachable". No retry, nothing recorded.
   - **Race, accepted:** if the device comes back online before the queued update is cleared, the Device Builder may flash it unseen. Nothing is recorded; the update still shows and the next Install fixes it.
5. **Persistence:** active job ids are persisted. After an HA restart the integration re-attaches with `follow_job` and finishes normally.
6. **On success:** record the Installed ESPHome Version and the Installed Commits.

## Entity

- **`in_progress` / `update_percentage`:** `in_progress` is true while jobs run. `update_percentage` is one 0–100 bar over the whole Install. It is read by polling `firmware/get_job` for the active job about every 2 s, on the connection that follows the job. `follow_job` frames carry no progress, and `follow_jobs` is not used: it streams every job's output and drops frames when its queue is full.
  - **Fixed bands:** with Clean Build, clean is 0–5, compile 5–90 and upload 90–100. Without it, compile is 0–90 and upload 90–100. Each job's own 0–100 is scaled into its band.
  - **Never goes backwards:** while a job reports `null`, the bar holds its last value. When a job ends, the bar moves to the end of its band. Clean never reports progress, and a remote compile tops out around 99.
  - **Remote compiles report progress too:** the add-on parses the forwarded output lines with the same parser it uses for local jobs.
  - **Cosmetic only:** a failed or unexpected poll stops the bar from updating (logged at debug level) but never fails the Install.
  - **Re-attach:** a re-attached Install (step 5) shows the bar as well.
- **`release_summary`:** one line per changed Source, e.g. "ESPHome 2026.10.1 → 2026.10.2; marsrelay abc1234 → def5678 (3 commits)".
- **`RELEASE_NOTES`:**
  - GitHub sources: the compare-API commit list from installed to latest.
  - Other remotes: the sha change only.
  - Unknown installed commit: only the latest commit.
- **Notifications:** a persistent notification on failure, with stage, error and the last 30 log lines. On success, a notification only when there were warnings. No log file is written to `/config`; full output stays in the Device Builder.

## Constraints

- Requires Supervisor for "add-on (auto)" mode. The minimum HA version is the current stable at first release, because of the per-config-entry device registry.
- The Device Builder `/ws` API is unstable. The client is one module, checks `server_version`, and raises a Repair issue on unexpected frames (ADR-0002).
- The marsrelay example YAML has no `ota:` block. Such devices need one USB flash with `ota: - platform: esphome` first.

## Plan

1. **Spike:** turn `DeviceBuilder` into a standalone client module with a CLI (`list`, `version`, `get-config`, `install`). Run it against the real add-on and capture the ws frames. Optionally run it first against a local Docker Device Builder.
2. **Integration:** build it on that client.
3. **Tests:**
   - `pytest-homeassistant-custom-component`
   - a fake `/ws` server that replays the captured frames
   - unit tests for the YAML parser and version logic
4. **Packaging:** HACS (`hacs.json`, versioned manifest, release workflow). The user adds the GitHub origin.
5. **Migration:** on the real HA, set up the entry (Installed Commits are seeded from the Latest Commit, assuming the device already runs it), then delete the package, the script and the helpers by hand.
