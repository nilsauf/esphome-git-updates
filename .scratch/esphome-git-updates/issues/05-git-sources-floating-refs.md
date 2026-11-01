# 05: Git Sources: discover Floating Refs and track Latest Commit

**What to build:** The update entity also offers an update when any tracked Git Source's Latest Commit differs from its Installed Commit.

- **Discovery:** Git Sources are discovered from the Tracked Config's raw YAML, read over `/ws` with `devices/get_config` for the Configuration and every file it includes (ADR-0006, amended). Substitutions are applied, and `!include` and local `packages:` files are followed. `external_components` and remote `packages:` entries count; remote packages are tracked themselves but not recursed into. Only Floating Refs are tracked. Pinned Refs (tag or sha) and local or path sources are ignored.
- **Latest Commit:** found with `git ls-remote` for every remote.
- **Version display:** the entity's version is a composite of the ESPHome version and the short sha of each Git Source. `release_summary` lists one line per changed Source, e.g. "ESPHome 2026.10.1 → 2026.10.2; marsrelay abc1234 → def5678".
- **Recording:** on a successful Install, each Git Source's Installed Commit is the Latest Commit observed when Install was pressed (ADR-0003).
- **Seeding:** a Git Source with no Installed Commit, at entry creation or when it first appears in the YAML, is seeded with its current Latest Commit.

**Blocked by:** 03 (Install: compile + upload with notifications)

**Status:** closed

- [x] Parser unit tests cover substitutions, `!include`, local `packages:`, remote `packages:` (not recursed), Floating vs Pinned Refs, and local/path sources.
- [x] The Configuration and its `!include` / local `packages:` files are read over `/ws` with `devices/get_config`, never from the filesystem.
- [x] `secrets.yaml` is never read; a Git Source whose URL or ref depends on `!secret` is logged as untrackable and ignored.
- [x] A refused read of an included file raises the protocol-drift Repair issue from 02.
- [x] Latest Commits are polled with `git ls-remote` every 30 min.
- [x] Installed Commits are kept in the entry's store and written only on Install success, using the commits seen when Install was pressed.
- [x] Newly seen Git Sources are seeded with their Latest Commit and produce no update until the next push.
- [x] The composite installed/latest version strings and `version_is_newer` ("differs") are covered by unit tests.
- [x] `release_summary` shows one line per changed Source.
- [x] An unreachable remote does not break the entity; the Source keeps its last known Latest Commit and the failure is logged.

## Comments

### 2026-11-01: agent

**What exists:**

- `git_sources.py`, the parser. It has no Home Assistant imports and reads every file through a callable.
  - It applies substitutions, including ones that refer to other substitutions, with the main config winning over packages.
  - It follows `!include`, with `vars` and as a `<<:` merge key, and local `packages:` in map or list form, nested.
  - It lists remote `packages:` as Sources without reading them.
  - It reports untrackable entries: anything depending on `!secret`, and `!include_dir_*` under `packages:` or `external_components:`, since directories can't be listed over `/ws`.
- `git_remote.py` runs `git ls-remote` with no prompts and a 60 s timeout. It refuses URLs starting with `-`.
- `git_coordinator.py`, the `GitSourcesCoordinator`. Every 30 min it reads the YAML over `/ws`, then runs ls-remote on every remote.
  - Seeding and pruning go through `TrackedConfigStore.async_seed_installed_commits`.
- `versions.py` holds the composite version strings and `release_summary`.
- `ProtocolIssue` in `coordinator.py`: both pollers share the one protocol-drift issue. Each reports and clears its own problem, so a healthy ESPHome poll does not hide a refused include.
- Tests:
  - `test_git_sources.py`, `test_git_remote.py`: real git repos in `tmp_path`, via `tests/git_repo.py`.
  - `test_versions.py`.
  - `test_git_updates.py`: end to end, with the fake `/ws` server answering `devices/get_config` from a `files` map.

**Decisions:**

- **Tags vs branches.** The YAML can't tell a tag from a branch, so `git ls-remote` decides; only commit-sha refs are classed as pinned in the parser. The criterion "Parser unit tests cover … Floating vs Pinned Refs" is therefore met by the parser tests (sha) together with `test_git_remote.py` (tag). A ref of 7–40 lowercase hex characters counts as a commit, so a branch named like `20261031` is not tracked.
- **Source identity.** The key is `url@ref` (`@HEAD` for the default branch). Changing a Source's URL or ref makes it a new Source, which is seeded and shows no update. Sources no longer in the YAML are forgotten, so one that comes back is seeded afresh.
- **Version string.** `2026.10.1 (marsrelay abc1234, sensors 1234567)`, in YAML order. Same-named Sources get `@ref`. "Differs" compares these strings, so it compares 7-character shas.
- **Unreachable remotes.** They keep their last known Latest Commit. A failure is logged once, and so is the recovery. A remote that has never answered is left out of the version, so an Install does not record it either; it is seeded once it answers.
- **Repair issue.**
  - A refused read of an *included* file raises the existing protocol-drift issue id under a new translation key, `include_refused`, whose text also mentions a removed or renamed include. A typo'd include path looks the same as a refusal: both are `not_found`.
  - A failed read of the Configuration itself, or YAML that won't parse, is only logged.
- **Setup.** The Git Sources poller's first refresh is not a `first_refresh`, so Git Sources that can't be read never hold up the ESPHome update.

**Open points:**

- Ticket 06 needs each Source's `refresh:`. `GitSource` doesn't carry it yet.
- Sources on tags are still sent to ls-remote every 30 min (one call each).
