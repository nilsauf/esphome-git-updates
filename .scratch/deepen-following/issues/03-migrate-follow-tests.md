# 03: Move follow-decision tests to the module, delete their HA versions

**What to build:** Go through `test_unset_refs.py`, `test_tag_install.py`, `test_github_releases.py` and `test_git_updates.py`. For each test that only checks a follow decision (what is followed, offered, seeded or kept), make sure `tests/test_following.py` covers it, then delete the HA-level test. Replace tests; don't keep both layers.

**Blocked by:** 02

**Status:** resolved

- [ ] These remain at HA level:
  - one end-to-end case per ref kind (Floating, Tag, Unset, Pinned);
  - YAML rewrites during an Install;
  - real `git ls-remote` via `git_repo`;
  - GitHub paging and the token option against `fake_github`;
  - Repair issues for the Refresh Policy.
- [ ] Every deleted test's scenario is named by a test in `tests/test_following.py`. List the mapping under `## Comments`.
- [ ] Coverage of `following.py` does not drop.

## Comments

Deleted HA-level test → `tests/test_following.py` test naming its scenario:

`test_unset_refs.py`
- `test_an_unset_ref_without_versions_follows_commits_and_needs_refresh_always` → `test_an_unset_ref_without_version_tags_follows_its_latest_commit`, `test_an_unset_ref_needs_refresh_always_only_while_it_follows_its_latest_commit` (new)
- `test_a_first_version_tag_switches_to_versions_without_offering_it` → `test_a_first_version_tag_switches_an_unset_ref_to_versions_without_offering_it`, `test_an_unset_ref_needs_refresh_always_only_while_it_follows_its_latest_commit` (new)
- `test_an_install_without_a_newer_version_leaves_an_unset_ref_unwritten` → `test_an_install_plan_leaves_an_unset_ref_unwritten_without_a_newer_version` (new)
- `test_an_existing_unset_ref_with_releases_offers_nothing_until_the_next_release` → `test_an_unset_ref_that_followed_commits_before_starts_from_the_newest_release` (new), `test_an_unset_ref_follows_releases_starting_from_the_newest`
- `test_an_unset_ref_on_github_shows_no_version_until_github_answers` → `test_an_unset_ref_shows_no_version_until_github_answers` (new)
- `test_a_flow_style_unset_ref_keeps_following_commits_and_says_why` → `test_an_unset_ref_where_no_ref_can_be_inserted_follows_its_latest_commit`, `test_an_install_plan_records_a_latest_commit_without_touching_its_ref` (new). The "flow-style" reason is covered in `test_git_sources.py`; the poller's "cannot be tracked" log by `test_the_yaml_and_its_includes_are_read_from_the_device_builder_but_never_secrets`.

`test_github_releases.py`
- `test_a_tag_ref_follows_the_newest_release_not_newer_tags_or_prereleases` → `test_a_tag_ref_follows_releases_over_newer_version_tags` (prerelease filtering stays covered by the paging tests against `fake_github`)
- `test_install_moves_the_tag_ref_to_the_newest_release_not_a_newer_tag` → `test_an_install_plan_moves_a_tag_ref_to_the_newest_release_not_a_newer_tag` (new)
- `test_a_repository_publishing_its_first_release_is_followed_by_releases_next_poll` → `test_a_first_release_switches_a_tag_ref_from_version_tags_to_releases` (new)
- `test_a_source_github_never_answered_shows_no_update_until_it_answers` → `test_a_tag_ref_github_answers_has_no_releases_follows_its_version_tags` (new), `test_nothing_newer_is_offered_while_github_has_never_answered`
- `test_a_failing_github_keeps_the_last_answer_and_warns_once`: trimmed, not deleted, to `test_every_github_failure_keeps_the_last_answer_rather_than_meaning_none` (403/429/404/500 are errors, not "no Releases": adapter behaviour). Warn-once and recovery logging → `test_a_failing_github_is_warned_about_once_and_its_return_logged` (new); keeping the last answer → `test_github_failing_keeps_its_last_answer`.

`test_git_updates.py`
- `test_a_new_entry_seeds_its_git_sources_with_their_latest_commit` → `test_new_sources_are_seeded_with_what_they_start_from`
- `test_a_git_source_added_to_the_yaml_later_is_seeded_and_updates_from_its_next_push` → `test_a_source_added_later_is_seeded_and_offered_its_next_push` (new)
- `test_an_unreachable_remote_keeps_its_last_known_latest_commit_and_is_logged` → `test_a_remote_that_fails_keeps_its_last_known_state`, `test_a_failing_remote_is_warned_about_once_and_its_return_logged` (new). An unreachable real remote raising `GitRemoteError` is covered in `test_git_remote.py`.
- `test_a_tag_ref_shows_the_highest_version_tag_ordered_by_version_not_by_date` → `test_a_tag_ref_without_releases_follows_its_highest_version_tag`
- `test_a_tag_ref_is_never_offered_an_equal_or_older_version` → `test_a_tag_ref_is_never_offered_an_equal_or_older_version` (new)
- `test_a_tag_changed_in_the_yaml_by_hand_leaves_the_installed_tag_unchanged` → `test_a_tag_changed_in_the_yaml_by_hand_leaves_the_installed_tag_unchanged` (new)
- `test_a_moved_version_tag_or_a_commit_never_produces_an_update` (parametrized): reduced to the Pinned end-to-end case `test_a_pinned_ref_is_never_followed`; the moved Version Tag → `test_a_moved_version_tag_offers_nothing` (new), the commit → `test_a_pinned_ref_is_not_followed`.

`test_tag_install.py`: nothing deleted. Every test there is an Install (YAML rewrite, failure, re-attach, the two-step protocol) or the Tag end-to-end case.

Kept end-to-end per ref kind: Floating `test_a_non_version_tag_is_a_floating_ref_that_follows_its_commit`, Tag `test_install_moves_the_tag_ref_to_the_version_shown_and_records_it`, Unset `test_install_inserts_the_ref_after_the_url_and_then_follows_it_as_a_tag_ref`, Pinned `test_a_pinned_ref_is_never_followed`.

Coverage of `following.py` (full suite, line): 98% (4 missed) before, 99% (3 missed) after. `test_following.py` alone: 97% → 99%. The remaining misses are the re-raise of unexpected exceptions and the "no branch or tag" error.
