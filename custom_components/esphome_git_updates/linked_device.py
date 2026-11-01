"""The Linked Device: the ESPHome or MQTT device a Tracked Config's update entity sits on
(ADR-0005), and that reports the Installed ESPHome Version (ADR-0003)."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .versions import parse_sw_version

_LOGGER = logging.getLogger(__name__)


class LinkedDevice:
    """A Tracked Config's Linked Device, read from the registries whenever asked."""

    def __init__(self, hass: HomeAssistant, device_id: str) -> None:
        self._hass = hass
        self.device_id = device_id

    @classmethod
    def of(cls, hass: HomeAssistant, entry: ConfigEntry) -> LinkedDevice | None:
        """The entry's Linked Device; None when none was picked or it is gone."""
        device_id: str | None = entry.data.get(CONF_DEVICE_ID)
        if device_id is None:
            return None
        if dr.async_get(hass).async_get(device_id) is None:
            _LOGGER.warning(
                "%s: its Linked Device no longer exists; "
                "the update entity gets a device of its own",
                entry.title,
            )
            return None
        return cls(hass, device_id)

    @property
    def device_entry(self) -> dr.AnyDeviceEntry | None:
        return dr.async_get(self._hass).async_get(self.device_id)

    @property
    def sw_version(self) -> str | None:
        """The firmware version the device reports in the registry."""
        device = self.device_entry
        return device.sw_version if isinstance(device, dr.DeviceEntry) else None

    @property
    def esphome_version(self) -> str | None:
        """The ESPHome version in the device's `sw_version`, if it can be parsed."""
        return parse_sw_version(self.sw_version)

    def is_offline(self, own_entity_id: str | None) -> bool:
        """Whether every entity the device has, other than *own_entity_id*, is unavailable.

        A device with no other entity gives no sign either way, so it is not offline.
        """
        entities = [
            entity.entity_id
            for entity in er.async_entries_for_device(er.async_get(self._hass), self.device_id)
            if entity.entity_id != own_entity_id
        ]
        return bool(entities) and all(
            (state := self._hass.states.get(entity_id)) is None or state.state == STATE_UNAVAILABLE
            for entity_id in entities
        )
