"""The one update entity of a Tracked Config (ADR-0005)."""

from __future__ import annotations

from typing import Any, NoReturn

from homeassistant.components import persistent_notification
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .connection import CONNECTION_ERRORS, connect_to_device_builder
from .const import CONF_CONFIGURATION, DOMAIN
from .coordinator import (
    EsphomeGitUpdatesConfigEntry,
    EsphomeVersionCoordinator,
    TrackedConfigStore,
)
from .device_builder import InstallOutcome
from .install_log import InstallLog


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EsphomeGitUpdatesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the Tracked Config's one update entity."""
    async_add_entities([TrackedConfigUpdate(entry)])


class TrackedConfigUpdate(CoordinatorEntity[EsphomeVersionCoordinator], UpdateEntity):
    """Offers an update when the Available ESPHome Version differs from the installed one.

    Until a Linked Device is supported, it sits on a device of its own.
    """

    _attr_has_entity_name = True
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    # No PROGRESS: follow_job frames carry no progress (ticket 01), so Home
    # Assistant's own in_progress handling around async_install is enough.
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, entry: EsphomeGitUpdatesConfigEntry) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._notification_id = f"{DOMAIN}_{entry.entry_id}_install"
        self._store: TrackedConfigStore = entry.runtime_data.store
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            model=entry.data[CONF_CONFIGURATION],
        )

    @property
    def installed_version(self) -> str | None:
        return self._store.installed_esphome_version

    @property
    def latest_version(self) -> str | None:
        return self.coordinator.data

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        # "Newer" means "differs": a Device Builder can also move back a version.
        return latest_version != installed_version

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Run an Install: compile, then upload over the air.

        The outcome is told through a persistent notification; the full
        output stays in the Device Builder, nothing is written to /config.
        """
        log = InstallLog()
        try:
            async with connect_to_device_builder(
                self.hass, self._entry.data, long_running=True
            ) as client:
                # The first job's output names its stage; until then, it is compile.
                log.stage = "compile"
                result = await client.install(
                    self._entry.data[CONF_CONFIGURATION], on_output=log
                )
                esphome_version = client.esphome_version
        except CONNECTION_ERRORS as err:
            self._fail(log.stage, str(err), log)
        if result.outcome is not InstallOutcome.COMPLETED:
            self._fail(result.stage, result.error or result.outcome.value, log)
        await self._store.async_set_installed_esphome_version(esphome_version)
        if log.warnings:
            self._notify("Installed with warnings", log.warnings_text())
        else:
            persistent_notification.async_dismiss(self.hass, self._notification_id)

    def _fail(self, stage: str, error: str, log: InstallLog) -> NoReturn:
        """Tell the user why the Install failed, then fail the action."""
        self._notify("Install failed", log.failure_text(stage, error))
        raise HomeAssistantError(f"Install of {self._entry.title} failed at {stage}: {error}")

    def _notify(self, headline: str, message: str) -> None:
        persistent_notification.async_create(
            self.hass,
            message,
            title=f"{self._entry.title}: {headline}",
            notification_id=self._notification_id,
        )
