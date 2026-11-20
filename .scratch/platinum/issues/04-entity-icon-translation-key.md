# 04: Activate the entity's icon definitions

**What to build:** The Gold `icon-translations` rule, and with it the translation story for the entity's name. The integration's icon definitions are keyed on an entity translation key that the entity does not set, so they are never looked up and the entity shows no icon of its own.

Give the entity that translation key, matching the key its icon definitions already use. Do **not** add a name for it: an unnamed update entity is named by its device class, which the update component translates into every language Home Assistant ships, and naming it here would replace that with an English-only string. Entity ids already end in the device-class name, so existing entities keep their ids.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] The update entity sets a translation key equal to the key in the integration's icon definitions.
- [ ] `strings.json` and `translations/en.json` gain no name for that key (this is deliberate; a comment in the code says why).
- [ ] The entity registry entry for the update entity has that translation key.
- [ ] The entity's friendly name and entity id are unchanged (`<device> Firmware`, `update.<device>_firmware`).
- [ ] A test asserts the registry's translation key and that the icon-definition file has an entry for that key, so the pairing cannot silently break.
