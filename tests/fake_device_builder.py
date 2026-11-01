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

The server answers `devices/get_config` from `files` (path -> raw YAML),
the way the Device Builder reads its config directory, at any point: reads
are not part of a transcript, and a connection that only reads files and
closes is no mismatch. A path not in `files` gets `not_found`. Every path
asked for is recorded in `reads`.

It also answers `firmware/follow_job` from `jobs` (job id -> the frames' event
data, `{"event": "output" | "result", "data": ...}`), the way the Device
Builder keeps jobs across connections; an unknown id gets the
`internal_error` the Device Builder answers for a pruned job. A job with no
`result` event is still running: its output is sent, then the follow is
held open and `running` is set. Every job id followed is recorded in
`followed`. `firmware/clear_queued_update` is answered with `null` and its
configuration recorded in `cleared`.
"""

from __future__ import annotations

import asyncio
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
        self.jobs: dict[str, list[dict[str, Any]]] = {}
        self.followed: list[str] = []
        self.cleared: list[str] = []
        self.running = asyncio.Event()

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
            # File reads are not part of a transcript: they are answered whenever they come.
            while msg.type == WSMsgType.TEXT and _is_read(sent := json.loads(msg.data), frame):
                await self._read(ws, sent)
                msg = await ws.receive()
            if msg.type != WSMsgType.TEXT:
                if not ids and msg.type in _CLOSING:
                    # A connection that only read files, like a poll's.
                    return ws
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
        message_id = sent["message_id"]
        if sent.get("command") == "firmware/follow_job":
            await self._follow(ws, message_id, sent["args"]["job_id"])
            return
        if sent.get("command") == "firmware/clear_queued_update":
            self.cleared.append(sent["args"]["configuration"])
            await ws.send_json({"message_id": message_id, "result": None})
            return
        if sent.get("command") != "devices/get_config":
            self.mismatches.append(f"unexpected after the transcript: {sent}")
            await ws.close()
            return
        await self._read(ws, sent)

    async def _read(self, ws: web.WebSocketResponse, sent: dict[str, Any]) -> None:
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


    async def _follow(self, ws: web.WebSocketResponse, message_id: str, job_id: str) -> None:
        self.followed.append(job_id)
        if job_id not in self.jobs:
            await ws.send_json(
                {
                    "message_id": message_id,
                    "error_code": "internal_error",
                    "details": "Command failed: firmware/follow_job",
                }
            )
            return
        events = self.jobs[job_id]
        for event in events:
            await ws.send_json({"message_id": message_id, **event})
        if any(event["event"] == "result" for event in events):
            await ws.send_json({"message_id": message_id, "result": None})
        else:
            self.running.set()


_CLOSING = (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED)


def _is_read(sent: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Whether *sent* is a file read where the transcript expects another command."""
    return bool(sent.get("command") == "devices/get_config" != expected["command"])


def job_events(*lines: str, status: str | None = "completed") -> list[dict[str, Any]]:
    """A job for `FakeDeviceBuilder.jobs`: its output *lines*, then its end
    with *status*, or no end if *status* is None (the job is still running)."""
    events: list[dict[str, Any]] = [{"event": "output", "data": line} for line in lines]
    if status is not None:
        events.append(
            {
                "event": "result",
                "data": {
                    "status": status,
                    "exit_code": 0 if status == "completed" else 1,
                    "error": None,
                    "queued_update_armed": False,
                },
            }
        )
    return events


def _index_of(frames: Transcript, command: str) -> int:
    """Where the client sends *command* in *frames*."""
    return next(
        i
        for i, step in enumerate(frames)
        if step["dir"] == "out" and step["frame"]["command"] == command
    )


def with_clean_jobs(
    frames: Transcript, remote: dict[str, str] | None = None, *, ended: tuple[str, ...] = ()
) -> Transcript:
    """A captured Clean Build Install, as the client now runs it: `firmware/get_jobs`
    right after `firmware/clean`, then each *remote* clean job followed after the
    local one, before compiling.

    The captures predate that, so these frames are synthesized. `firmware/clean`
    fans out one clean job per connected remote build server, and the add-on's
    `get_jobs` showed them alongside the local one and older, finished cleans.
    *remote* maps each such job id to the status it ends with; those in *ended*
    have ended by the time `get_jobs` answers. A clean that does not complete
    ends the Install, so the frames after it are dropped.
    """
    remote = remote or {}
    clean_at = _index_of(frames, "firmware/clean")
    configuration = frames[clean_at]["frame"]["args"]["configuration"]
    local = frames[clean_at + 1]["frame"]["result"]
    jobs = [
        {"job_id": "older-clean", "job_type": "clean", "status": "completed",
         "created_at": "2026-10-29T21:48:18.799927+00:00", "source": "remote"},
        {"job_id": local["job_id"], "job_type": "clean", "status": "running",
         "created_at": local["created_at"], "source": "local"},
        *(
            {"job_id": job_id, "job_type": "clean",
             "status": status if job_id in ended else "queued",
             # Queued right after the local clean, by the same command.
             "created_at": local["created_at"], "source": "remote"}
            for job_id, status in remote.items()
        ),
    ]
    listing: Transcript = [
        {"dir": "out", "frame": {"command": "firmware/get_jobs", "message_id": "clean-jobs",
                                 "args": {"configuration": configuration}}},
        {"dir": "in", "frame": {"message_id": "clean-jobs", "result": jobs}},
    ]
    install_at = _index_of(frames, "firmware/install")
    replay = [*frames[: clean_at + 2], *listing, *frames[clean_at + 2 : install_at]]
    for job_id, status in remote.items():
        message_id = f"follow-{job_id}"
        replay += [
            {"dir": "out", "frame": {"command": "firmware/follow_job", "message_id": message_id,
                                     "args": {"job_id": job_id}}},
            *(
                {"dir": "in", "frame": {"message_id": message_id, **event}}
                for event in job_events(f"INFO Deleting build/{job_id}\n", status=status)
            ),
            {"dir": "in", "frame": {"message_id": message_id, "result": None}},
        ]
        if status != "completed":
            return replay
    return [*replay, *frames[install_at:]]


def install_transcript(
    fixture: str,
    configuration: str = "host-node.yaml",
    *,
    clean: dict[str, str] | None = None,
    port: str = "OTA",
) -> Transcript:
    """A captured Install, replayed as the entity runs it: uploading to *port*, and
    with no Clean Build unless *clean* is given.

    *clean* is a Clean Build's remote clean jobs, as `with_clean_jobs` takes them;
    the fixture must have captured a Clean Build. The captures were made with
    other configurations, ports and Clean Build choices; only the frames the
    entity sends are rewritten, the server's answers stay as captured.
    """
    frames = load_fixture(fixture)
    start = _index_of(frames, "firmware/install" if clean is None else "firmware/clean")
    replay: Transcript = [frames[0]]
    for step in frames[start:]:
        if step["dir"] == "out":
            args = dict(step["frame"]["args"])
            if "configuration" in args:
                args["configuration"] = configuration
            if step["frame"]["command"] == "firmware/install":
                args["port"] = port
            step = {**step, "frame": {**step["frame"], "args": args}}
        replay.append(step)
    return replay if clean is None else with_clean_jobs(replay, clean)


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
