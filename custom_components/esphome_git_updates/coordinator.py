"""Read the Available ESPHome Version from the Device Builder, and keep the Installed one."""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .connection import CONNECTION_ERRORS, connect_to_device_builder
from .const import DOMAIN, VERSION_POLL_INTERVAL
from .device_builder import UnexpectedFrame, UnsupportedServerVersion

if TYPE_CHECKING:
    from .git_coordinator import GitSourcesCoordinator

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


@dataclass
class RuntimeData:
    """What a set-up entry holds while it runs."""

    coordinator: EsphomeVersionCoordinator
    git_coordinator: GitSourcesCoordinator
    store: TrackedConfigStore


type EsphomeGitUpdatesConfigEntry = ConfigEntry[RuntimeData]


def protocol_issue_id(entry_id: str) -> str:
    """The Repair issue raised when an entry's Device Builder answers unexpectedly."""
    return f"device_builder_protocol_{entry_id}"


class ProtocolIssue:
    """The one protocol-drift Repair issue of an entry (ADR-0002).

    Several pollers talk to the Device Builder. Each reports its own problem
    under its own name, and the issue stays until every one of them is healthy
    again, so one poller's success does not hide another's drift.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._title = entry.title
        self._issue_id = protocol_issue_id(entry.entry_id)
        self._problems: dict[str, tuple[str, dict[str, str]]] = {}

    def report(self, poller: str, translation_key: str, placeholders: dict[str, str]) -> None:
        self._problems[poller] = (translation_key, placeholders)
        self._raise(translation_key, placeholders)

    def clear(self, poller: str) -> None:
        if self._problems.pop(poller, None) is not None and self._problems:
            # Another poller's problem remains: show that one.
            self._raise(*next(iter(self._problems.values())))
        elif not self._problems:
            ir.async_delete_issue(self._hass, DOMAIN, self._issue_id)

    def _raise(self, translation_key: str, placeholders: dict[str, str]) -> None:
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=translation_key,
            translation_placeholders={"title": self._title, **placeholders},
        )


class _StoredData(TypedDict, total=False):
    installed_esphome_version: str
    # Git Source key -> Installed Commit.
    installed_commits: dict[str, str]


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

    async def async_record_install(self, esphome_version: str, commits: Mapping[str, str]) -> None:
        """Record a successful Install: the version compiled with, and the commits seen
        when Install was pressed (ADR-0003)."""
        self._data["installed_esphome_version"] = esphome_version
        self._data["installed_commits"] = {**self._data.get("installed_commits", {}), **commits}
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
        self, hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry, issue: ProtocolIssue
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=VERSION_POLL_INTERVAL,
        )
        self._issue = issue

    async def _async_update_data(self) -> str:
        try:
            async with connect_to_device_builder(self.hass, self.config_entry.data) as client:
                version = client.esphome_version
        except (UnsupportedServerVersion, UnexpectedFrame) as err:
            report_protocol_drift(self._issue, "version", err)
            raise UpdateFailed(str(err)) from err
        except CONNECTION_ERRORS as err:
            raise UpdateFailed(f"cannot reach the Device Builder: {err}") from err
        self._issue.clear("version")
        return version


def report_protocol_drift(
    issue: ProtocolIssue, poller: str, err: UnsupportedServerVersion | UnexpectedFrame
) -> None:
    if isinstance(err, UnsupportedServerVersion):
        issue.report(poller, "unsupported_server_version", {"server_version": err.server_version})
    else:
        issue.report(poller, "unexpected_frame", {"error": str(err)})
