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

It answers `firmware/get_job` at any point with the job's progress, parsed
from the output it has replayed (or from `jobs`), unless `progress` scripts a
sequence for that job. `progress_errors` makes the next polls for a job fail,
and `progress_malformed` makes them answer with an unexpected shape. Every
poll is recorded in `get_job_polls`, and `polled` is set after each answer.

A job in `waits` holds its follow open until the test sets that event; then
the events added to `jobs` in the meantime, including a result, are replayed.
`waiting` is set once a follow is held. After the transcript, each client
command is answered on its own task, so `get_job` still runs while a follow
is held.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer
from yarl import URL

FIXTURES = Path(__file__).parent / "fixtures"

_NINJA = re.compile(r"\[(\d+)/(\d+)\]")
_UPLOAD_PERCENT = re.compile(r"Uploading:.*?(\d+)%")

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
        # `firmware/get_job` answers: a scripted sequence per job (the last
        # value repeats), else progress parsed from the job's output.
        self.progress: dict[str, list[int | None]] = {}
        # How many of the next polls for a job fail with `internal_error`.
        self.progress_errors: dict[str, int] = {}
        # How many of the next polls for a job answer with a malformed progress.
        self.progress_malformed: dict[str, int] = {}
        # A job whose follow should wait for its event before ending, so a
        # test can observe its running progress first. `waiting` is set once
        # a follow is held. Adding events to `jobs` before setting the event
        # replays them, including the result.
        self.waits: dict[str, asyncio.Event] = {}
        self.waiting = asyncio.Event()
        self.get_job_polls: list[str] = []
        self.polled = asyncio.Event()
        # Output lines replayed by the transcript, per followed job, so
        # get_job reports progress for captured jobs too.
        self._replayed_output: dict[str, list[str]] = {}
        self._current_follow: str | None = None

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
                if self._current_follow is not None and frame.get("event") == "output":
                    self._replayed_output.setdefault(self._current_follow, []).append(
                        str(frame.get("data", ""))
                    )
                await ws.send_json(frame)
                if frame.get("event") == "result":
                    self._current_follow = None
                continue
            msg = await ws.receive()
            # File reads and progress polls are not part of a transcript:
            # they are answered whenever they come.
            while msg.type == WSMsgType.TEXT and _is_side_command(
                sent := json.loads(msg.data), frame
            ):
                await self._answer(ws, sent)
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
            if sent.get("command") == "firmware/follow_job":
                self._current_follow = sent["args"]["job_id"]
        self.finished = True
        # Leave the socket open until the client closes it, like a real server.
        # Each command runs on its own, so a held `follow_job` does not stop
        # `get_job` polls from being answered.
        tasks: set[asyncio.Task[None]] = set()

        def done(task: asyncio.Task[None]) -> None:
            tasks.discard(task)
            if not task.cancelled():
                task.exception()

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    task = asyncio.create_task(
                        self._answer(ws, json.loads(msg.data))
                    )
                    tasks.add(task)
                    task.add_done_callback(done)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return ws

    async def _answer(self, ws: web.WebSocketResponse, sent: dict[str, Any]) -> None:
        message_id = sent["message_id"]
        if sent.get("command") == "firmware/follow_job":
            await self._follow(ws, message_id, sent["args"]["job_id"])
            return
        if sent.get("command") == "firmware/get_job":
            await self._get_job(ws, message_id, sent["args"]["job_id"])
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

    async def _get_job(self, ws: web.WebSocketResponse, message_id: str, job_id: str) -> None:
        self.get_job_polls.append(job_id)
        failures = self.progress_errors.get(job_id, 0)
        if failures:
            self.progress_errors[job_id] = failures - 1
            await ws.send_json(
                {
                    "message_id": message_id,
                    "error_code": "internal_error",
                    "details": "Command failed: firmware/get_job",
                }
            )
            self.polled.set()
            return
        script = self.progress.get(job_id)
        malformed = self.progress_malformed.get(job_id, 0)
        if malformed:
            self.progress_malformed[job_id] = malformed - 1
            progress: int | str | None = "halfway"
        elif script:
            progress = script.pop(0) if len(script) > 1 else script[0]
        else:
            progress = self._job_progress(job_id)
        await ws.send_json(
            {"message_id": message_id, "result": {"job_id": job_id, "progress": progress}}
        )
        self.polled.set()

    def _job_progress(self, job_id: str) -> int | None:
        """Progress from a scripted job's output, or from the transcript's replay."""
        if job_id in self.jobs:
            return progress_from_events(self.jobs[job_id])
        return progress_from_lines(self._replayed_output.get(job_id, []))

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
        sent = 0
        for event in events:
            await ws.send_json({"message_id": message_id, **event})
            sent += 1
        if any(event["event"] == "result" for event in events):
            await ws.send_json({"message_id": message_id, "result": None})
            return
        wait = self.waits.get(job_id)
        if wait is None:
            self.running.set()
            return
        # Hold the follow open so the test can observe the running job (and
        # poll it), then replay whatever it adds to `jobs`, including a result.
        self.waiting.set()
        await wait.wait()
        for event in self.jobs[job_id][sent:]:
            await ws.send_json({"message_id": message_id, **event})
        if any(event["event"] == "result" for event in self.jobs[job_id][sent:]):
            await ws.send_json({"message_id": message_id, "result": None})
        else:
            self.running.set()


_CLOSING = (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED)


def _is_side_command(sent: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Whether *sent* is a file read or progress poll where the transcript
    expects another command."""
    return bool(
        sent.get("command") in ("devices/get_config", "firmware/get_job")
        and sent.get("command") != expected["command"]
    )


def progress_from_events(events: list[dict[str, Any]]) -> int | None:
    """The progress a job's output lines report, as the Device Builder parses it."""
    return progress_from_lines(
        [str(event.get("data", "")) for event in events if event.get("event") == "output"]
    )


def _higher(current: int | None, value: int) -> int:
    """The higher of *current* and *value*; progress is monotonic within a job."""
    return value if current is None else max(current, value)


def progress_from_lines(lines: list[str]) -> int | None:
    """Parse ninja `[N/M]` and `Uploading: … NN%` lines, the way the add-on does."""
    progress: int | None = None
    for line in lines:
        match = _NINJA.search(line)
        if match is not None:
            done, total = int(match.group(1)), int(match.group(2))
            if total:
                progress = _higher(progress, done * 100 // total)
            continue
        match = _UPLOAD_PERCENT.search(line)
        if match is not None:
            progress = _higher(progress, int(match.group(1)))
    return progress


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
