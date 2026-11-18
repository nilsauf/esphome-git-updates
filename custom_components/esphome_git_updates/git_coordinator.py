"""Discover a Tracked Config's Git Sources and poll their Latest Commits and Version Tags."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .comparisons import GitHubComparisons, SourceChange
from .connection import DeviceBuilderSession, DeviceBuilderUnavailable, ProtocolProblem
from .const import CONF_CONFIGURATION, DOMAIN
from .coordinator import EsphomeGitUpdatesConfigEntry, TrackedConfigStore
from .device_builder import CommandFailed, DeviceBuilderClient
from .git_remote import (
    GitRemoteError,
    RemoteRefKind,
    resolve_ref,
    resolve_ref_and_tags,
)
from .git_sources import (
    Discovery,
    DiscoveryError,
    GitSource,
    Reader,
    RefKind,
    RefLocation,
    discover_git_sources,
    rewritten,
    version_of,
)
from .github import Release
from .install import InstallPlan, YamlRewrite
from .options import Options
from .pre_install import PreInstallCheck, PreInstallIssues
from .releases import GitHubReleases
from .versions import SourceCommits

_LOGGER = logging.getLogger(__name__)

# The name this poller reports protocol drift under.
_POLLER = "git"


@dataclass(frozen=True)
class TrackedGitSource:
    """A Git Source following Latest Commit (a Floating Ref or Unset Ref), with the
    Latest Commit last seen on its remote."""

    source: GitSource
    latest_commit: str

    @property
    def key(self) -> str:
        return self.source.key


@dataclass(frozen=True)
class VersionedGitSource:
    """A Git Source following versions (a Tag Ref), with the Version Tags last seen on
    its remote and its repository's Releases."""

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
        newest = max(followed, key=_version, default=None)
        if newest is not None and _version(newest) > _version(installed):
            return newest
        return installed

    def releases_between(self, installed: str, offered: str) -> tuple[Release, ...]:
        """The Releases newer than *installed*, up to *offered*, newest first."""
        return tuple(
            sorted(
                (
                    release
                    for release in self.releases or ()
                    if _version(installed) < _version(release.tag) <= _version(offered)
                ),
                key=lambda release: _version(release.tag),
                reverse=True,
            )
        )


type GitSourceState = TrackedGitSource | VersionedGitSource


class _ReadRefused(Exception):
    """The Device Builder refused to read a file the Tracked Config includes.

    A refused read of the Configuration itself is not this: the Device
    Builder may simply no longer have it.
    """

    def __init__(self, path: str, err: CommandFailed) -> None:
        super().__init__(f"{path}: {err}")
        self.path = path
        self.details = f"{err.error_code}: {err.details}"


class GitSourcesCoordinator(DataUpdateCoordinator[dict[str, GitSourceState]]):
    """Polls Git Sources; its data maps each tracked Source's key to what its remote
    was last seen to hold.

    Each poll reads the Tracked Config and every file it includes over `/ws`
    (ADR-0006), then asks every remote with `git ls-remote`. A remote that
    cannot be asked keeps what was last known of it. Newly seen Sources
    following Latest Commit are seeded with it, so they show no update until
    the next push (ADR-0003); Tag Refs are seeded with the tag the YAML names.
    A Tag Ref on GitHub also asks GitHub for its Releases, keeping the last
    answer while GitHub cannot be asked. Each changed Git Source on GitHub is
    then compared over GitHub's API, for release notes only.
    """

    config_entry: EsphomeGitUpdatesConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EsphomeGitUpdatesConfigEntry,
        store: TrackedConfigStore,
        session: DeviceBuilderSession,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title} git sources",
            update_interval=Options.of(entry.options).git_poll_interval,
        )
        self._store = store
        self._session = session
        self._untrackable: tuple[str, ...] = ()
        self._failing: set[str] = set()
        self._pre_install_issues = PreInstallIssues(hass, entry)
        # What the YAML said at the last successful poll.
        self._pre_install_check: PreInstallCheck | None = None
        # The text of each file read at the last successful poll, by path.
        self._files: Mapping[str, str] = {}
        self.comparisons = GitHubComparisons(hass, entry)
        self._releases = GitHubReleases(hass, entry)

    async def _async_update_data(self) -> dict[str, GitSourceState]:
        discovery, files = await self._discover()
        self._log_untrackable(discovery.untrackable)
        previous = self.data or {}
        results = await asyncio.gather(
            *(_ask_remote(source) for source in discovery.sources),
            return_exceptions=True,
        )
        tracked: dict[str, GitSourceState] = {}
        for source, result in zip(discovery.sources, results, strict=True):
            if isinstance(result, GitRemoteError):
                self._log_failure(source, result)
                kept = previous.get(source.key) or previous.get(source.url)
                if kept is not None:
                    tracked[kept.key] = replace(kept, source=source)
                continue
            if isinstance(result, BaseException):
                raise result
            self._log_recovery(source)
            tracked[result.key] = result
        tracked = await self._with_releases(tracked)
        await self._store.async_seed_installed_commits(
            _latest_commits(tracked), {source.key for source in discovery.sources}
        )
        await self._store.async_seed_installed_tags(
            {
                state.key: state.source.ref
                for state in tracked.values()
                if isinstance(state, VersionedGitSource) and state.source.ref is not None
            },
            {source.url for source in discovery.sources if source.kind is RefKind.TAG},
        )
        self._check(discovery, tracked)
        await self.comparisons.async_update(self._changes(tracked))
        self._files = files
        return tracked

    async def _with_releases(
        self, tracked: dict[str, GitSourceState]
    ) -> dict[str, GitSourceState]:
        """*tracked*, each Source following versions with its repository's Releases."""
        versioned = [state for state in tracked.values() if isinstance(state, VersionedGitSource)]
        await self._releases.async_update(state.key for state in versioned)
        return {
            key: (
                replace(state, releases=self._releases.get(state.key))
                if isinstance(state, VersionedGitSource)
                else state
            )
            for key, state in tracked.items()
        }

    def _check(self, discovery: Discovery, tracked: dict[str, GitSourceState]) -> None:
        """Check the YAML just read for an Install, and raise or clear its Repair issues."""
        following_commits = _latest_commits(tracked)
        self._pre_install_check = PreInstallCheck(
            ota=discovery.ota,
            # Only a Source following Latest Commit can be built from a stale
            # clone: a Pinned Ref never moves, and a changed tag is a new
            # `url@ref` in ESPHome's clone cache (ADR-0004).
            wrong_refresh_policy=tuple(
                source
                for source in discovery.wrong_refresh_policy
                if source.key in following_commits
            ),
        )
        self._pre_install_issues.report(self._pre_install_check)

    async def async_check_before_install(self) -> None:
        """Read the YAML again and raise HomeAssistantError if an Install must be refused.

        It is refused when the YAML cannot be read, or when a pre-install check fails.
        """
        title = self.config_entry.title
        await self.async_refresh()
        check = self._pre_install_check
        if not self.last_update_success or check is None:
            raise HomeAssistantError(
                f"Install of {title} refused: cannot read its YAML to check "
                f"it before installing: {self.last_exception}"
            )
        if (refusal := check.refusal(title)) is not None:
            raise HomeAssistantError(refusal)
        # It would build the old version and record the new one.
        unwritable = [
            f"{state.source.name} {state.source.ref} → {tag}"
            for state, tag, location in self._moves()
            if location is None
        ]
        if unwritable:
            raise HomeAssistantError(
                f"Install of {title} refused: cannot rewrite a ref that is not written "
                f"as is in the YAML ({'; '.join(unwritable)})"
            )

    def install_plan(self, port: str, clean_build: bool) -> InstallPlan:
        """An Install of what the last poll showed: it records the Latest Commits and
        versions shown (ADR-0003), after moving each Tag Ref to the version shown
        (ADR-0007). Call it right after `async_check_before_install`."""
        edits: dict[str, list[tuple[RefLocation, str]]] = {}
        for _state, tag, location in self._moves():
            if location is not None:
                edits.setdefault(location.file, []).append((location, tag))
        rewrites = tuple(
            YamlRewrite(file, rewritten(self._files[file], refs), self._files[file])
            for file, refs in edits.items()
        )
        tags = {
            state.key: self._built(state)
            for state in (self.data or {}).values()
            if isinstance(state, VersionedGitSource)
        }
        return InstallPlan(
            _latest_commits(self.data or {}), port, clean_build, tags=tags, rewrites=rewrites
        )

    def _moves(self) -> list[tuple[VersionedGitSource, str, RefLocation | None]]:
        """Each Source following versions whose ref must move to the version offered,
        with that version and where its ref is written, if it can be rewritten."""
        moves = []
        for state in (self.data or {}).values():
            if isinstance(state, VersionedGitSource):
                tag = self._built(state)
                if tag != state.source.ref:
                    moves.append((state, tag, state.source.written_at))
        return moves

    def _built(self, state: VersionedGitSource) -> str:
        """The version an Install pressed now builds: the one offered, if any, else the
        one the YAML names, even if changed by hand."""
        installed, offered = self._installed_and_offered(state)
        return offered if offered != installed else (state.source.ref or installed)

    async def async_set_poll_interval(self, interval: timedelta) -> None:
        """Poll every *interval* from now on, starting with a poll right away.

        The poll already scheduled would otherwise still come after the old
        interval; a refresh schedules the next one after the new interval.
        """
        if interval == self.update_interval:
            return
        self.update_interval = interval
        await self.async_refresh()

    async def async_compare_changed(self) -> None:
        """Ask GitHub again for any change whose comparison is missing."""
        if await self.comparisons.async_update(self._changes(self.data or {})):
            self.async_update_listeners()

    def shown_sources(self) -> tuple[SourceCommits, ...]:
        """Each tracked Git Source, installed against latest, as the entity shows it."""
        return tuple(self._shown(state) for state in (self.data or {}).values())

    def _shown(self, state: GitSourceState) -> SourceCommits:
        if isinstance(state, TrackedGitSource):
            installed = self._store.installed_commit(state.key)
            latest = state.latest_commit
            return SourceCommits(
                state.source.name,
                installed,
                latest,
                self.comparisons.get(state.key, installed, latest),
            )
        installed, offered = self._installed_and_offered(state)
        installed_commit = state.version_tags.get(installed)
        offered_commit = state.version_tags.get(offered)
        comparison = (
            None
            if offered_commit is None
            else self.comparisons.get(state.key, installed_commit, offered_commit)
        )
        return SourceCommits(
            state.source.name,
            installed,
            offered,
            comparison,
            versions=True,
            releases=state.releases_between(installed, offered),
        )

    def _installed_and_offered(self, state: VersionedGitSource) -> tuple[str, str]:
        """The Installed Tag (until seeded, the one the YAML names) and the one offered."""
        installed = self._store.installed_tag(state.key) or state.source.ref or ""
        return installed, state.offered(installed)

    def _changes(self, tracked: Mapping[str, GitSourceState]) -> list[SourceChange]:
        """The tracked Sources with an update whose two commits are known."""
        changes = []
        for key, state in tracked.items():
            if isinstance(state, TrackedGitSource):
                installed = self._store.installed_commit(key)
                latest: str | None = state.latest_commit
            else:
                installed_tag, offered_tag = self._installed_and_offered(state)
                installed = state.version_tags.get(installed_tag)
                latest = state.version_tags.get(offered_tag)
            if installed is not None and latest is not None and installed != latest:
                changes.append(SourceChange(key, state.source.url, installed, latest))
        return changes

    async def _discover(self) -> tuple[Discovery, dict[str, str]]:
        """The Git Sources of the Tracked Config, and the text of each file read."""
        configuration = self.config_entry.data[CONF_CONFIGURATION]
        files: dict[str, str] = {}
        try:
            async with self._session.connect(_POLLER) as client:
                try:
                    discovery = await discover_git_sources(
                        configuration, _reader(client, configuration, files)
                    )
                    return discovery, files
                except _ReadRefused as err:
                    # The Device Builder may have started restricting reads to
                    # device files, as its MCP layer does; then includes stop
                    # resolving (ADR-0006).
                    raise ProtocolProblem(
                        f"the Device Builder refused to read {err}",
                        "include_refused",
                        {"file": err.path, "error": err.details},
                    ) from err
        except DeviceBuilderUnavailable as err:
            raise UpdateFailed(str(err)) from err
        except CommandFailed as err:
            raise UpdateFailed(f"cannot read {configuration}: {err}") from err
        except DiscoveryError as err:
            raise UpdateFailed(f"cannot read the Git Sources of {configuration}: {err}") from err

    def _log_untrackable(self, untrackable: tuple[str, ...]) -> None:
        if untrackable != self._untrackable:
            for reason in untrackable:
                _LOGGER.warning(
                    "%s: a Git Source cannot be tracked: %s", self.config_entry.title, reason
                )
        self._untrackable = untrackable

    def _log_failure(self, source: GitSource, err: GitRemoteError) -> None:
        if source.key not in self._failing:
            _LOGGER.warning(
                "%s: cannot get the Latest Commit of %s, keeping the last one known: %s",
                self.config_entry.title,
                source.name,
                err,
            )
        self._failing.add(source.key)

    def _log_recovery(self, source: GitSource) -> None:
        if source.key in self._failing:
            _LOGGER.info("%s: %s is reachable again", self.config_entry.title, source.name)
        self._failing.discard(source.key)


async def _ask_remote(source: GitSource) -> GitSourceState:
    """What *source*'s remote holds of what the Source follows.

    A Tag Ref follows the remote's Version Tags; but only the remote can tell
    a branch from a tag, and a Tag Ref that is a branch follows its Latest
    Commit, as a Floating Ref does.
    """
    if source.kind is not RefKind.TAG or source.ref is None:
        return TrackedGitSource(source, (await resolve_ref(source.url, source.ref)).commit)
    remote = await resolve_ref_and_tags(source.url, source.ref)
    if remote.ref is not None and remote.ref.kind is RemoteRefKind.BRANCH:
        return TrackedGitSource(source, remote.ref.commit)
    return VersionedGitSource(
        source,
        {tag: commit for tag, commit in remote.tags.items() if version_of(tag) is not None},
    )


def _version(tag: str) -> tuple[int, ...]:
    """*tag*'s version for ordering; a tag that is no Version Tag sorts lowest."""
    return version_of(tag) or ()


def _latest_commits(tracked: Mapping[str, GitSourceState]) -> dict[str, str]:
    return {
        key: state.latest_commit
        for key, state in tracked.items()
        if isinstance(state, TrackedGitSource)
    }


def _reader(client: DeviceBuilderClient, configuration: str, files: dict[str, str]) -> Reader:
    """Reads over *client*, keeping each file's text in *files*."""

    async def read(path: str) -> str:
        try:
            files[path] = await client.get_config(path)
        except CommandFailed as err:
            if path == configuration:
                raise
            raise _ReadRefused(path, err) from err
        return files[path]

    return read
