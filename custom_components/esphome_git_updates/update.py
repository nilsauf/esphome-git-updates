"""The one update entity of a Tracked Config (ADR-0005)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, assert_never

from homeassistant.components import persistent_notification
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo, EventDeviceRegistryUpdatedData
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_device_registry_updated_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CONFIGURATION, DOMAIN
from .coordinator import EsphomeGitUpdatesConfigEntry, EsphomeVersionCoordinator
from .git_coordinator import GitSourcesCoordinator
from .install import (
    ActiveInstall,
    DeviceOffline,
    Failed,
    Installed,
    InstallEnding,
    InstallRunner,
    OutcomeUnknown,
)
from .installed_record import InstalledRecord
from .linked_device import LinkedDevice
from .options import Options
from .versions import Versions


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EsphomeGitUpdatesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the Tracked Config's one update entity."""
    async_add_entities([TrackedConfigUpdate(entry)])


class TrackedConfigUpdate(CoordinatorEntity[EsphomeVersionCoordinator], UpdateEntity):
    """Offers an update when any Source differs from what is installed.

    The Sources are the ESPHome version (polled by the coordinator) and every
    Git Source on a Floating Ref (polled by the git coordinator). It sits on
    the Linked Device if there is one, else on a device of its own (ADR-0005).
    """

    _attr_has_entity_name = True
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    # PROGRESS so that in_progress is the entity's own: a re-attached Install
    # runs outside async_install, and both set update_percentage (ticket 14).
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, entry: EsphomeGitUpdatesConfigEntry) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._notification_id = f"{DOMAIN}_{entry.entry_id}_install"
        self._record: InstalledRecord = entry.runtime_data.record
        self._git: GitSourcesCoordinator = entry.runtime_data.git_coordinator
        self._linked: LinkedDevice | None = entry.runtime_data.linked_device
        self._runner = InstallRunner(
            entry.data[CONF_CONFIGURATION],
            entry.runtime_data.session,
            self._record,
        )
        self._attr_unique_id = entry.entry_id
        if self._linked is not None:
            # Set explicitly: HA no longer merges devices across config entries.
            self.device_entry = self._linked.device_entry
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)},
                name=entry.title,
                model=entry.data[CONF_CONFIGURATION],
            )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._git.async_add_listener(self._handle_coordinator_update))
        if self._linked is not None:
            self.async_on_remove(
                async_track_device_registry_updated_event(
                    self.hass, self._linked.device_id, self._linked_device_updated
                )
            )
            self._record.warn_if_esphome_version_unknown(self._linked.sw_version)
        if (active := self._record.active_install) is not None:
            self._reattach(active)

    @callback
    def _linked_device_updated(self, event: Event[EventDeviceRegistryUpdatedData]) -> None:
        if event.data["action"] == "update" and "sw_version" in event.data["changes"]:
            if self._linked is not None:
                self._record.warn_if_esphome_version_unknown(self._linked.sw_version)
            self.async_write_ha_state()

    def _installed_esphome(self) -> str | None:
        return self._record.installed_esphome_version(
            self._linked.sw_version if self._linked is not None else None
        )

    @property
    def installed_version(self) -> str | None:
        return self._versions().installed

    @property
    def latest_version(self) -> str | None:
        return self._versions().latest

    @property
    def release_summary(self) -> str | None:
        return self._versions().release_summary

    async def async_release_notes(self) -> str | None:
        """What changed in each Source; GitHub Sources list their commits."""
        await self._git.async_compare_changed()
        return self._versions().release_notes

    def _versions(self) -> Versions:
        return Versions(
            installed_esphome=self._installed_esphome(),
            available_esphome=self.coordinator.data,
            git_sources=self._git.shown_sources(),
        )

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        # "Newer" means "differs": a Device Builder can also move back a version.
        return latest_version != installed_version

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Run an Install: compile, then upload over the air.

        The outcome is told through a persistent notification; the full
        output stays in the Device Builder, nothing is written to /config.
        """
        options = Options.of(self._entry.options)
        device_offline = self._linked is not None and self._linked.is_offline(self.entity_id)
        # In progress from here, so a second press while this one is being
        # prepared is refused rather than starting a second Install.
        # The bar is 0 until the first progress arrives.
        self._set_state(True, 0)
        try:
            plan = await self._git.async_prepare_install(options, device_offline)
        except BaseException:
            self._set_state(False, None)
            raise
        task = self._start(lambda: self._runner.run(plan, self._set_percentage), reattached=False)
        # Not awaited directly: cancelling the action must not cancel the Install.
        await asyncio.wait([task])
        if task.cancelled():
            raise self._error("install_interrupted")
        task.result()

    def _set_state(self, in_progress: bool, percentage: int | None) -> None:
        """Write both Install attributes in one state change."""
        self._attr_in_progress = in_progress
        self._attr_update_percentage = percentage
        self.async_write_ha_state()

    def _set_percentage(self, percentage: int) -> None:
        self._set_state(True, percentage)

    def _reattach(self, active: ActiveInstall) -> None:
        """Finish the Install that was running when Home Assistant stopped (ADR-0002)."""
        self._start(lambda: self._runner.reattach(active, self._set_percentage), reattached=True)

    def _start(
        self, run: Callable[[], Awaitable[InstallEnding]], *, reattached: bool
    ) -> asyncio.Task[None]:
        """Run an Install as a task of the entry.

        Unloading the entry, as stopping Home Assistant does, cancels the task
        but leaves its jobs in the Installed Record, to be re-attached on the next setup.
        """
        self._set_state(True, 0)
        return self._entry.async_create_background_task(
            self.hass,
            self._install(run, reattached),
            f"{DOMAIN} install {self._entry.title}",
        )

    async def _install(self, run: Callable[[], Awaitable[InstallEnding]], reattached: bool) -> None:
        try:
            ending = await run()
        except asyncio.CancelledError:
            # It stays in progress, and is re-attached on the next setup.
            raise
        except BaseException:
            self._set_state(False, None)
            raise
        self._set_state(False, None)
        if ending.notification is None:
            persistent_notification.async_dismiss(self.hass, self._notification_id)
        else:
            self._notify(*ending.notification)
        # Nobody waits for a re-attached Install: its notification says how it went.
        if not isinstance(ending, Installed) and not reattached:
            raise self._ending_error(ending)

    def _ending_error(self, ending: Failed | DeviceOffline | OutcomeUnknown) -> HomeAssistantError:
        """The translated error failing the Install action with *ending*."""
        match ending:
            case Failed(stage=stage, error=error):
                return self._error("install_failed", {"stage": str(stage), "error": error})
            case DeviceOffline():
                return self._error("install_failed_device_offline")
            case OutcomeUnknown():
                return self._error("install_outcome_unknown")
            case _:
                assert_never(ending)

    def _error(
        self, translation_key: str, placeholders: dict[str, str] | None = None
    ) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders={"title": self._entry.title, **(placeholders or {})},
        )

    def _notify(self, headline: str, message: str) -> None:
        persistent_notification.async_create(
            self.hass,
            message,
            title=f"{self._entry.title}: {headline}",
            notification_id=self._notification_id,
        )
