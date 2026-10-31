"""Client for the ESPHome Device Builder's multiplexed `/ws` API.

This module holds all knowledge of the `/ws` protocol (ADR-0002). It has no
Home Assistant imports so the CLI spike and the integration share it.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import aiohttp

# `/ws` is unstable upstream. The protocol below was read from Device Builder
# 1.26.0's source and checked against a running 1.26.0. 1.25.0's `/ws` code
# is identical to 1.26.0's (only the MQTT monitor differs), so it is the
# floor. Lower the floor or raise the ceiling only after re-checking against
# that version.
MIN_SERVER_VERSION = (1, 25, 0)
MAX_SERVER_MAJOR = 1

# Called with (stage, line) for every line of job output.
OutputCallback = Callable[[str, str], None]

# Called with ("out", frame) for every frame sent and ("in", frame) for every
# frame received; the CLI's --record uses it to capture test fixtures.
Recorder = Callable[[str, Any], None]

# Message types that mean the websocket is gone.
_CLOSED_TYPES = frozenset(
    {
        aiohttp.WSMsgType.CLOSE,
        aiohttp.WSMsgType.CLOSING,
        aiohttp.WSMsgType.CLOSED,
        aiohttp.WSMsgType.ERROR,
    }
)

# PEP 440-style versions as published on PyPI: 1.26.0, 1.26.1b5, 1.26.1rc2.
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)((?:a|b|rc)\d+)?")


class DeviceBuilderError(Exception):
    """Base class for every error this client raises."""


class UnexpectedFrame(DeviceBuilderError):
    """The server sent a frame this client does not understand."""

    def __init__(self, reason: str, frame: object = None) -> None:
        super().__init__(f"unexpected frame from Device Builder: {reason}: {frame!r}")
        self.frame = frame


class UnsupportedServerVersion(DeviceBuilderError):
    """The Device Builder's `server_version` is outside the range this client knows."""

    def __init__(self, server_version: str) -> None:
        low = ".".join(map(str, MIN_SERVER_VERSION))
        super().__init__(
            f"Device Builder {server_version} is not supported "
            f"(need >= {low}, < {MAX_SERVER_MAJOR + 1}.0.0)"
        )
        self.server_version = server_version


class CommandFailed(DeviceBuilderError):
    """The Device Builder answered a command with an `error_code` frame."""

    def __init__(self, command: str, error_code: str, details: str) -> None:
        super().__init__(f"{command} failed: {error_code}: {details}")
        self.command = command
        self.error_code = error_code
        self.details = details


class AuthenticationFailed(DeviceBuilderError):
    """The Device Builder requires a login and it failed or was not possible."""


class ConnectionLost(DeviceBuilderError):
    """The websocket closed while a command was waiting for its answer."""


@dataclass(frozen=True)
class Configuration:
    """One YAML configuration the Device Builder manages."""

    configuration: str
    name: str
    friendly_name: str


class InstallOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    # The device was offline, so the Device Builder queued the flash for when
    # it comes back. The client refuses that: it cancels the job and clears
    # the queued update before returning this outcome.
    DEFERRED = "deferred"


@dataclass(frozen=True)
class JobResult:
    """The terminal state of one Device Builder firmware job."""

    job_id: str
    stage: str
    status: str
    exit_code: int | None
    error: str | None
    queued_update_armed: bool

    @property
    def succeeded(self) -> bool:
        return self.status == "completed" and self.exit_code in (0, None)


@dataclass(frozen=True)
class InstallResult:
    outcome: InstallOutcome
    # The stage that decided the outcome: "clean", "compile" or "upload".
    stage: str
    jobs: tuple[JobResult, ...]

    @property
    def error(self) -> str | None:
        """A one-line reason for a failed or deferred Install."""
        if self.outcome is InstallOutcome.COMPLETED:
            return None
        if self.outcome is InstallOutcome.DEFERRED:
            return "device offline"
        last = self.jobs[-1]
        return last.error or f"{last.stage} {last.status} (exit code {last.exit_code})"


def is_supported_server_version(server_version: str) -> bool:
    match = _VERSION.fullmatch(server_version)
    if match is None:
        return False
    major, minor, patch, pre_release = match.groups()
    release = (int(major), int(minor), int(patch))
    # A pre-release comes before its release: 1.25.0b1 < 1.25.0.
    if pre_release and release == MIN_SERVER_VERSION:
        return False
    return MIN_SERVER_VERSION <= release and release[0] <= MAX_SERVER_MAJOR


class DeviceBuilderClient:
    """A connected Device Builder. Create it with `connect`.

    Commands run one at a time: the client sends a command, then reads frames
    until that command's `result` or `error_code` frame. A frame for any
    other message id is a protocol surprise and raises `UnexpectedFrame`.
    """

    def __init__(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        info: dict[str, Any],
        recorder: Recorder | None = None,
    ) -> None:
        self._ws = ws
        self._record = recorder or (lambda direction, frame: None)
        self._lock = asyncio.Lock()
        self._next_id = 0
        self.server_version: str = info["server_version"]
        # The Available ESPHome Version: what the Device Builder compiles with.
        self.esphome_version: str = info["esphome_version"]

    async def _login(self, username: str, password: str) -> None:
        try:
            await self._command("auth/login", username=username, password=password)
        except CommandFailed as err:
            raise AuthenticationFailed(f"login refused: {err.details}") from err

    async def list_configurations(self) -> list[Configuration]:
        result = await self._command("devices/list")
        try:
            return [
                Configuration(d["configuration"], d["name"], d["friendly_name"])
                for d in result["configured"]
            ]
        except (KeyError, TypeError) as err:
            raise UnexpectedFrame("malformed devices/list result", result) from err

    async def get_config(self, configuration: str) -> str:
        """Return the configuration's raw YAML, exactly as stored."""
        result = await self._command("devices/get_config", configuration=configuration)
        if not isinstance(result, str):
            raise UnexpectedFrame("malformed devices/get_config result", result)
        return result

    async def install(
        self,
        configuration: str,
        *,
        port: str = "OTA",
        clean_build: bool = False,
        on_output: OutputCallback | None = None,
    ) -> InstallResult:
        """Run one Install: an optional Clean Build, then compile, then upload.

        `firmware/install` queues a compile job plus an upload job that
        depends on it, and returns only the compile job. The upload job is
        found through `firmware/get_jobs`. Both are followed to the end.

        A Deferred Install is refused, whichever way the Device Builder
        defers: the compile-only job it queues for an offline device is
        cancelled, and a queued update armed by a job that finished while
        the device was offline is cleared.
        """
        output = on_output or (lambda stage, line: None)
        jobs: list[JobResult] = []

        def done(outcome: InstallOutcome) -> InstallResult:
            return InstallResult(outcome, jobs[-1].stage, tuple(jobs))

        if clean_build:
            clean = await self._command("firmware/clean", configuration=configuration)
            jobs.append(await self.follow_job(_job_id(clean), "clean", output))
            if not jobs[-1].succeeded:
                return done(InstallOutcome.FAILED)

        compile_job = await self._command(
            "firmware/install", configuration=configuration, port=port
        )
        compile_id = _job_id(compile_job)
        if compile_job.get("is_deferred_install"):
            await self._refuse_deferred_compile(configuration, compile_id)
            jobs.append(await self.follow_job(compile_id, "compile", output))
            return done(InstallOutcome.DEFERRED)
        upload_id = await self._dependent_upload(configuration, compile_id)

        for job_id, stage in ((compile_id, "compile"), (upload_id, "upload")):
            jobs.append(await self.follow_job(job_id, stage, output))
            if jobs[-1].queued_update_armed:
                await self._command("firmware/clear_queued_update", configuration=configuration)
                return done(InstallOutcome.DEFERRED)
            if not jobs[-1].succeeded:
                return done(InstallOutcome.FAILED)
        return done(InstallOutcome.COMPLETED)

    async def follow_job(
        self, job_id: str, stage: str, on_output: OutputCallback | None = None
    ) -> JobResult:
        """Follow a firmware job to its end, replaying its output so far.

        Jobs live on the Device Builder independently of any connection, so
        this also re-attaches to a job started on an earlier connection.
        """
        output = on_output or (lambda stage, line: None)
        result: dict[str, Any] | None = None
        async with self._lock:
            message_id = await self._send("firmware/follow_job", {"job_id": job_id})
            async for frame in self._frames(message_id):
                event = frame.get("event")
                if event == "output":
                    output(stage, str(frame.get("data", "")).rstrip("\n"))
                elif event == "result":
                    result = frame.get("data")
                elif event is not None:
                    raise UnexpectedFrame("unknown follow_job event", frame)
                else:
                    self._result("firmware/follow_job", frame)
                    break
        if not isinstance(result, dict) or "status" not in result:
            raise UnexpectedFrame("follow_job ended without a result event", result)
        return JobResult(
            job_id=job_id,
            stage=stage,
            status=str(result["status"]),
            exit_code=result.get("exit_code"),
            error=result.get("error"),
            queued_update_armed=bool(result.get("queued_update_armed")),
        )

    async def _refuse_deferred_compile(self, configuration: str, compile_id: str) -> None:
        """Stop a compile queued for an offline device from ever flashing it.

        Cancelling stops it from arming a queued update when it completes. A
        cached compile may already have completed (and armed) by then, which
        the cancel refuses with `invalid_args`; clearing disarms that case.
        """
        try:
            await self._command("firmware/cancel", job_id=compile_id)
        except CommandFailed as err:
            if err.error_code != "invalid_args":
                raise
        await self._command("firmware/clear_queued_update", configuration=configuration)

    async def _dependent_upload(self, configuration: str, compile_id: str) -> str:
        jobs = await self._command("firmware/get_jobs", configuration=configuration)
        if isinstance(jobs, list):
            for job in jobs:
                if (
                    isinstance(job, dict)
                    and job.get("depends_on") == compile_id
                    and job.get("job_type") == "upload"
                ):
                    return _job_id(job)
        raise UnexpectedFrame(f"no upload job depends on compile job {compile_id}", jobs)

    async def _command(self, command: str, **args: Any) -> Any:
        """Run a command and return its `result`."""
        async with self._lock:
            message_id = await self._send(command, args)
            async for frame in self._frames(message_id):
                return self._result(command, frame)
        raise AssertionError("unreachable")

    async def _send(self, command: str, args: dict[str, Any]) -> str:
        self._next_id += 1
        message_id = str(self._next_id)
        frame = {"command": command, "message_id": message_id, "args": args}
        self._record("out", frame)
        try:
            await self._ws.send_json(frame)
        except (ConnectionError, RuntimeError) as err:
            raise ConnectionLost(f"websocket closed while sending {command}") from err
        return message_id

    async def _frames(self, message_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield every frame received for *message_id*; raise on anything else."""
        while True:
            msg = await self._ws.receive()
            if msg.type in _CLOSED_TYPES:
                raise ConnectionLost(f"websocket closed ({msg.type.name})")
            if msg.type != aiohttp.WSMsgType.TEXT:
                raise UnexpectedFrame(f"{msg.type.name} message", msg.data)
            try:
                frame = msg.json()
            except ValueError as err:
                raise UnexpectedFrame("invalid JSON", msg.data) from err
            self._record("in", frame)
            if not isinstance(frame, dict) or frame.get("message_id") != message_id:
                raise UnexpectedFrame(f"expected a frame for message {message_id}", frame)
            yield frame

    @staticmethod
    def _result(command: str, frame: dict[str, Any]) -> Any:
        if "error_code" in frame:
            raise CommandFailed(command, str(frame["error_code"]), str(frame.get("details", "")))
        if "result" not in frame:
            raise UnexpectedFrame(f"expected the result of {command}", frame)
        return frame["result"]


def _job_id(job: object) -> str:
    job_id = job.get("job_id") if isinstance(job, dict) else None
    if not isinstance(job_id, str):
        raise UnexpectedFrame("expected a firmware job", job)
    return job_id


def _check_server_info(frame: object) -> dict[str, Any]:
    if not isinstance(frame, dict) or not all(
        isinstance(frame.get(key), str) for key in ("server_version", "esphome_version")
    ):
        raise UnexpectedFrame("expected server info on connect", frame)
    if not is_supported_server_version(frame["server_version"]):
        raise UnsupportedServerVersion(frame["server_version"])
    return frame


@asynccontextmanager
async def connect(
    session: aiohttp.ClientSession,
    url: str,
    *,
    username: str | None = None,
    password: str | None = None,
    recorder: Recorder | None = None,
) -> AsyncIterator[DeviceBuilderClient]:
    """Connect to the Device Builder at *url* (its base URL, without `/ws`).

    Two ways in (ADR-0002):
    - the add-on's ingress port on 127.0.0.1, which needs no auth;
    - a manual URL, with *username* and *password* sent through `auth/login`
      when the server's handshake says `requires_auth`.

    No `Origin` header is sent: the Device Builder rejects untrusted browser
    origins, and non-browser clients authenticate in-band instead.
    """
    async with session.ws_connect(url.rstrip("/") + "/ws", heartbeat=30) as ws:
        msg = await ws.receive()
        if msg.type in _CLOSED_TYPES:
            raise ConnectionLost(f"websocket closed before the handshake ({msg.type.name})")
        if msg.type != aiohttp.WSMsgType.TEXT:
            raise UnexpectedFrame("expected server info on connect", msg.data)
        try:
            handshake = msg.json()
        except ValueError as err:
            raise UnexpectedFrame("expected server info on connect", msg.data) from err
        if recorder is not None:
            recorder("in", handshake)
        info = _check_server_info(handshake)
        client = DeviceBuilderClient(ws, info, recorder)
        if info.get("requires_auth"):
            if not username or not password:
                raise AuthenticationFailed(
                    "this Device Builder requires a username and password"
                )
            await client._login(username, password)
        yield client
