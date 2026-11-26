# 11: Document setup, options, reconfigure and removal

**What to build:** Three Bronze/Silver documentation rules. The README is this integration's documentation page. It gains:

- **Setup parameters** (`docs-installation-parameters`): every field the setup flow asks for — the add-on / manual choice, the add-on slug and when to change it, the URL, the optional username and password, the Configuration picker, and the Linked Device picker including what leaving it empty does.
- **Options** (`docs-configuration-parameters`): Clean Build, the Git poll interval (with its default), the upload address (and what OTA means), and the GitHub token (what it is used for and that it is optional).
- **Reconfigure**: that the Device Builder Location can be changed from the entry without removing it, including switching between the add-on and a URL, and what happens to a running Install.
- **Removal** (`docs-removal-instructions`): removing the config entry, what the integration forgets with it (its Installed Record and Repair issues), and how to remove the integration itself from HACS.

Write for a user who has just installed it: no internal vocabulary that the glossary does not define, and no claim the code does not keep.

**Blocked by:** 03

**Status:** resolved

- [ ] Every field asked during setup is documented, including what is optional and what the default is.
- [ ] Every option is documented with what it does and its default.
- [ ] The reconfigure flow is documented, including switching modes.
- [ ] Removal is documented, including what data goes with the entry.
- [ ] The text matches the implemented flows; a reader can follow it without opening the code.

## Comments

Implemented and merged into `quality-scale-platinum` (2026-11-26), followed by the code-review fixes merge.
