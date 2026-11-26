# ESPHome Git Updates

A Home Assistant integration that offers firmware updates for ESPHome devices whose firmware pulls code from git. It watches one ESPHome configuration per config entry and shows a single update entity for it. An update appears when the Device Builder would compile with a newer ESPHome version, when a Floating Ref (a branch) used by one of the configuration's `external_components` or remote `packages:` gets a new commit, or when a Tag Ref or Unset Ref gets a newer Release or Version Tag. Pressing Install compiles and flashes the device over the air through the ESPHome Device Builder.

Terms used here (Tracked Config, Git Source, Floating Ref, Tag Ref, Unset Ref, Version Tag, …) are defined in [GLOSSARY.md](GLOSSARY.md).

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

ESPHome reuses its cached clone of a Git Source until its Refresh Policy (`refresh`, default `1d`) runs out, so without it an Install can compile the old commit. Every Git Source that follows commits (a Floating Ref, or an Unset Ref following its Latest Commit) must set `refresh: always` (or `0s`); a Tag Ref, or an Unset Ref following versions, needs no Refresh Policy:

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

Floating Refs (a branch, or a tag that is not a Version Tag such as `stable`) follow their Latest Commit. An Unset Ref (a Git Source with no ref) follows the GitHub Releases or Version Tags of its repository as a Tag Ref does, and its Latest Commit only while the repository has neither; when it starts following a version, that version counts as installed, and the first Install of a newer one adds the ref (`ref: v1.5.0` after `url:`, or `@v1.5.0` on the `github://` shorthand). An Unset Ref in a flow-style mapping (`{type: git, url: ...}`) keeps following its Latest Commit, as no ref can be inserted there. A Tag Ref (a Version Tag such as `v1.2.3`) follows the newest GitHub Release (drafts, prereleases and non-version tags ignored), or, when the repository has no Releases or is not on GitHub, the highest Version Tag on its remote. Install writes the version shown into the YAML through the Device Builder (refusing if the file changed since it was read), then compiles and uploads; it rewrites the Tag Ref wherever its text is written: in the device YAML, an `!include`d or local-package file, the `@tag` of a `github://` shorthand, or a substitution used only for that ref, and in each of those places when the same Tag Ref is written more than once (see [ADR-0007](docs/adr/0007-install-rewrites-tag-refs.md)). A version YAML would read as a number, such as `1.10`, is written quoted. A Tag Ref whose text is shared (a substitution used elsewhere or defined twice, an include's `vars:`, a ref built from several parts, or the same repository at two different refs) is logged as untrackable instead. Pinned Refs (a commit) never produce an update. See [ADR-0004](docs/adr/0004-require-refresh-always.md) for why a Clean Build is not a substitute.

## How data is updated

The integration polls; nothing is pushed to it.

- **ESPHome version, every 5 minutes.** The Device Builder is asked for its ESPHome version, the Available ESPHome Version. If the Device Builder cannot be reached, the update entity becomes unavailable until a poll reaches it again. If it cannot be reached when the integration is set up, Home Assistant retries the setup later.
- **Git Sources, every 30 minutes (the Git poll interval option, in minutes).** The integration reads the Configuration and every file it includes through the Device Builder, finds its Git Sources, and asks each Source's remote for its branches and tags. For a Tag Ref or Unset Ref on GitHub it also asks GitHub for the repository's Releases, and for each changed Source on GitHub it asks GitHub to compare the installed and latest commits, for the release notes. Changing the Git poll interval polls right away and then at the new interval. A failed poll keeps what the last good poll showed and logs why; a remote or GitHub that cannot be asked keeps its last answer. The update entity stays available.
- **GitHub's rate limit.** Without a token, GitHub allows 60 requests an hour from your IP address, shared by every Tracked Config. Set the GitHub token option to raise it; a fine-grained token with no permissions is enough for public repositories.
- **Release notes, when opened.** Opening the release notes asks GitHub again for any comparison still missing.
- **Install, when pressed.** An Install reads the YAML again and plans from that read (which commits and versions it installs, which Tag Refs it rewrites) before anything is compiled, so it never installs from a stale poll.

The Installed ESPHome Version comes from the Linked Device's reported firmware version, read whenever the device registry changes it, and otherwise from the last successful Install. Installed Commits and Installed Tags are what the integration recorded at the last successful Install (see [ADR-0003](docs/adr/0003-integration-remembers-installed-commits.md)).

## Supported functionality

### The update entity

Each Tracked Config gets exactly one update entity, named **Firmware**, of device class firmware. It sits on the Tracked Config's Linked Device (the device from the ESPHome or MQTT integration) when there is one, else on a device of its own named after the entry (see [ADR-0005](docs/adr/0005-one-combined-entity-on-linked-device.md)). Its entity ID follows the device's name, for example `update.living_room_sensor_firmware`; on an ESPHome device, which has a Firmware update entity of its own, it may get a `_2` suffix. Check the entity ID under **Settings → Devices & services → Entities**.

The entity is `on` when any Source differs from what is installed: the ESPHome version, or any Git Source. "Differs" counts, not "newer": a Device Builder moved back to an older ESPHome also shows an update. Its attributes:

| Attribute | What it shows |
| --- | --- |
| `installed_version` | The Installed ESPHome Version, followed by each tracked Git Source's Installed Commit (shortened) or Installed Tag in parentheses, for example `2026.10.1 (components 1a2b3c4, marsrelay v1.4.0)`. Unknown while any part of it is unknown. A Configuration without tracked Git Sources shows only the ESPHome version. |
| `latest_version` | The same composite for what an Install would build: the Available ESPHome Version and each Source's Latest Commit or newest Release or Version Tag. |
| `release_summary` | One `<source> <installed> → <latest>` part per changed Source, joined by `; `, with the commit count for a GitHub Source once GitHub has compared it, for example `components 1a2b3c4 → 5d6e7f8 (3 commits)`. Cut to 255 characters. |
| `in_progress` | `true` while an Install runs. |
| `update_percentage` | The Install's progress, 0 to 100, while it runs: compile up to 90 (a Clean Build takes the first 5), upload the rest. |

Sources are named after their repository, with `@<ref>` added when the same repository appears at two refs.

**Release notes** (the entity's release notes, shown in the update dialog) have a section per changed Source. A Source following GitHub Releases lists each newer Release with its notes; a Source following commits on GitHub lists its new commits with links; other Sources show the installed and latest commit or tag. Release notes need GitHub to answer: while it cannot, they show only the change.

### Install

The entity supports **Install** (the `update.install` action, without `version` or `backup`) and **Skip**, as every Home Assistant update entity does. An Install:

1. reads the YAML again and checks it; it is refused if the device looks offline (every entity of its Linked Device is unavailable), the YAML cannot be read, or a check below fails;
2. writes the newer Version Tag of each Tag Ref (and of each Unset Ref that follows versions) into the YAML through the Device Builder;
3. runs a Clean Build if the option is set, then compiles and uploads over the air (to the Upload address option, by default the device's own address), reporting progress in `update_percentage`.

The action waits until the Install ends, and fails with the reason if the Install does not succeed. A failed Install, one the Device Builder deferred because the device is offline, and one that succeeded with ESPHome warnings each leave a persistent notification titled `<entry title>: <what happened>`, with the end of the build log for a failure. A successful Install records what it installed, so the entity turns `off` (with a Linked Device and a new ESPHome version, once the device has restarted and reports that version). An Install still running when Home Assistant stops is picked up again at the next start.

### Repair issues

| Repair issue | Raised when | Clears when |
| --- | --- | --- |
| *\<title\> cannot be updated over the air* | The YAML has no `ota:` with platform `esphome` or `web_server`. Installs are refused. | The YAML has one and is read again. |
| *Git Sources of \<title\> may be built from a stale cached clone* | A Git Source that follows commits lacks `refresh: always`. Installs are refused. | Each sets `refresh: always` (or `0s`) and the YAML is read again. |
| *Unsupported Device Builder for \<title\>* | The Device Builder reports a version this integration does not support. Updates pause. | The Device Builder or the integration is updated. |
| *Unexpected answer from the Device Builder for \<title\>* | The Device Builder answers in a way this integration does not understand. Updates pause. | The Device Builder answers as expected again. |
| *Device Builder refused to read a file of \<title\>* | The Device Builder refuses to read a file the Configuration includes, so its Git Sources are not refreshed. | The file can be read again. |

The last three share one Repair issue per Tracked Config, which shows one problem at a time. While the Device Builder answers in a way this integration does not understand, the update entity is unavailable.

If a manually located Device Builder rejects the stored username and password, the integration stops connecting to it, the update entity becomes unavailable, and Home Assistant asks you to re-authenticate the entry.

## Use cases

- **A shared component repository that follows `main`.** Several devices pull `external_components` from your own repository at `ref: main` with `refresh: always`. Each push to `main` shows an update on each of those devices, with the new commits in the release notes; Install builds and flashes the commit that was latest when you pressed it.
- **A Configuration pinned to a release.** A device uses `external_components` at `ref: v1.4.0`, a Tag Ref. When the repository publishes Release `v1.5.0`, the entity offers it with its release notes, and Install rewrites the ref to `v1.5.0` in the YAML before building, so the YAML always says what the device runs.
- **A fleet of devices built from one Configuration.** Several identical devices share one remote `packages:` repository. Add the integration once per device's Configuration; each device gets its own update entity, so you can install on one device first and on the rest once it works.
- **Keeping ESPHome itself current.** Even a Configuration with no Git Sources gets an update whenever the Device Builder's ESPHome version changes, with progress and a notification on failure, from the same entity as its Git Sources.

## Examples

Replace `update.living_room_sensor_firmware` with your entity's ID, and `notify.mobile_app_phone` with your own notify action.

Notify when an update is available:

```yaml
alias: Notify about ESPHome Git updates
triggers:
  - trigger: state
    entity_id: update.living_room_sensor_firmware
    from: "off"
    to: "on"
actions:
  - action: notify.mobile_app_phone
    data:
      title: Firmware update for the living room sensor
      message: >-
        {{ state_attr('update.living_room_sensor_firmware', 'release_summary')
           or state_attr('update.living_room_sensor_firmware', 'latest_version') }}
```

Install at night, and report how it went. `continue_on_error` lets the automation report a failed Install instead of stopping. A failure keeps the entity `on`; after a success it turns `off`, which can take until the device has restarted and reconnected:

```yaml
alias: Install ESPHome Git updates at night
triggers:
  - trigger: time
    at: "03:00:00"
conditions:
  - condition: state
    entity_id: update.living_room_sensor_firmware
    state: "on"
actions:
  - action: update.install
    target:
      entity_id: update.living_room_sensor_firmware
    continue_on_error: true
  - wait_template: "{{ is_state('update.living_room_sensor_firmware', 'off') }}"
    timeout: "00:05:00"
    continue_on_timeout: true
  - action: notify.mobile_app_phone
    data:
      title: Living room sensor
      message: >-
        {% if wait.completed %}
          Installed {{ state_attr('update.living_room_sensor_firmware', 'installed_version') }}.
        {% else %}
          Install did not succeed; see the notification in Home Assistant.
        {% endif %}
```

Forward the integration's Install notifications (a failure, a device that was offline, an Install that succeeded with warnings) to your phone. This also covers Installs pressed by hand and Installs finished after a restart:

```yaml
alias: Forward ESPHome Git Updates install notifications
triggers:
  - trigger: persistent_notification
    update_type:
      - added
      - updated
conditions:
  - condition: template
    value_template: >-
      {{ trigger.notification.notification_id.startswith('esphome_git_updates_') }}
actions:
  - action: notify.mobile_app_phone
    data:
      title: "{{ trigger.notification.title }}"
      message: "{{ trigger.notification.message }}"
```

## Supported Configurations

The integration tracks Configurations: the device YAML files the Device Builder lists. A Configuration is supported when it meets all of these requirements (each is explained under [Requirements on the Configuration](#requirements-on-the-configuration)):

- **It has an OTA platform.** The YAML has an `ota:` entry with `platform: esphome` or `platform: web_server`, and the firmware running on the device was built with it. Without one, Installs are refused.
- **Every Git Source that follows commits sets `refresh: always`** (or `0s`). This applies to a Floating Ref, and to an Unset Ref while its repository has no Releases and no Version Tags. A Tag Ref, an Unset Ref following versions and a Pinned Ref need no Refresh Policy. Without it, Installs are refused.
- **Its Git Sources are `external_components` or remote `packages:`** that point at a git repository: a `source:` of `type: git`, a `github://` shorthand, or a remote package with `url:`. They may be written in the device YAML or in a file it pulls in with `!include` or local `packages:`.

A Configuration with no Git Sources is supported too: it gets updates when the Device Builder's ESPHome version changes.

Out of scope:

- **Devices whose firmware is not built from a Configuration the Device Builder lists**, such as firmware built with the ESPHome command line elsewhere, or a device whose YAML lives outside the Device Builder's configuration directory. Only Configurations the Device Builder lists can be picked during setup.
- **Firmware files read from disk.** The integration never uploads a prebuilt `.bin`; every Install compiles the Configuration through the Device Builder.
- **Dependencies that are not git**, such as `external_components` of `type: local`, PlatformIO `libraries:`, or ESPHome's own components. Only the ESPHome version stands for those: they update when the Device Builder's ESPHome version changes.

## Known limitations

- **One config entry per Configuration.** Each Tracked Config is its own entry with one update entity ([ADR-0001](docs/adr/0001-generic-per-config-integration.md)). Add the integration once per Configuration; adding the same Configuration twice is refused.
- **OTA is required.** Installs are only flashed over the air. A device whose running firmware has never had an `ota:` platform must be flashed once over USB (for example from the Device Builder) with one before this integration can update it.
- **`refresh: always` is required for a Git Source that follows commits** (a Floating Ref, or an Unset Ref following its Latest Commit). ESPHome would otherwise build its cached clone, up to a day old by default, and a Clean Build does not refetch Git Sources ([ADR-0004](docs/adr/0004-require-refresh-always.md)). A `github://` shorthand remote package cannot take `refresh:`; write it as a mapping with `url:`, `ref:`, `files:` and `refresh: always`.
- **Some refs cannot be tracked.** A Tag Ref is skipped, and logged as a warning (`a Git Source cannot be tracked: ...`), when its text cannot be rewritten without changing something else: a substitution that is used elsewhere or defined more than once, a ref that comes from an include's `vars:`, a ref built from several parts (such as `v${major}.${minor}`), or a repository referred to at two different refs. Write the ref as plain text, or in a substitution used only for that ref. A Git Source whose URL or ref depends on a `!secret`, and files pulled in with `!include_dir_*`, are skipped the same way. An Unset Ref written in a flow-style mapping (`{type: git, url: ...}`) or referred to in several places cannot follow versions, as no ref can be inserted safely; it follows its Latest Commit instead (and then needs `refresh: always`). Write it in block style, or give it a ref.
- **Git Sources inside a remote package are not seen.** The files of a remote `packages:` entry are not read, so only the package itself is tracked, not the `external_components` it declares ([ADR-0006](docs/adr/0006-parse-raw-yaml-not-resolved-config.md)). An `ota:` that only a remote package adds is not known either; then Installs are not refused for a missing OTA platform.
- **A Deferred Install is refused.** When the device is offline, the Device Builder would keep the firmware and flash it once the device comes back. The integration never lets it: the queued update is cleared, nothing is installed, and the Install fails with "device offline". Install again once the device is online.
- **An offline Linked Device is refused.** When every other entity of the Linked Device is unavailable, Install is refused before anything is compiled. A Linked Device with no other entities is never taken to be offline.
- **Only what the Device Builder lists is visible.** Setup offers only the Configurations the Device Builder lists, and every file is read through the Device Builder. If it refuses to read an included file, the Git Sources in it are not refreshed (see the *Device Builder refused to read a file* Repair issue below).
- **A Tag Ref is rewritten in the YAML on Install.** Installing a newer Version Tag writes it into the YAML through the Device Builder before compiling, wherever the ref is written, including an `!include`d file or a substitution ([ADR-0007](docs/adr/0007-install-rewrites-tag-refs.md)). The first Install of a newer version for an Unset Ref adds a ref. If the file changed since it was read, the Install is refused rather than overwriting your edit.

## Troubleshooting

### Repair issues

Each Repair issue names the Tracked Config in its title. The pre-install checks are re-run on every Git poll (every 30 minutes by default) and at every Install, so an issue clears once the YAML is fixed and read again; to clear it sooner, change the Git poll interval option, which polls right away, or reload the entry.

- ***\<title\> cannot be updated over the air*** (missing OTA). The YAML has no `ota:` entry with platform `esphome` or `web_server`, and Installs are refused. Add one:

  ```yaml
  ota:
    - platform: esphome
  ```

  If the device has never run firmware with OTA, flash it once over USB (for example from the Device Builder).
- ***Git Sources of \<title\> may be built from a stale cached clone*** (Refresh Policy). The Git Sources it names follow commits but lack `refresh: always`, and Installs are refused. Add `refresh: always` (or `0s`) to each, next to its `source:` for `external_components` or inside the remote package's mapping. Turn a `github://` shorthand remote package into a mapping with `url:`, `ref:`, `files:` and `refresh: always`.
- ***Unsupported Device Builder for \<title\>***. The Device Builder reports a version this integration does not support, so updates for the Tracked Config pause and the update entity is unavailable. Update the ESPHome add-on or Device Builder, or update this integration. The issue clears on the next poll that the Device Builder answers as expected.
- ***Unexpected answer from the Device Builder for \<title\>***. The Device Builder sent something this integration does not understand, usually because its protocol changed; updates pause, and the issue shows the details. Update this integration or the Device Builder; if both are current, open an issue with the details and the diagnostics.
- ***Device Builder refused to read a file of \<title\>*** (refused include). The Configuration includes a file the Device Builder refused to read, so its Git Sources are not refreshed. If the file was removed or renamed, fix the `!include` or local package in the YAML. If it exists, the Device Builder probably restricts which files can be read; open an issue with the diagnostics.

The last three share one Repair issue per Tracked Config, which shows one problem at a time.

### Waiting for credentials, reauthentication and reconfigure

When a Device Builder reached by URL rejects the stored username or password, the integration logs a warning that it is asking for new credentials and pausing polls, stops connecting to the Device Builder (polls fail with `waiting for new Device Builder credentials`), makes the update entity unavailable, and Home Assistant shows **Re-authenticate** for the entry under **Settings → Devices & services**. Retrying would only get the Device Builder to answer with a rate limit, so nothing is tried until you act. An entry using the add-on has no credentials to re-enter: a rejected login is logged as an error and polling continues.

- **Reauthentication** asks only for the new username and password, checks them against the same Device Builder, saves them and reloads the entry. Use it when the Device Builder's login changed.
- **Reconfigure** (the entry's ⋮ menu → **Reconfigure**) changes the Device Builder Location itself: a new URL, new credentials, or a switch between the add-on and a URL. The new location is tested and must list the Tracked Config's Configuration. Saving reloads the entry, which also ends a wait for credentials. Use it when the Device Builder moved.

Both keep the Tracked Config, its entity and its Installed Record.

### The update entity is unavailable

The entity is unavailable while the Device Builder's ESPHome version cannot be polled (every 5 minutes):

- the Device Builder cannot be reached: it is stopped or restarting, its URL is wrong, or the add-on cannot be found;
- the entry is waiting for new credentials (see above);
- the Device Builder reports an unsupported version or sends an unexpected answer (see the Repair issues above).

It becomes available again with the next poll that succeeds. If the Device Builder cannot be reached when the integration is set up, Home Assistant retries the setup later. A Git poll that fails (an unreadable YAML, an unreachable git remote or GitHub) does not make the entity unavailable: it keeps what the last good poll showed and logs why. If the Device Builder moved, use **Reconfigure**.

### Diagnostics

To attach diagnostics to an issue, go to **Settings → Devices & services → ESPHome Git Updates**, open the entry's ⋮ menu and choose **Download diagnostics**. The download works while the Device Builder is unreachable. It contains:

- the entry's title, unique ID, data and options;
- the Device Builder Location: whether it is the add-on or a URL, and the add-on slug or the URL's scheme, host and port;
- the Available ESPHome Version and whether its last poll succeeded;
- whether the last Git poll succeeded, and each tracked Git Source with its installed and latest commit or tag and, for GitHub, the number of commits between them;
- the Installed Record: the Installed ESPHome Version, Installed Commits and Installed Tags, and the Install still running, if any;
- the Linked Device's device ID.

Redacted: the username, password and GitHub token; the Device Builder URL is cut to its scheme, host and port, and any username or password in a Git Source URL is removed. The YAML is not included.

## Releasing

One-time setup: give the GitHub repository a description, topics, and enabled issues. HACS validation fails without them.

1. Bump `version` in `custom_components/esphome_git_updates/manifest.json` and `pyproject.toml`, and commit.
2. Tag the commit `v<version>` (for example `v0.1.0`) and push the tag.

The release workflow runs the tests, hassfest and HACS validation, checks that the tag matches the manifest version, and creates the GitHub release that HACS installs from.
