"""The update entity of one Tracked Config, fed by a fake Device Builder."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)

from custom_components.esphome_git_updates.const import DOMAIN
from custom_components.esphome_git_updates.coordinator import protocol_issue_id
from fake_device_builder import FakeDeviceBuilder, serve, server_info
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

ENTITY_ID = "update.host_node_firmware"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components/."""


@pytest.fixture
async def builder() -> AsyncIterator[tuple[str, FakeDeviceBuilder]]:
    """A Device Builder that compiles with ESPHome 2026.10.1."""
    async with serve([server_info(esphome_version="2026.10.1")]) as served:
        yield served


def _entry(url: str, **kwargs: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="host-node",
        unique_id="host-node.yaml",
        data={"url": url, "configuration": "host-node.yaml"},
        **kwargs,
    )


async def _set_up(hass: HomeAssistant, url: str, **kwargs: Any) -> MockConfigEntry:
    entry = _entry(url, **kwargs)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_a_new_entry_assumes_the_device_runs_the_available_version(
    hass: HomeAssistant, builder: tuple[str, FakeDeviceBuilder]
) -> None:
    url, _ = builder
    await _set_up(hass, url)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1"
    assert state.attributes["latest_version"] == "2026.10.1"


async def test_the_entity_sits_on_a_device_the_integration_creates(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    url, _ = builder
    entry = await _set_up(hass, url)

    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    assert [d.name for d in devices] == ["host-node"]
    entities = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    assert [(e.entity_id, e.device_id) for e in entities] == [(ENTITY_ID, devices[0].id)]


async def test_an_update_is_offered_when_the_available_version_differs_from_the_installed_one(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, _ = builder
    entry_id = "tracked"
    hass_storage[f"{DOMAIN}.{entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry_id}",
        "data": {"installed_esphome_version": "2026.10.2"},
    }
    await _set_up(hass, url, entry_id=entry_id)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    # "Newer" means "differs": the installed version is ahead, and that is still an update.
    assert state.state == STATE_ON
    assert state.attributes["installed_version"] == "2026.10.2"
    assert state.attributes["latest_version"] == "2026.10.1"


async def test_the_available_version_is_read_again_every_5_minutes(
    hass: HomeAssistant, builder: tuple[str, FakeDeviceBuilder], freezer: FrozenDateTimeFactory
) -> None:
    url, fake = builder
    await _set_up(hass, url)
    fake.transcript = [server_info(esphome_version="2026.11.0")]

    freezer.tick(timedelta(minutes=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(ENTITY_ID).attributes["latest_version"] == "2026.10.1"  # type: ignore[union-attr]

    # The coordinator spreads refreshes by up to a second.
    freezer.tick(timedelta(minutes=1, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["installed_version"] == "2026.10.1"
    assert state.attributes["latest_version"] == "2026.11.0"


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.parametrize(
    ("handshake", "translation_key"),
    [
        (server_info("2.0.0"), "unsupported_server_version"),
        ({"dir": "in", "frame": {"hello": "world"}}, "unexpected_frame"),
    ],
)
async def test_a_device_builder_that_answers_unexpectedly_raises_a_repair_issue_until_it_is_healthy(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
    handshake: dict[str, Any],
    translation_key: str,
) -> None:
    url, fake = builder
    entry = await _set_up(hass, url)
    healthy = fake.transcript

    fake.transcript = [handshake]
    await _poll(hass, freezer)

    issue = issue_registry.async_get_issue(DOMAIN, protocol_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.translation_key == translation_key
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE  # type: ignore[union-attr]

    fake.transcript = healthy
    await _poll(hass, freezer)

    assert issue_registry.async_get_issue(DOMAIN, protocol_issue_id(entry.entry_id)) is None
    assert hass.states.get(ENTITY_ID).state == STATE_OFF  # type: ignore[union-attr]


async def test_an_unreachable_device_builder_is_not_a_repair_issue(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
) -> None:
    url, fake = builder
    await _set_up(hass, url)

    fake.transcript = [{"dir": "close"}]
    await _poll(hass, freezer)

    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE  # type: ignore[union-attr]
    assert list(issue_registry.issues) == []


async def test_an_entry_whose_device_builder_is_unsupported_at_startup_raises_a_repair_issue(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    async with serve([server_info("2.0.0")]) as (url, _):
        entry = _entry(url)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    issue = issue_registry.async_get_issue(DOMAIN, protocol_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.translation_key == "unsupported_server_version"


async def test_removing_the_entry_drops_its_repair_issue_and_stored_versions(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up(hass, url)
    fake.transcript = [server_info("2.0.0")]
    await _poll(hass, freezer)
    assert list(issue_registry.issues) != []
    assert f"{DOMAIN}.{entry.entry_id}" in hass_storage

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert list(issue_registry.issues) == []
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage
