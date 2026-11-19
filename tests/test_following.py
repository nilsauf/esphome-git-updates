"""What each Followed Source follows, polled through in-memory remote, GitHub and
installed record: no Home Assistant, no Device Builder, no git."""

from __future__ import annotations

from collections.abc import Collection, Mapping

import pytest

from custom_components.esphome_git_updates.following import (
    FollowedSources,
    Refusal,
    Snapshot,
    SourceChange,
)
from custom_components.esphome_git_updates.git_remote import (
    GitRemoteError,
    RemoteRef,
    RemoteRefKind,
    RemoteTags,
)
from custom_components.esphome_git_updates.git_sources import GitSource, discover_git_sources
from custom_components.esphome_git_updates.github import (
    Commit,
    Comparison,
    GitHubError,
    Release,
)
from custom_components.esphome_git_updates.install import InstallPlan, YamlRewrite
from custom_components.esphome_git_updates.versions import ShownSource

URL = "https://github.com/someone/marsrelay.git"
OTHER = "https://github.com/someone/sensors.git"
CONFIGURATION = "node.yaml"


class InMemoryRemotes:
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

    async def resolve_ref(self, url: str, ref: str | None) -> RemoteRef:
        found = (await self._resolve(url, ref, tags=False)).ref
        if found is None:
            raise GitRemoteError(f"{url} has no branch or tag {ref}")
        return found

    async def resolve_ref_and_tags(self, url: str, ref: str | None) -> RemoteTags:
        return await self._resolve(url, ref, tags=True)

    async def _resolve(self, url: str, ref: str | None, *, tags: bool) -> RemoteTags:
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


class InMemoryGitHub:
    """GitHub's Releases and comparisons by url; a url not listed has no Releases, and
    no comparison (as one not on GitHub). It records each comparison asked for."""

    def __init__(self) -> None:
        self.listed: dict[str, tuple[Release, ...]] = {}
        self.comparisons: dict[tuple[str, str, str], Comparison] = {}
        self.compared: list[tuple[str, str, str]] = []
        # The urls whose Releases GitHub cannot list.
        self.failing: set[str] = set()
        # Whether GitHub cannot compare.
        self.compare_failing = False

    def release(self, url: str, tag: str) -> Release:
        release = Release(tag, f"Release {tag}", "", f"{url}/releases/{tag}", False, False)
        self.listed[url] = (*self.listed.get(url, ()), release)
        return release

    def comparison(self, url: str, base: str, head: str) -> Comparison:
        """List the commit *head* as the one after *base* at *url*."""
        comparison = Comparison(1, (Commit(head, f"Commit {head}", f"{url}/commit/{head}"),))
        self.comparisons[url, base, head] = comparison
        return comparison

    async def releases(self, url: str) -> tuple[Release, ...]:
        if url in self.failing:
            raise GitHubError("GitHub cannot be reached")
        return self.listed.get(url, ())

    async def compare(self, url: str, base: str, head: str) -> Comparison | None:
        self.compared.append((url, base, head))
        if self.compare_failing:
            raise GitHubError("GitHub cannot be reached")
        return self.comparisons.get((url, base, head))


class InMemoryRecord:
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


class InMemoryTrackedConfig:
    """A Tracked Config whose YAML is held in memory, its Followed Sources polled over
    in-memory remotes, GitHub and installed record."""

    def __init__(self) -> None:
        self.remote = InMemoryRemotes()
        self.github = InMemoryGitHub()
        self.record = InMemoryRecord()
        self.followed = FollowedSources(self.remote, self.github, self.record, lambda: "node")
        self.yaml = ""

    async def poll(self, yaml: str | None = None, *, then: Exception | None = None) -> Snapshot:
        """Poll, then raise *then* inside the poll's block, if given."""
        if yaml is not None:
            self.yaml = yaml
        files: dict[str, str] = {}

        async def read(path: str) -> str:
            files[path] = {CONFIGURATION: self.yaml}[path]
            return files[path]

        discovery = await discover_git_sources(CONFIGURATION, read)
        async with self.followed.async_poll(discovery, files) as snapshot:
            if then is not None:
                raise then
            return snapshot

    def shown(self) -> tuple[ShownSource, ...]:
        return self.followed.shown()


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


def plan(snapshot: Snapshot) -> InstallPlan:
    """The Install *snapshot* plans over the air, without a clean build."""
    plan_or_refusal = snapshot.install("OTA", False, "node")
    assert isinstance(plan_or_refusal, InstallPlan), plan_or_refusal
    return plan_or_refusal


def refusal(snapshot: Snapshot) -> Refusal:
    """Why *snapshot* refuses an Install over the air, without a clean build."""
    plan_or_refusal = snapshot.install("OTA", False, "node")
    assert isinstance(plan_or_refusal, Refusal), plan_or_refusal
    return plan_or_refusal


def _logged(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    """What FollowedSources logged, by level, in order."""
    return [
        (record.levelname, record.getMessage())
        for record in caplog.records
        if record.name == "custom_components.esphome_git_updates.following"
    ]


# Each follow mode.


async def test_a_floating_ref_follows_its_latest_commit() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))

    tracked.remote.branch(URL, "dev", "c2")
    await tracked.poll()

    assert tracked.shown() == (ShownSource("marsrelay", "c1", "c2"),)


async def test_a_pinned_ref_is_not_followed() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c1")

    await tracked.poll(on_ref("0123456789abcdef0123456789abcdef01234567"))

    assert tracked.shown() == ()


async def test_a_tag_ref_follows_releases_over_newer_version_tags() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.0.0", "c1")
    tracked.remote.tag(URL, "v1.1.0", "c2")
    tracked.remote.tag(URL, "v2.0.0", "c3")
    tracked.github.release(URL, "v1.0.0")
    newer = tracked.github.release(URL, "v1.1.0")

    await tracked.poll(on_ref("v1.0.0"))

    assert tracked.shown() == (versions("v1.0.0", "v1.1.0", newer),)


async def test_a_tag_ref_without_releases_follows_its_highest_version_tag() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.0.0", "c1")
    tracked.remote.tag(URL, "v1.10.0", "c3")
    tracked.remote.tag(URL, "v1.9.0", "c2")
    tracked.remote.tag(URL, "nightly", "c4")

    await tracked.poll(on_ref("v1.0.0"))

    assert tracked.shown() == (versions("v1.0.0", "v1.10.0"),)


async def test_a_tag_ref_that_is_a_branch_follows_its_latest_commit() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "v1.0", "c1", default=False)
    tracked.remote.tag(URL, "v1.1", "c9")
    await tracked.poll(on_ref("v1.0"))

    tracked.remote.branch(URL, "v1.0", "c2", default=False)
    await tracked.poll()

    assert tracked.shown() == (ShownSource("marsrelay", "c1", "c2"),)


async def test_an_unset_ref_follows_releases_starting_from_the_newest() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c9")
    tracked.remote.tag(URL, "v1.0.0", "c1")
    tracked.remote.tag(URL, "v1.1.0", "c2")
    tracked.github.release(URL, "v1.0.0")
    await tracked.poll(on_ref(None))
    assert tracked.shown() == (versions("v1.0.0", "v1.0.0"),)

    newer = tracked.github.release(URL, "v1.2.0")
    tracked.remote.tag(URL, "v1.2.0", "c3")
    await tracked.poll()

    assert tracked.shown() == (versions("v1.0.0", "v1.2.0", newer),)


async def test_an_unset_ref_without_releases_follows_version_tags() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c9")
    tracked.remote.tag(URL, "v1.0.0", "c1")
    await tracked.poll(on_ref(None))

    tracked.remote.tag(URL, "v1.1.0", "c2")
    await tracked.poll()

    assert tracked.shown() == (versions("v1.0.0", "v1.1.0"),)


async def test_an_unset_ref_without_version_tags_follows_its_latest_commit() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c1")
    tracked.remote.tag(URL, "nightly", "c0")
    await tracked.poll(on_ref(None))

    tracked.remote.branch(URL, "main", "c2")
    await tracked.poll()

    assert tracked.shown() == (ShownSource("marsrelay", "c1", "c2"),)


async def test_an_unset_ref_where_no_ref_can_be_inserted_follows_its_latest_commit() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c1")
    tracked.remote.tag(URL, "v1.0.0", "c0")
    await tracked.poll(config(f"  - source: {{type: git, url: {URL}}}\n"))

    tracked.remote.branch(URL, "main", "c2")
    tracked.remote.tag(URL, "v1.1.0", "c2")
    await tracked.poll()

    assert tracked.shown() == (ShownSource("marsrelay", "c1", "c2"),)


async def test_a_moved_version_tag_offers_nothing() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.2.3", "c1")
    await tracked.poll(on_ref("v1.2.3"))

    tracked.remote.tag(URL, "v1.2.3", "c2")
    await tracked.poll()

    assert tracked.shown() == (versions("v1.2.3", "v1.2.3"),)
    assert tracked.followed.changes() == []


async def test_a_tag_ref_is_never_offered_an_equal_or_older_version() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.9.0", "c1")
    tracked.remote.tag(URL, "v2.0.0", "c2")
    await tracked.poll(on_ref("v2.0.0"))

    # Equal to v2.0.0, and a prerelease: neither is newer.
    tracked.remote.tag(URL, "2.0", "c3")
    tracked.remote.tag(URL, "v2.1.0-rc1", "c4")
    await tracked.poll()

    assert tracked.shown() == (versions("v2.0.0", "v2.0.0"),)


async def test_a_first_release_switches_a_tag_ref_from_version_tags_to_releases() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.4.0", "c1")
    tracked.remote.tag(URL, "v1.5.0", "c2")
    tracked.remote.tag(URL, "v1.6.0", "c3")
    await tracked.poll(on_ref("v1.4.0"))
    assert tracked.shown() == (versions("v1.4.0", "v1.6.0"),)

    release = tracked.github.release(URL, "v1.5.0")
    await tracked.poll()

    assert tracked.shown() == (versions("v1.4.0", "v1.5.0", release),)


# Remotes and GitHub that cannot be asked.


async def test_a_remote_that_fails_keeps_its_last_known_latest_commit() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))
    tracked.remote.branch(URL, "dev", "c2")
    await tracked.poll()

    tracked.remote.failing.add(URL)
    await tracked.poll()

    assert tracked.shown() == (ShownSource("marsrelay", "c1", "c2"),)


async def test_an_unset_ref_that_switched_to_versions_keeps_them_while_its_remote_fails() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c1")
    await tracked.poll(on_ref(None))
    tracked.remote.tag(URL, "v1.0.0", "c2")
    await tracked.poll()
    tracked.remote.tag(URL, "v1.1.0", "c3")
    await tracked.poll()

    tracked.remote.failing.add(URL)
    await tracked.poll()

    assert tracked.shown() == (versions("v1.0.0", "v1.1.0"),)


async def test_a_first_version_tag_switches_an_unset_ref_to_versions_without_offering_it() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c1")
    await tracked.poll(on_ref(None))

    tracked.remote.tag(URL, "v1.0.0", "c2")
    tracked.remote.branch(URL, "main", "c2")
    await tracked.poll()

    assert tracked.shown() == (versions("v1.0.0", "v1.0.0"),)
    assert tracked.followed.changes() == []


async def test_nothing_newer_is_offered_while_github_has_never_answered() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.0.0", "c1")
    tracked.remote.tag(URL, "v2.0.0", "c2")
    tracked.github.release(URL, "v1.0.0")
    newer = tracked.github.release(URL, "v1.1.0")
    tracked.github.failing.add(URL)
    await tracked.poll(on_ref("v1.0.0"))
    assert tracked.shown() == (versions("v1.0.0", "v1.0.0"),)

    tracked.github.failing.clear()
    await tracked.poll()

    assert tracked.shown() == (versions("v1.0.0", "v1.1.0", newer),)


async def test_github_failing_keeps_its_last_answer() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.0.0", "c1")
    tracked.remote.tag(URL, "v2.0.0", "c2")
    tracked.github.release(URL, "v1.0.0")
    newer = tracked.github.release(URL, "v1.1.0")
    await tracked.poll(on_ref("v1.0.0"))

    tracked.github.failing.add(URL)
    await tracked.poll()

    assert tracked.shown() == (versions("v1.0.0", "v1.1.0", newer),)


async def test_a_tag_ref_github_answers_has_no_releases_follows_its_version_tags() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.4.0", "c1")
    tracked.remote.tag(URL, "v1.5.0", "c2")
    tracked.github.failing.add(URL)
    await tracked.poll(on_ref("v1.4.0"))
    assert tracked.shown() == (versions("v1.4.0", "v1.4.0"),)

    tracked.github.failing.clear()
    await tracked.poll()

    assert tracked.shown() == (versions("v1.4.0", "v1.5.0"),)


async def test_an_unset_ref_shows_no_version_until_github_answers() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c9")
    tracked.remote.tag(URL, "v1.4.0", "c1")
    tracked.github.failing.add(URL)
    await tracked.poll(on_ref(None))
    assert tracked.shown() == (versions("", ""),)
    assert tracked.record.tags == {}

    tracked.remote.tag(URL, "v1.5.0", "c2")
    tracked.github.failing.clear()
    await tracked.poll()

    # GitHub answered "no Releases" at last: it follows Version Tags from v1.5.0 on.
    assert tracked.shown() == (versions("v1.5.0", "v1.5.0"),)
    assert tracked.record.tags == {URL: "v1.5.0"}


async def test_a_failing_remote_is_warned_about_once_and_its_return_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))

    tracked.remote.failing.add(URL)
    await tracked.poll()
    await tracked.poll()
    tracked.remote.failing.clear()
    await tracked.poll()

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


async def test_a_failing_remote_of_a_source_following_versions_keeps_its_version_tags(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.0.0", "c1")
    await tracked.poll(on_ref("v1.0.0"))

    tracked.remote.failing.add(URL)
    await tracked.poll()

    assert _logged(caplog) == [
        (
            "WARNING",
            (
                f"node: cannot get the Version Tags of marsrelay, keeping the last "
                f"ones known: {URL} cannot be reached"
            ),
        ),
    ]


async def test_a_poll_whose_block_fails_is_dropped() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))
    tracked.remote.branch(URL, "dev", "c2")

    with pytest.raises(RuntimeError):
        await tracked.poll(then=RuntimeError("unexpected"))
    assert tracked.shown() == (ShownSource("marsrelay", "c1", "c1"),)

    # The next poll starts from the last one that was not dropped.
    tracked.remote.failing.add(URL)
    await tracked.poll()
    assert tracked.shown() == (ShownSource("marsrelay", "c1", "c1"),)


async def test_a_failing_github_is_warned_about_once_and_its_return_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.0.0", "c1")
    await tracked.poll(on_ref("v1.0.0"))

    tracked.github.failing.add(URL)
    await tracked.poll()
    await tracked.poll()
    tracked.github.failing.clear()
    await tracked.poll()

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


# Comparing changes on GitHub, for release notes.


async def test_a_change_is_compared_during_a_poll_and_shown_with_its_commits() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))
    comparison = tracked.github.comparison(URL, "c1", "c2")

    tracked.remote.branch(URL, "dev", "c2")
    await tracked.poll()

    assert tracked.shown() == (ShownSource("marsrelay", "c1", "c2", comparison),)


async def test_a_change_is_compared_once_while_its_commits_stay_the_same() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))
    tracked.github.comparison(URL, "c1", "c2")
    tracked.remote.branch(URL, "dev", "c2")
    await tracked.poll()

    await tracked.poll()
    assert not await tracked.followed.async_compare_missing()

    assert tracked.github.compared == [(URL, "c1", "c2")]


async def test_a_failed_comparison_is_asked_for_again_on_the_next_poll() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))
    comparison = tracked.github.comparison(URL, "c1", "c2")
    tracked.github.compare_failing = True
    tracked.remote.branch(URL, "dev", "c2")
    await tracked.poll()

    tracked.github.compare_failing = False
    await tracked.poll()

    assert tracked.github.compared == [(URL, "c1", "c2"), (URL, "c1", "c2")]
    assert tracked.shown() == (ShownSource("marsrelay", "c1", "c2", comparison),)


async def test_a_failed_comparison_is_asked_for_again_when_compare_missing_is_asked() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))
    comparison = tracked.github.comparison(URL, "c1", "c2")
    tracked.github.compare_failing = True
    tracked.remote.branch(URL, "dev", "c2")
    await tracked.poll()
    assert not await tracked.followed.async_compare_missing()

    tracked.github.compare_failing = False

    assert await tracked.followed.async_compare_missing()
    assert tracked.shown() == (ShownSource("marsrelay", "c1", "c2", comparison),)


async def test_compare_missing_without_a_change_to_compare_has_nothing_arrive() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))
    # GitHub lists nothing for it, as for a Source not on GitHub.
    tracked.remote.branch(URL, "dev", "c2")
    await tracked.poll()

    assert not await tracked.followed.async_compare_missing()
    assert tracked.shown() == (ShownSource("marsrelay", "c1", "c2"),)


async def test_a_comparison_whose_change_is_gone_is_forgotten() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))
    tracked.github.comparison(URL, "c1", "c2")
    tracked.remote.branch(URL, "dev", "c2")
    await tracked.poll()

    # The push is undone, then made again.
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll()
    tracked.remote.branch(URL, "dev", "c2")
    await tracked.poll()

    assert tracked.github.compared == [(URL, "c1", "c2"), (URL, "c1", "c2")]


async def test_a_compare_failure_does_not_fail_the_poll() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))
    tracked.github.compare_failing = True

    tracked.remote.branch(URL, "dev", "c2")
    await tracked.poll()

    assert tracked.shown() == (ShownSource("marsrelay", "c1", "c2"),)
    assert tracked.followed.changes() == [SourceChange(f"{URL}@dev", URL, "c1", "c2")]


async def test_a_failing_compare_is_warned_about_once_and_its_return_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    tracked.remote.branch(OTHER, "dev", "s1")
    await tracked.poll(config(source("dev"), source("dev", url=OTHER)))
    tracked.github.compare_failing = True
    tracked.remote.branch(URL, "dev", "c2")
    tracked.remote.branch(OTHER, "dev", "s2")

    await tracked.poll()
    await tracked.poll()
    await tracked.followed.async_compare_missing()
    tracked.github.compare_failing = False
    await tracked.poll()

    assert _logged(caplog) == [
        (
            "WARNING",
            (
                "node: release notes show only commit changes while GitHub cannot be "
                "asked: GitHub cannot be reached"
            ),
        ),
        ("INFO", "node: GitHub answers again"),
    ]


# Seeding the installed record (ADR-0003).


async def test_new_sources_are_seeded_with_what_they_start_from() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    tracked.remote.tag(OTHER, "v1.0.0", "c2")
    tracked.remote.tag(OTHER, "v1.1.0", "c3")

    await tracked.poll(config(source("dev"), source("v1.0.0", url=OTHER)))

    assert tracked.record.commits == {f"{URL}@dev": "c1"}
    assert tracked.record.tags == {OTHER: "v1.0.0"}


async def test_removed_sources_are_forgotten() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    tracked.remote.tag(OTHER, "v1.0.0", "c2")
    await tracked.poll(config(source("dev"), source("v1.0.0", url=OTHER)))

    await tracked.poll(config())

    assert tracked.record.commits == {}
    assert tracked.record.tags == {}


async def test_a_source_added_later_is_seeded_and_offered_its_next_push() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    tracked.remote.branch(OTHER, "dev", "s1")
    await tracked.poll(config(source("dev")))

    await tracked.poll(config(source("dev"), source("dev", url=OTHER)))
    assert tracked.record.commits == {f"{URL}@dev": "c1", f"{OTHER}@dev": "s1"}

    tracked.remote.branch(OTHER, "dev", "s2")
    await tracked.poll()

    assert tracked.followed.changes() == [SourceChange(f"{OTHER}@dev", OTHER, "s1", "s2")]


async def test_an_unset_ref_that_followed_commits_before_starts_from_the_newest_release() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c9")
    tracked.remote.tag(URL, "v1.4.0", "c1")
    tracked.remote.tag(URL, "v1.5.0", "c2")
    tracked.github.release(URL, "v1.4.0")
    tracked.github.release(URL, "v1.5.0")
    # Recorded before the upgrade, when every Unset Ref followed its Latest Commit.
    tracked.record.commits[f"{URL}@HEAD"] = "c0"

    await tracked.poll(on_ref(None))

    assert tracked.shown() == (versions("v1.5.0", "v1.5.0"),)
    assert tracked.record.tags == {URL: "v1.5.0"}


async def test_a_tag_changed_in_the_yaml_by_hand_leaves_the_installed_tag_unchanged() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.4.0", "c1")
    tracked.remote.tag(URL, "v1.5.0", "c2")
    await tracked.poll(on_ref("v1.4.0"))

    await tracked.poll(on_ref("v1.5.0"))

    assert tracked.record.tags == {URL: "v1.4.0"}
    assert tracked.shown() == (versions("v1.4.0", "v1.5.0"),)


async def test_a_tag_ref_whose_remote_fails_keeps_its_installed_tag() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.record.tags[URL] = "v1.1.0"
    tracked.remote.failing.add(URL)

    await tracked.poll(on_ref("v1.0.0"))

    assert tracked.record.tags == {URL: "v1.1.0"}


# Queries read the installed record when asked.


async def test_shown_and_changes_reflect_an_install_recorded_after_the_poll() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))
    tracked.remote.branch(URL, "dev", "c2")
    await tracked.poll()
    assert tracked.followed.changes() == [SourceChange(f"{URL}@dev", URL, "c1", "c2")]

    tracked.record.commits[f"{URL}@dev"] = "c2"

    assert tracked.shown() == (ShownSource("marsrelay", "c2", "c2"),)
    assert tracked.followed.changes() == []


async def test_an_install_after_an_install_with_no_poll_rewrites_nothing() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c9")
    tracked.remote.tag(URL, "v1.0.0", "c1")
    await tracked.poll(on_ref(None))
    tracked.remote.tag(URL, "v1.1.0", "c2")
    snapshot = await tracked.poll()
    assert plan(snapshot).rewrites != ()

    tracked.record.tags[URL] = "v1.1.0"

    assert plan(snapshot).rewrites == ()
    assert plan(snapshot).tags == {URL: "v1.1.0"}


async def test_an_install_rewrites_the_files_as_its_poll_read_them() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.0.0", "c1")
    tracked.remote.tag(URL, "v1.1.0", "c2")
    await tracked.poll(on_ref("v1.0.0"))
    read = "# edited\n" + on_ref("v1.0.0")

    snapshot = await tracked.poll(read)

    assert snapshot.install("192.168.1.2", True, "node") == InstallPlan(
        {},
        "192.168.1.2",
        True,
        tags={URL: "v1.1.0"},
        rewrites=(YamlRewrite(CONFIGURATION, "# edited\n" + on_ref("v1.1.0"), read),),
    )


async def test_an_install_is_of_its_own_poll_not_a_later_one() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    await tracked.poll(on_ref("dev"))
    tracked.remote.branch(URL, "dev", "c2")
    snapshot = await tracked.poll()

    tracked.remote.branch(URL, "dev", "c3")
    await tracked.poll()

    assert plan(snapshot) == InstallPlan({f"{URL}@dev": "c2"}, "OTA", False)


async def test_an_install_moves_a_tag_ref_to_the_newest_release_not_a_newer_tag() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.4.0", "c1")
    tracked.remote.tag(URL, "v1.5.0", "c2")
    # A Version Tag without a Release.
    tracked.remote.tag(URL, "v1.7.0", "c3")
    tracked.github.release(URL, "v1.4.0")
    tracked.github.release(URL, "v1.5.0")

    snapshot = await tracked.poll(on_ref("v1.4.0"))

    assert plan(snapshot) == InstallPlan(
        {},
        "OTA",
        False,
        tags={URL: "v1.5.0"},
        rewrites=(YamlRewrite(CONFIGURATION, on_ref("v1.5.0"), on_ref("v1.4.0")),),
    )


async def test_an_install_leaves_an_unset_ref_unwritten_without_a_newer_version() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c9")
    tracked.remote.tag(URL, "v1.4.0", "c1")

    snapshot = await tracked.poll(on_ref(None))

    assert plan(snapshot) == InstallPlan({}, "OTA", False, tags={URL: "v1.4.0"})


async def test_an_install_records_a_latest_commit_without_touching_its_ref() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c1")
    tracked.remote.tag(URL, "v1.0.0", "c0")
    await tracked.poll(config(f"  - source: {{type: git, url: {URL}}}\n    refresh: always\n"))
    tracked.remote.branch(URL, "main", "c2")

    snapshot = await tracked.poll()

    assert plan(snapshot) == InstallPlan({f"{URL}@HEAD": "c2"}, "OTA", False)


# Refusing an Install it cannot take over the air.


def without_ota(yaml: str) -> str:
    return yaml.replace("ota:\n  - platform: esphome\n", "")


NO_OTA = (
    "its YAML has no `ota:` platform `esphome` or `web_server`, so it cannot be "
    "updated over the air; flash it once over USB with OTA enabled"
)


def refused(*reasons: str) -> Refusal:
    return Refusal(f"Install of node refused: {'; and '.join(reasons)}. See Repairs.")


def stale(names: str) -> str:
    return (
        f"the Git Sources {names} lack `refresh: always`, so ESPHome may build a stale cached clone"
    )


async def test_an_install_without_ota_is_refused_naming_the_ota_platforms() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")

    snapshot = await tracked.poll(without_ota(on_ref("dev")))

    assert refusal(snapshot) == refused(NO_OTA)
    assert snapshot.check.ota_missing


async def test_an_install_whose_ota_a_remote_package_may_add_is_not_refused() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    tracked.remote.branch(OTHER, "main", "p1")
    package = (
        f"packages:\n  base:\n    url: {OTHER}\n    ref: main\n"
        "    refresh: always\n    files: [base.yaml]\n"
    )

    snapshot = await tracked.poll(package + without_ota(on_ref("dev")))

    assert isinstance(snapshot.install("OTA", False, "node"), InstallPlan)


async def test_an_install_of_a_floating_ref_without_refresh_always_is_refused_naming_it() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    tracked.remote.tag(OTHER, "v1.0.0", "c2")

    snapshot = await tracked.poll(
        config(source("dev", refresh="1d"), source("v1.0.0", url=OTHER, refresh="1d"))
    )

    assert refusal(snapshot) == refused(stale("marsrelay"))
    assert snapshot.check.wrong_refresh_policy == (GitSource("marsrelay", URL, "dev"),)


async def test_an_install_of_a_tag_that_is_no_version_tag_needs_refresh_always() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "stable", "c1")

    snapshot = await tracked.poll(on_ref("stable").replace("    refresh: always\n", ""))

    assert refusal(snapshot) == refused(stale("marsrelay"))


async def test_an_install_of_a_tag_ref_needs_no_refresh_policy() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.tag(URL, "v1.0.0", "c1")

    snapshot = await tracked.poll(on_ref("v1.0.0").replace("    refresh: always\n", ""))

    assert isinstance(snapshot.install("OTA", False, "node"), InstallPlan)


async def test_an_unset_ref_needs_refresh_always_only_while_it_follows_its_latest_commit() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "main", "c1")
    # A tag that is no Version Tag does not make it follow versions.
    tracked.remote.tag(URL, "stable", "c1")
    unset = on_ref(None).replace("    refresh: always\n", "")
    snapshot = await tracked.poll(unset)
    assert refusal(snapshot) == refused(stale("marsrelay"))

    tracked.remote.tag(URL, "v1.4.0", "c2")
    snapshot = await tracked.poll()

    assert isinstance(snapshot.install("OTA", False, "node"), InstallPlan)
    assert snapshot.check.wrong_refresh_policy == ()


async def test_an_install_failing_several_checks_is_refused_naming_every_reason() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    tracked.remote.branch(OTHER, "dev", "s1")

    snapshot = await tracked.poll(
        without_ota(config(source("dev", refresh="1d"), source("dev", url=OTHER, refresh="1d")))
    )

    assert refusal(snapshot) == refused(NO_OTA, stale("marsrelay, sensors"))


async def test_a_check_is_of_its_own_poll_not_a_later_one() -> None:
    tracked = InMemoryTrackedConfig()
    tracked.remote.branch(URL, "dev", "c1")
    snapshot = await tracked.poll(without_ota(config(source("dev", refresh="1d"))))

    later = await tracked.poll(on_ref("dev"))

    assert snapshot.check.ota_missing
    assert snapshot.check.wrong_refresh_policy == (GitSource("marsrelay", URL, "dev"),)
    assert not later.check.ota_missing
    assert later.check.wrong_refresh_policy == ()
