"""The version coordinator, which polls the Device Builder for the Available ESPHome
Version, and the runtime data a set-up entry holds."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .connection import DeviceBuilderSession, DeviceBuilderUnavailable
from .const import DOMAIN, VERSION_POLL_INTERVAL

if TYPE_CHECKING:
    from .git_coordinator import GitSourcesCoordinator
    from .installed_record import InstalledRecord
    from .linked_device import LinkedDevice

_LOGGER = logging.getLogger(__name__)


@dataclass
class RuntimeData:
    """What a set-up entry holds while it runs."""

    coordinator: EsphomeVersionCoordinator
    git_coordinator: GitSourcesCoordinator
    record: InstalledRecord
    linked_device: LinkedDevice | None
    session: DeviceBuilderSession


type EsphomeGitUpdatesConfigEntry = ConfigEntry[RuntimeData]


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
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="device_builder_unavailable",
                translation_placeholders={"title": self.config_entry.title, "error": str(err)},
            ) from err
