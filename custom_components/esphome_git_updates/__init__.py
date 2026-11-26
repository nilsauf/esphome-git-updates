"""ESPHome Git Updates: firmware updates for ESPHome configs built from git sources."""

from __future__ import annotations

from homeassistant.const import CONF_DEVICE_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from .connection import DeviceBuilderSession, protocol_issue_id
from .const import DOMAIN
from .coordinator import EsphomeGitUpdatesConfigEntry, EsphomeVersionCoordinator, RuntimeData
from .git_coordinator import GitSourcesCoordinator
from .installed_record import STORAGE_VERSION, InstalledRecord, StoredData, storage_key
from .linked_device import LinkedDevice
from .pre_install import missing_ota_issue_id, refresh_policy_issue_id

PLATFORMS = [Platform.UPDATE]


async def async_setup_entry(hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry) -> bool:
    """Set up one Tracked Config."""
    record = InstalledRecord(
        record_store(hass, entry.entry_id),
        linked_device=CONF_DEVICE_ID in entry.data,
        title=entry.title,
    )
    await record.async_load()
    session = DeviceBuilderSession(hass, entry)
    coordinator = EsphomeVersionCoordinator(hass, entry, session)
    await coordinator.async_config_entry_first_refresh()
    await record.async_seed_esphome_version(coordinator.data)
    linked_device = LinkedDevice.of(hass, entry)
    git_coordinator = GitSourcesCoordinator(hass, entry, record, session)
    # Not a first refresh: Git Sources that cannot be read must not hold up
    # the ESPHome update. The poller logs why and tries again at its next poll.
    await git_coordinator.async_refresh()
    # Not reloaded on an options change: that would interrupt a running Install.
    # The options flow applies the Git poll interval to the loaded entry; Clean
    # Build, the upload address and the GitHub token are read when they are used.
    entry.runtime_data = RuntimeData(coordinator, git_coordinator, record, linked_device, session)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry) -> bool:
    """Unload one Tracked Config."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry) -> None:
    """Forget a removed Tracked Config: its stored versions and its Repair issues."""
    for issue_id in (protocol_issue_id, missing_ota_issue_id, refresh_policy_issue_id):
        ir.async_delete_issue(hass, DOMAIN, issue_id(entry.entry_id))
    await record_store(hass, entry.entry_id).async_remove()


def record_store(hass: HomeAssistant, entry_id: str) -> Store[StoredData]:
    """Where the Installed Record of the config entry *entry_id* is kept."""
    return Store(hass, STORAGE_VERSION, storage_key(entry_id))
