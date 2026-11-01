"""Config flow: the ESPHome add-on or a manual URL, then one Tracked Config from its list."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esphome_git_updates.const import DOMAIN
from fake_device_builder import load_fixture, port_of, serve, server_info


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components/."""


@pytest.fixture(autouse=True)
def _no_setup() -> Generator[AsyncMock]:
    """These tests stop at the created entry; setting it up is tested elsewhere."""
    with patch(
        "custom_components.esphome_git_updates.async_setup_entry", return_value=True
    ) as setup:
        yield setup


async def _start_addon(hass: HomeAssistant, **user_input: str) -> ConfigFlowResult:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["addon", "manual"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "addon"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "addon"
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_the_esphome_add_on_is_found_through_the_supervisor(
    hass: HomeAssistant, supervisor: MagicMock
) -> None:
    async with serve(load_fixture("list")) as (url, fake):
        supervisor.addons_installed = {"5c53de3b_esphome": port_of(url)}
        result = await _start_addon(hass)

    assert fake.mismatches == []
    assert result["step_id"] == "configuration"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"configuration": "host-node.yaml"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"addon_slug": "5c53de3b_esphome", "configuration": "host-node.yaml"}


async def test_a_non_standard_add_on_slug_can_be_given(
    hass: HomeAssistant, supervisor: MagicMock
) -> None:
    async with serve(load_fixture("list")) as (url, _):
        supervisor.addons_installed = {"local_esphome": port_of(url)}
        result = await _start_addon(hass, addon_slug="local_esphome")

    assert result["step_id"] == "configuration"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"configuration": "host-node.yaml"}
    )
    assert result["data"] == {"addon_slug": "local_esphome", "configuration": "host-node.yaml"}


def _core_esphome_knows_device_builder(hass: HomeAssistant, url: str) -> None:
    """Stand in for core's esphome integration having discovered a Device Builder at *url*."""
    dashboard = SimpleNamespace(addon_slug="5c53de3b_esphome", url=url)
    hass.data["esphome_dashboard_manager"] = SimpleNamespace(async_get=lambda: dashboard)


async def test_without_the_add_on_the_device_builder_core_esphome_discovered_is_used(
    hass: HomeAssistant, supervisor: MagicMock
) -> None:
    async with serve(load_fixture("list")) as (url, fake):
        _core_esphome_knows_device_builder(hass, url)
        result = await _start_addon(hass)

    assert fake.mismatches == []
    assert result["step_id"] == "configuration"


async def test_neither_the_add_on_nor_a_discovered_device_builder_is_reported(
    hass: HomeAssistant, supervisor: MagicMock
) -> None:
    result = await _start_addon(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "addon"
    assert result["errors"] == {"base": "addon_not_found"}


async def _start(hass: HomeAssistant, url: str, **credentials: str) -> ConfigFlowResult:
    """Without a Supervisor the flow goes straight to the manual URL form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": url, **credentials}
    )


async def test_the_chosen_configuration_becomes_the_tracked_config(hass: HomeAssistant) -> None:
    async with serve(load_fixture("auth_login_list")) as (url, fake):
        result = await _start(hass, url, username="admin", password="secret")

    assert fake.mismatches == []
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "configuration"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"configuration": "host-node.yaml"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "host-node"
    assert result["data"] == {
        "url": url,
        "username": "admin",
        "password": "secret",
        "configuration": "host-node.yaml",
    }


async def test_a_refused_login_is_reported_and_the_form_is_shown_again(hass: HomeAssistant) -> None:
    async with serve(load_fixture("auth_login_rejected")) as (url, _):
        result = await _start(hass, url, username="admin", password="wrong")

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_missing_credentials_for_a_protected_builder_are_reported(hass: HomeAssistant) -> None:
    async with serve([server_info(requires_auth=True)]) as (url, _):
        result = await _start(hass, url)

    assert result["errors"] == {"base": "invalid_auth"}


async def test_a_url_nothing_listens_on_is_reported(hass: HomeAssistant) -> None:
    async with serve([]) as (url, _):
        pass  # the server is gone once this block ends

    result = await _start(hass, url)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_an_unsupported_device_builder_is_reported(hass: HomeAssistant) -> None:
    async with serve([server_info("2.0.0")]) as (url, _):
        result = await _start(hass, url)

    assert result["errors"] == {"base": "unsupported_version"}


async def test_a_configuration_that_is_already_tracked_is_refused(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="host-node.yaml").add_to_hass(hass)
    async with serve(load_fixture("auth_login_list")) as (url, _):
        result = await _start(hass, url, username="admin", password="secret")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"configuration": "host-node.yaml"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
