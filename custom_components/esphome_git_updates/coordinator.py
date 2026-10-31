"""Read the Available ESPHome Version from the Device Builder, and keep the Installed one."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TypedDict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .connection import CONNECTION_ERRORS, connect_to_device_builder
from .const import DOMAIN, VERSION_POLL_INTERVAL
from .device_builder import UnexpectedFrame, UnsupportedServerVersion

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


@dataclass
class RuntimeData:
    """What a set-up entry holds while it runs."""

    coordinator: EsphomeVersionCoordinator
    store: TrackedConfigStore


type EsphomeGitUpdatesConfigEntry = ConfigEntry[RuntimeData]


def protocol_issue_id(entry_id: str) -> str:
    """The Repair issue raised when an entry's Device Builder answers unexpectedly."""
    return f"device_builder_protocol_{entry_id}"


class _StoredData(TypedDict, total=False):
    installed_esphome_version: str


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


class EsphomeVersionCoordinator(DataUpdateCoordinator[str]):
    """Polls the Device Builder; its data is the Available ESPHome Version.

    Each poll is one short connection: the version arrives in the handshake.
    A Device Builder that answers in a way the client does not understand
    raises a Repair issue (ADR-0002); the next healthy poll clears it. One
    that cannot be reached only makes the entity unavailable.
    """

    config_entry: EsphomeGitUpdatesConfigEntry

    def __init__(self, hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=VERSION_POLL_INTERVAL,
        )
        self._issue_id = protocol_issue_id(entry.entry_id)

    async def _async_update_data(self) -> str:
        try:
            async with connect_to_device_builder(self.hass, self.config_entry.data) as client:
                version = client.esphome_version
        except UnsupportedServerVersion as err:
            self._raise_issue("unsupported_server_version", {"server_version": err.server_version})
            raise UpdateFailed(str(err)) from err
        except UnexpectedFrame as err:
            self._raise_issue("unexpected_frame", {"error": str(err)})
            raise UpdateFailed(str(err)) from err
        except CONNECTION_ERRORS as err:
            raise UpdateFailed(f"cannot reach the Device Builder: {err}") from err
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        return version

    def _raise_issue(self, translation_key: str, placeholders: dict[str, str]) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=translation_key,
            translation_placeholders={"title": self.config_entry.title, **placeholders},
        )
