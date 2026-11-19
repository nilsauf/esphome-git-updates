# 03: Keep the HA GitHub tests to the wiring

Status: ready-for-agent

**What to build:** as specified in `../spec.md` under "Testing Decisions" → "Full HA setup". The tests from 01 and 02 now cover paging, the token, comparison caching and failure. Trim `tests/test_github_releases.py` and `tests/test_release_notes.py` (and any other HA test that only repeats them) to the wiring.

**Blocked by:** 01, 02

- [ ] Kept or added at HA level:
  - release notes render each newer Release, and the commits of a changed Git Source;
  - changing the GitHub token option without reloading the entry reaches GitHub's next request (replaces the two separate token tests);
  - opening release notes with a missing comparison asks GitHub again and refreshes the entity.
- [ ] Deleted: the HA tests of paging, comparison caching and comparison failure, and the two separate token tests. Before deleting each, check case by case that a test in `tests/test_github.py` or `tests/test_following.py` covers its rule; add one there if not.
- [ ] Any other HA test that only repeats a Followed Sources or adapter case is deleted after the same check. Note in this ticket's Comments which tests were deleted and where each rule is now covered.
- [ ] The full test suite passes, and `ruff` is clean.

## Comments

**Kept at HA level** (all in `tests/test_release_notes.py`; `tests/test_github_releases.py` is deleted and its one kept test moved here):

- `test_a_github_source_lists_its_commits_since_the_installed_one`: the commits of a changed Git Source. Its "only asked once" assertion is dropped; compare-once is `test_following.py::test_a_change_is_compared_once_while_its_commits_stay_the_same`.
- `test_release_notes_show_each_newer_release_newest_first` (from `test_github_releases.py`): each newer Release. The non-version `test-foo` release is dropped; it is `test_github.py::test_drafts_prereleases_and_tags_that_are_no_version_are_no_releases`.
- `test_a_github_token_set_without_reloading_the_entry_is_sent_with_the_next_request` (new): sets the token through the options flow after setup and checks the next compare carries it. Replaces the two separate token tests.
- `test_opening_release_notes_asks_github_again_for_a_missing_comparison`: trimmed from `test_a_failing_github_api_falls_back_to_the_sha_change_and_is_asked_again`, without its log assertion.

**Deleted, and where each rule is covered now:**

- `test_github_releases.py::test_a_full_first_page_without_a_release_is_followed_by_the_next_page` → `test_github.py::test_a_full_page_without_a_release_is_followed_by_the_next_page`.
- `test_github_releases.py::test_pages_without_a_release_are_asked_for_only_so_far_and_a_warning_says_so` → `test_github.py::test_pages_without_a_release_are_asked_for_at_most_five_times_and_warned_about_once`; following Version Tags when there are no Releases → `test_following.py::test_a_tag_ref_without_releases_follows_its_highest_version_tag`.
- `test_github_releases.py::test_releases_are_asked_for_one_page_when_it_holds_a_release` → `test_github.py` test of the same name.
- `test_github_releases.py::test_every_github_failure_keeps_the_last_answer_rather_than_meaning_none` → `test_github.py::test_a_refusal_is_a_github_error` (403, 429, 404, 500) and `test_following.py::test_github_failing_keeps_its_last_answer`.
- `test_github_releases.py::test_the_github_token_option_is_sent_with_the_releases_request` and `test_release_notes.py::test_the_github_token_option_is_sent_with_the_compare_request` → `test_github.py::test_the_token_read_at_each_request_is_sent` and the new HA token test.
- `test_github_releases.py::test_a_tag_ref_off_github_follows_version_tags_without_asking_github` → `test_github.py::test_a_url_not_on_github_is_never_asked_about` and `test_following.py::test_a_tag_ref_without_releases_follows_its_highest_version_tag`.
- `test_release_notes.py::test_github_that_cannot_be_reached_falls_back_to_the_sha_change` → `test_github.py::test_an_unreachable_github_is_a_github_error`, `test_following.py::test_a_compare_failure_does_not_fail_the_poll`, and the uncompared rendering in `test_versions.py::test_the_release_notes_show_only_the_sha_change_of_an_uncompared_source`.
- `test_release_notes.py::test_a_remote_not_on_github_shows_the_sha_change_without_asking_github` → `test_github.py::test_a_url_not_on_github_is_never_asked_about`, `test_following.py::test_compare_missing_without_a_change_to_compare_has_nothing_arrive`.
- `test_release_notes.py::test_a_tag_ref_on_github_lists_the_commits_between_the_two_tags` → **added** `test_following.py::test_a_tag_ref_is_compared_between_the_commits_of_its_two_tags`; no existing test compared a Tag Ref's change. Rendering commits under a version label is in the kept Releases test and in `test_versions.py`.
- `test_release_notes.py::test_a_tag_ref_off_github_shows_only_the_version_change` → `test_github.py::test_a_url_not_on_github_is_never_asked_about` and `test_versions.py::test_the_release_notes_show_only_the_sha_change_of_an_uncompared_source`.
- `test_git_updates.py::test_a_poll_that_fails_after_asking_the_remotes_keeps_what_the_last_poll_saw`: it patched the private `git._followed.async_compare_missing`. Its rule belongs to `FollowedSources.async_poll`: `test_following.py::test_a_poll_whose_block_fails_is_dropped` already covers a failure inside the block. **Added** `test_following.py::test_an_unexpected_compare_error_fails_the_poll_and_drops_it` for the path the HA test actually exercised, a non-`GitHubError` from compare during the poll. To do that, `InMemoryGitHub` gained `compare_raises`. That a failed poll is not a successful refresh is Home Assistant's `DataUpdateCoordinator`, not ours.

Other HA tests using the fake GitHub (`test_unset_refs.py::test_install_appends_the_release_to_a_shorthand`) test Install wiring, not a Followed Sources or adapter case, and are kept. `pyproject.toml`'s mypy file list drops `tests/test_github_releases.py`.

The two added Followed Sources tests characterise behaviour that already existed, so they passed on first run. Each was checked to fail under a mutation of `following.py`: swapping compare's base and head, and swallowing the unexpected error. The new HA token test fails when the token is read once at construction.
