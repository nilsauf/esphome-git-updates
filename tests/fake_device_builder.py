"""A fake Device Builder `/ws` server that replays a recorded transcript.

A transcript is the list of frames one client connection exchanged with a
Device Builder, in order: `{"dir": "in", "frame": ...}` for server -> client
and `{"dir": "out", "frame": ...}` for client -> server. The CLI's `--record`
option writes them; `tests/fixtures/*.json` holds captured ones.

The server sends every `in` frame as soon as the frames before it are done,
and for every `out` frame waits for the client's next frame and checks that
it is the same command with the same args (a recorded "REDACTED", which the
CLI writes in place of credentials, matches any value). Message ids are remapped, so a
replay does not depend on the client numbering its messages the same way.
A mismatch closes the connection and is recorded in `mismatches`. A
`{"dir": "close"}` entry makes the server drop the connection at that point.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

FIXTURES = Path(__file__).parent / "fixtures"

Transcript = list[dict[str, Any]]


def load_fixture(name: str) -> Transcript:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    frames: Transcript = data["frames"]
    return frames


def server_info(
    server_version: str = "1.26.0",
    esphome_version: str = "2026.10.1",
    *,
    requires_auth: bool = False,
) -> dict[str, Any]:
    """A handshake frame shaped like Device Builder 1.26's ServerInfoMessage."""
    return {
        "dir": "in",
        "frame": {
            "server_version": server_version,
            "esphome_version": esphome_version,
            "port": 6052,
            "ha_addon": False,
            "friendly_name": "",
            "ha_ingress": False,
            "requires_auth": requires_auth,
            "desktop_version": "",
            "desktop_update_capable": False,
            "in_docker": False,
        },
    }


def _args_match(recorded: dict[str, Any], sent: dict[str, Any]) -> bool:
    """Compare args; a recorded "REDACTED" (credentials) matches any value."""
    return recorded.keys() == sent.keys() and all(
        value == "REDACTED" or sent[key] == value for key, value in recorded.items()
    )


class FakeDeviceBuilder:
    def __init__(self, transcript: Transcript) -> None:
        self.transcript = transcript
        self.mismatches: list[str] = []
        self.finished = False

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        ids: dict[str, str] = {}
        for entry in self.transcript:
            if entry["dir"] == "close":
                self.finished = True
                await ws.close()
                return ws
            frame = entry["frame"]
            if entry["dir"] == "in":
                if "message_id" in frame:
                    frame = {**frame, "message_id": ids.get(frame["message_id"], frame["message_id"])}
                await ws.send_json(frame)
                continue
            msg = await ws.receive()
            if msg.type != WSMsgType.TEXT:
                self.mismatches.append(f"expected {frame['command']}, client sent {msg.type.name}")
                return ws
            sent = json.loads(msg.data)
            if sent.get("command") != frame["command"] or not _args_match(
                frame.get("args", {}), sent.get("args", {})
            ):
                self.mismatches.append(f"expected {frame}, client sent {sent}")
                await ws.close()
                return ws
            ids[frame["message_id"]] = sent["message_id"]
        self.finished = True
        # Leave the socket open until the client closes it, like a real server.
        async for _ in ws:
            pass
        return ws


@asynccontextmanager
async def serve(transcript: Transcript) -> AsyncIterator[tuple[str, FakeDeviceBuilder]]:
    """Run a fake Device Builder; yields its base URL and the fake."""
    fake = FakeDeviceBuilder(transcript)
    app = web.Application()
    app.router.add_get("/ws", fake.handle)
    server = TestServer(app)
    await server.start_server()
    try:
        yield str(server.make_url("")), fake
    finally:
        await server.close()
