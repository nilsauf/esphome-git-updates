# Follow Releases and Version Tags: spec

Status: ready-for-agent

Terms are defined in `GLOSSARY.md`. The decision to rewrite refs is in `docs/adr/0007-install-rewrites-tag-refs.md`, and the Refresh Policy exemption is noted in `docs/adr/0004-require-refresh-always.md`.

## Problem Statement

Today the integration only follows Floating Refs, and treats any tag as a Pinned Ref that never updates. Many projects publish their components as GitHub Releases or version tags, and their users want to run the released versions. These users have two options today, and both are bad:

- They pin a tag. Then they never hear about the next release, and have to find it and edit the YAML themselves.
- They follow `main`. Then they get an update for every commit, including unreleased and broken work in progress.

## Solution

The ref a user writes says what they want to follow:

- **Tag Ref** (a ref naming a Version Tag): follows the repository's Releases, and its Version Tags when it has no Releases. When a newer one appears, the update entity offers `name v1.4.0 → v1.5.0`. Install writes `v1.5.0` into the YAML, then compiles and flashes.
- **Unset Ref** (no ref): follows Releases, else Version Tags, else the default branch's Latest Commit. When the integration moves it to a version, the YAML gains a ref, and the source is a Tag Ref from then on.
- **Floating Ref** (a branch, or a tag that isn't a Version Tag such as `stable`): follows Latest Commit as today and is never rewritten.
- **Pinned Ref** (a commit): never updates.

Which mode a source is in is re-checked on every poll. Only strictly newer versions are offered. Release notes show the GitHub release text for Releases, and the commit list for Version Tags.

## User Stories

1. As a user whose YAML pins a component with `ref: v1.4.0`, I want to be told when `v1.5.0` is released, so that I don't have to watch the repository myself.
2. As that user, I want Install to change the ref to `v1.5.0` and flash, so that updating takes one press instead of a YAML edit followed by a press.
3. As a user whose source has no ref, I want it to follow the repository's Releases when it has them, so that I get released versions and not every commit on the default branch.
4. As that user, I want the YAML to show the version I'm running after an Install, so that the config says what's on the device.
5. As a user whose source has no ref and whose repository has no Releases but has version tags, I want the newest Version Tag followed, so that I still get versions instead of commits.
6. As a user whose source has no ref, in a repository with neither Releases nor version tags, I want the default branch's Latest Commit followed as today, so that nothing gets worse for me.
7. As a user whose source names a branch, I want commits followed exactly as today and my YAML never touched, so that a branch I chose on purpose stays my choice.
8. As a user whose source names a commit, I want no update ever offered, so that a hard pin stays a hard pin.
9. As a user whose source names a non-version tag like `stable` or `nightly`, I want an update when the maintainer moves that tag to another commit, and my ref kept as written, so that moving tags behave like branches.
10. As a user, I want draft and prerelease GitHub Releases ignored, so that I'm offered only what the maintainer calls released.
11. As a user, I want tags like `v1.2.0-rc1`, `test-foo` or `firmware-2024-05` ignored when choosing a version, so that only real versions are offered.
12. As a user, I want versions ordered by number (`v1.10.0` after `v1.9.0`), not by tag date or GitHub's "Latest" marker, so that "newest" means what it says.
13. As a user whose repository publishes its first Release while I follow its Version Tags, I want the source to start following Releases on the next poll, so that I don't have to reconfigure anything.
14. As a user whose repository gets its first version tag while I follow commits on an Unset Ref, I want the source to switch to Version Tags on the next poll, without an update being offered for a version I effectively already run.
15. As a user, I want no update offered when the newest version is older than or equal to what I run (for example, I run `v2.0.0-rc1` and the newest Release is `v1.9.0`), so that I'm never downgraded.
16. As a user, I want GitHub being rate-limited or down not to change which mode my source is in, so that the entity doesn't flip between Release and tag versions from poll to poll.
17. As a user, I want a warning logged when GitHub can't be asked about Releases, so that I know why a new Release isn't showing up.
18. As a user with many GitHub sources, I want the existing GitHub token option to also cover the Releases check, so that I can raise the rate limit in one place.
19. As a user of a source on GitLab, Gitea, Forgejo or a self-hosted git server, I want Version Tags followed, so that I still get versions without a host-specific API.
20. As a user, I want the entity to show `name v1.4.0 → v1.5.0` for followed sources and keep commit hashes for branch sources, so that I can read the update at a glance.
21. As a user, I want the release notes to show each newer Release's GitHub text, newest first, so that I can read the changelog before flashing.
22. As a user following Version Tags on GitHub, I want the release notes to list the commits between my tag and the new one, so that I still see what changed.
23. As a user whose ref is written through a substitution (`ref: ${fw_ver}`) used only there, I want Install to rewrite the substitution's value, so that my substitution setup keeps working.
24. As a user whose ref is written in an `!include`d or local-package file, I want Install to rewrite that file, so that the edit lands where the ref actually lives.
25. As a user whose ref text is shared with something else (a substitution also used for `project.version`, an include's `vars:`, a ref built from several parts), I want the source reported as untrackable with the reason, so that the integration never silently edits something else I wrote.
26. As a user who uses the `github://owner/repo@v1.4.0` shorthand, I want Install to rewrite the `@v1.4.0` part, and to add `@v1.5.0` when there's none, so that the shorthand keeps working.
27. As a user who edited the YAML in the Device Builder after the integration last read it, I want Install to refuse instead of overwriting my edit, so that I never lose work.
28. As a user who changed the tag in the YAML by hand but didn't flash, I want the entity to still show the version the device runs as installed, and Install to build the tag I wrote, so that my hand edit and the update agree.
29. As a user whose Install failed after the ref was rewritten, I want the rewritten YAML kept and the update still offered, so that pressing Install again just retries.
30. As a user, I want Install to build the version the entity showed when I pressed it, even if a newer one appears during the build, so that I get what I agreed to.
31. As a user whose Tracked Config has both branch and tag sources, I want one Install to rewrite every outdated followed source and compile once, so that I don't flash several times.
32. As a user whose followed sources have no `refresh: always`, I want no Refresh Policy Repair issue for them, so that I'm not nagged about a setting that doesn't matter for tags.
33. As a user whose Unset Ref follows commits, I want the Refresh Policy Repair issue to still apply, so that commit tracking stays correct.
34. As a user upgrading the integration with an existing Tag Ref that is already behind the newest Release, I want the update offered right away, so that I learn about versions I missed.
35. As a user upgrading with an existing Unset Ref whose repository has Releases, I want no update offered until the next Release, so that the switch doesn't claim I run a version I may not.
36. As a user whose Install is re-attached after a Home Assistant restart, I want the Installed Tag recorded on success just as for a fresh Install, so that a restart doesn't lose track of what's on the device.
37. As a user, I want the Installed Tag recorded only when an Install succeeds, so that the entity never claims a version that wasn't flashed.

## Implementation Decisions

### Classifying a Git Source

- A Git Source's ref falls into one of five kinds:
  - **Pinned Ref:** a commit sha. Recognised as today.
  - **Floating Ref:** a branch, or a tag that isn't a Version Tag. A ref that exists as both a branch and a tag is a branch, as today.
  - **Tag Ref:** a Version Tag.
  - **Unset Ref:** no ref.
- **Version Tag:** a tag name with an optional leading `v`/`V`, followed by one to four dot-separated non-negative integers and nothing else. Versions are compared as integer tuples padded with zeros, so `v1.2` equals `1.2.0`. Equal versions are never newer than each other.
- **Release:** a GitHub release that isn't a draft or prerelease and whose tag is a Version Tag. Remotes not on GitHub have no Releases.
- **Followed version** of a Tag Ref or Unset Ref:
  - the newest Release, if the repository has any Release;
  - otherwise, the newest Version Tag, if it has any;
  - otherwise, for an Unset Ref, its Latest Commit, as today. A Tag Ref whose repository lost every Version Tag shows no update.
- **Identity:** a followed source's identity (its key in the store and in the entity's data) doesn't include its ref. It is the repository URL, so rewriting the ref doesn't make it a new source. If one Tracked Config refers to the same repository as a followed source at different refs, that source is reported as untrackable.

### Asking the remotes

- **Git remote module:** in addition to resolving a ref, it lists a remote's tags with their peeled commits, using one `ls-remote --tags` call. It does this only for sources that need tags.
- **GitHub module:** gains a call that lists a repository's Releases (tag name, draft and prerelease flags, title, body, page URL). It uses the same token handling and the same error type as the compare call. The answer distinguishes "this repository has no Releases" from "GitHub couldn't be asked". A refusal, a rate limit, a 404 or a timeout all count as "couldn't be asked".
- **Last known answer:** the poller keeps the last known Releases answer for each source. When GitHub can't be asked, it keeps that answer and logs a warning once, like other remote failures. It falls back to Version Tags only once GitHub answers that there are no Releases.
- **No answer yet:** if a GitHub source has never had an answer about Releases, it shows no update until one arrives. This decision was made while writing the spec; see Further Notes.

### Installed state

- **Installed Tag store:** the Tracked Config's store gains an Installed Tag per followed source, next to the Installed Commits. A successful Install records it, including a re-attached Install (the re-attach record carries the planned tags as well as the commits).
- **Seeding:**
  - **Tag Ref:** seeded with the tag the YAML names.
  - **Unset Ref**, when it first follows a version (at first discovery, after an upgrade, or after switching from commits): seeded with the version it follows at that moment, so that no update is offered until the next one.
  - **Existing entries:** Installed Commits are seeded as today.
- **Update condition:** a followed source has an update when its followed version is strictly newer than its Installed Tag. While it follows commits, an Unset Ref behaves exactly as Floating Refs do today.

### Rewriting the YAML

- **Discovery** also returns, for each Tag Ref and Unset Ref, where its ref's text is written:
  - the file;
  - the exact text span of the ref scalar, the `@ref` part of a shorthand, or the substitution value it comes from;
  - or, for an Unset Ref, where a ref would be inserted (after the `url:` line at the same indentation, or appended as `@<tag>` to a shorthand).
- **Untrackable cases:** the source is reported as untrackable with a reason when its ref text:
  - comes from an include's `vars:`;
  - is built from several parts;
  - comes from a substitution that is used elsewhere or defined in more than one place;
  - is an Unset Ref in a flow-style mapping, where a ref can't be inserted safely.
- **Write command:** the Device Builder client gains `devices/update_config` with `configuration`, `content` and `expected`. A `precondition_failed` refusal comes back as a distinct error.
- **Rewrite edits:** a rewrite changes only the located span, leaving comments, formatting and every other byte unchanged. Its `expected` is the file text that the pre-install refresh just read.
- **Install plan:** the plan carries the commits to record (as today), the Installed Tags to record, and the rewrites. Rewrites are taken from what the entity showed when Install was pressed.
- **Order of an Install:** pre-install check, then rewrites (one write per file, in order), then the optional Clean Build, compile and upload.
- **Failed write:** a refused write, or one that fails because the file changed meanwhile, ends the Install as `Failed` at a new first stage ("update YAML") with a clear error, and nothing is compiled. Writes that already succeeded are kept.
- **Failed compile or upload:** keeps the rewritten YAML and records nothing (ADR-0007).
- **Pre-install check:** the Refresh Policy check skips Tag Refs, and Unset Refs while they follow a version.

### Display

- The composite version shows a followed source as `name <version>` instead of `name <short sha>`. The release summary line reads `name v1.4.0 → v1.5.0`.
- **Release notes** for a source following Releases have one sub-section per Release newer than the Installed Tag, up to the followed one, newest first. Each sub-section has the Release's title, a link and its body.
- **Release notes** for a source following Version Tags on GitHub use the existing compare between the two tags' commits. Off GitHub, they show only the version change.
- **Options flow:** the GitHub token's description says it is also used to check for Releases.

## Testing Decisions

- **What makes a good test:** it checks only behaviour a user or the Device Builder can see:
  - the entity's state, installed and latest versions, summary and release notes;
  - Repair issues, Install errors and notifications;
  - the YAML text the fake Device Builder holds after an Install.

  Tests never assert on the store's layout or on private helpers.
- **Primary seam:** the whole integration in Home Assistant, as in the existing Git Source, Install, release-notes and update tests:
  - real git repositories on disk as remotes, using the existing test repo helper, which already makes tags;
  - the fake Device Builder serving files from its map;
  - the fake GitHub.

  It covers user stories 1–9, 13–18, 20–22, 27–37.
- **Fake extensions** needed for that seam:
  - **Fake Device Builder:** answers `devices/update_config` by updating its file map, and refuses with `precondition_failed` when `expected` doesn't match. A test can change a file between the pre-install read and the write.
  - **Fake GitHub:** serves a repository's Releases list (including drafts, prereleases and non-version tags), can answer "no Releases", and can be told to fail or rate-limit.
- **Secondary seam:** discovery from raw YAML, a pure function tested as in the existing discovery tests. It covers ref classification and the rewrite location for every YAML shape:
  - inline ref
  - shorthand with and without `@ref`
  - ref from a substitution, used once or shared
  - include `vars:`
  - included file
  - local package
  - remote package
  - flow-style mapping
  - Unset Ref insertion
  - the same repository at two refs

  It covers user stories 23–26 and the untrackable cases.
- **Version ordering** (user stories 10–12 and 15) is tested through those two seams, with no third seam: version-shaped and non-version tags in a test repository, and Releases with drafts, prereleases and odd tag names in the fake GitHub.
- **Prior art:** the existing Git Source poll tests (discovery, Latest Commits, seeding, recording), the release-notes tests with the fake GitHub, the Install tests with transcripts, the pre-install check tests, and the discovery unit tests.

## Out of Scope

- Releases from GitLab, Gitea, Forgejo or other hosts' APIs. They fall back to Version Tags.
- Opting in to prereleases, or following a version range (for example "1.x only").
- Moving a Floating Ref (branch) to Releases.
- Downgrades, and choosing a specific older version to install.
- Rewriting refs inside remote packages' own files. Those files are never read (ADR-0006).
- Rolling back a rewritten YAML after a failed Install.
- Refs that depend on `!secret`. They stay untrackable.

## Further Notes

- These decisions were made while writing the spec, beyond what was settled in the grilling session. Confirm them before or during implementation:
  - A GitHub source that has never had an answer about Releases shows no update until one arrives, instead of guessing from tags.
  - A followed source's identity is its repository URL, not its ref, so the Installed Tag survives a rewrite.
  - The same repository followed at two different refs in one Tracked Config is reported as untrackable.
  - An Unset Ref in a flow-style mapping is untrackable for following, because a ref can't be inserted safely.
- ESPHome's clone cache is keyed by `url@ref`, so a rewritten tag is always fetched fresh, whatever its Refresh Policy (ADR-0004 note).
- GitHub's unauthenticated limit is 60 requests an hour. With the default 30-minute poll, that means about 30 GitHub sources checked for Releases, plus compares. The token option lifts the limit.
