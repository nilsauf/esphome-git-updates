"""The Installed Record of a config entry, kept in Home Assistant's store as the
integration keeps it, built and read back through the record's own interface.

Under the `hass` fixture the store lives in `hass_storage`, so a record written here
before setup is the one the integration loads, and one read here after a test is what
the integration saved.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from custom_components.esphome_git_updates.installed_record import (
    STORAGE_VERSION,
    InstalledRecord,
    StoredData,
    storage_key,
)


async def stored_record(hass: HomeAssistant, entry_id: str) -> InstalledRecord:
    """The Installed Record of the entry *entry_id*, loaded from Home Assistant's store."""
    store: Store[StoredData] = Store(hass, STORAGE_VERSION, storage_key(entry_id))
    record = InstalledRecord(store, linked_device=False, title=entry_id)
    await record.async_load()
    return record


async def recorded_esphome_version(hass: HomeAssistant, entry_id: str) -> str | None:
    """The Installed ESPHome Version recorded for the entry *entry_id*, if any."""
    return (await stored_record(hass, entry_id)).installed_esphome_version(None)


async def record_an_install(
    hass: HomeAssistant, entry_id: str, esphome_version: str
) -> InstalledRecord:
    """Store the Installed Record of the entry *entry_id* as after a successful Install
    that compiled with *esphome_version*, and return it."""
    record = await stored_record(hass, entry_id)
    await record.async_record_install(esphome_version, {}, {})
    return record
