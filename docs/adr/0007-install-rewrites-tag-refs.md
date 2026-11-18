# Follow Releases by rewriting Tag Refs in the YAML

ESPHome builds exactly the `ref:` the YAML names; it has no "latest release" ref. To follow a Git Source's Releases (else its Version Tags), something has to change that ref, so an Install of a Tag Ref or Unset Ref writes the newer Version Tag into the YAML before compiling. Floating Refs (branches) keep following Latest Commit and are never rewritten: the ref a user wrote says what they want followed — a tag means versions, a branch means commits, no ref means "the best this repo offers".

This ends the integration's read-only stance toward configs.

## Considered Options

- **Gate only**: offer an update when a new Release appears but keep building the branch. Rejected: the version shown and the firmware flashed would not match.
- **Move every ref to Releases**, branches included. Rejected: it overrides a user who deliberately put a source on a branch.

## Consequences

- Writes go through the Device Builder's `devices/update_config` with `expected` set to the text just read, so a file edited in between is never overwritten. This keeps working in "manual" mode (ADR-0006).
- The rewrite targets the one place the ref's text is written: the device YAML, an included file, a substitution's value. If that text is shared with anything but this ref (a substitution also used elsewhere, an include's `vars:`), the source is reported as untrackable instead.
- An Unset Ref gains a ref on its first Install and is a Tag Ref from then on.
- Install writes the version the entity showed when it was pressed, not one found mid-run.
- A failed Install keeps the rewritten YAML. The Installed Tag (recorded as in ADR-0003, seeded from the YAML) still names the old version, so the update stays offered and a retry builds the new one.
- Releases are read from GitHub's API only; other hosts fall back to Version Tags. When GitHub can't be asked (rate limit, outage), the last known answer is kept, so a source doesn't flip between Releases and tags from poll to poll.
- Only strictly newer versions are offered; nothing is downgraded.
