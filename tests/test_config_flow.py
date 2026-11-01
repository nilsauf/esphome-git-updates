"""Config flow: the ESPHome add-on or a manual URL, then one Tracked Config from its list."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esphome_git_updates.const import DOMAIN
from fake_device_builder import load_fixture, port_of, serve, server_info


pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def _no_setup() -> Generator[AsyncMock]:
    """These tests stop at the created entry; setting it up is tested elsewhere."""
    with patch(
        "custom_components.esphome_git_updates.async_setup_entry", return_value=True
    ) as setup:
        yield setup


async def _pick(
    hass: HomeAssistant, result: ConfigFlowResult, configuration: str, **device: str
) -> ConfigFlowResult:
    """Pick *configuration*, then the Linked Device (none unless *device* gives `device_id`)."""
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"configuration": configuration}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "device"
    return await hass.config_entries.flow.async_configure(result["flow_id"], dict(device))


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
    result = await _pick(hass, result, "host-node.yaml")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"addon_slug": "5c53de3b_esphome", "configuration": "host-node.yaml"}


async def test_a_non_standard_add_on_slug_can_be_given(
    hass: HomeAssistant, supervisor: MagicMock
) -> None:
    async with serve(load_fixture("list")) as (url, _):
        supervisor.addons_installed = {"local_esphome": port_of(url)}
        result = await _start_addon(hass, addon_slug="local_esphome")

    assert result["step_id"] == "configuration"
    result = await _pick(hass, result, "host-node.yaml")
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
    assert result["step_id"] == "device"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

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


def _device(
    hass: HomeAssistant, domain: str, name: str, mac: str = "aa:bb:cc:dd:ee:ff"
) -> dr.DeviceEntry:
    """A device of another integration's config entry."""
    entry = MockConfigEntry(domain=domain)
    entry.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
        name=name,
    )


def _device_selector(result: ConfigFlowResult) -> tuple[dict[str, Any], str | None]:
    """The device step's selector config, and the device it pre-selects."""
    schema = result["data_schema"]
    assert schema is not None
    (key,) = schema.schema
    assert key == "device_id"
    suggested = (key.description or {}).get("suggested_value")
    return schema.schema[key].config, suggested


async def _to_device_step(hass: HomeAssistant) -> ConfigFlowResult:
    async with serve(load_fixture("auth_login_list")) as (url, _):
        result = await _start(hass, url, username="admin", password="secret")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"configuration": "host-node.yaml"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "device"
    return result


async def test_the_device_picker_offers_esphome_and_mqtt_devices_and_pre_selects_the_yamls_name(
    hass: HomeAssistant,
) -> None:
    _device(hass, "esphome", "kitchen", mac="11:22:33:44:55:66")
    # Another integration's device of the same name is not the one.
    _device(hass, "zha", "host-node", mac="22:33:44:55:66:77")
    # The registry's name is the friendly one: `Host Node` for `name: host-node`.
    linked = _device(hass, "esphome", "Host Node")

    result = await _to_device_step(hass)

    config, suggested = _device_selector(result)
    assert config["filter"] == [{"integration": "esphome"}, {"integration": "mqtt"}]
    assert suggested == linked.id


async def test_without_a_matching_device_nothing_is_pre_selected(hass: HomeAssistant) -> None:
    _device(hass, "mqtt", "kitchen")

    result = await _to_device_step(hass)

    assert _device_selector(result)[1] is None


async def test_the_picked_device_becomes_the_linked_device(hass: HomeAssistant) -> None:
    linked = _device(hass, "mqtt", "host-node")
    result = await _to_device_step(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device_id": linked.id}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["device_id"] == linked.id


async def test_a_device_of_another_integration_is_refused(hass: HomeAssistant) -> None:
    other = _device(hass, "zha", "host-node")
    result = await _to_device_step(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device_id": other.id}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"device_id": "not_linkable_device"}


async def _options_form(hass: HomeAssistant) -> tuple[MockConfigEntry, ConfigFlowResult]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="host-node",
        unique_id="host-node.yaml",
        data={"url": "http://esphome.lan:6052", "configuration": "host-node.yaml"},
    )
    entry.add_to_hass(hass)
    return await _options_form_of(hass, entry)


async def test_the_options_default_to_no_clean_build_a_30_minute_git_poll_and_ota(
    hass: HomeAssistant,
) -> None:
    entry, result = await _options_form(hass)

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"clean_build": False, "git_poll_interval": 30, "upload_address": "OTA"}


async def test_the_options_can_be_changed_and_are_shown_again_as_set(
    hass: HomeAssistant,
) -> None:
    entry, result = await _options_form(hass)
    chosen = {"clean_build": True, "git_poll_interval": 5, "upload_address": "192.168.1.42"}

    result = await hass.config_entries.options.async_configure(result["flow_id"], chosen)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == chosen

    _, result = await _options_form_of(hass, entry)
    schema = result["data_schema"]
    assert schema is not None
    assert {str(key): key.description["suggested_value"] for key in schema.schema} == chosen


async def test_a_blank_upload_address_is_saved_as_ota(hass: HomeAssistant) -> None:
    entry, result = await _options_form(hass)

    await hass.config_entries.options.async_configure(
        result["flow_id"], {"clean_build": False, "git_poll_interval": 30, "upload_address": "  "}
    )

    assert entry.options["upload_address"] == "OTA"


async def _options_form_of(
    hass: HomeAssistant, entry: MockConfigEntry
) -> tuple[MockConfigEntry, ConfigFlowResult]:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    return entry, result
