"""Re-authenticate when the Device Builder rejects the stored credentials (ticket 13)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState, ConfigFlowResult
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esphome_git_updates.const import DOMAIN
from fake_device_builder import FakeDeviceBuilder, Transcript, serve, server_info
from tracked_config import CONFIGURATION, ENTITY_ID, OTA, press_install, set_up, tick

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

type ReauthStarted = tuple[MockConfigEntry, FakeDeviceBuilder, str]

FILES = {CONFIGURATION: f"esphome:\n  name: host-node\n{OTA}"}


def _login(*, accepted: bool = True) -> Transcript:
    """One connection to a password-protected Device Builder."""
    frames: Transcript = [
        server_info(requires_auth=True),
        {
            "dir": "out",
            "frame": {
                "command": "auth/login",
                "message_id": "1",
                "args": {"username": "REDACTED", "password": "REDACTED"},
            },
        },
    ]
    if accepted:
        frames.append(
            {
                "dir": "in",
                "frame": {
                    "message_id": "1",
                    "result": {"token": "token", "expires_at": 1796074264.912075},
                },
            }
        )
    else:
        frames.append(
            {
                "dir": "in",
                "frame": {
                    "message_id": "1",
                    "error_code": "not_authenticated",
                    "details": "Invalid credentials",
                },
            }
        )
    return frames


def _reauth_flow(hass: HomeAssistant) -> ConfigFlowResult:
    """The one reauth flow HA is showing for this integration."""
    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]
    assert len(flows) == 1
    return flows[0]


@pytest.fixture
async def reauth_started(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> AsyncIterator[ReauthStarted]:
    """An entry whose next poll is rejected, with HA's reauth flow open."""
    async with serve(_login(), FILES) as (_, fake):
        entry = await set_up(hass, fake, username="admin", password="secret")
        fake.transcript = _login(accepted=False)
        await tick(hass, freezer, 5.1)
        yield entry, fake, _reauth_flow(hass)["flow_id"]


async def _assert_polling_paused(
    hass: HomeAssistant, fake: FakeDeviceBuilder, freezer: FrozenDateTimeFactory
) -> None:
    """Move past the next version and Git polls, and check neither connected."""
    attempts = fake.connections
    await tick(hass, freezer, 5.1)
    await tick(hass, freezer, 30.1)
    assert fake.connections == attempts


async def test_a_rejected_login_while_polling_starts_reauth(
    hass: HomeAssistant, reauth_started: ReauthStarted
) -> None:
    entry, _, _ = reauth_started

    flow = _reauth_flow(hass)
    assert flow["step_id"] == "reauth_confirm"
    assert flow["context"]["entry_id"] == entry.entry_id
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE  # type: ignore[union-attr]


async def test_polling_does_not_login_again_while_reauth_is_open(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    reauth_started: ReauthStarted,
) -> None:
    _, fake, _ = reauth_started

    await _assert_polling_paused(hass, fake, freezer)


async def test_reauth_rejects_credentials_the_device_builder_refuses(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    reauth_started: ReauthStarted,
) -> None:
    entry, fake, flow_id = reauth_started
    fake.transcript = _login(accepted=False)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"username": "admin", "password": "wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data["password"] == "secret"
    assert entry.state is ConfigEntryState.LOADED

    # The rejected credentials are still not tried again from the pollers.
    await _assert_polling_paused(hass, fake, freezer)


async def test_successful_reauth_saves_the_credentials_reloads_and_recovers_the_entity(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    reauth_started: ReauthStarted,
) -> None:
    entry, fake, flow_id = reauth_started
    fake.transcript = _login()

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"username": "admin2", "password": "new-secret"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.data["username"] == "admin2"
    assert entry.data["password"] == "new-secret"
    # The new credentials, not the old ones, are what the Device Builder accepted.
    assert fake.logins[-1] == {"username": "admin2", "password": "new-secret"}
    assert entry.state is ConfigEntryState.LOADED
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF

    # The pause is gone: polling connects again after the successful reauth.
    attempts = fake.connections
    await tick(hass, freezer, 5.1)
    assert fake.connections > attempts


async def test_a_rejected_login_while_installing_starts_reauth(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    async with serve(_login(), FILES) as (_, fake):
        hass_storage[f"{DOMAIN}.tracked"] = {
            "version": 1,
            "key": f"{DOMAIN}.tracked",
            "data": {"installed_esphome_version": "2026.10.0"},
        }
        await set_up(hass, fake, username="admin", password="secret")
        # The pre-install check reads the YAML with the old credentials; the
        # Install's own connection is then refused.
        fake.transcripts = [_login(), _login(accepted=False)]

        with pytest.raises(HomeAssistantError, match="login refused"):
            await press_install(hass)

        flow = _reauth_flow(hass)
        assert flow["step_id"] == "reauth_confirm"
        await _assert_polling_paused(hass, fake, freezer)
