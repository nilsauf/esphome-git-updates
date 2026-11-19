"""Discover a Tracked Config's Git Sources and poll their Latest Commits and Version Tags."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .comparisons import GitHubComparisons
from .connection import DeviceBuilderSession, DeviceBuilderUnavailable, ProtocolProblem
from .const import CONF_CONFIGURATION, DOMAIN
from .coordinator import EsphomeGitUpdatesConfigEntry, TrackedConfigStore
from .device_builder import CommandFailed, DeviceBuilderClient
from .following import FollowedSources, Snapshot
from .git_remote import LsRemote
from .git_sources import Discovery, DiscoveryError, Reader, discover_git_sources
from .install import InstallPlan
from .options import Options
from .pre_install import PreInstallCheck, PreInstallIssues
from .releases import GitHubReleases
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


class GitSourcesCoordinator(DataUpdateCoordinator[Snapshot]):
    """Polls the Followed Sources of a Tracked Config for Home Assistant.

    Each poll reads the Tracked Config and every file it includes over `/ws`
    (ADR-0006), then has `FollowedSources` ask every remote and GitHub. Each
    changed Git Source on GitHub is then compared over GitHub's API, for
    release notes only.
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
        self._session = session
        self._untrackable: tuple[str, ...] = ()
        self._pre_install_issues = PreInstallIssues(hass, entry)
        # What the YAML said at the last successful poll.
        self._pre_install_check: PreInstallCheck | None = None
        self.comparisons = GitHubComparisons(hass, entry)
        self._followed = FollowedSources(
            LsRemote(), GitHubReleases(hass, entry), store, entry.title
        )

    async def _async_update_data(self) -> Snapshot:
        discovery, files = await self._discover()
        self._log_untrackable(discovery.untrackable)
        snapshot = await self._followed.async_poll(discovery, files)
        self._check(discovery)
        await self.comparisons.async_update(self._followed.changes())
        return snapshot

    def _check(self, discovery: Discovery) -> None:
        """Check the YAML just read for an Install, and raise or clear its Repair issues."""
        self._pre_install_check = PreInstallCheck(
            ota=discovery.ota, wrong_refresh_policy=self._followed.wrong_refresh_policy()
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
        return self._followed.install_plan(port, clean_build)

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
        if await self.comparisons.async_update(self._followed.changes()):
            self.async_update_listeners()

    def shown_sources(self) -> tuple[ShownSource, ...]:
        """Each tracked Git Source, installed against latest, as the entity shows it."""
        return self._followed.shown(self.comparisons)

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
