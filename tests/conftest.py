"""Shared fixtures for every test."""

from __future__ import annotations

import asyncio
from collections.abc import Generator

import pytest
from aiohttp import ClientWebSocketResponse


@pytest.fixture(autouse=True)
def _allow_sockets(socket_enabled: None) -> None:
    """Let tests reach the fake Device Builder on 127.0.0.1.

    pytest-homeassistant-custom-component blocks sockets in every test.
    """


@pytest.fixture(autouse=True)
def _cancel_stray_websocket_heartbeats(verify_cleanup: None) -> Generator[None]:
    """Cancel heartbeat timers aiohttp re-arms on an already closed websocket.

    aiohttp 3.14 resets a client websocket's heartbeat whenever data arrives,
    without checking whether the socket is closed. The server's close frame
    arrives after `close()` has cancelled the heartbeat, so the timer comes
    back. If a test jumps time past it, it pings the dead socket and arms a
    pong timeout. Neither does anything, but `verify_cleanup` fails the test
    on them. Depending on `verify_cleanup` makes this teardown run first, and
    only these timers are cancelled, so real leaks still fail.
    """
    yield
    stray = {ClientWebSocketResponse._send_heartbeat, ClientWebSocketResponse._pong_not_received}
    loop = asyncio.get_event_loop()
    for handle in loop._scheduled:  # type: ignore[attr-defined]
        if getattr(getattr(handle, "_callback", None), "__func__", None) in stray:
            handle.cancel()
