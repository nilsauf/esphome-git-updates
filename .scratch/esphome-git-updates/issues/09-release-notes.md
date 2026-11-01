# 09: Release notes

**What to build:** The update entity's release notes show what changed in each Git Source since the Installed Commit:
- **GitHub sources:** the commit list from the GitHub compare API, from installed to latest.
- **Other remotes:** only the sha change.
- **Unknown installed commit:** only the latest commit.

An optional GitHub token in the options flow raises the API rate limit and is used only for release notes. `release_summary` lines for GitHub sources also gain a commit count, e.g. "marsrelay abc1234 → def5678 (3 commits)".

**Blocked by:** 05 (Git Sources: discover Floating Refs and track Latest Commit)

**Status:** closed

- [x] The entity supports `RELEASE_NOTES` and returns notes covering every changed Source.
- [x] GitHub sources list the compare-API commits between Installed and Latest Commit.
- [x] Non-GitHub remotes show the sha change; an unknown Installed Commit shows only the Latest Commit.
- [x] An optional GitHub token option is used for compare-API calls when set.
- [x] A GitHub API failure (rate limit, network) falls back to the sha change rather than failing.
- [x] Tests cover all three cases and the API-failure fallback.

## Comments

### 2026-11-01: agent: release notes as built

- `github.py` (no HA imports) calls `GET /repos/{owner}/{repo}/compare/{installed}...{latest}`, with `Authorization: Bearer <token>` when the `github_token` option is set. A remote counts as GitHub when its URL is `https://github.com/owner/repo[.git]`, or the SSH or scp form; `github://` sources always are.
- `comparisons.py` (`GitHubComparisons`) asks once per (Installed Commit, Latest Commit) change. It runs at each git poll, so `release_summary` can show "(N commits)", and again when the release notes are opened if the poll's attempt failed. Any GitHub failure (non-200, network, timeout, bad JSON) is logged once and falls back to the commit change.
- The release notes are Markdown, with one `### <Source>` section per changed Source. ESPHome is included, since it is a Source.
- The integration tests run end to end. A github.com URL is mapped to a local repo through `GIT_CONFIG_*` `insteadOf`, and a fake compare API answers from that repo's `git log`.

**Caveats:**
- An unknown Installed Commit is tested only on `Versions`: seeding gives every tracked Source an Installed Commit, so the entity never shows one.
- Over 250 commits, GitHub lists only 250 unless paginated; the notes end with "… and N more". No pagination.
- After a force-push (compare status `diverged`), the count is only the commits ahead; the notes do not say the Installed Commit was dropped.
- The token is stored in the entry's options in plain text, as HA stores every option.
