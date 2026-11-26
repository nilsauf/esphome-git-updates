"""Reconfigure the Device Builder Location of an entry without removing it."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esphome_git_updates.const import DOMAIN
from fake_device_builder import Transcript, load_fixture, port_of, serve, server_info

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

MANUAL = {
    "url": "http://old-builder.lan:6052",
    "username": "admin",
    "password": "secret",
    "configuration": "host-node.yaml",
}
ADDON = {"addon_slug": "5c53de3b_esphome", "configuration": "host-node.yaml"}


@pytest.fixture
def setup_entry() -> Generator[AsyncMock]:
    """These tests stop at the saved entry; reloading it for real is tested below."""
    with patch(
        "custom_components.esphome_git_updates.async_setup_entry", return_value=True
    ) as setup:
        yield setup


def _entry(hass: HomeAssistant, data: dict[str, str]) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="host-node", unique_id="host-node.yaml", data=data)
    entry.add_to_hass(hass)
    return entry


async def _start(hass: HomeAssistant, entry: MockConfigEntry) -> ConfigFlowResult:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )


async def _choose(hass: HomeAssistant, result: ConfigFlowResult, mode: str) -> ConfigFlowResult:
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "reconfigure"
    assert result["menu_options"] == ["addon", "manual"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": mode}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == mode
    return result


def _suggested(result: ConfigFlowResult) -> dict[str, Any]:
    schema = result["data_schema"]
    assert schema is not None
    return {
        str(key): key.description["suggested_value"]
        for key in schema.schema
        if key.description and "suggested_value" in key.description
    }


def _list_without_the_tracked_config() -> Transcript:
    frames = load_fixture("list")
    listing = frames[-1]["frame"]["result"]
    configured = [c for c in listing["configured"] if c["configuration"] != "host-node.yaml"]
    return [
        *frames[:-1],
        {
            **frames[-1],
            "frame": {**frames[-1]["frame"], "result": {**listing, "configured": configured}},
        },
    ]


@pytest.mark.usefixtures("setup_entry")
async def test_a_manual_entry_is_reconfigured_from_its_url_form_pre_filled(
    hass: HomeAssistant, setup_entry: AsyncMock
) -> None:
    entry = _entry(hass, {**MANUAL, "device_id": "linked"})

    result = await _start(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert _suggested(result) == {
        "url": "http://old-builder.lan:6052",
        "username": "admin",
        "password": "secret",
    }

    async with serve(load_fixture("auth_login_list")) as (url, fake):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": url, "username": "admin", "password": "new"}
        )
    await hass.async_block_till_done()

    assert fake.mismatches == []
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert dict(entry.data) == {
        "url": url,
        "username": "admin",
        "password": "new",
        "configuration": "host-node.yaml",
        "device_id": "linked",
    }
    assert entry.unique_id == "host-node.yaml"
    # Saving reloads the entry, which sets it up again.
    assert setup_entry.call_count == 1


async def test_an_add_on_entry_offers_the_menu_and_its_slug_pre_filled(
    hass: HomeAssistant, supervisor: MagicMock, setup_entry: AsyncMock
) -> None:
    entry = _entry(hass, ADDON)

    result = await _choose(hass, await _start(hass, entry), "addon")
    assert _suggested(result) == {"addon_slug": "5c53de3b_esphome"}

    async with serve(load_fixture("list")) as (url, _):
        supervisor.addons_installed = {"local_esphome": port_of(url)}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"addon_slug": "local_esphome"}
        )
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert dict(entry.data) == {"addon_slug": "local_esphome", "configuration": "host-node.yaml"}


async def test_moving_off_the_add_on_to_a_url_drops_the_slug(
    hass: HomeAssistant, supervisor: MagicMock, setup_entry: AsyncMock
) -> None:
    entry = _entry(hass, {**ADDON, "device_id": "linked"})

    result = await _choose(hass, await _start(hass, entry), "manual")
    assert _suggested(result) == {}

    async with serve(load_fixture("list")) as (url, _):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": url})
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert dict(entry.data) == {
        "url": url,
        "configuration": "host-node.yaml",
        "device_id": "linked",
    }


async def test_moving_to_the_add_on_drops_the_url_and_credentials(
    hass: HomeAssistant, supervisor: MagicMock, setup_entry: AsyncMock
) -> None:
    entry = _entry(hass, MANUAL)

    result = await _choose(hass, await _start(hass, entry), "addon")
    # Nothing to carry over from the URL: the add-on form shows its default slug.
    assert _suggested(result) == {}

    async with serve(load_fixture("list")) as (url, _):
        supervisor.addons_installed = {"5c53de3b_esphome": port_of(url)}
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert dict(entry.data) == ADDON


@pytest.mark.parametrize(
    ("transcript", "error"),
    [
        (load_fixture("auth_login_rejected"), "invalid_auth"),
        ([server_info("2.0.0")], "unsupported_version"),
    ],
)
@pytest.mark.usefixtures("setup_entry")
async def test_a_device_builder_that_refuses_is_reported_and_nothing_is_saved(
    hass: HomeAssistant, transcript: Transcript, error: str
) -> None:
    entry = _entry(hass, MANUAL)
    result = await _start(hass, entry)

    async with serve(transcript) as (url, _):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": url, "username": "admin", "password": "wrong"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": error}
    assert dict(entry.data) == MANUAL


@pytest.mark.usefixtures("setup_entry")
async def test_a_url_nothing_listens_on_is_reported(hass: HomeAssistant) -> None:
    entry = _entry(hass, MANUAL)
    result = await _start(hass, entry)
    async with serve([]) as (url, _):
        pass  # the server is gone once this block ends

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": url})

    assert result["errors"] == {"base": "cannot_connect"}
    assert dict(entry.data) == MANUAL


@pytest.mark.usefixtures("setup_entry")
async def test_an_add_on_that_cannot_be_found_is_reported(
    hass: HomeAssistant, supervisor: MagicMock
) -> None:
    entry = _entry(hass, ADDON)
    result = await _choose(hass, await _start(hass, entry), "addon")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"addon_slug": "gone"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "addon_not_found"}
    assert dict(entry.data) == ADDON


@pytest.mark.usefixtures("setup_entry")
async def test_a_builder_that_does_not_list_the_tracked_config_is_refused(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass, MANUAL)
    result = await _start(hass, entry)

    async with serve(_list_without_the_tracked_config()) as (url, _):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": url})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "configuration_not_listed"
    assert result["description_placeholders"] == {"configuration": "host-node.yaml"}
    assert dict(entry.data) == MANUAL
