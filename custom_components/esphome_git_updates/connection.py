"""Connect to an entry's Device Builder, and handle what can go wrong doing so."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .device_builder import (
    AuthenticationFailed,
    ConnectionLost,
    DeviceBuilderClient,
    DeviceBuilderUnavailable,
    UnexpectedFrame,
    UnsupportedServerVersion,
    connect,
)
from .location import DeviceBuilderNotFound, async_resolve_location
from .reauth import Reauthentication

__all__ = [
    "DeviceBuilderSession",
    "DeviceBuilderUnavailable",
    "ProtocolIssue",
    "ProtocolProblem",
    "connect_to_device_builder",
    "protocol_issue_id",
]

# Connecting, logging in and one quick command should take well under this.
CONNECT_TIMEOUT = 15

# What a Device Builder that cannot be reached raises. Not CommandFailed or
# JobNotFound: those are answers, which the caller handles.
_UNREACHABLE = (ConnectionLost, DeviceBuilderNotFound, aiohttp.ClientError, OSError, TimeoutError)


@asynccontextmanager
async def connect_to_device_builder(
    hass: HomeAssistant, location: Mapping[str, Any], *, long_running: bool = False
) -> AsyncIterator[DeviceBuilderClient]:
    """Connect to the Device Builder at *location*, logging in if it has credentials.

    An add-on location is resolved afresh on every call, so a moved ingress
    port is picked up; a vanished add-on raises DeviceBuilderNotFound.

    The timeout covers the body too, so keep it to quick commands. An Install
    passes *long_running*: then only connecting and logging in are timed, and
    the websocket heartbeat notices a Device Builder that goes away.
    """
    timeout = asyncio.timeout(CONNECT_TIMEOUT)
    async with timeout:
        resolved = await async_resolve_location(hass, location)
        async with connect(
            async_get_clientsession(hass),
            resolved.url,
            username=resolved.username,
            password=resolved.password,
        ) as client:
            if long_running:
                timeout.reschedule(None)
            yield client


class ProtocolProblem(Exception):
    """A caller found the Device Builder answering in a way it cannot work with.

    Raised inside `DeviceBuilderSession.connect`, it becomes the entry's
    protocol Repair issue, under the caller's name.
    """

    def __init__(self, message: str, translation_key: str, placeholders: dict[str, str]) -> None:
        super().__init__(message)
        self.translation_key = translation_key
        self.placeholders = placeholders


def protocol_issue_id(entry_id: str) -> str:
    """The Repair issue raised when an entry's Device Builder answers unexpectedly."""
    return f"device_builder_protocol_{entry_id}"


class ProtocolIssue:
    """The one protocol-drift Repair issue of an entry (ADR-0002).

    Several pollers and the Install talk to the Device Builder. Each poller or
    the Install reports under its own name, and the issue stays until every one
    of them is healthy again, so one's success does not hide another's drift.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._title = entry.title
        self._issue_id = protocol_issue_id(entry.entry_id)
        self._problems: dict[str, tuple[str, dict[str, str]]] = {}

    def report(self, name: str, translation_key: str, placeholders: dict[str, str]) -> None:
        self._problems[name] = (translation_key, placeholders)
        self._raise(translation_key, placeholders)

    def report_drift(self, name: str, err: UnsupportedServerVersion | UnexpectedFrame) -> None:
        """Report, under *name*, that the Device Builder answered in a way this
        integration does not understand."""
        if isinstance(err, UnsupportedServerVersion):
            self.report(
                name, "unsupported_server_version", {"server_version": err.server_version}
            )
        else:
            self.report(name, "unexpected_frame", {"error": str(err)})

    def clear(self, name: str) -> None:
        if self._problems.pop(name, None) is not None and self._problems:
            # Another name's problem remains: show that one.
            self._raise(*next(iter(self._problems.values())))
        elif not self._problems:
            ir.async_delete_issue(self._hass, DOMAIN, self._issue_id)

    def _raise(self, translation_key: str, placeholders: dict[str, str]) -> None:
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=translation_key,
            translation_placeholders={"title": self._title, **placeholders},
        )


class ConnectFactory(Protocol):
    """Opens one connection to the entry's Device Builder."""

    def __call__(
        self, *, long_running: bool
    ) -> AbstractAsyncContextManager[DeviceBuilderClient]: ...


class DeviceBuilderSession:
    """How every caller of one entry talks to its Device Builder.

    The callers (the pollers and the Install) each connect under their own
    name. The session holds back connections while new credentials are
    awaited, asks for them when the login is rejected, and raises or clears
    the protocol Repair issue under the caller's name. Whatever of this goes
    wrong reaches the caller as one `DeviceBuilderUnavailable`; any other
    error, such as `CommandFailed`, passes through untouched.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, connect: ConnectFactory | None = None
    ) -> None:
        self._reauth = Reauthentication(hass, entry)
        self._issue = ProtocolIssue(hass, entry)
        def connect_to_entry(
            *, long_running: bool
        ) -> AbstractAsyncContextManager[DeviceBuilderClient]:
            return connect_to_device_builder(hass, entry.data, long_running=long_running)

        self._connect: ConnectFactory = connect or connect_to_entry

    @asynccontextmanager
    async def connect(
        self, name: str, *, long_running: bool = False
    ) -> AsyncIterator[DeviceBuilderClient]:
        """Connect for the caller *name*, which reports protocol drift under it.

        Its problem clears only when the block exits without an exception: a
        reply this integration does not understand can come mid-command.
        *long_running* lifts the timeout once connected (see
        `connect_to_device_builder`).
        """
        if self._reauth.required:
            # Trying again would only get the Device Builder to answer
            # `rate_limited` (ticket 13).
            raise DeviceBuilderUnavailable("waiting for new Device Builder credentials")
        try:
            async with self._connect(long_running=long_running) as client:
                yield client
        except AuthenticationFailed as err:
            self._reauth.async_require(err)
            raise DeviceBuilderUnavailable(f"the Device Builder refused the login: {err}") from err
        except (UnsupportedServerVersion, UnexpectedFrame) as err:
            self._issue.report_drift(name, err)
            raise DeviceBuilderUnavailable(str(err)) from err
        except ProtocolProblem as err:
            self._issue.report(name, err.translation_key, err.placeholders)
            raise DeviceBuilderUnavailable(str(err)) from err
        except _UNREACHABLE as err:
            raise DeviceBuilderUnavailable(f"cannot reach the Device Builder: {err}") from err
        self._issue.clear(name)
