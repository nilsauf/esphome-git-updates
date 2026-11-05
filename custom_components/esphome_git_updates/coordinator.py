"""Read the Available ESPHome Version from the Device Builder; keep the Installed one and the running Install."""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, NotRequired, TypedDict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .connection import DeviceBuilderSession, DeviceBuilderUnavailable
from .const import DOMAIN, VERSION_POLL_INTERVAL
from .device_builder import OTA, ActiveJob, Stage
from .install import ActiveInstall

if TYPE_CHECKING:
    from .git_coordinator import GitSourcesCoordinator
    from .linked_device import LinkedDevice

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


@dataclass
class RuntimeData:
    """What a set-up entry holds while it runs."""

    coordinator: EsphomeVersionCoordinator
    git_coordinator: GitSourcesCoordinator
    store: TrackedConfigStore
    linked_device: LinkedDevice | None
    session: DeviceBuilderSession


type EsphomeGitUpdatesConfigEntry = ConfigEntry[RuntimeData]


class _StoredJob(TypedDict):
    job_id: str
    stage: str


class _StoredInstall(TypedDict):
    # The jobs still to follow, in order.
    jobs: list[_StoredJob]
    # The commits to record if the Install succeeds.
    commits: dict[str, str]
    # Where the Install uploads; absent in stores written before ticket 08.
    port: NotRequired[str]
    # Whether it started with a Clean Build; absent in stores written before
    # ticket 14, where the progress bands did not need it.
    clean_build: NotRequired[bool]


class _StoredData(TypedDict, total=False):
    installed_esphome_version: str
    # Git Source key -> Installed Commit.
    installed_commits: dict[str, str]
    # The Install running on the Device Builder, if any (ADR-0002).
    active_install: _StoredInstall


class TrackedConfigStore:
    """What the integration remembers about one Tracked Config (ADR-0003)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[_StoredData] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self._data: _StoredData = {}

    async def async_load(self) -> None:
        self._data = await self._store.async_load() or {}

    async def async_remove(self) -> None:
        await self._store.async_remove()

    @property
    def installed_esphome_version(self) -> str | None:
        """The ESPHome version recorded for the Tracked Config, if any."""
        return self._data.get("installed_esphome_version")

    async def async_set_installed_esphome_version(self, version: str) -> None:
        self._data["installed_esphome_version"] = version
        await self._store.async_save(self._data)

    def installed_commit(self, source_key: str) -> str | None:
        """The Installed Commit of the Git Source with *source_key*, if any."""
        return self._data.get("installed_commits", {}).get(source_key)

    async def async_seed_installed_commits(
        self, latest_commits: Mapping[str, str], present: Collection[str]
    ) -> None:
        """Seed Git Sources that have no Installed Commit yet with their Latest Commit.

        Sources no longer in the YAML (not in *present*) are forgotten, so one
        that comes back is seeded afresh (ADR-0003).
        """
        recorded = self._data.get("installed_commits", {})
        commits = {key: sha for key, sha in recorded.items() if key in present}
        for key, sha in latest_commits.items():
            commits.setdefault(key, sha)
        if commits != recorded:
            self._data["installed_commits"] = commits
            await self._store.async_save(self._data)

    @property
    def active_install(self) -> ActiveInstall | None:
        """The Install left running when Home Assistant last stopped, if any."""
        stored = self._data.get("active_install")
        if stored is None:
            return None
        return ActiveInstall(
            tuple(ActiveJob(job["job_id"], Stage(job["stage"])) for job in stored["jobs"]),
            stored["commits"],
            stored.get("port", OTA),
            stored.get("clean_build", False),
        )

    async def async_set_active_install(self, active: ActiveInstall) -> None:
        """Remember the jobs an Install still has to follow, until it ends."""
        self._data["active_install"] = {
            "jobs": [{"job_id": job.job_id, "stage": str(job.stage)} for job in active.jobs],
            "commits": dict(active.commits),
            "port": active.port,
            "clean_build": active.clean_build,
        }
        await self._store.async_save(self._data)

    async def async_clear_active_install(self) -> None:
        """Forget the active Install: it ended without success."""
        if self._data.pop("active_install", None) is not None:
            await self._store.async_save(self._data)

    async def async_record_install(self, esphome_version: str, commits: Mapping[str, str]) -> None:
        """Record a successful Install: the version compiled with, and the commits seen
        when Install was pressed (ADR-0003). The Install is no longer active."""
        self._data["installed_esphome_version"] = esphome_version
        self._data["installed_commits"] = {**self._data.get("installed_commits", {}), **commits}
        self._data.pop("active_install", None)
        await self._store.async_save(self._data)


class EsphomeVersionCoordinator(DataUpdateCoordinator[str]):
    """Polls the Device Builder; its data is the Available ESPHome Version.

    Each poll is one short connection: the version arrives in the handshake.
    A Device Builder that answers in a way the client does not understand
    raises a Repair issue (ADR-0002); the next healthy poll clears it. One
    that cannot be reached only makes the entity unavailable.
    """

    config_entry: EsphomeGitUpdatesConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EsphomeGitUpdatesConfigEntry,
        session: DeviceBuilderSession,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=VERSION_POLL_INTERVAL,
        )
        self._session = session

    async def _async_update_data(self) -> str:
        try:
            async with self._session.connect("version") as client:
                return client.esphome_version
        except DeviceBuilderUnavailable as err:
            raise UpdateFailed(str(err)) from err
