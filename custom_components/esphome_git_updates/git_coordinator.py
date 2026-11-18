"""Discover a Tracked Config's Git Sources and poll their Latest Commits and Version Tags."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .comparisons import GitHubComparisons, SourceChange
from .connection import DeviceBuilderSession, DeviceBuilderUnavailable, ProtocolProblem
from .const import CONF_CONFIGURATION, DOMAIN
from .coordinator import EsphomeGitUpdatesConfigEntry, TrackedConfigStore
from .device_builder import CommandFailed, DeviceBuilderClient
from .following import (
    CommitFollowingSource,
    FollowedSource,
    VersionFollowingSource,
    plan_install,
)
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
    discover_git_sources,
)
from .install import InstallPlan
from .options import Options
from .pre_install import PreInstallCheck, PreInstallIssues
from .releases import GitHubReleases
from .version_tags import is_version_tag
from .versions import ShownSource

_LOGGER = logging.getLogger(__name__)

# The name this poller reports protocol drift under.
_POLLER = "git"


class _ReadRefused(Exception):
    """The Device Builder refused to read a file the Tracked Config includes.

    A refused read of the Configuration itself is not this: the Device
    Builder may simply no longer have it.
    """

    def __init__(self, path: str, err: CommandFailed) -> None:
        super().__init__(f"{path}: {err}")
        self.path = path
        self.details = f"{err.error_code}: {err.details}"


class GitSourcesCoordinator(DataUpdateCoordinator[dict[str, FollowedSource]]):
    """Polls Git Sources; its data maps each tracked Source's key to what its remote
    was last seen to hold.

    Each poll reads the Tracked Config and every file it includes over `/ws`
    (ADR-0006), then asks every remote with `git ls-remote`. A remote that
    cannot be asked keeps what was last known of it. Newly seen Sources
    following Latest Commit are seeded with it, so they show no update until
    the next push (ADR-0003); Tag Refs are seeded with the tag the YAML names,
    and Unset Refs that start following versions with the version they follow.
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

    async def _async_update_data(self) -> dict[str, FollowedSource]:
        discovery, files = await self._discover()
        self._log_untrackable(discovery.untrackable)
        previous = self.data or {}
        results = await asyncio.gather(
            *(_ask_remote(source) for source in discovery.sources),
            return_exceptions=True,
        )
        tracked: dict[str, FollowedSource] = {}
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
        versioned = _following_versions(tracked)
        await self._store.async_seed_installed_tags(
            {state.key: seed for state in versioned if (seed := state.seed())},
            {state.key for state in versioned}
            | {source.url for source in discovery.sources if source.kind is RefKind.TAG},
        )
        self._check(discovery, tracked)
        await self.comparisons.async_update(self._changes(tracked))
        self._files = files
        return tracked

    async def _with_releases(
        self, tracked: dict[str, FollowedSource]
    ) -> dict[str, FollowedSource]:
        """*tracked*, each Source following versions with its repository's Releases."""
        await self._releases.async_update(state.key for state in _following_versions(tracked))
        return {
            key: (
                replace(state, releases=self._releases.get(state.key))
                if isinstance(state, VersionFollowingSource)
                else state
            )
            for key, state in tracked.items()
        }

    def _check(self, discovery: Discovery, tracked: dict[str, FollowedSource]) -> None:
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

    def install_plan(self, port: str, clean_build: bool) -> InstallPlan:
        """An Install of what the last poll showed: it records the Latest Commits and
        versions shown (ADR-0003), after moving each Tag Ref to the version shown
        (ADR-0007). Call it right after `async_check_before_install`."""
        return plan_install(
            (self.data or {}).values(), self._store, self._files, port, clean_build
        )

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

    def shown_sources(self) -> tuple[ShownSource, ...]:
        """Each tracked Git Source, installed against latest, as the entity shows it."""
        return tuple(
            state.shown(self._store, self.comparisons) for state in (self.data or {}).values()
        )

    def _changes(self, tracked: Mapping[str, FollowedSource]) -> list[SourceChange]:
        """The tracked Sources with an update whose two commits are known."""
        return [
            change for state in tracked.values() if (change := state.change(self._store))
        ]

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


async def _ask_remote(source: GitSource) -> FollowedSource:
    """What *source*'s remote holds of what the Source follows.

    A Tag Ref follows the remote's Version Tags; but only the remote can tell
    a branch from a tag, and a Tag Ref that is a branch follows its Latest
    Commit, as a Floating Ref does. An Unset Ref follows versions while its
    remote has a Version Tag (a Release's tag is one) and an Install can
    insert its ref; else its Latest Commit.
    """
    if source.kind is RefKind.UNSET and source.written_at:
        remote = await resolve_ref_and_tags(source.url, None)
        versions = _version_tags(remote.tags)
        if not versions and remote.ref is not None:
            return CommitFollowingSource(source, remote.ref.commit)
        return VersionFollowingSource(source, versions)
    if source.kind is not RefKind.TAG or source.ref is None:
        return CommitFollowingSource(source, (await resolve_ref(source.url, source.ref)).commit)
    remote = await resolve_ref_and_tags(source.url, source.ref)
    if remote.ref is not None and remote.ref.kind is RemoteRefKind.BRANCH:
        return CommitFollowingSource(source, remote.ref.commit)
    return VersionFollowingSource(source, _version_tags(remote.tags))


def _version_tags(tags: Mapping[str, str]) -> dict[str, str]:
    return {tag: commit for tag, commit in tags.items() if is_version_tag(tag)}


def _latest_commits(tracked: Mapping[str, FollowedSource]) -> dict[str, str]:
    """The Latest Commit of each Source following one, by key."""
    commits: dict[str, str] = {}
    for state in tracked.values():
        commits.update(state.recorded_commits())
    return commits


def _following_versions(tracked: Mapping[str, FollowedSource]) -> list[VersionFollowingSource]:
    return [state for state in tracked.values() if isinstance(state, VersionFollowingSource)]


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
