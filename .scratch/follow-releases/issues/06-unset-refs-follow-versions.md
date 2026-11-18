# 06: Unset Refs follow versions

**What to build:** A Git Source with no ref (an Unset Ref) follows Releases if the repository has any, else Version Tags, else Latest Commit as today. The mode is re-checked on every poll. When the source first follows a version (at setup, after upgrading from commit tracking, or when the repository gets its first version tag or Release), the Installed Tag is seeded with that version, so no update is offered until the next one. Install adds the ref: a `ref: <tag>` line after `url:` at the same indentation, or `@<tag>` on the shorthand. From then on the source is a Tag Ref, and its record carries over because it's keyed by repository. A flow-style mapping such as `{type: git, url: …}` can't take an inserted ref safely, so it is reported as untrackable. The Refresh Policy Repair issue applies only while the source follows commits.

**Blocked by:** 03, 05

**Status:** done

- [x] An Unset Ref on a remote without tags or Releases behaves exactly as today: it follows commits and needs `refresh: always`.
- [x] When that remote gets a first Version Tag, the next poll switches the source to versions with no update offered and no Refresh Policy issue; a later higher tag offers an update.
- [x] Install of that update inserts the ref (block mapping and shorthand cases), keeps all other bytes, and afterwards the source shows as a Tag Ref with no update.
- [x] An existing entry with an Unset Ref on a repository that has Releases shows no update after upgrading, until the next Release.
- [x] A flow-style Unset Ref is reported as untrackable for following, with the reason.

## Comments

- Mode: an Unset Ref follows versions while `ls-remote` lists any Version Tag (a Release's tag is one), so GitHub failing never flips it back to commits. Releases then pick the version as for a Tag Ref. `_ask_remote` returns a `VersionedGitSource` keyed by url, and `git_remote.resolve_ref_and_tags(url, None)` asks for `HEAD` plus tags in one call. With no Version Tag it is a `TrackedGitSource`, as today. While GitHub has never answered, it shows no update and is not seeded.
- Seeding: `_seed(state)` in git_coordinator.py gives the YAML's tag for a Tag Ref, else `offered("")` once the Releases answer is known. The Installed Tags of every `VersionedGitSource` count as present.
- Install: an Unset Ref gains a ref only when a newer version is offered (`_moves`). An Install for something else leaves its YAML alone and keeps its Installed Tag. Empty tags are never recorded.
- Insertion: `RefLocation.prefix`, with an empty span (`start == end`). `_Loader.construct_mapping` marks each `url:` key (a `_Written`) with `ref_insertion` (at the end of the url's line, prefix `\n<indent>ref: `, CRLF kept, also at end of file) and `in_flow_style`. `rewritten` quotes an inserted `ref:` value that would not read back as a string (`"1.10"`). A shorthand written as is gets `@` at the end of its scalar. An empty `ref:` key, a url scalar spanning lines, or a shorthand from a substitution get no location, so they keep following commits.
- Flow-style: the source still follows its Latest Commit, and discovery reports it in `untrackable` ("... is a flow-style mapping, where no ref can be inserted, so it cannot follow versions; it follows its Latest Commit"), logged like other untrackable reasons. It is reported even when the repository has no Version Tags.
- Refresh Policy: no code change was needed, as the check already covers only `TrackedGitSource`s.
- Tests: tests/test_unset_refs.py (integration) and the discovery tests at the end of tests/test_git_sources.py. README updated.
