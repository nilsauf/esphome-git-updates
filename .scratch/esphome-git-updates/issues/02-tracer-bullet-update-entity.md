# 02: Tracer bullet: manual-URL entry → update entity showing ESPHome version

**What to build:** A minimal, installable `esphome_git_updates` integration built on the client from 01. The user adds an entry, enters a "manual" Device Builder URL and optional credentials, and picks one Tracked Config from the Device Builder's configurations (ADR-0001). The entry creates one update entity on the integration's own device. The entity shows the Available ESPHome Version against the stored Installed ESPHome Version, and offers an update when they differ.

If the Device Builder reports an unexpected `server_version` or sends unexpected frames, the user sees a Repair issue (ADR-0002).

**Blocked by:** 01 (Device Builder client + CLI spike)

**Status:** closed

- [x] The config flow accepts a manual URL with optional username and password, and rejects a URL it cannot connect to or log in to.
- [x] The config flow lists the Tracked Configs from `devices/list`, and the chosen one becomes the entry's single Tracked Config.
- [x] One update entity per entry, on a device the integration creates.
- [x] The Available ESPHome Version is read on connect and every 5 min.
- [x] The entity offers an update when the Available ESPHome Version differs from the stored installed one ("newer" means "differs").
- [x] An unexpected `server_version` or unexpected frame raises a Repair issue, which clears once the connection is healthy again.
- [x] Tests use `pytest-homeassistant-custom-component` and the fake `/ws` server from 01.

## Comments

### 2026-11-01: agent implementation

**What exists:** `custom_components/esphome_git_updates/`:
- `config_flow.py`: manual URL and optional credentials, then a Tracked Config picked from `devices/list`.
- `connection.py`: opens a Device Builder connection from an entry's data.
- `coordinator.py`: polls the Available ESPHome Version and raises or clears the Repair issue. It also holds `TrackedConfigStore`.
- `update.py`: the update entity.

Tests are `tests/test_config_flow.py` and `tests/test_update.py`, run against the fake `/ws` server.

**Decisions to check:**
- **Seeding the Installed ESPHome Version.** A new entry stores the Available ESPHome Version as installed, so it shows no update until the Device Builder's ESPHome changes. This mirrors Git Source seeding, but the glossary says the stored value is "the one recorded at the last successful flash", and a seed is not a flash. Ticket 07 makes the stored value the fallback for the Linked Device's `sw_version`. A seeded value could then hide a real update. 07 should decide whether to tell seeded values from flashed ones, and the glossary or ADR-0003 should record the decision.
- **Polling.** Each poll opens a short connection and reads `esphome_version` from the handshake. No command is sent.
- **Unique id.** The `unique_id` is the configuration filename, without the URL, because ticket 10's add-on URL can change. The cost: two Device Builders that both hold the same filename can't both be tracked.
- **Repair issues.** An unreachable Device Builder makes the entity unavailable and raises no Repair issue. Only `UnsupportedServerVersion` and `UnexpectedFrame` raise one, using one issue id per entry. Removing the entry deletes the issue and the Store.
- **Client change.** A socket closed before the handshake now raises `ConnectionLost`, not `UnexpectedFrame`, so an outage doesn't raise a Repair issue.

**Open points:**
- **Login failures are silent.** Credentials that stop working (`AuthenticationFailed` during polling) only make the entity unavailable. There is no reauth flow and no Repair issue, and no later ticket covers this.
- **Two mypy runs.** `device_builder.py` has two module names: the CLI imports it top-level, the integration as part of the package. So `mypy` now checks the integration, and the spike needs the second command written in `pyproject.toml`.
- **aiohttp workaround in the tests.** aiohttp 3.14 re-arms a closed websocket's heartbeat timer. `tests/conftest.py` cancels exactly those timers, so PHACC's lingering-timer check still catches real leaks.
- **Manifest placeholders.** `manifest.json` has placeholder `documentation` and `codeowners` until ticket 11.
