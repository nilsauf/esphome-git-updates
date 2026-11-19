"""The Followed Sources of one Tracked Config: what each Git Source follows, as last
seen on its remote, and what that means for the update entity and for an Install.

`FollowedSources` polls each Source's remote, and GitHub for the Releases of
those following versions, and decides anew on every poll what each follows: a
Tag Ref follows Releases, else Version Tags; an Unset Ref follows Releases,
else Version Tags, else its Latest Commit; a Floating Ref, or a Tag Ref that
turns out to be a branch, follows its Latest Commit. It seeds the installed
record with what new Sources start from, and forgets removed ones (ADR-0003).

No Home Assistant imports: the remote, GitHub and the installed record are
ports handed in.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Collection, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Protocol

from .comparisons import SourceChange
from .git_remote import GitRemoteError, RemoteRef, RemoteRefKind, RemoteTags
from .git_sources import Discovery, GitSource, RefKind, RefLocation, rewritten
from .github import Comparison, GitHubError, Release
from .install import InstallPlan, YamlRewrite
from .version_tags import Version, is_version_tag
from .versions import ShownSource

_LOGGER = logging.getLogger(__name__)


class Remote(Protocol):
    """A Source's git remote."""

    async def resolve_ref(self, url: str, ref: str | None) -> RemoteRef:
        """What *ref* (None: the default branch) points to at *url*; raises
        GitRemoteError if the remote cannot be asked or has no such ref."""
        ...

    async def resolve_ref_and_tags(self, url: str, ref: str | None) -> RemoteTags:
        """What *ref* (None: the default branch) points to at *url*, if anything, with
        every tag the remote has; raises GitRemoteError if the remote cannot be asked."""
        ...


class Releases(Protocol):
    """GitHub's Releases of a repository."""

    async def releases(self, url: str) -> tuple[Release, ...]:
        """The Releases of the repository at *url*: empty when it has none or is not on
        GitHub; raises GitHubError if GitHub cannot be asked."""
        ...


class InstalledVersions(Protocol):
    """What the integration recorded at the last successful Install (ADR-0003)."""

    def installed_commit(self, source_key: str) -> str | None: ...

    def installed_tag(self, url: str) -> str | None: ...


class InstalledRecord(InstalledVersions, Protocol):
    """The installed record, which new Sources are seeded into (ADR-0003)."""

    async def async_seed_commits(self, seeds: Mapping[str, str], present: Collection[str]) -> None:
        """Record *seeds* (by key) where no Installed Commit is recorded yet, and forget
        every key not in *present*."""
        ...

    async def async_seed_tags(self, seeds: Mapping[str, str], present: Collection[str]) -> None:
        """Record *seeds* (by url) where no Installed Tag is recorded yet, and forget
        every url not in *present*."""
        ...


class Comparisons(Protocol):
    """The commits between two commits of a Source, when GitHub was asked for them."""

    def get(self, key: str, installed: str | None, latest: str) -> Comparison | None: ...


# A ref's location, and the tag to write there.
RefMove = tuple[RefLocation, str]


@dataclass(frozen=True)
class _CommitFollowingSource:
    """A Git Source following its Latest Commit (a Floating Ref, or an Unset Ref), with
    the Latest Commit last seen on its remote."""

    source: GitSource
    latest_commit: str

    @property
    def key(self) -> str:
        return self.source.key

    def shown(self, installed: InstalledVersions, comparisons: Comparisons) -> ShownSource:
        commit = installed.installed_commit(self.key)
        return ShownSource(
            self.source.name,
            commit,
            self.latest_commit,
            comparisons.get(self.key, commit, self.latest_commit),
        )

    def change(self, installed: InstalledVersions) -> SourceChange | None:
        return _change(self, installed.installed_commit(self.key), self.latest_commit)

    def recorded_commits(self) -> Mapping[str, str]:
        """The commits an Install records: the Latest Commit seen (ADR-0003)."""
        return {self.key: self.latest_commit}

    def recorded_tags(self, installed: InstalledVersions) -> Mapping[str, str]:
        return {}

    def moves(self, installed: InstalledVersions) -> tuple[RefMove, ...]:
        """A Latest Commit is followed without touching the ref."""
        return ()


@dataclass(frozen=True)
class _VersionFollowingSource:
    """A Git Source following versions (a Tag Ref, or an Unset Ref on a remote with
    Version Tags), with the Version Tags last seen on its remote and its
    repository's Releases."""

    source: GitSource
    # Each Version Tag on the remote, with the commit it points to.
    version_tags: Mapping[str, str]
    # Its Releases at GitHub's last answer: empty when it has none or is not on
    # GitHub, None while GitHub has never answered.
    releases: tuple[Release, ...] | None = ()

    @property
    def key(self) -> str:
        """Its repository, not its ref: an Install rewrites the ref (ADR-0007)."""
        return self.source.url

    def offered(self, installed: str) -> str:
        """The newest Release, or without Releases the newest Version Tag, if strictly
        newer than *installed*; else *installed*.

        Nothing newer is offered while GitHub has never answered, as it is not
        known yet which of the two the Source follows.
        """
        if self.releases is None:
            return installed
        followed = [release.tag for release in self.releases] or list(self.version_tags)
        newest = max(followed, key=Version.key, default=None)
        if newest is not None and Version.key(newest) > Version.key(installed):
            return newest
        return installed

    def seed(self) -> str | None:
        """The Installed Tag it starts from: the tag a Tag Ref names, or the version an
        Unset Ref follows, once GitHub has answered."""
        if self.source.ref is not None:
            return self.source.ref
        if self.releases is None:
            return None
        return self.offered("") or None

    def shown(self, installed: InstalledVersions, comparisons: Comparisons) -> ShownSource:
        tag, offered = self._installed_and_offered(installed)
        offered_commit = self.version_tags.get(offered)
        comparison = (
            None
            if offered_commit is None
            else comparisons.get(self.key, self.version_tags.get(tag), offered_commit)
        )
        return ShownSource(
            self.source.name,
            tag,
            offered,
            comparison,
            versions=True,
            releases=self._releases_between(tag, offered),
        )

    def change(self, installed: InstalledVersions) -> SourceChange | None:
        tag, offered = self._installed_and_offered(installed)
        return _change(self, self.version_tags.get(tag), self.version_tags.get(offered))

    def recorded_commits(self) -> Mapping[str, str]:
        return {}

    def recorded_tags(self, installed: InstalledVersions) -> Mapping[str, str]:
        """The Installed Tag an Install records: the version it builds (ADR-0007)."""
        built = self._built(installed)
        return {self.key: built} if built else {}

    def moves(self, installed: InstalledVersions) -> tuple[RefMove, ...]:
        """Where the ref moves to the version built, if it differs from the YAML's.

        An Unset Ref gains a ref only when a newer version is offered.
        """
        tag, offered = self._installed_and_offered(installed)
        built = self._built(installed)
        unset_and_current = self.source.ref is None and offered == tag
        if built == self.source.ref or unset_and_current:
            return ()
        return tuple((location, built) for location in self.source.written_at)

    def _installed_and_offered(self, installed: InstalledVersions) -> tuple[str, str]:
        """The Installed Tag (until seeded, the one the YAML names) and the one offered."""
        tag = installed.installed_tag(self.key) or self.source.ref or ""
        return tag, self.offered(tag)

    def _built(self, installed: InstalledVersions) -> str:
        """The version an Install pressed now builds: the one offered, if any, else the
        one the YAML names, even if changed by hand."""
        tag, offered = self._installed_and_offered(installed)
        return offered if offered != tag else (self.source.ref or tag)

    def _releases_between(self, installed: str, offered: str) -> tuple[Release, ...]:
        """The Releases newer than *installed*, up to *offered*, newest first."""
        return tuple(
            sorted(
                (
                    release
                    for release in self.releases or ()
                    if Version.key(installed) < Version.key(release.tag) <= Version.key(offered)
                ),
                key=lambda release: Version.key(release.tag),
                reverse=True,
            )
        )


type _FollowedSource = _CommitFollowingSource | _VersionFollowingSource


def _change(
    followed: _FollowedSource, installed: str | None, latest: str | None
) -> SourceChange | None:
    """The change between two commits of *followed*, if both are known and differ."""
    if installed is None or latest is None or installed == latest:
        return None
    return SourceChange(followed.key, followed.source.url, installed, latest)


def _plan_install(
    followed: Iterable[_FollowedSource],
    installed: InstalledVersions,
    files: Mapping[str, str],
    port: str,
    clean_build: bool,
) -> InstallPlan:
    """An Install of what *followed* shows: it records the Latest Commits and versions
    shown (ADR-0003), after moving each ref to the version shown (ADR-0007), each
    file of *files* (by path, as last read) written once with all its moves."""
    commits: dict[str, str] = {}
    tags: dict[str, str] = {}
    moves: dict[str, list[RefMove]] = {}
    for each in followed:
        commits.update(each.recorded_commits())
        tags.update(each.recorded_tags(installed))
        for location, tag in each.moves(installed):
            moves.setdefault(location.file, []).append((location, tag))
    rewrites = tuple(
        YamlRewrite(file, rewritten(files[file], edits), files[file])
        for file, edits in moves.items()
    )
    return InstallPlan(commits, port, clean_build, tags=tags, rewrites=rewrites)


@dataclass(frozen=True)
class _Poll:
    """What one poll saw."""

    # Each Followed Source by key: `url@ref` following Latest Commit, `url`
    # following versions (an Install rewrites the ref, ADR-0007).
    followed: Mapping[str, _FollowedSource]
    # The text of each file read, by path, which an Install rewrites.
    files: Mapping[str, str]
    # The Sources whose Refresh Policy lets ESPHome build a stale cached clone.
    wrong_refresh_policy: tuple[GitSource, ...]


_NOTHING_POLLED = _Poll(MappingProxyType({}), MappingProxyType({}), ())


@dataclass(frozen=True)
class Snapshot:
    """What one poll saw, opaque: compare two to tell whether anything changed."""

    _poll: _Poll


class _Outages:
    """What cannot be asked, by key: warned about once, and logged when it can be again."""

    def __init__(self, title: Callable[[], str]) -> None:
        self._title = title
        self._failing: set[str] = set()

    def failed(self, key: str, message: str, *args: object) -> None:
        """Warn *message* (after the title) unless *key* is already failing."""
        if key not in self._failing:
            _LOGGER.warning("%s: " + message, self._title(), *args)
        self._failing.add(key)

    def recovered(self, key: str, message: str, *args: object) -> None:
        """Log *message* (after the title) if *key* was failing."""
        if key in self._failing:
            _LOGGER.info("%s: " + message, self._title(), *args)
        self._failing.discard(key)

    def keep_only(self, keys: Iterable[str]) -> None:
        """Forget every key not in *keys*."""
        self._failing &= set(keys)


class FollowedSources:
    """The Followed Sources of one Tracked Config, polled from their remotes and GitHub.

    A remote that cannot be asked keeps what was last known of it, and GitHub
    that cannot be asked keeps its last answer, so a Source does not flip
    between following Releases and Version Tags (ADR-0007). Each query combines
    the last poll with the installed record as it is when asked.

    *title* gives the Tracked Config's title as it is when a message is logged.
    """

    def __init__(
        self,
        remote: Remote,
        releases: Releases,
        record: InstalledRecord,
        title: Callable[[], str],
    ) -> None:
        self._remote = remote
        self._releases = releases
        self._record = record
        self._last = _NOTHING_POLLED
        # Each repository's Releases at GitHub's last answer, by url.
        self._answers: dict[str, tuple[Release, ...]] = {}
        # The keys of Sources whose remote fails, and the urls GitHub fails for.
        self._remote_outages = _Outages(title)
        self._releases_outages = _Outages(title)

    @asynccontextmanager
    async def async_poll(
        self, discovery: Discovery, files: Mapping[str, str]
    ) -> AsyncIterator[Snapshot]:
        """Ask the remote of each Source *discovery* found, and GitHub for the Releases of
        each following versions; seed new Sources and forget removed ones (ADR-0003).

        *files* is the text of each file *discovery* was read from, by path.
        Within the block, queries answer from this poll. If the block raises,
        the poll is dropped: queries, and the next poll, go back to the last
        one (the seeds and GitHub's answers stay).
        """
        previous = self._last
        self._last = await self._poll(discovery, files)
        try:
            yield Snapshot(self._last)
        except BaseException:
            self._last = previous
            raise

    def shown(self, comparisons: Comparisons) -> tuple[ShownSource, ...]:
        """Each Followed Source, installed against latest, as the entity shows it."""
        return tuple(
            followed.shown(self._record, comparisons) for followed in self._last.followed.values()
        )

    def changes(self) -> list[SourceChange]:
        """The Followed Sources with an update whose two commits are known."""
        return [
            change
            for followed in self._last.followed.values()
            if (change := followed.change(self._record))
        ]

    def install_plan(self, port: str, clean_build: bool) -> InstallPlan:
        """An Install of what the last poll showed: it records the Latest Commits and
        versions shown (ADR-0003), after moving each ref to the version shown
        (ADR-0007) in the files as that poll read them."""
        return _plan_install(
            self._last.followed.values(), self._record, self._last.files, port, clean_build
        )

    def wrong_refresh_policy(self) -> tuple[GitSource, ...]:
        """The Sources of the last poll whose Refresh Policy lets ESPHome build a stale
        clone of them.

        Only a Source following Latest Commit can be: a Pinned Ref never moves,
        and a changed tag is a new `url@ref` in ESPHome's clone cache (ADR-0004).
        """
        following_commits = _latest_commits(self._last.followed.values())
        return tuple(
            source for source in self._last.wrong_refresh_policy if source.key in following_commits
        )

    async def _poll(self, discovery: Discovery, files: Mapping[str, str]) -> _Poll:
        previous = self._last.followed
        results = await asyncio.gather(
            *(self._ask_remote(source) for source in discovery.sources),
            return_exceptions=True,
        )
        followed: dict[str, _FollowedSource] = {}
        for source, result in zip(discovery.sources, results, strict=True):
            if isinstance(result, GitRemoteError):
                # It may have switched between following Latest Commit and versions.
                kept = previous.get(source.key) or previous.get(source.url)
                self._remote_outages.failed(source.key, _remote_failure(kept), source.name, result)
                if kept is not None:
                    followed[kept.key] = replace(kept, source=source)
                continue
            if isinstance(result, BaseException):
                raise result
            self._remote_outages.recovered(source.key, "%s is reachable again", source.name)
            followed[result.key] = result
        self._remote_outages.keep_only(source.key for source in discovery.sources)
        followed = await self._with_releases(followed)
        await self._seed(discovery, followed)
        return _Poll(
            MappingProxyType(followed),
            MappingProxyType(dict(files)),
            discovery.wrong_refresh_policy,
        )

    async def _ask_remote(self, source: GitSource) -> _FollowedSource:
        """What *source*'s remote holds of what the Source follows.

        A Tag Ref follows the remote's Version Tags; but only the remote can tell
        a branch from a tag, and a Tag Ref that is a branch follows its Latest
        Commit, as a Floating Ref does. An Unset Ref follows versions while its
        remote has a Version Tag (a Release's tag is one) and an Install can
        insert its ref; else its Latest Commit.
        """
        if source.kind is RefKind.UNSET and source.written_at:
            remote = await self._remote.resolve_ref_and_tags(source.url, None)
            versions = _version_tags(remote.tags)
            if not versions and remote.ref is not None:
                return _CommitFollowingSource(source, remote.ref.commit)
            return _VersionFollowingSource(source, versions)
        if source.kind is not RefKind.TAG or source.ref is None:
            found = await self._remote.resolve_ref(source.url, source.ref)
            return _CommitFollowingSource(source, found.commit)
        remote = await self._remote.resolve_ref_and_tags(source.url, source.ref)
        if remote.ref is not None and remote.ref.kind is RemoteRefKind.BRANCH:
            return _CommitFollowingSource(source, remote.ref.commit)
        return _VersionFollowingSource(source, _version_tags(remote.tags))

    async def _with_releases(
        self, followed: dict[str, _FollowedSource]
    ) -> dict[str, _FollowedSource]:
        """*followed*, each Source following versions with its repository's Releases at
        GitHub's last answer; the answers for any other repository are forgotten."""
        urls = [each.key for each in _following_versions(followed.values())]
        results = await asyncio.gather(
            *(self._releases.releases(url) for url in urls), return_exceptions=True
        )
        answers = {url: self._answers[url] for url in urls if url in self._answers}
        for url, result in zip(urls, results, strict=True):
            if isinstance(result, GitHubError):
                self._releases_outages.failed(
                    url,
                    "cannot ask GitHub for the Releases of %s, keeping the last answer: %s",
                    url,
                    result,
                )
            elif isinstance(result, BaseException):
                raise result
            else:
                self._releases_outages.recovered(url, "GitHub lists the Releases of %s again", url)
                answers[url] = result
        self._answers = answers
        self._releases_outages.keep_only(urls)
        return {
            key: (
                replace(each, releases=answers.get(each.key))
                if isinstance(each, _VersionFollowingSource)
                else each
            )
            for key, each in followed.items()
        }

    async def _seed(self, discovery: Discovery, followed: Mapping[str, _FollowedSource]) -> None:
        """Seed new Sources and forget removed ones (ADR-0003).

        A Source following Latest Commit starts from the one seen; a Tag Ref from
        the tag the YAML names, kept while its remote cannot be asked; an Unset
        Ref following versions from the version it follows.
        """
        await self._record.async_seed_commits(
            _latest_commits(followed.values()), {source.key for source in discovery.sources}
        )
        versioned = _following_versions(followed.values())
        await self._record.async_seed_tags(
            {each.key: seed for each in versioned if (seed := each.seed())},
            {each.key for each in versioned}
            | {source.url for source in discovery.sources if source.kind is RefKind.TAG},
        )


def _remote_failure(kept: _FollowedSource | None) -> str:
    """The warning that a Source's remote cannot be asked, saying what is *kept*."""
    if isinstance(kept, _VersionFollowingSource):
        return "cannot get the Version Tags of %s, keeping the last ones known: %s"
    if kept is not None:
        return "cannot get the Latest Commit of %s, keeping the last one known: %s"
    return "cannot ask the remote of %s, nothing is known of it yet: %s"


def _version_tags(tags: Mapping[str, str]) -> dict[str, str]:
    return {tag: commit for tag, commit in tags.items() if is_version_tag(tag)}


def _latest_commits(followed: Iterable[_FollowedSource]) -> dict[str, str]:
    """The Latest Commit of each Source following one, by key."""
    commits: dict[str, str] = {}
    for each in followed:
        commits.update(each.recorded_commits())
    return commits


def _following_versions(followed: Iterable[_FollowedSource]) -> list[_VersionFollowingSource]:
    return [each for each in followed if isinstance(each, _VersionFollowingSource)]
