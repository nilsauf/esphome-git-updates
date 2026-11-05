"""ESPHome Git Updates: firmware updates for ESPHome configs built from git sources."""

from __future__ import annotations

from homeassistant.const import CONF_DEVICE_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .connection import DeviceBuilderSession, protocol_issue_id
from .const import DOMAIN
from .coordinator import (
    EsphomeGitUpdatesConfigEntry,
    EsphomeVersionCoordinator,
    RuntimeData,
    TrackedConfigStore,
)
from .git_coordinator import GitSourcesCoordinator
from .linked_device import LinkedDevice
from .options import Options
from .pre_install import missing_ota_issue_id, refresh_policy_issue_id

PLATFORMS = [Platform.UPDATE]


async def async_setup_entry(hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry) -> bool:
    """Set up one Tracked Config."""
    store = TrackedConfigStore(hass, entry.entry_id)
    await store.async_load()
    session = DeviceBuilderSession(hass, entry)
    coordinator = EsphomeVersionCoordinator(hass, entry, session)
    await coordinator.async_config_entry_first_refresh()
    if CONF_DEVICE_ID not in entry.data and store.installed_esphome_version is None:
        # Seeding: a new entry assumes the device already runs the Available
        # ESPHome Version, so it shows no update until the Device Builder's
        # ESPHome changes. An entry with a Linked Device is never seeded, even
        # when that device has since gone (ADR-0003).
        await store.async_set_installed_esphome_version(coordinator.data)
    linked_device = LinkedDevice.of(hass, entry)
    git_coordinator = GitSourcesCoordinator(hass, entry, store, session)
    # Not a first refresh: Git Sources that cannot be read must not hold up
    # the ESPHome update. The poller logs why and tries again at its next poll.
    await git_coordinator.async_refresh()
    entry.runtime_data = RuntimeData(coordinator, git_coordinator, store, linked_device, session)
    # Not reloaded on an options change: that would interrupt a running Install.
    # Clean Build and the upload address are read when Install is pressed.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry
) -> None:
    """Apply changed options to what runs; Clean Build and the upload address are
    read anew at each Install."""
    await entry.runtime_data.git_coordinator.async_set_poll_interval(
        Options.of(entry.options).git_poll_interval
    )


async def async_unload_entry(hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry) -> bool:
    """Unload one Tracked Config."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry) -> None:
    """Forget a removed Tracked Config: its stored versions and its Repair issues."""
    for issue_id in (protocol_issue_id, missing_ota_issue_id, refresh_policy_issue_id):
        ir.async_delete_issue(hass, DOMAIN, issue_id(entry.entry_id))
    await TrackedConfigStore(hass, entry.entry_id).async_remove()
