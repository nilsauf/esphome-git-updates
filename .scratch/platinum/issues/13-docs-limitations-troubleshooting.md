# 13: Document limitations, supported Configurations and troubleshooting

**What to build:** Three Gold documentation rules.

- **Known limitations** (`docs-known-limitations`): one config entry per Configuration; OTA is required (and a device never flashed with it needs one USB flash); `refresh: always` is required for a Floating Ref or an Unset Ref following its Latest Commit; refs that are shared, built from parts or written in flow style cannot be tracked; a Deferred Install is refused; an offline Linked Device is refused; only what the Device Builder lists as a Configuration is visible; a Tag Ref is rewritten in the YAML on Install.
- **Supported Configurations** (`docs-supported-devices`): what a Configuration must look like (an `ota:` platform, the refresh policy where it applies, Git Sources as `external_components` or remote `packages`), and what is out of scope (devices built outside the Device Builder's configurations, firmware files read from disk, non-git dependencies).
- **Troubleshooting** (`docs-troubleshooting`): each Repair issue and its fix (missing OTA, Refresh Policy, unsupported Device Builder version, unexpected answer, refused include); what "waiting for credentials" means and how reauthentication and reconfigure differ; how to download diagnostics and what they contain; how the entity's unavailable state maps to a Device Builder that cannot be reached.

**Blocked by:** 02

**Status:** resolved

- [ ] Each limitation above is stated, with what the user can do about it where there is something to do.
- [ ] The Configuration requirements are stated as requirements, not as examples only.
- [ ] Every Repair issue the integration raises is listed with its user-facing cause and fix.
- [ ] Diagnostics are mentioned with what they contain and what is redacted.
- [ ] No documented behaviour contradicts the code or the Repair issue strings.

## Comments

Implemented and merged into `quality-scale-platinum` (2026-11-26), followed by the code-review fixes merge.
