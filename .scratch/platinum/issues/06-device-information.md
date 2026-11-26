# 06: Complete device information, and a software version that follows Install

**What to build:** The Gold `devices` rule. When a Tracked Config has no Linked Device, the update entity creates a device of its own. That device carries only a name and a model today. It gains:

- manufacturer `ESPHome`;
- the Configuration as its model;
- the Device Builder address as its configuration URL, for a manual location only (an add-on's ingress address is internal and useless to the user);
- the Installed ESPHome Version as its software version.

The software version is kept current. Home Assistant reads an entity's device information only when the entity is added, so a version set once would go stale at the first Install. When the Installed ESPHome Version changes, update the device registry entry, so the device page follows the last Install instead of the last setup.

A Linked Device is not modified: ESPHome or MQTT owns it. The entity only pins itself to it, as today.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [ ] The device created for an entry without a Linked Device has manufacturer `ESPHome`, the Configuration as model, and the Installed ESPHome Version as its software version.
- [ ] Its configuration URL is the manual Device Builder address without credentials, and is absent for an add-on location.
- [ ] After a successful Install that changes the Installed ESPHome Version, the device registry entry's software version follows it, without a reload or restart.
- [ ] After a reconfigure, the configuration URL reflects the new location (the reload re-registers the device).
- [ ] A test asserts the registry fields for the own-device case, and asserts that a Linked Device's entry is unchanged.
- [ ] No change to the entity's versions, availability or device association.

## Comments

Implemented and merged into `quality-scale-platinum` (2026-11-26), followed by the code-review fixes merge.
