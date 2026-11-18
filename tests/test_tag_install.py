"""Install moves a Tag Ref written in the device YAML to the version shown (ADR-0007).

The Device Builder is the fake `/ws` server, whose files map is what
`devices/update_config` writes to; each Git Source's remote is a real
repository on disk.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import persistent_notification
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from fake_device_builder import (
    FakeDeviceBuilder,
    install_transcript,
    job_events,
    queue_install,
    serve,
    server_info,
)
from git_repo import GitRepo
from tracked_config import (
    CONFIGURATION,
    ENTITY_ID,
    OTA,
    install,
    press_install,
    set_up,
    tick,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _config(url: str, ref: str) -> str:
    """A Tracked Config with one external component from *url* at the Tag Ref *ref*,
    with comments and spacing an Install must keep."""
    return (
        f"# host-node: tracks {ref}\n"
        f"esphome:\n  name: host-node\n{OTA}"
        "external_components:\n"
        "  - source:\n"
        "      type: git\n"
        f"      url: {url}\n"
        f"      ref:  {ref}   # was {ref}\n"
    )


@pytest.fixture
async def builder(marsrelay: GitRepo) -> AsyncIterator[FakeDeviceBuilder]:
    """A Device Builder whose config follows marsrelay at v1.4.0, which has v1.5.0."""
    marsrelay.tag("v1.4.0")
    marsrelay.push()
    marsrelay.tag("v1.5.0")
    async with serve(
        [server_info(esphome_version="2026.10.1")],
        {CONFIGURATION: _config(marsrelay.url, "v1.4.0")},
    ) as (_, fake):
        yield fake


def _state(hass: HomeAssistant) -> Any:
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    return state


def _notifications(hass: HomeAssistant) -> list[persistent_notification.Notification]:
    return list(persistent_notification._async_get_or_create_notifications(hass).values())


def _moved(url: str, old: str, new: str) -> str:
    """The YAML of `_config(url, old)` with only its ref moved to *new*."""
    return _config(url, old).replace(f"ref:  {old}", f"ref:  {new}")


async def test_install_moves_the_tag_ref_to_the_version_shown_and_records_it(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    await set_up(hass, builder)
    assert _state(hass).state == STATE_ON

    await install(hass, builder, "install_clean_success")

    assert builder.files[CONFIGURATION] == _moved(marsrelay.url, "v1.4.0", "v1.5.0")
    state = _state(hass)
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1 (marsrelay v1.5.0)"
    await tick(hass, freezer, 30.1)
    assert _state(hass).state == STATE_OFF
    assert _state(hass).attributes["installed_version"] == "2026.10.1 (marsrelay v1.5.0)"


async def test_a_yaml_edited_since_the_pre_install_read_fails_the_install_before_compiling(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
) -> None:
    await set_up(hass, builder)
    edited = _config(marsrelay.url, "v1.4.0") + "logger:\n"

    def edit(path: str) -> None:
        builder.files[path] = edited

    builder.before_write = edit
    builder.transcript = install_transcript("install_clean_success", CONFIGURATION)
    builder.finished = False

    with pytest.raises(
        HomeAssistantError,
        match=rf"failed at update YAML: {CONFIGURATION} changed since it was read",
    ):
        await press_install(hass)

    assert builder.mismatches == []
    # Nothing was compiled: the Install never got to `firmware/install`.
    assert not builder.finished
    assert builder.files[CONFIGURATION] == edited
    [notification] = _notifications(hass)
    assert "update YAML" in notification["message"]
    state = _state(hass)
    assert state.state == STATE_ON
    assert state.attributes["installed_version"] == "2026.10.1 (marsrelay v1.4.0)"


async def test_a_failed_compile_keeps_the_rewritten_yaml_and_a_retry_does_not_rewrite_it(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
) -> None:
    await set_up(hass, builder)

    with pytest.raises(HomeAssistantError, match="failed at compile"):
        await install(hass, builder, "install_compile_failed")

    assert builder.files[CONFIGURATION] == _moved(marsrelay.url, "v1.4.0", "v1.5.0")
    state = _state(hass)
    assert state.state == STATE_ON
    assert state.attributes["installed_version"] == "2026.10.1 (marsrelay v1.4.0)"
    assert state.attributes["latest_version"] == "2026.10.1 (marsrelay v1.5.0)"

    await install(hass, builder, "install_clean_success")

    assert builder.writes == [CONFIGURATION]
    assert builder.files[CONFIGURATION] == _moved(marsrelay.url, "v1.4.0", "v1.5.0")
    assert _state(hass).state == STATE_OFF
    assert _state(hass).attributes["installed_version"] == "2026.10.1 (marsrelay v1.5.0)"


async def test_a_version_tagged_while_the_install_runs_is_not_what_gets_written(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    await set_up(hass, builder)

    def release(_path: str) -> None:
        marsrelay.push()
        marsrelay.tag("v1.6.0")

    builder.before_write = release
    await install(hass, builder, "install_clean_success")

    assert builder.files[CONFIGURATION] == _moved(marsrelay.url, "v1.4.0", "v1.5.0")
    assert _state(hass).attributes["installed_version"] == "2026.10.1 (marsrelay v1.5.0)"
    await tick(hass, freezer, 30.1)
    state = _state(hass)
    assert state.state == STATE_ON
    assert state.attributes["release_summary"] == "marsrelay v1.5.0 → v1.6.0"


async def test_one_install_moves_a_tag_ref_and_records_a_branch_source_too(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
) -> None:
    sensors = GitRepo(tmp_path / "sensors")
    branch_source = (
        f"  - source:\n      type: git\n      url: {sensors.url}\n    refresh: always\n"
    )
    builder.files[CONFIGURATION] = _config(marsrelay.url, "v1.4.0") + branch_source
    await set_up(hass, builder)
    pushed = sensors.push()
    await tick(hass, freezer, 30.1)
    assert _state(hass).attributes["release_summary"].startswith("marsrelay v1.4.0 → v1.5.0; ")

    await install(hass, builder, "install_clean_success")

    assert builder.files[CONFIGURATION] == (
        _moved(marsrelay.url, "v1.4.0", "v1.5.0") + branch_source
    )
    state = _state(hass)
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == (
        f"2026.10.1 (marsrelay v1.5.0, sensors {pushed[:7]})"
    )
    await tick(hass, freezer, 30.1)
    assert _state(hass).state == STATE_OFF


async def test_an_install_re_attached_after_a_restart_records_the_version_it_wrote(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = await set_up(hass, builder)
    builder.transcript = queue_install("c1", "u1", configuration=CONFIGURATION)
    builder.jobs = {"c1": job_events("INFO Compiling", status=None)}
    await hass.services.async_call("update", "install", {"entity_id": ENTITY_ID})
    await builder.running.wait()

    # Home Assistant stops while the compile runs on; both jobs finish meanwhile.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    builder.transcript = [server_info(esphome_version="2026.10.1")]
    builder.jobs = {"c1": job_events("INFO Compiling"), "u1": job_events("INFO OTA successful")}
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert builder.mismatches == []
    assert builder.followed[-2:] == ["c1", "u1"]
    assert builder.writes == [CONFIGURATION]
    assert builder.files[CONFIGURATION] == _moved(marsrelay.url, "v1.4.0", "v1.5.0")
    state = _state(hass)
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1 (marsrelay v1.5.0)"
    await tick(hass, freezer, 30.1)
    assert _state(hass).state == STATE_OFF
