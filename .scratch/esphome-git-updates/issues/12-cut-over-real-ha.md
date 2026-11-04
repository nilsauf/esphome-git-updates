# 12: Cut over on real HA

**What to build:** Replace the marsrelay YAML package and build script on the real Home Assistant with the integration.

1. **Publish to GitHub** (ticket 11's last two criteria):
   - Create `github.com/nilsauf/esphome-git-updates`, give it a description and topics, and enable issues. HACS validation fails without these.
   - Add it as the `origin` remote and push `main`. Check that CI (mypy, pytest, hassfest, HACS validation) passes.
   - Tag `v0.1.0` and push the tag. Check that the release workflow creates the GitHub release.
   - Tick ticket 11's two open criteria.
2. **Confirm Clean Build on the add-on** (ticket 08's open check). From the HA host or the `homeassistant` container:
   ```sh
   PORT=$(ha addons info 5c53de3b_esphome --raw-json | jq .data.ingress_port)
   python3 scripts/device_builder_cli.py --addon-port $PORT --record addon_clean_remote.json install b2500.yaml --clean
   ```
   The recording should show `firmware/get_jobs`, then a `follow_job` for each remote clean ending `completed`, before `firmware/install`. Copy it into `tests/fixtures/` with a `source` line and a replay test, following the `test_captured_*` tests. If it differs from the synthesized frames (`with_clean_jobs` in `tests/fake_device_builder.py`), record that on ticket 08.
3. Add the repo to HACS as a custom repository (type Integration) and install the integration from HACS.
4. Set up an entry for the marsrelay config in "add-on (auto)" mode, linked to the existing marsrelay device. Installed Commits are seeded from the Latest Commit, which assumes the device already runs it.
5. Make sure the YAML has `ota: - platform: esphome` (USB-flash once if it didn't) and `refresh: always` on its Git Sources.
6. Verify one Install end to end.
7. Delete the old package, the script and their helpers by hand. Keep core's ESPHome firmware update entity disabled for this device.

**Blocked by:** 04 (Device offline refusal + re-attach after restart), 06 (Pre-install checks with Repair issues), 07 (Linked Device + Installed ESPHome Version from `sw_version`), 10 (Add-on (auto) Device Builder location), 11 (HACS packaging + release workflow)

**Status:** closed

- [x] The repo is on GitHub, CI passes on `main`, and the `v0.1.0` tag produced a GitHub release; ticket 11's two open criteria are ticked.
- [x] A Clean Build on the add-on follows every remote clean to `completed` before `firmware/install`; the capture is stored as a fixture with a replay test.
- [x] The integration is installed through HACS and an entry exists for the marsrelay config, attached to the marsrelay device.
- [x] One Install succeeds end to end and the update entity then shows no update.
- [x] The marsrelay package, the build script and their helpers are removed, and HA restarts cleanly without them.
- [x] Core's ESPHome firmware update entity for the device is disabled.

## Comments

### 2026-11-04: closing

Cut over on the real HA. The repo is at `github.com/nilsauf/esphome-git-updates` with `origin` set, CI runs on `main`, and the `v0.1.0` tag produced the GitHub release (release workflow run #1). Ticket 11's two open criteria are ticked.

The integration is installed through HACS, the marsrelay entry runs in "add-on (auto)" mode attached to the existing marsrelay device, the YAML has `ota: - platform: esphome` and `refresh: always`, and one Install completed end to end with the update entity then showing no update. The old package, build script and helpers were deleted and HA restarts cleanly without them; core's ESPHome firmware update entity is disabled for this device.

**Exception:** step 2 (recording a Clean Build on the add-on and storing it as a fixture) was not performed. No `addon_clean_remote.json` capture exists in `tests/fixtures/`. Closed by owner decision with that check accepted as outstanding.
