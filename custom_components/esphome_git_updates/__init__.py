"""ESPHome Git Updates: firmware updates for ESPHome configs built from git sources."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .coordinator import (
    EsphomeGitUpdatesConfigEntry,
    EsphomeVersionCoordinator,
    ProtocolIssue,
    RuntimeData,
    TrackedConfigStore,
    protocol_issue_id,
)
from .git_coordinator import GitSourcesCoordinator
from .pre_install import delete_pre_install_issues

PLATFORMS = [Platform.UPDATE]


async def async_setup_entry(hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry) -> bool:
    """Set up one Tracked Config."""
    store = TrackedConfigStore(hass, entry.entry_id)
    await store.async_load()
    issue = ProtocolIssue(hass, entry)
    coordinator = EsphomeVersionCoordinator(hass, entry, issue)
    await coordinator.async_config_entry_first_refresh()
    if store.installed_esphome_version is None:
        # Seeding: a new entry assumes the device already runs the Available
        # ESPHome Version, so it shows no update until the Device Builder's
        # ESPHome changes. ADR-0003's Linked Device reading replaces this later.
        await store.async_set_installed_esphome_version(coordinator.data)
    git_coordinator = GitSourcesCoordinator(hass, entry, store, issue)
    # Not a first refresh: Git Sources that cannot be read must not hold up
    # the ESPHome update. The poller logs why and tries again in 30 min.
    await git_coordinator.async_refresh()
    entry.runtime_data = RuntimeData(coordinator, git_coordinator, store)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry) -> bool:
    """Unload one Tracked Config."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry) -> None:
    """Forget a removed Tracked Config: its stored versions and its Repair issues."""
    ir.async_delete_issue(hass, DOMAIN, protocol_issue_id(entry.entry_id))
    delete_pre_install_issues(hass, entry.entry_id)
    await TrackedConfigStore(hass, entry.entry_id).async_remove()
