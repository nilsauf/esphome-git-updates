# 10: Add-on (auto) Device Builder location

**What to build:** On a Supervisor install, the user can choose "add-on (auto)" in the config flow instead of entering a URL. The integration finds the ESPHome add-on through the Supervisor `hassio` helpers and connects to its ingress port on 127.0.0.1 with no auth (ADR-0002). The add-on slug can be overridden for non-standard installs. If the add-on can't be found that way, the integration falls back to the URL discovered by core's `esphome.dashboard`.

**Blocked by:** 02 (Tracer bullet: manual-URL entry → update entity showing ESPHome version)

**Status:** closed

- [x] The config flow offers "add-on (auto)" and "manual"; "add-on (auto)" is offered only when Supervisor is available.
- [x] Auto mode finds the add-on by slug, with the default slug overridable, and connects via its ingress port without auth.
- [x] If the add-on is not found, the URL from core's `esphome.dashboard` is used; if neither works, the flow shows a clear error.
- [x] The ingress port is re-resolved on setup, so an add-on restart or port change doesn't break the entry.
- [x] Tests cover add-on found, fallback, and neither available.
