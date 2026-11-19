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
from .following import FollowedSources, Refusal, Snapshot
from .git_remote import LsRemote
from .git_sources import Discovery, DiscoveryError, Reader, discover_git_sources
from .install import InstallPlan
from .options import Options
from .pre_install import PreInstallIssues
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
        self.comparisons = GitHubComparisons(hass, entry)
        self._followed = FollowedSources(
            # Renaming the entry does not reload it: log under its title of the moment.
            LsRemote(),
            GitHubReleases(hass, entry),
            store,
            lambda: entry.title,
        )

    async def _async_update_data(self) -> Snapshot:
        return await self._async_poll_now()

    async def async_prepare_install(self, options: Options, device_offline: bool) -> InstallPlan:
        """Read the YAML again and plan an Install of what that read shows.

        Raises HomeAssistantError when the Install is refused: the device looks
        offline (the Device Builder would only defer the Install), the YAML
        cannot be read, or a pre-install check fails.

        The plan records the Latest Commits and versions this read showed
        (ADR-0003), after moving each Tag Ref as it saw them (ADR-0007): what
        gets recorded is what the check just read, not what the remote holds
        once the build is done, so a push during the build stays an update.
        """
        title = self.config_entry.title
        if device_offline:
            raise HomeAssistantError(
                f"Install of {title} refused: device offline "
                "(every entity of its Linked Device is unavailable)"
            )
        try:
            snapshot = await self._async_poll_now()
        except Exception as err:
            # As after a failed scheduled poll, the last good poll stays.
            raise HomeAssistantError(
                f"Install of {title} refused: cannot read its YAML to check "
                f"it before installing: {err}"
            ) from err
        self.async_set_updated_data(snapshot)
        decided = snapshot.install(options.upload_address, options.clean_build, title)
        if isinstance(decided, Refusal):
            raise HomeAssistantError(decided.message)
        return decided

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

    async def _async_poll_now(self) -> Snapshot:
        """Poll the Followed Sources now, reporting the pre-install check and
        comparing changes; raise UpdateFailed when the YAML cannot be read."""
        discovery, files = await self._discover()
        self._log_untrackable(discovery.untrackable)
        # A poll that fails from here on changes nothing it saw.
        async with self._followed.async_poll(discovery, files) as snapshot:
            self._pre_install_issues.report(snapshot.check)
            await self.comparisons.async_update(self._followed.changes())
        return snapshot

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
