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

Once a connection's transcript is done, the server answers
`devices/get_config` from `files` (path -> raw YAML), the way the Device
Builder reads its config directory; a path not in `files` gets `not_found`.
Every path asked for is recorded in `reads`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer
from yarl import URL

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


def port_of(url: str) -> int:
    """The port a served fake listens on, as an add-on's ingress port."""
    port = URL(url).port
    assert port is not None
    return port


def _args_match(recorded: dict[str, Any], sent: dict[str, Any]) -> bool:
    """Compare args; a recorded "REDACTED" (credentials) matches any value."""
    return recorded.keys() == sent.keys() and all(
        value == "REDACTED" or sent[key] == value for key, value in recorded.items()
    )


class FakeDeviceBuilder:
    def __init__(self, transcript: Transcript, files: dict[str, str] | None = None) -> None:
        self.transcript = transcript
        self.files = files if files is not None else {}
        self.reads: list[str] = []
        # The base URL, once served.
        self.url = ""
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
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await self._answer(ws, json.loads(msg.data))
        return ws

    async def _answer(self, ws: web.WebSocketResponse, sent: dict[str, Any]) -> None:
        if sent.get("command") != "devices/get_config":
            self.mismatches.append(f"unexpected after the transcript: {sent}")
            await ws.close()
            return
        path = sent["args"]["configuration"]
        self.reads.append(path)
        if path in self.files:
            await ws.send_json({"message_id": sent["message_id"], "result": self.files[path]})
        else:
            await ws.send_json(
                {
                    "message_id": sent["message_id"],
                    "error_code": "not_found",
                    "details": f"Device '{path}' not found",
                }
            )


def install_transcript(fixture: str, configuration: str = "host-node.yaml") -> Transcript:
    """A captured Install, replayed as the entity runs it: no Clean Build, port OTA.

    The captures were made with other configurations, ports and a Clean Build
    step; only the frames the entity sends are rewritten, the server's answers
    stay as captured.
    """
    frames = load_fixture(fixture)
    start = next(
        i
        for i, step in enumerate(frames)
        if step["dir"] == "out" and step["frame"]["command"] == "firmware/install"
    )
    replay: Transcript = [frames[0]]
    for step in frames[start:]:
        if step["dir"] == "out":
            args = dict(step["frame"]["args"])
            if "configuration" in args:
                args["configuration"] = configuration
            if step["frame"]["command"] == "firmware/install":
                args["port"] = "OTA"
            step = {**step, "frame": {**step["frame"], "args": args}}
        replay.append(step)
    return replay


@asynccontextmanager
async def serve(
    transcript: Transcript, files: dict[str, str] | None = None
) -> AsyncIterator[tuple[str, FakeDeviceBuilder]]:
    """Run a fake Device Builder; yields its base URL and the fake."""
    fake = FakeDeviceBuilder(transcript, files)
    app = web.Application()
    app.router.add_get("/ws", fake.handle)
    server = TestServer(app)
    await server.start_server()
    fake.url = str(server.make_url(""))
    try:
        yield fake.url, fake
    finally:
        await server.close()
