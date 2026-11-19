"""What each Followed Source follows, polled through in-memory remote, Releases and
installed record: no Home Assistant, no Device Builder, no git."""

from __future__ import annotations

from collections.abc import Collection, Mapping

from custom_components.esphome_git_updates.following import FollowedSources, Snapshot
from custom_components.esphome_git_updates.git_remote import (
    GitRemoteError,
    RemoteRef,
    RemoteRefKind,
    RemoteTags,
)
from custom_components.esphome_git_updates.git_sources import discover_git_sources
from custom_components.esphome_git_updates.github import Comparison, GitHubError, Release
from custom_components.esphome_git_updates.versions import ShownSource

URL = "https://github.com/someone/marsrelay.git"
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


def on_ref(ref: str | None, *, url: str = URL, refresh: str = "always") -> str:
    """A Tracked Config with one external component from *url* at *ref* (None: unset)."""
    ref_line = f"      ref: {ref}\n" if ref is not None else ""
    return (
        "esphome:\n  name: node\nota:\n  - platform: esphome\n"
        f"external_components:\n  - source:\n      type: git\n      url: {url}\n"
        f"{ref_line}    refresh: {refresh}\n"
    )


async def test_a_floating_ref_follows_its_latest_commit() -> None:
    following = Following()
    following.remote.branch(URL, "dev", "c1")
    await following.poll(on_ref("dev"))

    following.remote.branch(URL, "dev", "c2")
    await following.poll()

    assert following.shown() == (ShownSource("marsrelay", "c1", "c2"),)
