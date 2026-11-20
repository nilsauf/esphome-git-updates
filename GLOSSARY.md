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

**Device Builder Location**:
Where an entry's Device Builder is reached: the slug of the ESPHome add-on, or a URL with optional credentials. Changing it does not change the Tracked Config.
_Avoid_: address, host (when the add-on counts too), connection (when meaning where the builder is)

### Sources and versions

**Source**:
Anything whose change makes a Tracked Config's firmware outdated: the ESPHome version, or a Git Source.
_Avoid_: dependency, input

**Git Source**:
An `external_components` or remote `packages:` entry of a Tracked Config that points at a git repository and ref.
_Avoid_: component, repo, external component (when meaning the source)

**Followed Source**:
A Git Source as last seen on its remote, together with what it follows (Releases, Version Tags or Latest Commit), which is decided anew on every poll.
_Avoid_: source state, tracked source

**Floating Ref**:
A Git Source ref that names a branch, or a tag that is not a Version Tag, so its commit can move over time; it is followed by Latest Commit and never rewritten.
_Avoid_: unpinned, moving ref

**Tag Ref**:
A Git Source ref that names a Version Tag; it is followed by Releases, else by Version Tags, and an Install moves it to the newer one.
_Avoid_: release ref

**Unset Ref**:
A Git Source with no ref, which ESPHome builds from the remote's default branch; it is followed by Releases, else by Version Tags, else by Latest Commit.
_Avoid_: default ref, HEAD

**Pinned Ref**:
A Git Source ref that names a commit; it never produces an update.
_Avoid_: fixed ref, locked ref

**Release**:
A published GitHub release of a Git Source's repository that is neither a draft nor a prerelease and whose tag is a Version Tag; other hosts are taken to have no Releases.
_Avoid_: version (when meaning the GitHub object)

**Version Tag**:
A git tag whose name reads as a version (such as `v1.2.3` or `1.2`) without a prerelease suffix; tags are ordered by that version, never by date.
_Avoid_: latest tag (when meaning the newest by date)

**Installed Commit**:
The commit of a Git Source that the integration recorded when it last successfully flashed the Tracked Config.
_Avoid_: current sha, deployed commit

**Installed Tag**:
The Version Tag of a Tag Ref that the integration recorded when it last successfully flashed the Tracked Config; until then, the tag the YAML names.
_Avoid_: current release, YAML tag (when meaning what the device runs)

**Latest Commit**:
The commit a Floating Ref, or an Unset Ref with no Releases and no Version Tags, currently points to on its remote.
_Avoid_: head, newest sha

**Installed ESPHome Version**:
The ESPHome version the Linked Device reports it is running, falling back to the one recorded at the last successful Install. With no Linked Device and no Install yet, it is assumed to be the Available ESPHome Version at setup; with a Linked Device it is never assumed, and is unknown until the device reports it or an Install succeeds.

**Available ESPHome Version**:
The ESPHome version the Device Builder would compile with, not the newest ESPHome release.
_Avoid_: latest ESPHome

**Installed Record**:
What the integration remembers about one Tracked Config: the Installed ESPHome Version, Installed Commits and Installed Tags of its last successful Install, the seeds that stand in for them until then, and the Install still running, if any.
_Avoid_: store, state (when meaning what the integration remembers)

**Refresh Policy**:
How long ESPHome reuses its cached clone of a Git Source before fetching again; Tracked Configs need it set to "always".

### Building

**Install**:
One update run for a Tracked Config: an "update YAML" step that rewrites any Tag Ref or Unset Ref moving to a newer version, then an optional Clean Build step, then compile, then over-the-air upload.
_Avoid_: build, flash, update (when meaning the run)

**Clean Build**:
An Install that first wipes the Tracked Config's build output and the build caches all configs share, locally and on each remote build server connected at that moment; it leaves cached Git Sources alone, so they are fetched only when their Refresh Policy says so.
_Avoid_: fresh build, rebuild

**Deferred Install**:
A Device Builder install that waits for an offline device to come back before flashing; the integration refuses these.
