"""Discover a Tracked Config's Git Sources and poll their Latest Commits."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .connection import connect_to_device_builder
from .const import CONF_CONFIGURATION, DOMAIN
from .coordinator import (
    EsphomeGitUpdatesConfigEntry,
    ProtocolIssue,
    TrackedConfigStore,
)
from .device_builder import (
    CONNECTION_ERRORS,
    AuthenticationFailed,
    CommandFailed,
    DeviceBuilderClient,
    UnexpectedFrame,
    UnsupportedServerVersion,
)
from .comparisons import GitHubComparisons, SourceChange
from .git_remote import GitRemoteError, RefKind, RemoteRef, resolve_ref
from .git_sources import (
    Discovery,
    DiscoveryError,
    GitSource,
    Reader,
    discover_git_sources,
)
from .options import Options
from .pre_install import PreInstallCheck, PreInstallIssues
from .reauth import Reauthentication

_LOGGER = logging.getLogger(__name__)

# The name this poller reports protocol drift under.
_POLLER = "git"


@dataclass(frozen=True)
class TrackedGitSource:
    """A Git Source on a Floating Ref, with the Latest Commit last seen on its remote."""

    source: GitSource
    latest_commit: str


class _ReadRefused(Exception):
    """The Device Builder refused to read a file the Tracked Config includes.

    A refused read of the Configuration itself is not this: the Device
    Builder may simply no longer have it.
    """

    def __init__(self, path: str, err: CommandFailed) -> None:
        super().__init__(f"{path}: {err}")
        self.path = path
        self.details = f"{err.error_code}: {err.details}"


class GitSourcesCoordinator(DataUpdateCoordinator[dict[str, TrackedGitSource]]):
    """Polls Git Sources; its data maps each tracked Source's key to the Source and its
    Latest Commit.

    Each poll reads the Tracked Config and every file it includes over `/ws`
    (ADR-0006), then asks every remote with `git ls-remote`. A remote that
    cannot be asked keeps its last known Latest Commit. Newly seen Sources
    are seeded with their Latest Commit, so they show no update until the
    next push (ADR-0003). Each changed Git Source on GitHub is then
    compared over GitHub's API, for release notes only.
    """

    config_entry: EsphomeGitUpdatesConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EsphomeGitUpdatesConfigEntry,
        store: TrackedConfigStore,
        issue: ProtocolIssue,
        reauth: Reauthentication,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title} git sources",
            update_interval=Options.of(entry.options).git_poll_interval,
        )
        self._store = store
        self._issue = issue
        self._reauth = reauth
        self._untrackable: tuple[str, ...] = ()
        self._failing: set[str] = set()
        self._pre_install_issues = PreInstallIssues(hass, entry)
        # What the YAML said at the last successful poll.
        self._pre_install_check: PreInstallCheck | None = None
        self.comparisons = GitHubComparisons(hass, entry)

    async def _async_update_data(self) -> dict[str, TrackedGitSource]:
        if self._reauth.required:
            raise UpdateFailed("waiting for new Device Builder credentials")
        discovery = await self._discover()
        self._log_untrackable(discovery.untrackable)
        previous = self.data or {}
        results = await asyncio.gather(
            *(resolve_ref(source.url, source.ref) for source in discovery.sources),
            return_exceptions=True,
        )
        tracked: dict[str, TrackedGitSource] = {}
        for source, result in zip(discovery.sources, results, strict=True):
            if isinstance(result, GitRemoteError):
                self._log_failure(source, result)
                if source.key in previous:
                    tracked[source.key] = TrackedGitSource(
                        source, previous[source.key].latest_commit
                    )
                continue
            if isinstance(result, BaseException):
                raise result
            self._log_recovery(source)
            if _is_floating(result):
                tracked[source.key] = TrackedGitSource(source, result.commit)
        await self._store.async_seed_installed_commits(
            _latest_commits(tracked), {source.key for source in discovery.sources}
        )
        self._check(discovery, tracked)
        await self.comparisons.async_update(self._changes(tracked))
        return tracked

    def _check(self, discovery: Discovery, tracked: dict[str, TrackedGitSource]) -> None:
        """Check the YAML just read for an Install, and raise or clear its Repair issues."""
        self._pre_install_check = PreInstallCheck(
            ota=discovery.ota,
            # A Pinned Ref never moves, so its Refresh Policy does not matter.
            wrong_refresh_policy=tuple(
                source for source in discovery.wrong_refresh_policy if source.key in tracked
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

    def _changes(self, tracked: dict[str, TrackedGitSource]) -> list[SourceChange]:
        """The tracked Sources whose Latest Commit differs from a known Installed Commit."""
        changes = []
        for key, tracked_source in tracked.items():
            installed = self._store.installed_commit(key)
            latest = tracked_source.latest_commit
            if installed is not None and installed != latest:
                changes.append(SourceChange(key, tracked_source.source.url, installed, latest))
        return changes

    def latest_commits(self) -> dict[str, str]:
        """The Latest Commit last seen of each tracked Git Source, by key."""
        return _latest_commits(self.data or {})

    async def _discover(self) -> Discovery:
        configuration = self.config_entry.data[CONF_CONFIGURATION]
        try:
            async with connect_to_device_builder(self.hass, self.config_entry.data) as client:
                discovery = await discover_git_sources(
                    configuration, _reader(client, configuration)
                )
        except _ReadRefused as err:
            # The Device Builder may have started restricting reads to device
            # files, as its MCP layer does; then includes stop resolving (ADR-0006).
            self._issue.report(_POLLER, "include_refused", {"file": err.path, "error": err.details})
            raise UpdateFailed(f"the Device Builder refused to read {err}") from err
        except (UnsupportedServerVersion, UnexpectedFrame) as err:
            self._issue.report_drift(_POLLER, err)
            raise UpdateFailed(str(err)) from err
        except CommandFailed as err:
            raise UpdateFailed(f"cannot read {configuration}: {err}") from err
        except AuthenticationFailed as err:
            self._reauth.async_require(err)
            raise UpdateFailed(f"the Device Builder refused the login: {err}") from err
        except CONNECTION_ERRORS as err:
            raise UpdateFailed(f"cannot reach the Device Builder: {err}") from err
        except DiscoveryError as err:
            raise UpdateFailed(f"cannot read the Git Sources of {configuration}: {err}") from err
        self._issue.clear(_POLLER)
        return discovery

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


def _latest_commits(tracked: dict[str, TrackedGitSource]) -> dict[str, str]:
    return {key: tracked_source.latest_commit for key, tracked_source in tracked.items()}


def _is_floating(ref: RemoteRef) -> bool:
    """Only Floating Refs are tracked; a tag is a Pinned Ref."""
    return ref.kind is RefKind.BRANCH


def _reader(client: DeviceBuilderClient, configuration: str) -> Reader:
    async def read(path: str) -> str:
        try:
            return await client.get_config(path)
        except CommandFailed as err:
            if path == configuration:
                raise
            raise _ReadRefused(path, err) from err

    return read
