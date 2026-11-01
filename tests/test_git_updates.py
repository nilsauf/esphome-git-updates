"""Git Sources on the update entity: discovery, Latest Commits, seeding and recording.

The Device Builder is the fake `/ws` server, answering `devices/get_config`
from a files map; each Git Source's remote is a real repository on disk.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.esphome_git_updates.const import DOMAIN
from custom_components.esphome_git_updates.coordinator import protocol_issue_id
from fake_device_builder import FakeDeviceBuilder, install_transcript, serve, server_info
from git_repo import GitRepo

ENTITY_ID = "update.host_node_firmware"
CONFIGURATION = "host-node.yaml"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components/."""


@pytest.fixture
def marsrelay(tmp_path: Path) -> GitRepo:
    return GitRepo(tmp_path / "marsrelay")


def _config(*sources: str) -> str:
    """A Tracked Config whose external components come from *sources* (remote URLs)."""
    entries = "".join(
        f"  - source:\n      type: git\n      url: {url}\n    refresh: always\n" for url in sources
    )
    return f"esphome:\n  name: host-node\nota:\n  - platform: esphome\nexternal_components:\n{entries}"


@pytest.fixture
async def builder(marsrelay: GitRepo) -> AsyncIterator[FakeDeviceBuilder]:
    """A Device Builder with ESPHome 2026.10.1 whose config tracks marsrelay's main."""
    async with serve(
        [server_info(esphome_version="2026.10.1")],
        {CONFIGURATION: _config(marsrelay.url)},
    ) as (_, fake):
        yield fake


async def _set_up(hass: HomeAssistant, fake: FakeDeviceBuilder) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="host-node",
        unique_id=CONFIGURATION,
        data={"url": fake.url, "configuration": CONFIGURATION},
        entry_id="tracked",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _state(hass: HomeAssistant) -> Any:
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    return state


async def test_a_new_entry_seeds_its_git_sources_with_their_latest_commit(
    hass: HomeAssistant, builder: FakeDeviceBuilder, marsrelay: GitRepo
) -> None:
    latest = marsrelay.latest_commit()

    await _set_up(hass, builder)

    state = _state(hass)
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == f"2026.10.1 (marsrelay {latest[:7]})"
    assert state.attributes["latest_version"] == f"2026.10.1 (marsrelay {latest[:7]})"


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: float) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_push_is_offered_as_an_update_at_the_next_30_minute_poll(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    installed = marsrelay.latest_commit()
    await _set_up(hass, builder)
    pushed = marsrelay.push()

    await _tick(hass, freezer, 29)
    assert _state(hass).state == STATE_OFF

    # The coordinator spreads refreshes by up to a second.
    await _tick(hass, freezer, 1.1)

    state = _state(hass)
    assert state.state == STATE_ON
    assert state.attributes["installed_version"] == f"2026.10.1 (marsrelay {installed[:7]})"
    assert state.attributes["latest_version"] == f"2026.10.1 (marsrelay {pushed[:7]})"
    assert state.attributes["release_summary"] == (
        f"marsrelay {installed[:7]} → {pushed[:7]}"
    )


async def _install(hass: HomeAssistant, fake: FakeDeviceBuilder, fixture: str) -> None:
    """Press Install against a captured Install, then go back to answering polls."""
    polls = fake.transcript
    fake.transcript = install_transcript(fixture, CONFIGURATION)
    try:
        await hass.services.async_call(
            "update", "install", {"entity_id": ENTITY_ID}, blocking=True
        )
        await hass.async_block_till_done()
    finally:
        assert fake.mismatches == []
        fake.transcript = polls


def _installed_commits(hass_storage: dict[str, Any]) -> dict[str, str]:
    commits: dict[str, str] = hass_storage[f"{DOMAIN}.tracked"]["data"]["installed_commits"]
    return commits


async def test_an_install_records_the_latest_commits_read_again_when_it_was_pressed(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    await _set_up(hass, builder)
    marsrelay.push()
    await _tick(hass, freezer, 30.1)
    assert _state(hass).state == STATE_ON
    # Pushed after the last poll: Install reads the Git Sources again first.
    pressed_for = marsrelay.push()

    await _install(hass, builder, "install_clean_success")

    assert _installed_commits(hass_storage) == {f"{marsrelay.url}@HEAD": pressed_for}
    assert _state(hass).state == STATE_OFF
    await _tick(hass, freezer, 30.1)
    assert _state(hass).state == STATE_OFF


async def test_a_failed_install_records_no_commits(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    seeded = marsrelay.latest_commit()
    await _set_up(hass, builder)
    marsrelay.push()
    await _tick(hass, freezer, 30.1)

    with pytest.raises(HomeAssistantError):
        await _install(hass, builder, "install_compile_failed")

    assert _installed_commits(hass_storage) == {f"{marsrelay.url}@HEAD": seeded}
    assert _state(hass).state == STATE_ON


async def test_a_git_source_added_to_the_yaml_later_is_seeded_and_updates_from_its_next_push(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _set_up(hass, builder)
    sensors = GitRepo(tmp_path / "sensors")
    builder.files[CONFIGURATION] = _config(marsrelay.url, sensors.url)

    await _tick(hass, freezer, 30.1)

    state = _state(hass)
    assert state.state == STATE_OFF
    expected = f"2026.10.1 (marsrelay {marsrelay.latest_commit()[:7]}, sensors {sensors.latest_commit()[:7]})"
    assert state.attributes["installed_version"] == expected

    seeded = sensors.latest_commit()
    pushed = sensors.push()
    await _tick(hass, freezer, 30.1)

    state = _state(hass)
    assert state.state == STATE_ON
    assert state.attributes["release_summary"] == f"sensors {seeded[:7]} → {pushed[:7]}"


async def test_an_unreachable_remote_keeps_its_last_known_latest_commit_and_is_logged(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    never_reachable = (tmp_path / "never-there").as_uri()
    builder.files[CONFIGURATION] = _config(marsrelay.url, never_reachable)
    await _set_up(hass, builder)
    pushed = marsrelay.push()
    await _tick(hass, freezer, 30.1)
    assert _state(hass).state == STATE_ON

    marsrelay.path.rename(tmp_path / "moved")
    await _tick(hass, freezer, 30.1)

    state = _state(hass)
    assert state.state == STATE_ON
    assert state.attributes["latest_version"] == f"2026.10.1 (marsrelay {pushed[:7]})"
    assert "cannot get the Latest Commit of marsrelay" in caplog.text
    assert "cannot get the Latest Commit of never-there" in caplog.text


async def test_a_refused_include_read_is_a_repair_issue_until_the_include_resolves(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
) -> None:
    builder.files[CONFIGURATION] = "packages:\n  base: !include common/base.yaml\n"
    builder.files["common/base.yaml"] = _config(marsrelay.url)
    await _set_up(hass, builder)
    common = builder.files.pop("common/base.yaml")

    await _tick(hass, freezer, 30.1)

    issue = issue_registry.async_get_issue(DOMAIN, protocol_issue_id("tracked"))
    assert issue is not None
    assert issue.translation_key == "include_refused"
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["file"] == "common/base.yaml"
    # The entity keeps what it knew; the ESPHome version poll is healthy and
    # does not clear the issue.
    assert _state(hass).attributes["latest_version"] == (
        f"2026.10.1 (marsrelay {marsrelay.latest_commit()[:7]})"
    )
    await _tick(hass, freezer, 5.1)
    assert issue_registry.async_get_issue(DOMAIN, protocol_issue_id("tracked")) is not None

    builder.files["common/base.yaml"] = common
    await _tick(hass, freezer, 30.1)

    assert issue_registry.async_get_issue(DOMAIN, protocol_issue_id("tracked")) is None


async def test_the_yaml_and_its_includes_are_read_from_the_device_builder_but_never_secrets(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    builder.files[CONFIGURATION] = (
        "wifi: !include wifi.yaml\n"
        "external_components:\n"
        "  - source:\n      type: git\n      url: !secret private_repo\n"
        "packages:\n  base: !include base.yaml\n"
    )
    builder.files["wifi.yaml"] = "password: !secret wifi_password\n"
    builder.files["base.yaml"] = _config(marsrelay.url)
    builder.files["secrets.yaml"] = "private_repo: https://example.com/private.git\n"

    await _set_up(hass, builder)

    assert sorted(builder.reads) == ["base.yaml", CONFIGURATION, "wifi.yaml"]
    assert _state(hass).attributes["installed_version"] == (
        f"2026.10.1 (marsrelay {marsrelay.latest_commit()[:7]})"
    )
    assert "cannot be tracked" in caplog.text
    assert "!secret private_repo" in caplog.text


async def test_a_git_source_on_a_tag_is_pinned_and_not_tracked(
    hass: HomeAssistant, builder: FakeDeviceBuilder, marsrelay: GitRepo
) -> None:
    marsrelay.tag("v1.0.0")
    builder.files[CONFIGURATION] = (
        f"external_components:\n  - source:\n      type: git\n"
        f"      url: {marsrelay.url}\n      ref: v1.0.0\n"
    )

    await _set_up(hass, builder)

    assert _state(hass).attributes["installed_version"] == "2026.10.1"


async def test_git_sources_that_cannot_be_read_at_setup_do_not_hold_up_the_esphome_update(
    hass: HomeAssistant, builder: FakeDeviceBuilder, issue_registry: ir.IssueRegistry
) -> None:
    builder.files[CONFIGURATION] = "external_components: [\n"

    await _set_up(hass, builder)

    state = _state(hass)
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1"
    assert list(issue_registry.issues) == []


async def test_a_configuration_the_device_builder_no_longer_has_is_not_protocol_drift(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
) -> None:
    await _set_up(hass, builder)
    del builder.files[CONFIGURATION]

    await _tick(hass, freezer, 30.1)

    assert list(issue_registry.issues) == []
