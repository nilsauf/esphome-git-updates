"""Reconfigure the Device Builder Location of an entry without removing it."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntryState,
    ConfigFlowResult,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esphome_git_updates.const import DOMAIN
from fake_device_builder import (
    FakeDeviceBuilder,
    Transcript,
    job_events,
    load_fixture,
    port_of,
    queue_install,
    serve,
    server_info,
)
from stored_record import record_an_install, recorded_esphome_version
from tracked_config import CONFIGURATION, ENTITY_ID, OTA, notifications, set_up, tick

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


# Through a real reload of a set-up entry. Without the reload, a running Install
# is never re-attached and waiting for it would hang: the timeout fails it instead.
reloads = pytest.mark.timeout(10)

FILES = {CONFIGURATION: f"esphome:\n  name: host-node\n{OTA}"}


def _listing() -> Transcript:
    """One connection that lists configurations, including the Tracked Config."""
    return load_fixture("list")


async def _reconfigure_to(hass: HomeAssistant, entry: MockConfigEntry, **location: str) -> None:
    result = await _start(hass, entry)
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], location)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)


@reloads
async def test_moving_the_device_builder_keeps_the_entity_and_the_installed_record(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    await record_an_install(hass, "tracked", "2026.10.0")
    async with serve([server_info(esphome_version="2026.10.1")], FILES) as (_, old):
        entry = await set_up(hass, old)
        before = entity_registry.async_get(ENTITY_ID)
        assert before is not None
        assert hass.states.get(ENTITY_ID).state == STATE_ON  # type: ignore[union-attr]

    # The old Device Builder is gone; a new one has the same Configuration.
    async with serve(_listing(), FILES) as (url, new):
        new.transcripts = [_listing(), [server_info(esphome_version="2026.10.1")]]
        await _reconfigure_to(hass, entry, url=url)

        # Saving schedules the reload itself, the way Home Assistant expects.
        assert "update listener" not in caplog.text
        assert new.mismatches == []
        assert entry.state is ConfigEntryState.LOADED
        assert entity_registry.async_get(ENTITY_ID) == before
        state = hass.states.get(ENTITY_ID)
        assert state is not None
        assert state.state == STATE_ON
        assert state.attributes["installed_version"] == "2026.10.0"
        assert state.attributes["latest_version"] == "2026.10.1"
        assert await recorded_esphome_version(hass, entry.entry_id) == "2026.10.0"
        assert await hass.config_entries.async_unload(entry.entry_id)


def _reauth_flows(hass: HomeAssistant) -> list[ConfigFlowResult]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


@reloads
async def test_reconfiguring_with_working_credentials_ends_the_reauthentication_pause(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    async with serve(load_fixture("auth_login_list"), FILES) as (url, fake):
        entry = await set_up(hass, fake, username="admin", password="secret")
        # The Device Builder starts rejecting the stored login: polling pauses.
        fake.transcript = load_fixture("auth_login_rejected")
        await tick(hass, freezer, 5.1)
        assert len(_reauth_flows(hass)) == 1
        attempts = fake.connections
        await tick(hass, freezer, 5.1)
        assert fake.connections == attempts

        fake.transcript = load_fixture("auth_login_list")
        await _reconfigure_to(hass, entry, url=url, username="admin2", password="new-secret")

        assert fake.logins[-1] == {"username": "admin2", "password": "new-secret"}
        assert entry.state is ConfigEntryState.LOADED
        assert hass.states.get(ENTITY_ID).state == STATE_OFF  # type: ignore[union-attr]
        # The stale reauthentication request is gone, and polling connects again.
        assert _reauth_flows(hass) == []
        attempts = fake.connections
        await tick(hass, freezer, 5.1)
        assert fake.connections > attempts
        assert fake.logins[-1] == {"username": "admin2", "password": "new-secret"}


async def _start_an_install(hass: HomeAssistant, fake: FakeDeviceBuilder) -> MockConfigEntry:
    """An entry with an update whose Install is compiling on *fake*."""
    await record_an_install(hass, "tracked", "2026.10.0")
    entry = await set_up(hass, fake)
    fake.transcript = queue_install("c1", "u1")
    fake.jobs = {"c1": job_events("INFO Compiling", status=None)}
    await hass.services.async_call("update", "install", {"entity_id": ENTITY_ID})
    await fake.running.wait()
    return entry


@reloads
async def test_an_install_running_on_the_same_device_builder_is_followed_through(
    hass: HomeAssistant,
) -> None:
    async with serve([server_info(esphome_version="2026.10.1")], FILES) as (url, fake):
        entry = await _start_an_install(hass, fake)

        # Saved again with the same Device Builder, which kept the jobs.
        fake.transcripts = [_listing(), [server_info(esphome_version="2026.10.1")]]
        fake.jobs = {"c1": job_events("INFO Compiling"), "u1": job_events("INFO OTA successful")}
        fake.followed.clear()
        await _reconfigure_to(hass, entry, url=url)

        assert fake.mismatches == []
        assert fake.followed == ["c1", "u1"]
        state = hass.states.get(ENTITY_ID)
        assert state is not None
        assert state.state == STATE_OFF
        assert state.attributes["in_progress"] is False
        assert await recorded_esphome_version(hass, entry.entry_id) == "2026.10.1"
        assert notifications(hass) == []


@reloads
async def test_an_install_running_on_a_replaced_device_builder_ends_with_its_outcome_unknown(
    hass: HomeAssistant,
) -> None:
    async with serve([server_info(esphome_version="2026.10.1")], FILES) as (_, old):
        entry = await _start_an_install(hass, old)

        async with serve(_listing(), FILES) as (url, new):
            new.transcripts = [_listing(), [server_info(esphome_version="2026.10.1")]]
            await _reconfigure_to(hass, entry, url=url)

            assert new.followed == ["c1"]
            state = hass.states.get(ENTITY_ID)
            assert state is not None
            assert state.state == STATE_ON
            assert state.attributes["in_progress"] is False
            assert await recorded_esphome_version(hass, entry.entry_id) == "2026.10.0"
            [notification] = notifications(hass)
            assert "unknown" in str(notification["title"]).lower()
            # It names the reload as a cause, not only a restart.
            assert "reloaded" in notification["message"]
            assert await hass.config_entries.async_unload(entry.entry_id)
