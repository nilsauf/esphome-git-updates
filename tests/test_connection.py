"""One Device Builder session per config entry: the reauth gate, rejected logins
and protocol drift, for every caller (ticket 18)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import cast

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esphome_git_updates.connection import (
    DeviceBuilderSession,
    DeviceBuilderUnavailable,
    ProtocolProblem,
    protocol_issue_id,
)
from custom_components.esphome_git_updates.const import DOMAIN
from custom_components.esphome_git_updates.device_builder import (
    AuthenticationFailed,
    CommandFailed,
    ConnectionLost,
    DeviceBuilderClient,
    JobNotFound,
    UnexpectedFrame,
    UnsupportedServerVersion,
)
from custom_components.esphome_git_updates.install import (
    ActiveInstall,
    Failed,
    InstallPlan,
    InstallRunner,
)
from custom_components.esphome_git_updates.location import DeviceBuilderNotFound

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

MANUAL = {"url": "http://device-builder:6052", "configuration": "host-node.yaml"}


@dataclass
class FakeConnect:
    """A connect factory that fails with the next of *errors*, or connects."""

    errors: list[BaseException] = field(default_factory=list)
    calls: list[bool] = field(default_factory=list)

    def __call__(
        self, *, long_running: bool
    ) -> AbstractAsyncContextManager[DeviceBuilderClient]:
        self.calls.append(long_running)
        return self._connect()

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[DeviceBuilderClient]:
        if self.errors:
            raise self.errors.pop(0)
        yield cast(DeviceBuilderClient, object())


def _session(
    hass: HomeAssistant, connect: FakeConnect, data: dict[str, str] | None = None
) -> tuple[DeviceBuilderSession, MockConfigEntry]:
    entry = MockConfigEntry(domain=DOMAIN, title="host-node", data=data or MANUAL)
    entry.add_to_hass(hass)
    return DeviceBuilderSession(hass, entry, connect), entry


def _reauth_flows(hass: HomeAssistant) -> list[str]:
    return [
        flow["context"]["entry_id"]
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_a_rejected_login_starts_reauth_once_and_no_caller_connects_until_then(
    hass: HomeAssistant,
) -> None:
    connect = FakeConnect([AuthenticationFailed("login refused: Invalid credentials")])
    session, entry = _session(hass, connect)

    with pytest.raises(DeviceBuilderUnavailable, match="^the Device Builder refused the login: "):
        async with session.connect("version"):
            pass
    await hass.async_block_till_done()

    for name in ("version", "git", "install"):
        with pytest.raises(
            DeviceBuilderUnavailable, match="^waiting for new Device Builder credentials$"
        ):
            async with session.connect(name, long_running=True):
                pass
    await hass.async_block_till_done()

    assert connect.calls == [False]
    assert _reauth_flows(hass) == [entry.entry_id]


async def test_a_rejected_login_on_an_add_on_entry_is_logged_once_and_never_pauses_connecting(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    rejected = AuthenticationFailed("login refused: Invalid credentials")
    connect = FakeConnect([rejected, rejected])
    session, _ = _session(
        hass, connect, {"addon_slug": "5c53de3b_esphome", "configuration": "host-node.yaml"}
    )

    for _ in range(3):
        with pytest.raises(DeviceBuilderUnavailable):
            async with session.connect("version"):
                raise ConnectionLost("websocket closed")
    await hass.async_block_till_done()

    # The add-on has no credentials to re-enter, so there is no reauth to wait for.
    assert connect.calls == [False, False, False]
    assert _reauth_flows(hass) == []
    assert caplog.text.count("has no credentials to update") == 1


async def test_drift_is_a_repair_issue_until_the_same_caller_exits_cleanly(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    connect = FakeConnect([UnsupportedServerVersion("9.0.0")])
    session, entry = _session(hass, connect)
    issue_id = protocol_issue_id(entry.entry_id)

    with pytest.raises(DeviceBuilderUnavailable, match="^Device Builder 9.0.0 is not supported"):
        async with session.connect("version"):
            pass
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "unsupported_server_version"
    assert issue.translation_placeholders == {"title": "host-node", "server_version": "9.0.0"}

    # A clean handshake whose block then fails clears nothing: drift can come mid-command.
    with pytest.raises(DeviceBuilderUnavailable):
        async with session.connect("version"):
            raise ConnectionLost("websocket closed")
    with pytest.raises(CommandFailed):
        async with session.connect("version"):
            raise CommandFailed("config/get", "not_found", "no such file")
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    async with session.connect("version"):
        pass
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_one_callers_clean_exit_leaves_anothers_problem_visible(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    session, entry = _session(hass, FakeConnect())
    issue_id = protocol_issue_id(entry.entry_id)

    with pytest.raises(DeviceBuilderUnavailable, match="^the Device Builder refused to read "):
        async with session.connect("git"):
            raise ProtocolProblem(
                "the Device Builder refused to read common.yaml: config/get failed",
                "include_refused",
                {"file": "common.yaml", "error": "forbidden: no"},
            )
    with pytest.raises(DeviceBuilderUnavailable, match="^unexpected frame from Device Builder"):
        async with session.connect("install", long_running=True):
            raise UnexpectedFrame("no job id", {"job": "?"})

    async with session.connect("install", long_running=True):
        pass

    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "include_refused"
    assert issue.translation_placeholders == {
        "title": "host-node",
        "file": "common.yaml",
        "error": "forbidden: no",
    }

    async with session.connect("git"):
        pass
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.parametrize(
    "error",
    [
        ConnectionLost("websocket closed"),
        DeviceBuilderNotFound("add-on gone"),
        aiohttp.ClientConnectionError("connection refused"),
        OSError("no route to host"),
        TimeoutError(),
    ],
    ids=lambda err: type(err).__name__,
)
async def test_a_device_builder_that_cannot_be_reached_is_unavailable_and_not_a_repair_issue(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry, error: Exception
) -> None:
    session, _ = _session(hass, FakeConnect([error]))

    with pytest.raises(DeviceBuilderUnavailable, match="^cannot reach the Device Builder: ") as info:
        async with session.connect("version"):
            pass

    assert info.value.__cause__ is error
    assert list(issue_registry.issues) == []
    assert _reauth_flows(hass) == []


@pytest.mark.parametrize(
    "error",
    [
        CommandFailed("config/get", "not_found", "no such file"),
        JobNotFound("c1"),
        asyncio.CancelledError(),
        ValueError("a bug"),
    ],
    ids=lambda err: type(err).__name__,
)
async def test_an_answer_or_any_other_error_from_the_caller_passes_through(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry, error: BaseException
) -> None:
    session, _ = _session(hass, FakeConnect())

    with pytest.raises(type(error)) as info:
        async with session.connect("install", long_running=True):
            raise error

    assert info.value is error
    assert list(issue_registry.issues) == []


async def test_long_running_reaches_the_connection(hass: HomeAssistant) -> None:
    connect = FakeConnect()
    session, _ = _session(hass, connect)

    async with session.connect("install", long_running=True):
        pass
    async with session.connect("version"):
        pass

    assert connect.calls == [True, False]


class _NoBookkeeping:
    async def async_set_active_install(self, active: ActiveInstall) -> None:
        raise AssertionError("no job should start")

    async def async_clear_active_install(self) -> None:
        pass

    async def async_record_install(self, esphome_version: str, commits: Mapping[str, str]) -> None:
        raise AssertionError("nothing should be recorded")


async def test_an_install_pressed_while_reauth_is_pending_fails_at_connect_without_logging_in(
    hass: HomeAssistant,
) -> None:
    connect = FakeConnect([AuthenticationFailed("login refused: Invalid credentials")])
    session, _ = _session(hass, connect)
    with pytest.raises(DeviceBuilderUnavailable):
        async with session.connect("version"):
            pass

    runner = InstallRunner("host-node.yaml", session, _NoBookkeeping())
    ending = await runner.run(InstallPlan({}, port="OTA", clean_build=False), lambda _: None)

    assert isinstance(ending, Failed)
    assert (ending.stage, ending.error) == ("connect", "waiting for new Device Builder credentials")
    assert connect.calls == [False]
