# Discover Git Sources from raw YAML, not the resolved config

Git Sources are found by reading the Tracked Config's raw YAML over the Device Builder's `/ws` API (`devices/get_config`), applying `substitutions`, and following `!include` and local `packages:` files, each read the same way. The fully resolved config is only available from the deprecated `/json-config` endpoint, which returns secrets in clear text and triggers a git refresh on every call. Reading files over `/ws` rather than from `/config/esphome` keeps Git Source tracking working when the Device Builder runs on another host ("manual" mode), where its files aren't on Home Assistant's filesystem.

## Consequences

- External components declared *inside* a remote package are invisible: their content lives only in the add-on's private `/data` clone.
- Only the remote package itself is tracked.
- `devices/get_config` returns raw text and does no git refresh. It reads any file under the Device Builder's config directory, not only Configurations; following `!include` depends on that. The Device Builder's MCP layer already restricts reads to bare device filenames, and if `/ws` adopts the same check, includes stop resolving. A refused include read is treated as protocol drift and raises the existing Repair issue.
- `secrets.yaml` is never read. A Git Source whose URL or ref depends on `!secret` is reported as untrackable rather than guessed.
