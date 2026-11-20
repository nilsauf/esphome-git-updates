# 02: Diagnostics for a Tracked Config

**What to build:** A diagnostics download for the config entry (the Gold `diagnostics` rule). It reports what the integration sees, never what it can be used to impersonate: the entry's title and unique id, its data and options with credentials redacted, the stored Device Builder Location, the version coordinator's Available ESPHome Version and success state, the Git coordinator's success state and shown Followed Sources, the Installed Record's contents, and the id of the Linked Device if there is one.

The download must work while the Device Builder is unreachable: it reads in-memory state only and performs no I/O, so it neither resolves an add-on ingress port nor asks the Device Builder or GitHub anything.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] A diagnostics module serves the entry's diagnostics through Home Assistant's diagnostics platform, with the typed config entry.
- [ ] Username, password and GitHub token are replaced by a redaction marker; a manual Device Builder URL is reduced to scheme, host and port. No credential or URL userinfo appears anywhere in the download.
- [ ] The YAML text, and any file read from the Device Builder, is not included.
- [ ] The download includes: entry title and unique id; device builder location kind and address (redacted); the version coordinator's data and last-update-success; the Git coordinator's last-update-success and each shown source's label, versions and commit count; the Installed Record's Installed ESPHome Version, Installed Commits, Installed Tags and active Install; the Linked Device id when present.
- [ ] The Installed Record gains a read-only snapshot method for this; diagnostics does not read its stored layout.
- [ ] A test downloads the diagnostics through the config-entry diagnostics helper and asserts the payload's shape and that a known secret value does not appear anywhere in the JSON.
- [ ] The same test passes with the Device Builder stopped, and the diagnostics still show the last polled state.
