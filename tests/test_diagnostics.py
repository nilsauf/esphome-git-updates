"""The diagnostics download of a Tracked Config, through Home Assistant's diagnostics API.

It reports what the integration sees and never what it could be used to impersonate,
and it is read from memory, so it works while the Device Builder is unreachable.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.esphome_git_updates.const import DOMAIN
from custom_components.esphome_git_updates.device_builder import ActiveJob, Stage
from custom_components.esphome_git_updates.install import ActiveInstall, InstallPlan
from fake_device_builder import job_events, port_of, serve, server_info
from fake_github import GITHUB_URL, FakeGitHub
from git_repo import GitRepo
from stored_record import record_an_install
from tracked_config import CONFIGURATION, OTA, set_up, tick

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

# Secrets the entry holds; none may appear in the download.
USERNAME = "builder-admin-7f3e"
PASSWORD = "builder-password-91c2"
URL_PASSWORD = "url-password-5d8a"
GITHUB_TOKEN = "ghp_diagnosticstoken0123456789"
GIT_PASSWORD = "git-password-2b6e"
# A Git Source off GitHub whose URL carries credentials; git reaches marsrelay there.
PRIVATE_URL = f"https://deploy:{GIT_PASSWORD}@git.example.com/nilsauf/relaykit.git"

YAML = (
    f"esphome:\n  name: host-node\n{OTA}external_components:\n"
    f"  - source:\n      type: git\n      url: {GITHUB_URL}\n    refresh: always\n"
    f"  - source:\n      type: git\n      url: {PRIVATE_URL}\n    refresh: always\n"
)


def _esphome_device(hass: HomeAssistant) -> dr.DeviceEntry:
    """The device core's ESPHome integration made for host-node."""
    entry = MockConfigEntry(domain="esphome")
    entry.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff")},
        name="host-node",
    )


async def test_diagnostics_show_the_last_polled_state_without_secrets_while_the_builder_is_down(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    on_github: None,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", f"url.{marsrelay.url}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", PRIVATE_URL)
    installed = marsrelay.latest_commit()
    device = _esphome_device(hass)
    await record_an_install(hass, "tracked", "2026.9.0")
    async with serve([server_info(esphome_version="2026.10.1")], {CONFIGURATION: YAML}) as (
        url,
        fake,
    ):
        port = port_of(url)
        entry = await set_up(
            hass,
            fake,
            options={"github_token": GITHUB_TOKEN, "git_poll_interval": 1},
            url=f"http://{USERNAME}:{URL_PASSWORD}@127.0.0.1:{port}/",
            username=USERNAME,
            password=PASSWORD,
            device_id=device.id,
        )
        marsrelay.push()
        latest = marsrelay.push()
        await tick(hass, freezer, 1.1)

    # The Device Builder is gone; the next polls fail. (Kept short of 30 minutes, when
    # the test client's access token expires.)
    await tick(hass, freezer, 5.1)

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    download = json.dumps(diagnostics)
    for secret in (
        USERNAME,
        PASSWORD,
        URL_PASSWORD,
        GITHUB_TOKEN,
        GIT_PASSWORD,
        "external_components",
    ):
        assert secret not in download
    address = f"http://127.0.0.1:{port}"
    assert diagnostics == {
        "entry": {
            "title": "host-node",
            "unique_id": CONFIGURATION,
            "data": {
                "url": address,
                "username": "**REDACTED**",
                "password": "**REDACTED**",
                "configuration": CONFIGURATION,
                "device_id": device.id,
            },
            "options": {"github_token": "**REDACTED**", "git_poll_interval": 1},
        },
        "device_builder_location": {"kind": "manual", "address": address},
        "esphome_version": {"available": "2026.10.1", "last_update_success": False},
        "git_sources": {
            "last_update_success": False,
            "shown": [
                {
                    "label": "marsrelay",
                    "installed": installed[:7],
                    "latest": latest[:7],
                    "commits": 2,
                },
                {
                    "label": "relaykit",
                    "installed": installed[:7],
                    "latest": latest[:7],
                    "commits": None,
                },
            ],
        },
        "installed_record": {
            "installed_esphome_version": "2026.9.0",
            "installed_commits": {
                f"{GITHUB_URL}@HEAD": installed,
                "https://git.example.com/nilsauf/relaykit.git@HEAD": installed,
            },
            "installed_tags": {},
            "active_install": None,
        },
        "linked_device_id": device.id,
    }


async def test_diagnostics_of_an_add_on_entry_name_the_add_on(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, supervisor: MagicMock
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="host-node",
        unique_id=CONFIGURATION,
        data={"addon_slug": "5c53de3b_esphome", "configuration": CONFIGURATION},
    )
    entry.add_to_hass(hass)
    async with serve(
        [server_info(esphome_version="2026.10.1")],
        {CONFIGURATION: f"esphome:\n  name: host-node\n{OTA}"},
    ) as (url, _):
        supervisor.addons_installed = {"5c53de3b_esphome": port_of(url)}
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["device_builder_location"] == {
        "kind": "add-on",
        "address": "5c53de3b_esphome",
    }
    assert diagnostics["git_sources"] == {"last_update_success": True, "shown": []}
    assert diagnostics["linked_device_id"] is None


async def test_diagnostics_of_a_running_install_show_its_plan_without_git_credentials(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    # A Git Source in scp-like form has no scheme, so no userinfo to cut.
    scp_url = "git@git.example.com:nilsauf/relaykit.git"
    record = await record_an_install(hass, "tracked", "2026.9.0")
    await record.async_set_active_install(
        ActiveInstall(
            (ActiveJob("c1", Stage.COMPILE), ActiveJob("u1", Stage.UPLOAD)),
            InstallPlan(
                {f"{PRIVATE_URL}@HEAD": "beef00", f"{scp_url}@HEAD": "decade"}, "OTA", False
            ),
        )
    )
    async with serve(
        [server_info(esphome_version="2026.10.1")],
        {CONFIGURATION: f"esphome:\n  name: host-node\n{OTA}"},
    ) as (_, fake):
        # The Install re-attached after the restart is still compiling.
        compiled = asyncio.Event()
        fake.waits = {"c1": compiled}
        fake.jobs = {"c1": job_events("[50/100] Compiling", status=None)}
        await set_up(hass, fake)
        await asyncio.wait_for(fake.waiting.wait(), 1)

        diagnostics = await get_diagnostics_for_config_entry(
            hass, hass_client, hass.config_entries.async_get_entry("tracked")
        )

        fake.jobs["c1"] = job_events("[100/100] Linking firmware.elf", status="failed")
        compiled.set()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert GIT_PASSWORD not in json.dumps(diagnostics)
    assert diagnostics["installed_record"] == {
        "installed_esphome_version": "2026.9.0",
        "installed_commits": {},
        "installed_tags": {},
        "active_install": {
            "jobs": [{"job_id": "c1", "stage": "compile"}, {"job_id": "u1", "stage": "upload"}],
            "commits": {
                "https://git.example.com/nilsauf/relaykit.git@HEAD": "beef00",
                f"{scp_url}@HEAD": "decade",
            },
            "port": "OTA",
            "clean_build": False,
            "tags": {},
        },
    }
