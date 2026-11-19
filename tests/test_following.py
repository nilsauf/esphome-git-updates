"""What each Followed Source follows, polled through in-memory remote, Releases and
installed record: no Home Assistant, no Device Builder, no git."""

from __future__ import annotations

from collections.abc import Collection, Mapping

import pytest

from custom_components.esphome_git_updates.comparisons import SourceChange
from custom_components.esphome_git_updates.following import FollowedSources, Snapshot
from custom_components.esphome_git_updates.git_remote import (
    GitRemoteError,
    RemoteRef,
    RemoteRefKind,
    RemoteTags,
)
from custom_components.esphome_git_updates.git_sources import GitSource, discover_git_sources
from custom_components.esphome_git_updates.github import Comparison, GitHubError, Release
from custom_components.esphome_git_updates.install import InstallPlan, YamlRewrite
from custom_components.esphome_git_updates.versions import ShownSource

URL = "https://github.com/someone/marsrelay.git"
OTHER = "https://github.com/someone/sensors.git"
CONFIGURATION = "node.yaml"


class Remote:
    """Git remotes by url: each with its branches (None: the default branch) and tags."""

    def __init__(self) -> None:
        self.branches: dict[str, dict[str | None, str]] = {}
        self.tags: dict[str, dict[str, str]] = {}
        self.failing: set[str] = set()

    def branch(self, url: str, name: str, commit: str, *, default: bool = True) -> None:
        branches = self.branches.setdefault(url, {})
        branches[name] = commit
        if default:
            branches[None] = commit
        self.tags.setdefault(url, {})

    def tag(self, url: str, name: str, commit: str) -> None:
        self.tags.setdefault(url, {})[name] = commit

    async def resolve(self, url: str, ref: str | None, *, tags: bool) -> RemoteTags:
        if url in self.failing:
            raise GitRemoteError(f"{url} cannot be reached")
        branches = self.branches.get(url, {})
        all_tags = self.tags.get(url, {})
        found: RemoteRef | None = None
        if ref in branches:
            found = RemoteRef(RemoteRefKind.BRANCH, branches[ref])
        elif ref is not None and ref in all_tags:
            found = RemoteRef(RemoteRefKind.TAG, all_tags[ref])
        elif ref is None:
            raise GitRemoteError(f"{url} has no default branch")
        listed = dict(all_tags) if tags else {t: c for t, c in all_tags.items() if t == ref}
        return RemoteTags(found, listed)


class Releases:
    """GitHub's Releases by url; a url not listed has none (or is not on GitHub)."""

    def __init__(self) -> None:
        self.listed: dict[str, tuple[Release, ...]] = {}
        self.failing: set[str] = set()

    def release(self, url: str, tag: str) -> Release:
        release = Release(tag, f"Release {tag}", "", f"{url}/releases/{tag}", False, False)
        self.listed[url] = (*self.listed.get(url, ()), release)
        return release

    async def releases(self, url: str) -> tuple[Release, ...]:
        if url in self.failing:
            raise GitHubError("GitHub cannot be reached")
        return self.listed.get(url, ())


class Record:
    """The installed record, in memory."""

    def __init__(self) -> None:
        self.commits: dict[str, str] = {}
        self.tags: dict[str, str] = {}

    def installed_commit(self, source_key: str) -> str | None:
        return self.commits.get(source_key)

    def installed_tag(self, url: str) -> str | None:
        return self.tags.get(url)

    async def async_seed_commits(self, seeds: Mapping[str, str], present: Collection[str]) -> None:
        self.commits = {key: sha for key, sha in self.commits.items() if key in present}
        for key, sha in seeds.items():
            self.commits.setdefault(key, sha)

    async def async_seed_tags(self, seeds: Mapping[str, str], present: Collection[str]) -> None:
        self.tags = {url: tag for url, tag in self.tags.items() if url in present}
        for url, tag in seeds.items():
            self.tags.setdefault(url, tag)


class NoComparisons:
    def get(self, key: str, installed: str | None, latest: str) -> Comparison | None:
        return None


class Following:
    """A FollowedSources over in-memory adapters, polling a YAML held in memory."""

    def __init__(self) -> None:
        self.remote = Remote()
        self.releases = Releases()
        self.record = Record()
        self.followed = FollowedSources(self.remote, self.releases, self.record, "node")
        self.yaml = ""

    async def poll(self, yaml: str | None = None) -> Snapshot:
        if yaml is not None:
            self.yaml = yaml
        files: dict[str, str] = {}

        async def read(path: str) -> str:
            files[path] = {CONFIGURATION: self.yaml}[path]
            return files[path]

        discovery = await discover_git_sources(CONFIGURATION, read)
        return await self.followed.async_poll(discovery, files)

    def shown(self) -> tuple[ShownSource, ...]:
        return self.followed.shown(NoComparisons())


def source(ref: str | None, *, url: str = URL, refresh: str = "always") -> str:
    """An external component from *url* at *ref* (None: an Unset Ref)."""
    ref_line = f"      ref: {ref}\n" if ref is not None else ""
    return f"  - source:\n      type: git\n      url: {url}\n{ref_line}    refresh: {refresh}\n"


def config(*sources: str) -> str:
    """A Tracked Config with the external components *sources*."""
    return "esphome:\n  name: node\nota:\n  - platform: esphome\nexternal_components:\n" + (
        "".join(sources) or "  []\n"
    )


def on_ref(ref: str | None) -> str:
    """A Tracked Config with one external component from URL at *ref* (None: unset)."""
    return config(source(ref))


def versions(installed: str, offered: str, *releases: Release) -> ShownSource:
    return ShownSource("marsrelay", installed, offered, versions=True, releases=releases)


def _logged(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    """What FollowedSources logged, by level, in order."""
    return [
        (record.levelname, record.getMessage())
        for record in caplog.records
        if record.name == "custom_components.esphome_git_updates.following"
    ]


# Each follow mode.


async def test_a_floating_ref_follows_its_latest_commit() -> None:
    following = Following()
    following.remote.branch(URL, "dev", "c1")
    await following.poll(on_ref("dev"))

    following.remote.branch(URL, "dev", "c2")
    await following.poll()

    assert following.shown() == (ShownSource("marsrelay", "c1", "c2"),)


async def test_a_pinned_ref_is_not_followed() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c1")

    await following.poll(on_ref("0123456789abcdef0123456789abcdef01234567"))

    assert following.shown() == ()


async def test_a_tag_ref_follows_releases_over_newer_version_tags() -> None:
    following = Following()
    following.remote.tag(URL, "v1.0.0", "c1")
    following.remote.tag(URL, "v1.1.0", "c2")
    following.remote.tag(URL, "v2.0.0", "c3")
    following.releases.release(URL, "v1.0.0")
    newer = following.releases.release(URL, "v1.1.0")

    await following.poll(on_ref("v1.0.0"))

    assert following.shown() == (versions("v1.0.0", "v1.1.0", newer),)


async def test_a_tag_ref_without_releases_follows_its_highest_version_tag() -> None:
    following = Following()
    following.remote.tag(URL, "v1.0.0", "c1")
    following.remote.tag(URL, "v1.10.0", "c3")
    following.remote.tag(URL, "v1.9.0", "c2")
    following.remote.tag(URL, "nightly", "c4")

    await following.poll(on_ref("v1.0.0"))

    assert following.shown() == (versions("v1.0.0", "v1.10.0"),)


async def test_a_tag_ref_that_is_a_branch_follows_its_latest_commit() -> None:
    following = Following()
    following.remote.branch(URL, "v1.0", "c1", default=False)
    following.remote.tag(URL, "v1.1", "c9")
    await following.poll(on_ref("v1.0"))

    following.remote.branch(URL, "v1.0", "c2", default=False)
    await following.poll()

    assert following.shown() == (ShownSource("marsrelay", "c1", "c2"),)


async def test_an_unset_ref_follows_releases_starting_from_the_newest() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c9")
    following.remote.tag(URL, "v1.0.0", "c1")
    following.remote.tag(URL, "v1.1.0", "c2")
    following.releases.release(URL, "v1.0.0")
    await following.poll(on_ref(None))
    assert following.shown() == (versions("v1.0.0", "v1.0.0"),)

    newer = following.releases.release(URL, "v1.2.0")
    following.remote.tag(URL, "v1.2.0", "c3")
    await following.poll()

    assert following.shown() == (versions("v1.0.0", "v1.2.0", newer),)


async def test_an_unset_ref_without_releases_follows_version_tags() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c9")
    following.remote.tag(URL, "v1.0.0", "c1")
    await following.poll(on_ref(None))

    following.remote.tag(URL, "v1.1.0", "c2")
    await following.poll()

    assert following.shown() == (versions("v1.0.0", "v1.1.0"),)


async def test_an_unset_ref_without_version_tags_follows_its_latest_commit() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c1")
    following.remote.tag(URL, "nightly", "c0")
    await following.poll(on_ref(None))

    following.remote.branch(URL, "main", "c2")
    await following.poll()

    assert following.shown() == (ShownSource("marsrelay", "c1", "c2"),)


async def test_an_unset_ref_where_no_ref_can_be_inserted_follows_its_latest_commit() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c1")
    following.remote.tag(URL, "v1.0.0", "c0")
    await following.poll(config(f"  - source: {{type: git, url: {URL}}}\n"))

    following.remote.branch(URL, "main", "c2")
    following.remote.tag(URL, "v1.1.0", "c2")
    await following.poll()

    assert following.shown() == (ShownSource("marsrelay", "c1", "c2"),)


async def test_a_moved_version_tag_offers_nothing() -> None:
    following = Following()
    following.remote.tag(URL, "v1.2.3", "c1")
    await following.poll(on_ref("v1.2.3"))

    following.remote.tag(URL, "v1.2.3", "c2")
    await following.poll()

    assert following.shown() == (versions("v1.2.3", "v1.2.3"),)
    assert following.followed.changes() == []


async def test_a_tag_ref_is_never_offered_an_equal_or_older_version() -> None:
    following = Following()
    following.remote.tag(URL, "v1.9.0", "c1")
    following.remote.tag(URL, "v2.0.0", "c2")
    await following.poll(on_ref("v2.0.0"))

    # Equal to v2.0.0, and a prerelease: neither is newer.
    following.remote.tag(URL, "2.0", "c3")
    following.remote.tag(URL, "v2.1.0-rc1", "c4")
    await following.poll()

    assert following.shown() == (versions("v2.0.0", "v2.0.0"),)


async def test_a_first_release_switches_a_tag_ref_from_version_tags_to_releases() -> None:
    following = Following()
    following.remote.tag(URL, "v1.4.0", "c1")
    following.remote.tag(URL, "v1.5.0", "c2")
    following.remote.tag(URL, "v1.6.0", "c3")
    await following.poll(on_ref("v1.4.0"))
    assert following.shown() == (versions("v1.4.0", "v1.6.0"),)

    release = following.releases.release(URL, "v1.5.0")
    await following.poll()

    assert following.shown() == (versions("v1.4.0", "v1.5.0", release),)


# Remotes and GitHub that cannot be asked.


async def test_a_remote_that_fails_keeps_its_last_known_state() -> None:
    following = Following()
    following.remote.branch(URL, "dev", "c1")
    await following.poll(on_ref("dev"))
    following.remote.branch(URL, "dev", "c2")
    await following.poll()

    following.remote.failing.add(URL)
    await following.poll()

    assert following.shown() == (ShownSource("marsrelay", "c1", "c2"),)


async def test_an_unset_ref_that_switched_to_versions_keeps_them_while_its_remote_fails() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c1")
    await following.poll(on_ref(None))
    following.remote.tag(URL, "v1.0.0", "c2")
    await following.poll()
    following.remote.tag(URL, "v1.1.0", "c3")
    await following.poll()

    following.remote.failing.add(URL)
    await following.poll()

    assert following.shown() == (versions("v1.0.0", "v1.1.0"),)


async def test_a_first_version_tag_switches_an_unset_ref_to_versions_without_offering_it() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c1")
    await following.poll(on_ref(None))

    following.remote.tag(URL, "v1.0.0", "c2")
    following.remote.branch(URL, "main", "c2")
    await following.poll()

    assert following.shown() == (versions("v1.0.0", "v1.0.0"),)
    assert following.followed.changes() == []


async def test_nothing_newer_is_offered_while_github_has_never_answered() -> None:
    following = Following()
    following.remote.tag(URL, "v1.0.0", "c1")
    following.remote.tag(URL, "v2.0.0", "c2")
    following.releases.release(URL, "v1.0.0")
    newer = following.releases.release(URL, "v1.1.0")
    following.releases.failing.add(URL)
    await following.poll(on_ref("v1.0.0"))
    assert following.shown() == (versions("v1.0.0", "v1.0.0"),)

    following.releases.failing.clear()
    await following.poll()

    assert following.shown() == (versions("v1.0.0", "v1.1.0", newer),)


async def test_github_failing_keeps_its_last_answer() -> None:
    following = Following()
    following.remote.tag(URL, "v1.0.0", "c1")
    following.remote.tag(URL, "v2.0.0", "c2")
    following.releases.release(URL, "v1.0.0")
    newer = following.releases.release(URL, "v1.1.0")
    await following.poll(on_ref("v1.0.0"))

    following.releases.failing.add(URL)
    await following.poll()

    assert following.shown() == (versions("v1.0.0", "v1.1.0", newer),)


async def test_a_tag_ref_github_answers_has_no_releases_follows_its_version_tags() -> None:
    following = Following()
    following.remote.tag(URL, "v1.4.0", "c1")
    following.remote.tag(URL, "v1.5.0", "c2")
    following.releases.failing.add(URL)
    await following.poll(on_ref("v1.4.0"))
    assert following.shown() == (versions("v1.4.0", "v1.4.0"),)

    following.releases.failing.clear()
    await following.poll()

    assert following.shown() == (versions("v1.4.0", "v1.5.0"),)


async def test_an_unset_ref_shows_no_version_until_github_answers() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c9")
    following.remote.tag(URL, "v1.4.0", "c1")
    following.releases.failing.add(URL)
    await following.poll(on_ref(None))
    assert following.shown() == (versions("", ""),)
    assert following.record.tags == {}

    following.remote.tag(URL, "v1.5.0", "c2")
    following.releases.failing.clear()
    await following.poll()

    # GitHub answered "no Releases" at last: it follows Version Tags from v1.5.0 on.
    assert following.shown() == (versions("v1.5.0", "v1.5.0"),)
    assert following.record.tags == {URL: "v1.5.0"}


async def test_a_failing_remote_is_warned_about_once_and_its_return_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    following = Following()
    following.remote.branch(URL, "dev", "c1")
    await following.poll(on_ref("dev"))

    following.remote.failing.add(URL)
    await following.poll()
    await following.poll()
    following.remote.failing.clear()
    await following.poll()

    assert _logged(caplog) == [
        (
            "WARNING",
            (
                f"node: cannot get the Latest Commit of marsrelay, keeping the last "
                f"one known: {URL} cannot be reached"
            ),
        ),
        ("INFO", "node: marsrelay is reachable again"),
    ]


async def test_a_failing_github_is_warned_about_once_and_its_return_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    following = Following()
    following.remote.tag(URL, "v1.0.0", "c1")
    await following.poll(on_ref("v1.0.0"))

    following.releases.failing.add(URL)
    await following.poll()
    await following.poll()
    following.releases.failing.clear()
    await following.poll()

    assert _logged(caplog) == [
        (
            "WARNING",
            (
                f"node: cannot ask GitHub for the Releases of {URL}, keeping the last "
                "answer: GitHub cannot be reached"
            ),
        ),
        ("INFO", f"node: GitHub lists the Releases of {URL} again"),
    ]


# Seeding the installed record (ADR-0003).


async def test_new_sources_are_seeded_with_what_they_start_from() -> None:
    following = Following()
    following.remote.branch(URL, "dev", "c1")
    following.remote.tag(OTHER, "v1.0.0", "c2")
    following.remote.tag(OTHER, "v1.1.0", "c3")

    await following.poll(config(source("dev"), source("v1.0.0", url=OTHER)))

    assert following.record.commits == {f"{URL}@dev": "c1"}
    assert following.record.tags == {OTHER: "v1.0.0"}


async def test_removed_sources_are_forgotten() -> None:
    following = Following()
    following.remote.branch(URL, "dev", "c1")
    following.remote.tag(OTHER, "v1.0.0", "c2")
    await following.poll(config(source("dev"), source("v1.0.0", url=OTHER)))

    await following.poll(config())

    assert following.record.commits == {}
    assert following.record.tags == {}


async def test_a_source_added_later_is_seeded_and_offered_its_next_push() -> None:
    following = Following()
    following.remote.branch(URL, "dev", "c1")
    following.remote.branch(OTHER, "dev", "s1")
    await following.poll(config(source("dev")))

    await following.poll(config(source("dev"), source("dev", url=OTHER)))
    assert following.record.commits == {f"{URL}@dev": "c1", f"{OTHER}@dev": "s1"}

    following.remote.branch(OTHER, "dev", "s2")
    await following.poll()

    assert following.followed.changes() == [SourceChange(f"{OTHER}@dev", OTHER, "s1", "s2")]


async def test_an_unset_ref_that_followed_commits_before_starts_from_the_newest_release() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c9")
    following.remote.tag(URL, "v1.4.0", "c1")
    following.remote.tag(URL, "v1.5.0", "c2")
    following.releases.release(URL, "v1.4.0")
    following.releases.release(URL, "v1.5.0")
    # Recorded before the upgrade, when every Unset Ref followed its Latest Commit.
    following.record.commits[f"{URL}@HEAD"] = "c0"

    await following.poll(on_ref(None))

    assert following.shown() == (versions("v1.5.0", "v1.5.0"),)
    assert following.record.tags == {URL: "v1.5.0"}


async def test_a_tag_changed_in_the_yaml_by_hand_leaves_the_installed_tag_unchanged() -> None:
    following = Following()
    following.remote.tag(URL, "v1.4.0", "c1")
    following.remote.tag(URL, "v1.5.0", "c2")
    await following.poll(on_ref("v1.4.0"))

    await following.poll(on_ref("v1.5.0"))

    assert following.record.tags == {URL: "v1.4.0"}
    assert following.shown() == (versions("v1.4.0", "v1.5.0"),)


async def test_a_tag_ref_whose_remote_fails_keeps_its_installed_tag() -> None:
    following = Following()
    following.record.tags[URL] = "v1.1.0"
    following.remote.failing.add(URL)

    await following.poll(on_ref("v1.0.0"))

    assert following.record.tags == {URL: "v1.1.0"}


# Queries read the installed record when asked.


async def test_shown_and_changes_reflect_an_install_recorded_after_the_poll() -> None:
    following = Following()
    following.remote.branch(URL, "dev", "c1")
    await following.poll(on_ref("dev"))
    following.remote.branch(URL, "dev", "c2")
    await following.poll()
    assert following.followed.changes() == [SourceChange(f"{URL}@dev", URL, "c1", "c2")]

    following.record.commits[f"{URL}@dev"] = "c2"

    assert following.shown() == (ShownSource("marsrelay", "c2", "c2"),)
    assert following.followed.changes() == []


async def test_an_install_plan_after_an_install_with_no_poll_rewrites_nothing() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c9")
    following.remote.tag(URL, "v1.0.0", "c1")
    await following.poll(on_ref(None))
    following.remote.tag(URL, "v1.1.0", "c2")
    await following.poll()
    assert following.followed.install_plan("OTA", False).rewrites != ()

    following.record.tags[URL] = "v1.1.0"

    plan = following.followed.install_plan("OTA", False)
    assert plan.rewrites == ()
    assert plan.tags == {URL: "v1.1.0"}


async def test_an_install_plan_rewrites_the_files_as_its_poll_read_them() -> None:
    following = Following()
    following.remote.tag(URL, "v1.0.0", "c1")
    following.remote.tag(URL, "v1.1.0", "c2")
    await following.poll(on_ref("v1.0.0"))
    read = "# edited\n" + on_ref("v1.0.0")

    await following.poll(read)

    assert following.followed.install_plan("192.168.1.2", True) == InstallPlan(
        {},
        "192.168.1.2",
        True,
        tags={URL: "v1.1.0"},
        rewrites=(YamlRewrite(CONFIGURATION, "# edited\n" + on_ref("v1.1.0"), read),),
    )


async def test_an_install_plan_moves_a_tag_ref_to_the_newest_release_not_a_newer_tag() -> None:
    following = Following()
    following.remote.tag(URL, "v1.4.0", "c1")
    following.remote.tag(URL, "v1.5.0", "c2")
    # A Version Tag without a Release.
    following.remote.tag(URL, "v1.7.0", "c3")
    following.releases.release(URL, "v1.4.0")
    following.releases.release(URL, "v1.5.0")
    await following.poll(on_ref("v1.4.0"))

    assert following.followed.install_plan("OTA", False) == InstallPlan(
        {},
        "OTA",
        False,
        tags={URL: "v1.5.0"},
        rewrites=(YamlRewrite(CONFIGURATION, on_ref("v1.5.0"), on_ref("v1.4.0")),),
    )


async def test_an_install_plan_leaves_an_unset_ref_unwritten_without_a_newer_version() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c9")
    following.remote.tag(URL, "v1.4.0", "c1")
    await following.poll(on_ref(None))

    assert following.followed.install_plan("OTA", False) == InstallPlan(
        {}, "OTA", False, tags={URL: "v1.4.0"}
    )


async def test_an_install_plan_records_a_latest_commit_without_touching_its_ref() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c1")
    following.remote.tag(URL, "v1.0.0", "c0")
    await following.poll(config(f"  - source: {{type: git, url: {URL}}}\n"))
    following.remote.branch(URL, "main", "c2")
    await following.poll()

    assert following.followed.install_plan("OTA", False) == InstallPlan(
        {f"{URL}@HEAD": "c2"}, "OTA", False
    )


# Refresh Policy (ADR-0004).


async def test_only_sources_following_latest_commit_need_refresh_always() -> None:
    following = Following()
    following.remote.branch(URL, "dev", "c1")
    following.remote.tag(OTHER, "v1.0.0", "c2")

    await following.poll(
        config(source("dev", refresh="1d"), source("v1.0.0", url=OTHER, refresh="1d"))
    )

    assert following.followed.wrong_refresh_policy() == (GitSource("marsrelay", URL, "dev"),)


async def test_an_unset_ref_needs_refresh_always_only_while_it_follows_its_latest_commit() -> None:
    following = Following()
    following.remote.branch(URL, "main", "c1")
    # A tag that is no Version Tag does not make it follow versions.
    following.remote.tag(URL, "stable", "c1")
    unset = on_ref(None).replace("    refresh: always\n", "")
    await following.poll(unset)
    assert following.followed.wrong_refresh_policy() == (GitSource("marsrelay", URL, None),)

    following.remote.tag(URL, "v1.4.0", "c2")
    await following.poll()

    assert following.followed.wrong_refresh_policy() == ()
