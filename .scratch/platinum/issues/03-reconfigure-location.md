# 03: Reconfigure the Device Builder Location

**What to build:** A reconfigure step (the Gold `reconfiguration-flow` rule) that changes where an entry's Device Builder is reached, without removing the entry. It offers the same add-on / manual choice as setup, pre-filled from the entry, and lets the user switch modes: moving off Supervisor to a standalone Device Builder is the case that matters. The saved data is a full replacement containing only the chosen mode's keys, the Configuration and, if present, the Linked Device — no stale key from the abandoned mode.

The new location is tested before it is saved, using the setup flow's connection and listing. It is then verified to still have the Tracked Config: a Device Builder that does not list the entry's Configuration aborts the flow with its own reason, because otherwise setup would succeed (the version poll works) while every Git poll fails.

Apply the change with a reload. The reload is what clears the entry's reauthentication pause, so credentials entered here are tried after the Device Builder rejected the old ones. A running Install is re-attached after the reload and either continues against the same Device Builder or ends as an unknown outcome when the builder was replaced.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] The config flow has a reconfigure step reachable from the entry, showing the add-on / manual menu with the current mode's values pre-filled.
- [ ] Each mode's form is the setup flow's form, with that flow's connection errors (`cannot_connect`, `invalid_auth`, `unsupported_version`, `addon_not_found`) reported in place.
- [ ] Switching modes drops the abandoned mode's keys: after reconfigure, an add-on entry has no `url`, `username` or `password`, and a manual entry has no add-on slug.
- [ ] A builder that does not list the entry's Configuration aborts the flow with its own reason and leaves the entry unchanged.
- [ ] A successful reconfigure updates and reloads the entry; the entity, its unique id, the Tracked Config and the Installed Record are unchanged, and no update appears or disappears because of it.
- [ ] The entry's reauthentication pause is cleared by a successful reconfigure: a test that first makes the Device Builder reject the stored login, then reconfigures with working credentials, sees polling resume without a restart.
- [ ] A running Install survives the reload: it is followed through when the same builder still has its jobs, and reported as an unknown outcome when it does not.
- [ ] The abort reason and any new strings exist in `strings.json` and `translations/en.json`.
- [ ] Tests cover: each mode, switching modes in either direction, each connection error, the wrong-configuration abort, and the reauth interaction.
