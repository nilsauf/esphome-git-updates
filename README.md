# ESPHome Git Updates

A Home Assistant integration that offers firmware updates for ESPHome devices whose firmware pulls code from git. It watches one ESPHome configuration per config entry and shows a single update entity for it. An update appears when the Device Builder would compile with a newer ESPHome version, or when a branch used by one of the configuration's `external_components` or remote `packages:` gets a new commit. Pressing Install compiles and flashes the device over the air through the ESPHome Device Builder.

Terms used here (Tracked Config, Git Source, Floating Ref, …) are defined in [GLOSSARY.md](GLOSSARY.md).

## Requirements

- Home Assistant 2026.10.0 or newer.
- An ESPHome Device Builder: the ESPHome add-on or a standalone `esphome dashboard`.
- For the "add-on (auto)" location mode: a Home Assistant install with Supervisor.

## Install with HACS

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/nilsauf/esphome-git-updates` with type **Integration**.
3. Search HACS for **ESPHome Git Updates**, download it, and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and pick **ESPHome Git Updates**.

HACS offers new versions of the integration itself as they are released.

## Device Builder location

The config flow asks where the Device Builder runs. **add-on (auto)** is offered only when Home Assistant has Supervisor:

- **add-on (auto)**: on a Supervisor install, the integration finds the ESPHome add-on itself and talks to it on its internal port, with no login. If your add-on has a non-standard slug, you can override it. If the add-on can't be found, the integration falls back to the Device Builder address that Home Assistant's own ESPHome integration discovered.
- **manual**: enter the Device Builder's base URL, for example `http://192.168.1.10:6052`. Add a username and password only if the Device Builder asks for a login. Use this for a standalone Device Builder, or when Supervisor isn't available.

Then pick the Configuration to track. Add the integration once for each Tracked Config.

## Requirements on the Configuration

### `ota:`

Installs are flashed over the air, so the Configuration needs an OTA platform:

```yaml
ota:
  - platform: esphome
```

(`platform: web_server` works too.) A device that has never had an `ota:` block must be flashed once over USB with it before this integration can update it.

### `refresh: always`

ESPHome reuses its cached clone of a Git Source until its Refresh Policy (`refresh`, default `1d`) runs out, so without it an Install can compile the old commit. Every tracked Git Source must set `refresh: always` (or `0s`):

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/example/components
      ref: main
    refresh: always

packages:
  remote:
    url: https://github.com/example/configs
    ref: main
    files: [base.yaml]
    refresh: always
```

Only Floating Refs (a branch, or no ref) are tracked. Pinned Refs (a tag or commit) never produce an update. See [ADR-0004](docs/adr/0004-require-refresh-always.md) for why a Clean Build is not a substitute.

## Releasing

One-time setup: give the GitHub repository a description, topics, and enabled issues. HACS validation fails without them.

1. Bump `version` in `custom_components/esphome_git_updates/manifest.json` and `pyproject.toml`, and commit.
2. Tag the commit `v<version>` (for example `v0.1.0`) and push the tag.

The release workflow runs the tests, hassfest and HACS validation, checks that the tag matches the manifest version, and creates the GitHub release that HACS installs from.
