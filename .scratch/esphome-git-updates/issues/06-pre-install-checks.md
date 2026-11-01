# 06: Pre-install checks with Repair issues

**What to build:** Before compiling anything, Install checks that the Tracked Config can actually be updated over the air with the latest Git Source commits. If it can't, the Install is refused with a clear error and a Repair issue explains the fix.
- **Missing OTA:** the YAML has no `ota:` entry with platform `esphome` or `web_server`. Such a device needs one USB flash with OTA enabled first.
- **Refresh Policy:** a tracked Git Source lacks `refresh: always` or `0s` (ADR-0004), so ESPHome may build a stale cached clone.

**Blocked by:** 05 (Git Sources: discover Floating Refs and track Latest Commit)

**Status:** closed

- [x] Install refuses with a clear error, and starts no Device Builder job, when the config has no OTA platform `esphome`/`web_server`.
- [x] Install refuses with a clear error when any tracked Git Source's Refresh Policy is not `always`/`0s`, naming the offending Sources.
- [x] Each condition raises its own Repair issue, which clears once the YAML is fixed and re-read.
- [x] Tests cover both refusals and the passing case.
