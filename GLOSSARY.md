# ESPHome Git Updates

A Home Assistant integration that offers firmware updates for ESPHome devices whose firmware depends on git-hosted code, and rebuilds and flashes them through the ESPHome Device Builder.

## Language

### Configs and devices

**Configuration**:
A device YAML file that the Device Builder lists; files it pulls in with `!include` or local `packages:` are not Configurations.
_Avoid_: device, node, YAML (when meaning the file the Device Builder manages)

**Tracked Config**:
A Configuration that the integration watches for updates; each has exactly one update entity.
_Avoid_: project, firmware, node

**Linked Device**:
The existing Home Assistant device (from the ESPHome or MQTT integration) that a Tracked Config's update entity is attached to.
_Avoid_: target, host

**Device Builder**:
The ESPHome add-on (or standalone server) that compiles and flashes firmware on the integration's behalf.
_Avoid_: dashboard, ESPHome add-on, builder

### Sources and versions

**Source**:
Anything whose change makes a Tracked Config's firmware outdated: the ESPHome version, or a Git Source.
_Avoid_: dependency, input

**Git Source**:
An `external_components` or remote `packages:` entry of a Tracked Config that points at a git repository and ref.
_Avoid_: component, repo, external component (when meaning the source)

**Floating Ref**:
A Git Source ref that names a branch (or no ref at all), so its commit moves over time; only Floating Refs are tracked.
_Avoid_: unpinned, moving ref

**Pinned Ref**:
A Git Source ref that names a tag or commit; it never produces an update.
_Avoid_: fixed ref, locked ref

**Installed Commit**:
The commit of a Git Source that the integration recorded when it last successfully flashed the Tracked Config.
_Avoid_: current sha, deployed commit

**Latest Commit**:
The commit a Floating Ref currently points to on its remote.
_Avoid_: head, newest sha

**Installed ESPHome Version**:
The ESPHome version the Linked Device reports it is running, falling back to the one recorded at the last successful Install. With no Linked Device and no Install yet, it is assumed to be the Available ESPHome Version at setup; with a Linked Device it is never assumed, and is unknown until the device reports it or an Install succeeds.

**Available ESPHome Version**:
The ESPHome version the Device Builder would compile with, not the newest ESPHome release.
_Avoid_: latest ESPHome

**Refresh Policy**:
How long ESPHome reuses its cached clone of a Git Source before fetching again; Tracked Configs need it set to "always".

### Building

**Install**:
One update run for a Tracked Config: an optional Clean Build step, then compile, then over-the-air upload.
_Avoid_: build, flash, update (when meaning the run)

**Clean Build**:
An Install that first wipes the Tracked Config's build directory; it does not re-fetch Git Sources.
_Avoid_: fresh build, rebuild

**Deferred Install**:
A Device Builder install that waits for an offline device to come back before flashing; the integration refuses these.
