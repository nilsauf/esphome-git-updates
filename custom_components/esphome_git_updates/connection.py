"""Open a Device Builder connection from a config entry's location data."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .device_builder import DeviceBuilderClient, DeviceBuilderError, connect
from .location import async_resolve_location

# Connecting, logging in and one quick command should take well under this.
CONNECT_TIMEOUT = 15

# Everything that means "could not talk to the Device Builder". Catch the more
# specific DeviceBuilderError subclasses before this when they need their own handling.
CONNECTION_ERRORS = (DeviceBuilderError, aiohttp.ClientError, OSError, TimeoutError)


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
