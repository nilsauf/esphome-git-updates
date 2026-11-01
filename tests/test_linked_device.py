"""The update entity on a Linked Device, whose `sw_version` gives the Installed ESPHome
Version (ADR-0003, ADR-0005)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esphome_git_updates.const import DOMAIN
from fake_device_builder import FakeDeviceBuilder, serve, server_info
from tracked_config import CONFIGURATION, ENTITY_ID, OTA, install, press_install, set_up

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def builder() -> AsyncIterator[FakeDeviceBuilder]:
    """A Device Builder that compiles with ESPHome 2026.10.1; the config has no Git Sources."""
    async with serve(
        [server_info(esphome_version="2026.10.1")],
        {CONFIGURATION: f"esphome:\n  name: host-node\n{OTA}"},
    ) as (_, fake):
        yield fake


def _esphome_device(hass: HomeAssistant, sw_version: str | None) -> dr.DeviceEntry:
    """The device core's ESPHome integration made for host-node."""
    entry = MockConfigEntry(domain="esphome")
    entry.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff")},
        name="host-node",
        sw_version=sw_version,
    )


def _entity_of(hass: HomeAssistant, device: dr.DeviceEntry, state: str) -> None:
    """Give *device* one entity of its own integration, in *state*."""
    entry = er.async_get(hass).async_get_or_create(
        "sensor",
        "esphome",
        "host-node-wifi-signal",
        config_entry=hass.config_entries.async_get_entry(device.config_entry_id),
        device_id=device.id,
    )
    hass.states.async_set(entry.entity_id, state)


def _store(hass_storage: dict[str, Any], **data: Any) -> None:
    hass_storage[f"{DOMAIN}.tracked"] = {"version": 1, "key": f"{DOMAIN}.tracked", "data": data}


async def _set_up(
    hass: HomeAssistant, fake: FakeDeviceBuilder, device: dr.DeviceEntry
) -> MockConfigEntry:
    return await set_up(hass, fake, device_id=device.id)


def _installed(hass: HomeAssistant) -> tuple[str, str | None]:
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    return state.state, state.attributes["installed_version"]


async def test_the_entity_sits_on_the_linked_device_and_no_device_of_its_own(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    device = _esphome_device(hass, "2026.10.1 (config hash 0x1a2b3c4d)")

    entry = await _set_up(hass, builder, device)

    assert dr.async_entries_for_config_entry(device_registry, entry.entry_id) == []
    entity = entity_registry.async_get(ENTITY_ID)
    assert entity is not None
    assert entity.device_id == device.id


async def test_the_installed_esphome_version_is_the_one_the_linked_device_reports(
    hass: HomeAssistant, builder: FakeDeviceBuilder, hass_storage: dict[str, Any]
) -> None:
    # What the device reports wins over what the last Install recorded.
    _store(hass_storage, installed_esphome_version="2026.10.1")
    device = _esphome_device(hass, "2026.10.0 (Oct 31 2026, 12:34:56)")

    await _set_up(hass, builder, device)

    assert _installed(hass) == (STATE_ON, "2026.10.0")


async def test_a_new_sw_version_on_the_linked_device_updates_the_entity(
    hass: HomeAssistant, builder: FakeDeviceBuilder, device_registry: dr.DeviceRegistry
) -> None:
    device = _esphome_device(hass, "1.4.0 (ESPHome 2026.10.0)")
    await _set_up(hass, builder, device)
    assert _installed(hass) == (STATE_ON, "2026.10.0")

    # The device reconnects after an Install, running the new firmware.
    device_registry.async_update_device(device.id, sw_version="1.4.0 (ESPHome 2026.10.1)")
    await hass.async_block_till_done()

    assert _installed(hass) == (STATE_OFF, "2026.10.1")


async def test_an_unparseable_sw_version_falls_back_to_the_version_of_the_last_install(
    hass: HomeAssistant, builder: FakeDeviceBuilder, hass_storage: dict[str, Any]
) -> None:
    _store(hass_storage, installed_esphome_version="2026.10.0")
    device = _esphome_device(hass, "custom firmware")

    await _set_up(hass, builder, device)

    assert _installed(hass) == (STATE_ON, "2026.10.0")


async def test_with_a_linked_device_the_version_is_never_seeded_and_unknown_is_warned_about(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    device = _esphome_device(hass, None)

    with caplog.at_level(logging.WARNING):
        await _set_up(hass, builder, device)

    assert _installed(hass) == (STATE_UNKNOWN, None)
    stored = hass_storage.get(f"{DOMAIN}.tracked", {}).get("data", {})
    assert "installed_esphome_version" not in stored
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("host-node" in w and "ESPHome version" in w for w in warnings)


async def test_a_linked_device_whose_entities_are_all_unavailable_refuses_the_install(
    hass: HomeAssistant, builder: FakeDeviceBuilder
) -> None:
    device = _esphome_device(hass, "2026.10.0 (config hash 0x1a2b3c4d)")
    _entity_of(hass, device, STATE_UNAVAILABLE)
    await _set_up(hass, builder, device)
    polls = builder.transcript
    # Any job the refused Install started would be a mismatch.
    builder.transcript = []

    with pytest.raises(HomeAssistantError, match="device offline"):
        await press_install(hass)

    assert builder.mismatches == []
    builder.transcript = polls
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["in_progress"] is False


async def test_a_linked_device_with_an_available_entity_installs(
    hass: HomeAssistant, builder: FakeDeviceBuilder
) -> None:
    device = _esphome_device(hass, "2026.10.0 (config hash 0x1a2b3c4d)")
    _entity_of(hass, device, "-60")
    await _set_up(hass, builder, device)

    await install(hass, builder, "install_clean_success")

    assert builder.finished


async def test_a_linked_device_that_is_gone_is_still_never_seeded_for(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    hass_storage: dict[str, Any],
    device_registry: dr.DeviceRegistry,
) -> None:
    device = _esphome_device(hass, "2026.10.0 (config hash 0x1a2b3c4d)")
    device_registry.async_remove_device(device.id)

    entry = await _set_up(hass, builder, device)

    # The entity falls back to a device of its own, but claims no version.
    assert len(dr.async_entries_for_config_entry(device_registry, entry.entry_id)) == 1
    assert _installed(hass) == (STATE_UNKNOWN, None)
    stored = hass_storage.get(f"{DOMAIN}.tracked", {}).get("data", {})
    assert "installed_esphome_version" not in stored
