# 05: Follow GitHub Releases

**What to build:** For a Tag Ref on GitHub, the followed version is the newest Release. Drafts, prereleases and Releases whose tag is not a Version Tag are ignored, and Releases are ordered by version, never by GitHub's "Latest" marker. The source falls back to Version Tags only once GitHub answers that the repository has no Releases. If GitHub can't be asked (rate limit, outage, 404, timeout), the last answer is kept and one warning is logged, so the mode doesn't flip between polls. A source with no answer yet shows no update. Release notes show one section per Release newer than the Installed Tag, up to the followed one, newest first, each with its title, link and body. The existing GitHub token option is used for this call, and its description says so. Hosts other than GitHub keep following Version Tags.

**Blocked by:** 02

**Status:** done

- [x] The fake GitHub serves a Releases list, including drafts, prereleases and non-version tags, can answer "no Releases", and can be told to fail or rate-limit.
- [x] With Releases `v1.5.0` and `v1.6.0-rc1` (prerelease) and tag `v1.7.0` without a Release, a Tag Ref on `v1.4.0` shows `→ v1.5.0`.
- [x] A repository whose first Release appears while it is followed by tags switches to Releases on the next poll.
- [x] When GitHub fails after a successful answer, the shown update doesn't change and a single warning is logged; recovery is logged at info.
- [x] A GitHub source never answered shows no update; once GitHub answers "no Releases" it follows Version Tags.
- [x] Release notes contain each newer Release's body, newest first.
- [ ] Install after a Release-driven update writes the Release's tag (via ticket 03's rewrite). Pending 03: the entity's latest version (`VersionedGitSource.offered`, which 03's rewrite uses) is the Release's tag, as tested; the end-to-end Install test still has to be added once 03 is merged.

## Comments

- `github.releases(session, repo, token) -> tuple[Release, ...]` lists GitHub's first page (`per_page=100`), drafts and prereleases included; `[]` means "no Releases", and any refusal, rate limit, 404, timeout or malformed answer raises `GitHubError`. `compare` and `releases` share `_headers(token)`.
- `releases.GitHubReleases` (owned by `GitSourcesCoordinator`) asks on every poll for each `VersionedGitSource` on GitHub, keeps only Releases (not draft, not prerelease, Version Tag), keeps the last answer per url while GitHub fails, warns once per url and logs recovery at info. `get(url)` is `()` off GitHub and `None` while GitHub has never answered.
- `VersionedGitSource.releases: tuple[Release, ...] | None` (default `()`). `offered(installed)` follows the newest Release if there is any, else the newest Version Tag, and offers nothing while `releases is None`. `releases_between(installed, offered)` feeds `SourceCommits.releases`, which the release notes render as `#### [title](url)\nbody` per Release instead of the commit list; the summary still carries the commit count from the compare between the two tags' commits.
- For ticket 06: an Unset Ref can be returned from `_ask_remote` as a `VersionedGitSource` too; `_with_releases` then fills in its Releases with no change. 06 must decide what an Unset Ref does while `releases is None` (spec: no update), and handle "no Releases and no Version Tags" falling back to Latest Commit, which needs the Releases answer before choosing between `VersionedGitSource` and `TrackedGitSource` (today Releases are asked only after `_ask_remote`). Seeding an Unset Ref's Installed Tag must use `offered(...)` once the answer is known, not before.
- The `on_github` and `fake_github` fixtures moved to `tests/conftest.py`, and `GITHUB_URL` to `tests/fake_github.py`. `FakeGitHub.release(repo, tag, title=, body=, draft=, prerelease=)` publishes one; `releases_authorizations` records the Releases requests apart from the compare ones.
- The token option's description, the `Options` comment and the README now say the token also covers the Releases check.
