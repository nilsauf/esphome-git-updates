"""Install refuses a Tracked Config that cannot take the latest Git Source commits
over the air, and a Repair issue says how to fix the YAML (ADR-0004).

The Device Builder is the fake `/ws` server, answering `devices/get_config`
from a files map; each Git Source's remote is a real repository on disk.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esphome_git_updates.const import DOMAIN
from fake_device_builder import FakeDeviceBuilder, install_transcript, serve, server_info
from git_repo import GitRepo
from tracked_config import CONFIGURATION, ENTITY_ID, OTA, install, press_install, set_up, tick

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _config(marsrelay: GitRepo, *, ota: str = OTA, refresh: str | None = "always") -> str:
    """A Tracked Config with marsrelay's main as its one external component."""
    refresh_line = "" if refresh is None else f"    refresh: {refresh}\n"
    return (
        "esphome:\n  name: host-node\n"
        f"{ota}"
        "external_components:\n"
        f"  - source:\n      type: git\n      url: {marsrelay.url}\n{refresh_line}"
    )


@pytest.fixture
async def builder(marsrelay: GitRepo) -> AsyncIterator[FakeDeviceBuilder]:
    async with serve(
        [server_info(esphome_version="2026.10.1")], {CONFIGURATION: _config(marsrelay)}
    ) as (_, fake):
        yield fake


async def _set_up_with_an_update(
    hass: HomeAssistant, fake: FakeDeviceBuilder, hass_storage: dict[str, Any]
) -> MockConfigEntry:
    """An entry whose device runs 2026.10.0 while the Device Builder has 2026.10.1."""
    hass_storage[f"{DOMAIN}.tracked"] = {
        "version": 1,
        "key": f"{DOMAIN}.tracked",
        "data": {"installed_esphome_version": "2026.10.0"},
    }
    return await set_up(hass, fake)


def _issue(issue_registry: ir.IssueRegistry, kind: str) -> ir.IssueEntry | None:
    return issue_registry.async_get_issue(DOMAIN, f"{kind}_tracked")


async def test_an_install_of_a_config_without_ota_is_refused_and_starts_no_job(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    hass_storage: dict[str, Any],
    issue_registry: ir.IssueRegistry,
) -> None:
    builder.files[CONFIGURATION] = _config(marsrelay, ota="")
    await _set_up_with_an_update(hass, builder, hass_storage)

    # The polls' transcript: a firmware/install would be a mismatch.
    with pytest.raises(HomeAssistantError, match="no `ota:` platform `esphome` or `web_server`"):
        await press_install(hass)

    assert builder.mismatches == []
    assert hass.states.get(ENTITY_ID).attributes["in_progress"] is False  # type: ignore[union-attr]
    issue = _issue(issue_registry, "missing_ota")
    assert issue is not None
    assert issue.translation_key == "missing_ota"


async def test_an_install_with_a_git_source_lacking_refresh_always_is_refused_naming_it(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    hass_storage: dict[str, Any],
    issue_registry: ir.IssueRegistry,
) -> None:
    builder.files[CONFIGURATION] = _config(marsrelay, refresh="1d")
    await _set_up_with_an_update(hass, builder, hass_storage)

    with pytest.raises(HomeAssistantError, match="Git Sources marsrelay lack `refresh: always`"):
        await press_install(hass)

    assert builder.mismatches == []
    issue = _issue(issue_registry, "refresh_policy")
    assert issue is not None
    assert issue.translation_placeholders == {"title": "host-node", "sources": "marsrelay"}
    assert _issue(issue_registry, "missing_ota") is None


async def test_each_repair_issue_is_raised_by_the_poll_and_clears_once_the_yaml_is_fixed(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    hass_storage: dict[str, Any],
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    builder.files[CONFIGURATION] = _config(marsrelay, ota="", refresh=None)
    await _set_up_with_an_update(hass, builder, hass_storage)
    assert _issue(issue_registry, "missing_ota") is not None
    assert _issue(issue_registry, "refresh_policy") is not None

    builder.files[CONFIGURATION] = _config(marsrelay, refresh=None)
    await tick(hass, freezer, 30.1)
    assert _issue(issue_registry, "missing_ota") is None
    assert _issue(issue_registry, "refresh_policy") is not None

    builder.files[CONFIGURATION] = _config(marsrelay, refresh="0s")
    await tick(hass, freezer, 30.1)
    assert _issue(issue_registry, "refresh_policy") is None


async def test_an_install_reads_the_yaml_again_so_a_fix_counts_without_waiting_for_a_poll(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    hass_storage: dict[str, Any],
    issue_registry: ir.IssueRegistry,
) -> None:
    builder.files[CONFIGURATION] = _config(marsrelay, ota="")
    await _set_up_with_an_update(hass, builder, hass_storage)
    builder.files[CONFIGURATION] = _config(marsrelay)

    await install(hass, builder, "install_clean_success")

    assert list(issue_registry.issues) == []
    assert hass.states.get(ENTITY_ID).state == STATE_OFF  # type: ignore[union-attr]


async def test_a_config_that_passes_both_checks_installs_and_raises_no_issue(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    hass_storage: dict[str, Any],
    issue_registry: ir.IssueRegistry,
) -> None:
    await _set_up_with_an_update(hass, builder, hass_storage)

    await install(hass, builder, "install_clean_success")

    assert list(issue_registry.issues) == []
    assert hass.states.get(ENTITY_ID).state == STATE_OFF  # type: ignore[union-attr]


async def test_a_git_source_on_a_tag_needs_no_refresh_policy(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    hass_storage: dict[str, Any],
    issue_registry: ir.IssueRegistry,
) -> None:
    marsrelay.tag("v1.0.0")
    builder.files[CONFIGURATION] = (
        f"{OTA}external_components:\n  - source:\n      type: git\n"
        f"      url: {marsrelay.url}\n      ref: v1.0.0\n"
    )
    await _set_up_with_an_update(hass, builder, hass_storage)

    await install(hass, builder, "install_clean_success")

    assert list(issue_registry.issues) == []


async def test_an_install_is_refused_when_the_yaml_cannot_be_read_to_check_it(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    hass_storage: dict[str, Any],
) -> None:
    await _set_up_with_an_update(hass, builder, hass_storage)
    builder.files[CONFIGURATION] = "external_components: [\n"

    with pytest.raises(HomeAssistantError, match="cannot read its YAML"):
        await press_install(hass)

    assert builder.mismatches == []


async def test_removing_the_entry_drops_its_pre_install_repair_issues(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    hass_storage: dict[str, Any],
    issue_registry: ir.IssueRegistry,
) -> None:
    builder.files[CONFIGURATION] = _config(marsrelay, ota="", refresh=None)
    entry = await _set_up_with_an_update(hass, builder, hass_storage)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert list(issue_registry.issues) == []


async def test_a_second_press_while_the_first_is_still_checking_is_refused(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    hass_storage: dict[str, Any],
) -> None:
    await _set_up_with_an_update(hass, builder, hass_storage)
    polls = builder.transcript
    builder.transcript = install_transcript("install_clean_success", CONFIGURATION)

    first = hass.async_create_task(press_install(hass))
    # Let the first press start its check, but not finish it.
    await asyncio.sleep(0)
    with pytest.raises(HomeAssistantError, match="already in progress"):
        await press_install(hass)
    await first

    assert builder.mismatches == []
    builder.transcript = polls
