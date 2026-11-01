# 11: HACS packaging + release workflow

**What to build:** Make the integration installable and updatable through HACS as a custom repository. Add `hacs.json`, keep the manifest version in step with releases, and add a GitHub workflow that validates and publishes a release. The user adds the GitHub origin.

**Blocked by:** 02 (Tracer bullet: manual-URL entry → update entity showing ESPHome version)

**Status:** closed

- [x] `hacs.json` declares the integration, with the minimum HA version set to the current stable at first release.
- [x] The manifest carries a version that matches the release tag.
- [ ] CI runs the test suite plus HACS and hassfest validation on pushes and pull requests.
- [ ] Pushing a version tag creates a GitHub release.
- [x] The README explains adding the repo to HACS, the two Device Builder location modes, and the `ota:` and `refresh: always` requirements.

## Comments

### 2026-11-01: closing

**What exists:**
- `hacs.json` declares a minimum HA version of 2026.10.0.
- The manifest is at version 0.1.0, with `codeowners`, `documentation` and `issue_tracker` pointing at `nilsauf/esphome-git-updates`.
- `.github/workflows/ci.yml` runs mypy, pytest, hassfest and HACS validation (brands ignored).
- `.github/workflows/release.yml` runs on `v*` tags. It reuses CI, checks that the tag matches the manifest version, refuses to publish while the word `OWNER` appears in the manifest or README, and then creates the GitHub release.
- `tests/test_packaging.py` checks that `hacs.json`, the manifest and `pyproject.toml` agree, and that no `OWNER` placeholder remains.

**Verified locally:** `hacs.json`, the manifest version (it matches `pyproject.toml`, and the release workflow enforces the tag match), and the README contents.

**Not yet verified (unticked):** "CI runs…" and "Pushing a version tag creates a GitHub release". The repo has no remote yet. They are verified on the first push to `github.com/nilsauf/esphome-git-updates` and the first `v0.1.0` tag. Before that tag, give the GitHub repo a description, topics and enabled issues, or HACS validation fails.
