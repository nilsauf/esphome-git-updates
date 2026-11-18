# 06: Unset Refs follow versions

**What to build:** A Git Source with no ref (an Unset Ref) follows Releases if the repository has any, else Version Tags, else Latest Commit as today. The mode is re-checked on every poll. When the source first follows a version (at setup, after upgrading from commit tracking, or when the repository gets its first version tag or Release), the Installed Tag is seeded with that version, so no update is offered until the next one. Install adds the ref: a `ref: <tag>` line after `url:` at the same indentation, or `@<tag>` on the shorthand. From then on the source is a Tag Ref, and its record carries over because it's keyed by repository. A flow-style mapping such as `{type: git, url: …}` can't take an inserted ref safely, so it is reported as untrackable. The Refresh Policy Repair issue applies only while the source follows commits.

**Blocked by:** 03, 05

**Status:** ready-for-agent

- [ ] An Unset Ref on a remote without tags or Releases behaves exactly as today: it follows commits and needs `refresh: always`.
- [ ] When that remote gets a first Version Tag, the next poll switches the source to versions with no update offered and no Refresh Policy issue; a later higher tag offers an update.
- [ ] Install of that update inserts the ref (block mapping and shorthand cases), keeps all other bytes, and afterwards the source shows as a Tag Ref with no update.
- [ ] An existing entry with an Unset Ref on a repository that has Releases shows no update after upgrading, until the next Release.
- [ ] A flow-style Unset Ref is reported as untrackable for following, with the reason.
