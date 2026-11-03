"""The update entity of one Tracked Config, fed by a fake Device Builder."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)

from custom_components.esphome_git_updates import device_builder
from custom_components.esphome_git_updates.const import DOMAIN
from custom_components.esphome_git_updates.coordinator import protocol_issue_id
from fake_device_builder import (
    FakeDeviceBuilder,
    Transcript,
    install_transcript,
    job_events,
    port_of,
    serve,
    server_info,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from tracked_config import OTA

ENTITY_ID = "update.host_node_firmware"

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def builder() -> AsyncIterator[tuple[str, FakeDeviceBuilder]]:
    """A Device Builder that compiles with ESPHome 2026.10.1; the config has no Git Sources."""
    async with serve(
        [server_info(esphome_version="2026.10.1")],
        {"host-node.yaml": f"esphome:\n  name: host-node\n{OTA}"},
    ) as served:
        yield served


def _entry(url: str, **kwargs: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="host-node",
        unique_id="host-node.yaml",
        data={"url": url, "configuration": "host-node.yaml"},
        **kwargs,
    )


async def _set_up(hass: HomeAssistant, url: str, **kwargs: Any) -> MockConfigEntry:
    entry = _entry(url, **kwargs)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_the_add_on_ingress_port_is_looked_up_again_when_the_entry_is_set_up(
    hass: HomeAssistant, supervisor: MagicMock
) -> None:
    files = {"host-node.yaml": f"esphome:\n  name: host-node\n{OTA}"}
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="host-node",
        unique_id="host-node.yaml",
        data={"addon_slug": "5c53de3b_esphome", "configuration": "host-node.yaml"},
    )
    entry.add_to_hass(hass)
    async with serve([server_info(esphome_version="2026.10.1")], files) as (url, _):
        supervisor.addons_installed = {"5c53de3b_esphome": port_of(url)}
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    # The add-on restarts on another ingress port; the old one is gone.
    async with serve([server_info(esphome_version="2026.11.0")], files) as (url, _):
        supervisor.addons_installed = {"5c53de3b_esphome": port_of(url)}
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["latest_version"] == "2026.11.0"


async def test_a_new_entry_assumes_the_device_runs_the_available_version(
    hass: HomeAssistant, builder: tuple[str, FakeDeviceBuilder]
) -> None:
    url, _ = builder
    await _set_up(hass, url)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1"
    assert state.attributes["latest_version"] == "2026.10.1"


async def test_the_entity_sits_on_a_device_the_integration_creates(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    url, _ = builder
    entry = await _set_up(hass, url)

    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    assert [d.name for d in devices] == ["host-node"]
    entities = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    assert [(e.entity_id, e.device_id) for e in entities] == [(ENTITY_ID, devices[0].id)]


async def test_an_update_is_offered_when_the_available_version_differs_from_the_installed_one(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, _ = builder
    entry_id = "tracked"
    hass_storage[f"{DOMAIN}.{entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry_id}",
        "data": {"installed_esphome_version": "2026.10.2"},
    }
    await _set_up(hass, url, entry_id=entry_id)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    # "Newer" means "differs": the installed version is ahead, and that is still an update.
    assert state.state == STATE_ON
    assert state.attributes["installed_version"] == "2026.10.2"
    assert state.attributes["latest_version"] == "2026.10.1"


async def test_the_available_version_is_read_again_every_5_minutes(
    hass: HomeAssistant, builder: tuple[str, FakeDeviceBuilder], freezer: FrozenDateTimeFactory
) -> None:
    url, fake = builder
    await _set_up(hass, url)
    fake.transcript = [server_info(esphome_version="2026.11.0")]

    freezer.tick(timedelta(minutes=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(ENTITY_ID).attributes["latest_version"] == "2026.10.1"  # type: ignore[union-attr]

    # The coordinator spreads refreshes by up to a second.
    freezer.tick(timedelta(minutes=1, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["installed_version"] == "2026.10.1"
    assert state.attributes["latest_version"] == "2026.11.0"


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.parametrize(
    ("handshake", "translation_key"),
    [
        (server_info("2.0.0"), "unsupported_server_version"),
        ({"dir": "in", "frame": {"hello": "world"}}, "unexpected_frame"),
    ],
)
async def test_a_device_builder_that_answers_unexpectedly_raises_a_repair_issue_until_it_is_healthy(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
    handshake: dict[str, Any],
    translation_key: str,
) -> None:
    url, fake = builder
    entry = await _set_up(hass, url)
    healthy = fake.transcript

    fake.transcript = [handshake]
    await _poll(hass, freezer)

    issue = issue_registry.async_get_issue(DOMAIN, protocol_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.translation_key == translation_key
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE  # type: ignore[union-attr]

    fake.transcript = healthy
    await _poll(hass, freezer)

    assert issue_registry.async_get_issue(DOMAIN, protocol_issue_id(entry.entry_id)) is None
    assert hass.states.get(ENTITY_ID).state == STATE_OFF  # type: ignore[union-attr]


async def test_an_unreachable_device_builder_is_not_a_repair_issue(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
) -> None:
    url, fake = builder
    await _set_up(hass, url)

    fake.transcript = [{"dir": "close"}]
    await _poll(hass, freezer)

    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE  # type: ignore[union-attr]
    assert list(issue_registry.issues) == []


async def test_an_entry_whose_device_builder_is_unsupported_at_startup_raises_a_repair_issue(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    async with serve([server_info("2.0.0")]) as (url, _):
        entry = _entry(url)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    issue = issue_registry.async_get_issue(DOMAIN, protocol_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.translation_key == "unsupported_server_version"


async def test_removing_the_entry_drops_its_repair_issue_and_stored_versions(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up(hass, url)
    fake.transcript = [server_info("2.0.0")]
    await _poll(hass, freezer)
    assert list(issue_registry.issues) != []
    assert f"{DOMAIN}.{entry.entry_id}" in hass_storage

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert list(issue_registry.issues) == []
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage


async def _set_up_with_an_update(
    hass: HomeAssistant, url: str, hass_storage: dict[str, Any], **options: Any
) -> MockConfigEntry:
    """An entry with *options* whose device runs 2026.10.0 while the Device Builder
    has 2026.10.1."""
    entry_id = "tracked"
    hass_storage[f"{DOMAIN}.{entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry_id}",
        "data": {"installed_esphome_version": "2026.10.0"},
    }
    entry = await _set_up(hass, url, entry_id=entry_id, options=options)
    assert hass.states.get(ENTITY_ID).state == STATE_ON  # type: ignore[union-attr]
    return entry


async def _install(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "update", "install", {"entity_id": ENTITY_ID}, blocking=True
    )
    await hass.async_block_till_done()


def _recorded_version(hass_storage: dict[str, Any], entry: MockConfigEntry) -> str | None:
    """The Installed ESPHome Version in the entry's store, as saved to disk."""
    version: str | None = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"].get(
        "installed_esphome_version"
    )
    return version


def _notifications(hass: HomeAssistant) -> list[persistent_notification.Notification]:
    return list(persistent_notification._async_get_or_create_notifications(hass).values())


async def test_a_successful_install_records_the_version_and_the_update_disappears(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage)
    fake.transcript = install_transcript("install_clean_success")
    in_progress: list[bool] = []
    stop_listening = hass.bus.async_listen(
        "state_changed",
        lambda event: _changed(in_progress, event.data["new_state"].attributes["in_progress"]),
        callback(lambda event_data: event_data["entity_id"] == ENTITY_ID),
    )

    await _install(hass)
    stop_listening()

    assert fake.mismatches == []
    assert fake.finished
    assert in_progress == [True, False]
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1"
    assert state.attributes["in_progress"] is False
    assert _recorded_version(hass_storage, entry) == "2026.10.1"
    assert _active_jobs(hass_storage, entry) is None
    assert _notifications(hass) == []


async def test_a_failed_compile_notifies_with_the_stage_error_and_log_tail_and_records_nothing(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage)
    fake.transcript = install_transcript("install_compile_failed")

    with pytest.raises(HomeAssistantError, match="compile"):
        await _install(hass)

    assert fake.mismatches == []
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["installed_version"] == "2026.10.0"
    assert state.attributes["in_progress"] is False
    assert _recorded_version(hass_storage, entry) == "2026.10.0"
    [notification] = _notifications(hass)
    assert "host-node" in str(notification["title"])
    message = notification["message"]
    assert "compile" in message
    assert "exit code 1" in message
    # The last 30 of the compile's 42 lines, with their ANSI colours stripped.
    assert "[7/30] CXX obj/src/esphome/components/host/gpio.cpp.o" not in message
    assert "[8/30] CXX obj/src/esphome/components/mdns/mdns_host.cpp.o" in message
    assert "error: ‘this_symbol_does_not_exist’ was not declared in this scope" in message
    assert "FAILED: [code=1] obj/src/main.cpp.o" in message
    assert "ERROR Host build failed\n" in message
    assert "\\033" not in message
    assert "\x1b" not in message


async def test_a_failed_upload_notifies_with_the_upload_stage_and_records_nothing(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage)
    fake.transcript = install_transcript("install_upload_failed")

    with pytest.raises(HomeAssistantError, match="upload"):
        await _install(hass)

    assert fake.mismatches == []
    assert hass.states.get(ENTITY_ID).state == STATE_ON  # type: ignore[union-attr]
    assert _recorded_version(hass_storage, entry) == "2026.10.0"
    [notification] = _notifications(hass)
    message = notification["message"]
    assert "**upload**" in message
    assert (
        "ERROR Upload failed after 3 attempts: connecting to 127.0.0.1 failed: "
        "[Errno 111] Connection refused"
    ) in message


def _with_output_line(transcript: Transcript, stage_index: int, line: str) -> Transcript:
    """*transcript* with *line* added to the output of its *stage_index*-th followed job."""
    follows = [
        i
        for i, step in enumerate(transcript)
        if step["dir"] == "out" and step["frame"]["command"] == "firmware/follow_job"
    ]
    follow = transcript[follows[stage_index]]["frame"]
    frame = {"message_id": follow["message_id"], "event": "output", "data": line}
    at = follows[stage_index] + 1
    return [*transcript[:at], {"dir": "in", "frame": frame}, *transcript[at:]]


async def test_a_successful_install_with_warnings_notifies_with_the_warnings(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage)
    warning = "\\033[33mWARNING GPIO2 is a strapping PIN and should only be used for I/O with care.\\033[0m\n"
    fake.transcript = _with_output_line(install_transcript("install_clean_success"), 0, warning)

    await _install(hass)

    assert fake.mismatches == []
    assert _recorded_version(hass_storage, entry) == "2026.10.1"
    [notification] = _notifications(hass)
    assert "host-node" in str(notification["title"])
    assert "warning" in str(notification["title"]).lower()
    assert (
        "WARNING GPIO2 is a strapping PIN and should only be used for I/O with care."
        in notification["message"]
    )
    assert "\\033" not in notification["message"]


async def test_compiler_warnings_and_the_remote_build_secrets_notice_are_not_warnings(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    """The add-on's real Install: a remote compile full of compiler warnings."""
    url, fake = builder
    await _set_up_with_an_update(hass, url, hass_storage)
    fake.transcript = install_transcript("addon_success")

    await _install(hass)

    assert fake.mismatches == []
    assert hass.states.get(ENTITY_ID).state == STATE_OFF  # type: ignore[union-attr]
    assert _notifications(hass) == []


async def test_a_clean_success_dismisses_the_notification_of_an_earlier_failed_install(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    await _set_up_with_an_update(hass, url, hass_storage)
    fake.transcript = install_transcript("install_upload_failed")
    with pytest.raises(HomeAssistantError):
        await _install(hass)
    assert len(_notifications(hass)) == 1

    fake.transcript = install_transcript("install_clean_success")
    await _install(hass)

    assert _notifications(hass) == []


async def test_a_connection_lost_mid_compile_notifies_with_the_stage_reached_and_records_nothing(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage)
    transcript = install_transcript("install_compile_failed")
    lost_at = next(
        i
        for i, step in enumerate(transcript)
        if step["frame"].get("data", "").startswith("[26/30]")
    )
    fake.transcript = [*transcript[: lost_at + 1], {"dir": "close"}]

    with pytest.raises(HomeAssistantError, match="compile"):
        await _install(hass)

    assert hass.states.get(ENTITY_ID).attributes["in_progress"] is False  # type: ignore[union-attr]
    assert _recorded_version(hass_storage, entry) == "2026.10.0"
    [notification] = _notifications(hass)
    assert "**compile**" in notification["message"]
    assert "websocket closed" in notification["message"]
    assert "[26/30] CXX obj/src/main.cpp.o" in notification["message"]


async def test_an_install_the_device_builder_defers_for_an_offline_device_fails_as_device_offline(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage)
    # Captured: the deferred compile is cancelled and the queued update cleared.
    fake.transcript = install_transcript("install_deferred_at_enqueue")

    with pytest.raises(HomeAssistantError, match="device offline"):
        await _install(hass)

    assert fake.mismatches == []
    assert fake.finished
    assert hass.states.get(ENTITY_ID).state == STATE_ON  # type: ignore[union-attr]
    assert _recorded_version(hass_storage, entry) == "2026.10.0"
    assert _active_jobs(hass_storage, entry) is None
    [notification] = _notifications(hass)
    assert "device offline" in str(notification["title"]).lower()
    assert "queued update was cleared" in notification["message"]


async def test_an_upload_that_cannot_reach_the_device_fails_as_device_unreachable(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    """With its web UI closed the Device Builder does not know the device is
    offline: the compile succeeds and the upload cannot connect."""
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage)
    fake.transcript = install_transcript("install_upload_failed")

    with pytest.raises(HomeAssistantError, match="device offline / unreachable"):
        await _install(hass)

    assert fake.mismatches == []
    assert _recorded_version(hass_storage, entry) == "2026.10.0"
    assert _active_jobs(hass_storage, entry) is None
    [notification] = _notifications(hass)
    assert "**upload**: device offline / unreachable" in notification["message"]
    assert "Connection refused" in notification["message"]


async def test_an_upload_that_fails_for_another_reason_is_not_called_unreachable(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    await _set_up_with_an_update(hass, url, hass_storage)
    transcript = install_transcript("install_upload_failed")
    fake.transcript = [
        step
        for step in transcript
        if "Connection refused" not in str(step["frame"].get("data", ""))
    ]
    fake.transcript = _with_output_line(fake.transcript, 1, "ERROR Error auth result: Invalid password\n")

    with pytest.raises(HomeAssistantError) as raised:
        await _install(hass)

    assert "unreachable" not in str(raised.value)
    [notification] = _notifications(hass)
    assert "unreachable" not in notification["message"]
    assert "Invalid password" in notification["message"]



def _queue_install(compile_id: str, upload_id: str, *, port: str = "OTA") -> Transcript:
    """The Device Builder's side of an Install uploading to *port*, up to following its jobs."""
    return [
        server_info(esphome_version="2026.10.1"),
        {"dir": "out", "frame": {"command": "firmware/install", "message_id": "1",
                                 "args": {"configuration": "host-node.yaml", "port": port}}},
        {"dir": "in", "frame": {"message_id": "1", "result": {
            "job_id": compile_id, "job_type": "compile", "is_deferred_install": False}}},
        {"dir": "out", "frame": {"command": "firmware/get_jobs", "message_id": "2",
                                 "args": {"configuration": "host-node.yaml"}}},
        {"dir": "in", "frame": {"message_id": "2", "result": [
            {"job_id": upload_id, "job_type": "upload", "depends_on": compile_id}]}},
    ]


def _active_jobs(hass_storage: dict[str, Any], entry: MockConfigEntry) -> list[dict[str, str]] | None:
    """The jobs of the running Install in the entry's store, as saved to disk."""
    active = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"].get("active_install")
    return None if active is None else list(active["jobs"])


def _changed(seen: list[Any], value: Any) -> None:
    """Append *value* when it differs from the last one, ignoring repeated writes."""
    if not seen or seen[-1] != value:
        seen.append(value)


def _attribute_changes(hass: HomeAssistant, attribute: str) -> tuple[list[Any], Any]:
    """Record each distinct value *attribute* takes on the entity."""
    seen: list[Any] = []
    stop = hass.bus.async_listen(
        "state_changed",
        lambda event: _changed(seen, event.data["new_state"].attributes.get(attribute)),
        callback(lambda event_data: event_data["entity_id"] == ENTITY_ID),
    )
    return seen, stop


async def test_an_install_interrupted_by_a_restart_is_re_attached_and_recorded(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage)
    fake.transcript = _queue_install("c1", "u1")
    fake.jobs = {"c1": job_events("INFO Compiling", status=None)}

    await hass.services.async_call("update", "install", {"entity_id": ENTITY_ID})
    await fake.running.wait()

    jobs = [{"job_id": "c1", "stage": "compile"}, {"job_id": "u1", "stage": "upload"}]
    assert _active_jobs(hass_storage, entry) == jobs

    # Home Assistant stops while the compile runs on.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert _active_jobs(hass_storage, entry) == jobs

    # It comes back; the Device Builder kept the jobs and one is still running.
    fake.transcript = [server_info(esphome_version="2026.10.1")]
    fake.running.clear()
    fake.jobs = {"c1": job_events("INFO Compiling", status=None)}
    seen, stop = _attribute_changes(hass, "in_progress")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await fake.running.wait()
    assert hass.states.get(ENTITY_ID).attributes["in_progress"] is True  # type: ignore[union-attr]

    # Restart again; this time both jobs have finished by the time it is back.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    fake.followed.clear()
    fake.jobs = {"c1": job_events("INFO Compiling"), "u1": job_events("INFO OTA successful")}
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    stop()

    assert fake.mismatches == []
    assert fake.followed == ["c1", "u1"]
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["in_progress"] is False
    assert seen[-1] is False and True in seen
    assert _recorded_version(hass_storage, entry) == "2026.10.1"
    assert _active_jobs(hass_storage, entry) is None
    assert _notifications(hass) == []


async def _set_up_after_a_restart(
    hass: HomeAssistant,
    url: str,
    hass_storage: dict[str, Any],
    *,
    wait: bool = True,
    clean_build: bool = False,
) -> MockConfigEntry:
    """An entry whose store holds an Install that was running when HA stopped.

    With *wait*, returns once the re-attached Install has ended."""
    entry_id = "tracked"
    hass_storage[f"{DOMAIN}.{entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry_id}",
        "data": {
            "installed_esphome_version": "2026.10.0",
            "active_install": {
                "jobs": [{"job_id": "c1", "stage": "compile"}, {"job_id": "u1", "stage": "upload"}],
                "commits": {},
                "clean_build": clean_build,
            },
        },
    }
    entry = await _set_up(hass, url, entry_id=entry_id)
    if wait:
        await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_a_re_attached_install_whose_jobs_are_gone_tells_the_user_the_outcome_is_unknown(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up_after_a_restart(hass, url, hass_storage)

    assert fake.followed == ["c1"]
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["in_progress"] is False
    assert _recorded_version(hass_storage, entry) == "2026.10.0"
    assert _active_jobs(hass_storage, entry) is None
    [notification] = _notifications(hass)
    assert "unknown" in str(notification["title"]).lower()
    assert "restarted" in notification["message"]


async def test_a_re_attached_install_that_fails_notifies_like_any_failed_install(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    fake.jobs = {
        "c1": job_events("INFO Successfully compiled program."),
        "u1": job_events(
            "ERROR Connecting to 192.168.1.50 port 3232 failed: [Errno 113] No route to host",
            status="failed",
        ),
    }
    entry = await _set_up_after_a_restart(hass, url, hass_storage)

    assert fake.followed == ["c1", "u1"]
    assert hass.states.get(ENTITY_ID).attributes["in_progress"] is False  # type: ignore[union-attr]
    assert _recorded_version(hass_storage, entry) == "2026.10.0"
    assert _active_jobs(hass_storage, entry) is None
    [notification] = _notifications(hass)
    assert "**upload**: device offline / unreachable" in notification["message"]
    assert "No route to host" in notification["message"]


async def test_an_install_cannot_start_while_a_re_attached_one_runs(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    fake.jobs = {"c1": job_events("INFO Compiling", status=None)}
    entry = await _set_up_after_a_restart(hass, url, hass_storage, wait=False)
    await fake.running.wait()

    with pytest.raises(HomeAssistantError, match="already in progress"):
        await _install(hass)
    # Stop following, so the fake Device Builder can shut down.
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_fresh_install_whose_job_vanishes_fails_without_claiming_a_restart(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage)
    fake.transcript = _queue_install("c1", "u1")
    # fake.jobs is empty: the Device Builder no longer knows c1.

    with pytest.raises(HomeAssistantError, match="failed at compile"):
        await _install(hass)

    assert _active_jobs(hass_storage, entry) is None
    [notification] = _notifications(hass)
    assert "Install failed" in str(notification["title"])
    assert "restarted" not in notification["message"]


async def test_with_clean_build_on_an_install_cleans_before_it_compiles(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage, clean_build=True)
    # The transcript expects `firmware/clean`, then the clean followed, then
    # `firmware/install`: any other order is a mismatch.
    fake.transcript = install_transcript("install_clean_success", clean={})

    await _install(hass)

    assert fake.mismatches == []
    assert fake.finished
    assert _recorded_version(hass_storage, entry) == "2026.10.1"
    assert _notifications(hass) == []


async def test_a_clean_build_that_fails_notifies_with_the_clean_stage_and_records_nothing(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage, clean_build=True)
    # A remote build server's clean fails; the Install stops before compiling.
    fake.transcript = install_transcript("install_clean_success", clean={"k2": "failed"})

    with pytest.raises(HomeAssistantError, match="failed at clean"):
        await _install(hass)

    assert fake.mismatches == []
    assert _recorded_version(hass_storage, entry) == "2026.10.0"
    [notification] = _notifications(hass)
    assert "Install failed at stage **clean**" in notification["message"]


async def test_the_upload_address_override_is_the_install_port(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage, upload_address="192.168.1.42")
    fake.transcript = install_transcript("install_clean_success", port="192.168.1.42")

    await _install(hass)

    assert fake.mismatches == []
    assert fake.finished
    assert _recorded_version(hass_storage, entry) == "2026.10.1"


async def test_an_install_re_attached_during_its_clean_build_uploads_where_it_was_started_for(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    entry_id = "tracked"
    hass_storage[f"{DOMAIN}.{entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry_id}",
        "data": {
            "installed_esphome_version": "2026.10.0",
            "active_install": {
                "jobs": [{"job_id": "k1", "stage": "clean"}],
                "commits": {},
                "port": "192.168.1.42",
            },
        },
    }
    fake.transcript = [
        server_info(esphome_version="2026.10.1"),
        {"dir": "out", "frame": {"command": "firmware/follow_job", "message_id": "k",
                                 "args": {"job_id": "k1"}}},
        *({"dir": "in", "frame": {"message_id": "k", **event}} for event in job_events("Done")),
        {"dir": "in", "frame": {"message_id": "k", "result": None}},
        *_queue_install("c1", "u1", port="192.168.1.42")[1:],
    ]
    fake.jobs = {"c1": job_events("INFO Compiling"), "u1": job_events("INFO OTA successful")}

    # The upload address was changed while Home Assistant was down.
    entry = await _set_up(hass, url, entry_id=entry_id, options={"upload_address": "10.0.0.9"})
    await hass.async_block_till_done(wait_background_tasks=True)

    assert fake.mismatches == []
    assert _recorded_version(hass_storage, entry) == "2026.10.1"


async def test_install_progress_moves_through_the_compile_and_upload_bands(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    await _set_up_with_an_update(hass, url, hass_storage)
    fake.transcript = _queue_install("c1", "u1")
    # The remote compile's ninja output tops out at 99; the upload reaches 100.
    fake.jobs = {
        "c1": job_events("[1057/1061] Linking firmware.elf", status="completed"),
        "u1": job_events("Uploading: [==========] 100% Done...", status="completed"),
    }
    seen, stop = _attribute_changes(hass, "update_percentage")

    await _install(hass)
    stop()

    assert fake.mismatches == []
    # compile 0-90: 99% of the band is 89, then its end fills it to 90;
    # upload 90-100 reaches 100. Once the Install ends the bar is None.
    assert seen == [0, 89, 90, 100, None]
    assert hass.states.get(ENTITY_ID).attributes["update_percentage"] is None  # type: ignore[union-attr]


async def test_a_failed_compile_holds_the_bar_below_its_band_end(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    """Only a completed job fills its band; a failed one leaves the bar alone."""
    url, fake = builder
    await _set_up_with_an_update(hass, url, hass_storage)
    fake.transcript = _queue_install("c1", "u1")
    fake.jobs = {"c1": job_events("[99/100] Compiling", status="failed")}
    seen, stop = _attribute_changes(hass, "update_percentage")

    with pytest.raises(HomeAssistantError):
        await _install(hass)
    stop()

    assert fake.mismatches == []
    # compile 0-90: 99% of the band is 89 and stays there; the upload never
    # runs, and once the Install ends the bar is None.
    assert seen == [0, 89, None]
    assert hass.states.get(ENTITY_ID).attributes["update_percentage"] is None  # type: ignore[union-attr]


async def test_a_clean_build_progresses_through_the_clean_band_before_compiling(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    await _set_up_with_an_update(hass, url, hass_storage, clean_build=True)
    # The clean job reports no progress of its own; its band fills when it ends.
    fake.transcript = install_transcript("install_clean_success", clean={})
    seen, stop = _attribute_changes(hass, "update_percentage")

    await _install(hass)
    stop()

    assert fake.mismatches == []
    # clean 0-5 ends at 5; compile 5-90 reaches 90; upload 90-100 reaches 100.
    assert seen == [0, 5, 90, 100, None]


async def test_a_failed_progress_poll_mid_compile_does_not_fail_the_install(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(
        logging.DEBUG, logger="custom_components.esphome_git_updates.device_builder"
    )
    monkeypatch.setattr(device_builder, "PROGRESS_POLL_INTERVAL", 0.01)
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage)
    fake.transcript = _queue_install("c1", "u1")
    compile_done = asyncio.Event()
    fake.waits = {"c1": compile_done}
    fake.jobs = {
        "c1": job_events("[10/100] Compiling", status=None),
        "u1": job_events("Uploading: [==========] 100% Done...", status="completed"),
    }

    installing = asyncio.create_task(_install(hass))
    await asyncio.wait_for(fake.waiting.wait(), 1)
    # The bar has moved on the compile's progress before the poll fails.
    await _wait_for_percentage(hass, 9)
    fake.progress_errors = {"c1": 1}
    await _wait_until(
        lambda: "progress poll for job c1 failed" in caplog.text,
        "no failed poll was logged",
    )

    fake.jobs["c1"] = job_events(
        "[10/100] Compiling", "[100/100] Linking firmware.elf", status="completed"
    )
    compile_done.set()
    await installing

    assert fake.mismatches == []
    assert _recorded_version(hass_storage, entry) == "2026.10.1"
    assert hass.states.get(ENTITY_ID).attributes["update_percentage"] is None  # type: ignore[union-attr]
    assert _notifications(hass) == []


async def test_a_malformed_progress_poll_is_logged_and_the_install_succeeds(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(
        logging.DEBUG, logger="custom_components.esphome_git_updates.device_builder"
    )
    url, fake = builder
    entry = await _set_up_with_an_update(hass, url, hass_storage)
    fake.transcript = _queue_install("c1", "u1")
    fake.jobs = {
        "c1": job_events("[1057/1061] Linking firmware.elf", status="completed"),
        "u1": job_events("Uploading: [==========] 100% Done...", status="completed"),
    }
    fake.progress_malformed = {"c1": 1}

    await _install(hass)

    assert fake.mismatches == []
    assert _recorded_version(hass_storage, entry) == "2026.10.1"
    assert "malformed job progress" in caplog.text
    assert hass.states.get(ENTITY_ID).attributes["update_percentage"] is None  # type: ignore[union-attr]
    assert _notifications(hass) == []


async def _wait_until(
    predicate: Callable[[], bool], message: str = "condition was never met"
) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(message)


async def _wait_for_percentage(hass: HomeAssistant, expected: int) -> None:
    def reached() -> bool:
        state = hass.states.get(ENTITY_ID)
        return state is not None and state.attributes.get("update_percentage") == expected

    try:
        await _wait_until(reached)
    except AssertionError:
        state = hass.states.get(ENTITY_ID)
        actual = None if state is None else state.attributes.get("update_percentage")
        raise AssertionError(
            f"update_percentage never became {expected}: {actual}"
        ) from None


async def test_a_re_attached_install_shows_progress_from_its_job_and_completes(
    hass: HomeAssistant,
    builder: tuple[str, FakeDeviceBuilder],
    hass_storage: dict[str, Any],
) -> None:
    url, fake = builder
    # It had a Clean Build on, so the compile's 0-100 maps onto 5-90.
    compile_done = asyncio.Event()
    fake.waits = {"c1": compile_done}
    fake.jobs = {
        "c1": job_events("[50/100] Compiling", status=None),
        "u1": job_events("Uploading: [==========] 100% Done...", status="completed"),
    }
    entry = await _set_up_after_a_restart(
        hass, url, hass_storage, wait=False, clean_build=True
    )
    await asyncio.wait_for(fake.waiting.wait(), 1)

    await _wait_for_percentage(hass, 47)
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["in_progress"] is True
    assert state.attributes["update_percentage"] == 47

    # The compile finishes; the Install goes on to upload and records its version.
    fake.jobs["c1"] = job_events(
        "[50/100] Compiling", "[100/100] Linking firmware.elf", status="completed"
    )
    compile_done.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["in_progress"] is False
    assert state.attributes["update_percentage"] is None
    assert _recorded_version(hass_storage, entry) == "2026.10.1"
    assert _active_jobs(hass_storage, entry) is None
    assert _notifications(hass) == []
