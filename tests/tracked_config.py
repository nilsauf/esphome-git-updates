"""Set up one Tracked Config against the fake Device Builder, and drive it.

Shared by the tests that run the integration with Git Sources.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.esphome_git_updates.const import DOMAIN
from fake_device_builder import FakeDeviceBuilder, install_transcript

ENTITY_ID = "update.host_node_firmware"
CONFIGURATION = "host-node.yaml"
# The `ota:` block that lets the Device Builder upload over the air.
OTA = "ota:\n  - platform: esphome\n"


async def set_up(
    hass: HomeAssistant,
    fake: FakeDeviceBuilder,
    *,
    options: dict[str, Any] | None = None,
    **data: str,
) -> MockConfigEntry:
    """Set up the entry `tracked` for CONFIGURATION on *fake*, with any further *data*
    and its *options*."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="host-node",
        unique_id=CONFIGURATION,
        data={"url": fake.url, "configuration": CONFIGURATION, **data},
        options=options or {},
        entry_id="tracked",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def press_install(hass: HomeAssistant) -> None:
    await hass.services.async_call("update", "install", {"entity_id": ENTITY_ID}, blocking=True)
    await hass.async_block_till_done()


async def install(hass: HomeAssistant, fake: FakeDeviceBuilder, fixture: str) -> None:
    """Press Install against a captured Install, then go back to answering polls."""
    polls = fake.transcript
    fake.transcript = install_transcript(fixture, CONFIGURATION)
    try:
        await press_install(hass)
    finally:
        assert fake.mismatches == []
        fake.transcript = polls


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: float) -> None:
    """Move time on by *minutes* and let the polls that fall due run."""
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
