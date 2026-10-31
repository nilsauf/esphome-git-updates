"""Config flow: a manual Device Builder URL, then one Tracked Config from its list."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esphome_git_updates.const import DOMAIN
from fake_device_builder import load_fixture, serve, server_info


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


async def _start(hass: HomeAssistant, url: str, **credentials: str) -> ConfigFlowResult:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
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
    assert result["step_id"] == "user"
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
