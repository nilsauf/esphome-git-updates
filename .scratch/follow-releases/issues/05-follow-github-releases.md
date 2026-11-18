# 05: Follow GitHub Releases

**What to build:** For a Tag Ref on GitHub, the followed version is the newest Release. Drafts, prereleases and Releases whose tag is not a Version Tag are ignored, and Releases are ordered by version, never by GitHub's "Latest" marker. The source falls back to Version Tags only once GitHub answers that the repository has no Releases. If GitHub can't be asked (rate limit, outage, 404, timeout), the last answer is kept and one warning is logged, so the mode doesn't flip between polls. A source with no answer yet shows no update. Release notes show one section per Release newer than the Installed Tag, up to the followed one, newest first, each with its title, link and body. The existing GitHub token option is used for this call, and its description says so. Hosts other than GitHub keep following Version Tags.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] The fake GitHub serves a Releases list, including drafts, prereleases and non-version tags, can answer "no Releases", and can be told to fail or rate-limit.
- [ ] With Releases `v1.5.0` and `v1.6.0-rc1` (prerelease) and tag `v1.7.0` without a Release, a Tag Ref on `v1.4.0` shows `→ v1.5.0`.
- [ ] A repository whose first Release appears while it is followed by tags switches to Releases on the next poll.
- [ ] When GitHub fails after a successful answer, the shown update doesn't change and a single warning is logged; recovery is logged at info.
- [ ] A GitHub source never answered shows no update; once GitHub answers "no Releases" it follows Version Tags.
- [ ] Release notes contain each newer Release's body, newest first.
- [ ] Install after a Release-driven update writes the Release's tag (via ticket 03's rewrite).
