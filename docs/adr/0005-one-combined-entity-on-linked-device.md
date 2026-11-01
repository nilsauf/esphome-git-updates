# One combined update entity, attached to the existing device

Each Tracked Config gets a single update entity that covers the ESPHome version and all Git Sources. A rebuild always picks up all of them, so per-Source Install buttons would mislead. The entity is attached to the Linked Device, an existing `esphome` or `mqtt` device chosen in the config flow. The attachment has to be explicit (look the device up, then set `device_entry`), because current HA no longer merges devices across config entries by MAC.

This overlaps HA core's ESPHome "firmware" dashboard update entity, which only compares ESPHome versions. That entity is disabled by default and should stay disabled for Tracked Configs.
