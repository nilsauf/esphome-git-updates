"""Device Builder client, tested against a fake `/ws` server that replays transcripts."""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

import device_builder
from fake_device_builder import (
    FakeDeviceBuilder,
    Transcript,
    job_events,
    load_fixture,
    serve,
    server_info,
    with_clean_jobs,
)


async def test_connect_reads_server_info() -> None:
    async with (
        serve([server_info("1.26.0", "2026.10.1")]) as (url, _),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        assert client.server_version == "1.26.0"
        assert client.esphome_version == "2026.10.1"


@pytest.mark.parametrize(
    "version", ["0.1.0b118", "1.24.0", "1.25.0b1", "1.26.0-junk", "2.0.0", "garbage"]
)
async def test_connect_rejects_unsupported_server_version(version: str) -> None:
    async with serve([server_info(version)]) as (url, _), aiohttp.ClientSession() as session:
        with pytest.raises(device_builder.UnsupportedServerVersion) as err:
            async with device_builder.connect(session, url):
                pass
    assert err.value.server_version == version


@pytest.mark.parametrize(
    "version", ["1.25.0", "1.25.1b1", "1.26.0", "1.26.1b5", "1.26.1rc2", "1.40.2"]
)
async def test_connect_accepts_supported_server_version(version: str) -> None:
    async with (
        serve([server_info(version)]) as (url, _),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        assert client.server_version == version


async def test_connect_rejects_a_handshake_that_is_not_server_info() -> None:
    async with (
        serve([{"dir": "in", "frame": {"hello": "world"}}]) as (url, _),
        aiohttp.ClientSession() as session,
    ):
        with pytest.raises(device_builder.UnexpectedFrame):
            async with device_builder.connect(session, url):
                pass


async def test_a_connection_dropped_before_the_handshake_is_a_lost_connection() -> None:
    async with serve([{"dir": "close"}]) as (url, _), aiohttp.ClientSession() as session:
        with pytest.raises(device_builder.ConnectionLost):
            async with device_builder.connect(session, url):
                pass


def _device(configuration: str, name: str, friendly_name: str) -> dict[str, object]:
    return {"name": name, "friendly_name": friendly_name, "configuration": configuration,
            "target_platform": "esp32", "address": f"{name}.local"}


async def test_list_configurations_returns_configured_devices() -> None:
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "devices/list", "message_id": "1", "args": {}}},
        {"dir": "in", "frame": {"message_id": "1", "result": {
            "configured": [_device("b2500.yaml", "b2500", "Marstek B2500"),
                           _device("kitchen.yaml", "kitchen", "Kitchen")],
            "importable": [{"name": "stray"}],
        }}},
    ]
    async with (
        serve(transcript) as (url, fake),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        configurations = await client.list_configurations()
    assert configurations == [
        device_builder.Configuration("b2500.yaml", "b2500", "Marstek B2500"),
        device_builder.Configuration("kitchen.yaml", "kitchen", "Kitchen"),
    ]
    assert fake.mismatches == []


async def test_get_config_returns_raw_yaml() -> None:
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "devices/get_config", "message_id": "1",
                                 "args": {"configuration": "b2500.yaml"}}},
        {"dir": "in", "frame": {"message_id": "1", "result": "esphome:\n  name: b2500\n"}},
    ]
    async with (
        serve(transcript) as (url, _),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        assert await client.get_config("b2500.yaml") == "esphome:\n  name: b2500\n"


async def test_a_command_error_raises_command_failed_with_the_server_code() -> None:
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "devices/get_config", "message_id": "1",
                                 "args": {"configuration": "nope.yaml"}}},
        {"dir": "in", "frame": {"message_id": "1", "error_code": "not_found",
                                "details": "Device 'nope.yaml' not found"}},
    ]
    async with (
        serve(transcript) as (url, _),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        with pytest.raises(device_builder.CommandFailed) as err:
            await client.get_config("nope.yaml")
    assert err.value.error_code == "not_found"
    assert err.value.details == "Device 'nope.yaml' not found"


async def test_a_frame_for_another_message_raises_unexpected_frame() -> None:
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "devices/list", "message_id": "1", "args": {}}},
        {"dir": "in", "frame": {"message_id": "99", "result": {}}},
    ]
    async with (
        serve(transcript) as (url, _),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        with pytest.raises(device_builder.UnexpectedFrame):
            await client.list_configurations()


async def test_get_job_progress_returns_the_jobs_own_progress() -> None:
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "firmware/get_job", "message_id": "1",
                                 "args": {"job_id": "c1"}}},
        {"dir": "in", "frame": {"message_id": "1", "result": {
            "job_id": "c1", "status": "running", "progress": 42}}},
    ]
    async with (
        serve(transcript) as (url, fake),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        assert await client.get_job_progress("c1") == 42
    assert fake.mismatches == []


@pytest.mark.parametrize(
    "result",
    [
        {"job_id": "c1", "progress": "halfway"},
        {"job_id": "c1", "progress": 101},
        {"job_id": "c1"},
    ],
)
async def test_a_malformed_get_job_result_raises_unexpected_frame(
    result: dict[str, object],
) -> None:
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "firmware/get_job", "message_id": "1",
                                 "args": {"job_id": "c1"}}},
        {"dir": "in", "frame": {"message_id": "1", "result": result}},
    ]
    async with (
        serve(transcript) as (url, _),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        with pytest.raises(device_builder.UnexpectedFrame):
            await client.get_job_progress("c1")


async def test_get_job_runs_while_a_follow_job_stream_is_open() -> None:
    """The frames of an open follow are routed aside while get_job answers."""
    async with serve([server_info()]) as (url, fake):
        fake.jobs = {"c1": job_events("[10/100] Compiling", status=None)}
        fake.progress = {"c1": [42]}
        async with (
            aiohttp.ClientSession() as session,
            device_builder.connect(session, url) as client,
        ):
            following = asyncio.create_task(client.follow_job("c1", device_builder.Stage.COMPILE))
            await fake.running.wait()
            assert await client.get_job_progress("c1") == 42
            following.cancel()
            with pytest.raises(asyncio.CancelledError):
                await following
    assert fake.mismatches == []


async def test_progress_is_polled_while_a_job_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_builder, "PROGRESS_POLL_INTERVAL", 0.01)
    progress: list[device_builder.JobProgress] = []
    polled = asyncio.Event()

    def on_progress(update: device_builder.JobProgress) -> None:
        progress.append(update)
        if sum(1 for update in progress if not update.finished) >= 3:
            polled.set()

    async with serve([server_info()]) as (url, fake):
        fake.jobs = {"c1": job_events("[10/100] Compiling", status=None)}
        fake.progress = {"c1": [10, 50, 99]}
        async with (
            aiohttp.ClientSession() as session,
            device_builder.connect(session, url) as client,
        ):
            following = asyncio.create_task(
                client.follow_job("c1", device_builder.Stage.COMPILE, on_progress=on_progress)
            )
            await asyncio.wait_for(polled.wait(), 1)
            following.cancel()
            with pytest.raises(asyncio.CancelledError):
                await following

    assert [update.percent for update in progress if not update.finished] == [10, 50, 99]
    assert fake.mismatches == []


@pytest.mark.parametrize("raising", ["polled", "finished"])
async def test_a_progress_callback_that_raises_never_fails_the_follow(raising: str) -> None:
    """Progress is cosmetic: a callback that raises loses only that update (ticket 14)."""
    updates: list[device_builder.JobProgress] = []

    def on_progress(update: device_builder.JobProgress) -> None:
        updates.append(update)
        if (raising == "finished") == update.finished:
            raise RuntimeError(f"the entity is gone on the {raising} update")

    async with serve([server_info()]) as (url, fake):
        fake.jobs = {"c1": job_events("[10/100] Compiling")}
        fake.progress = {"c1": [42]}
        async with (
            aiohttp.ClientSession() as session,
            device_builder.connect(session, url) as client,
        ):
            job = await client.follow_job("c1", device_builder.Stage.COMPILE, on_progress=on_progress)

    assert job.succeeded
    # Both updates were attempted, whichever one raised.
    assert [update.percent for update in updates if not update.finished] == [42]
    assert [update.percent for update in updates if update.finished] == [None]
    assert fake.mismatches == []


async def test_a_failed_job_does_not_fill_its_progress_band() -> None:
    """Only a completed job moves the bar to the end of its band (ticket 14)."""
    updates: list[device_builder.JobProgress] = []

    def on_progress(update: device_builder.JobProgress) -> None:
        updates.append(update)

    async with serve([server_info()]) as (url, fake):
        fake.jobs = {"c1": job_events("[99/100] Compiling", status="failed")}
        fake.progress = {"c1": [99]}
        async with (
            aiohttp.ClientSession() as session,
            device_builder.connect(session, url) as client,
        ):
            job = await client.follow_job("c1", device_builder.Stage.COMPILE, on_progress=on_progress)

    assert not job.succeeded
    # The failed job ends without the update that would fill its band.
    assert not [update for update in updates if update.finished]
    assert {update.percent for update in updates} == {99}
    assert fake.mismatches == []


async def test_a_malformed_result_raises_unexpected_frame() -> None:
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "devices/list", "message_id": "1", "args": {}}},
        {"dir": "in", "frame": {"message_id": "1", "result": {"devices": []}}},
    ]
    async with (
        serve(transcript) as (url, _),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        with pytest.raises(device_builder.UnexpectedFrame):
            await client.list_configurations()


async def test_a_dropped_connection_raises_connection_lost() -> None:
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "devices/list", "message_id": "1", "args": {}}},
        {"dir": "close"},
    ]
    async with (
        serve(transcript) as (url, _),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        with pytest.raises(device_builder.ConnectionLost):
            await client.list_configurations()


def _login(username: str, password: str) -> dict[str, object]:
    return {"dir": "out", "frame": {"command": "auth/login", "message_id": "1",
                                    "args": {"username": username, "password": password}}}


async def test_connect_logs_in_when_the_server_requires_auth() -> None:
    transcript = [
        server_info(requires_auth=True),
        _login("admin", "secret"),
        {"dir": "in", "frame": {"message_id": "1", "result": {"token": "t", "expires_at": 1.0}}},
        {"dir": "out", "frame": {"command": "devices/list", "message_id": "2", "args": {}}},
        {"dir": "in", "frame": {"message_id": "2", "result": {"configured": [], "importable": []}}},
    ]
    async with (
        serve(transcript) as (url, fake),
        aiohttp.ClientSession() as session,
        device_builder.connect(
                session, url, username="admin", password="secret"
        ) as client,
    ):
        assert await client.list_configurations() == []
    assert fake.mismatches == []


async def test_connect_skips_login_when_the_server_does_not_require_auth() -> None:
    transcript = [
        server_info(requires_auth=False),
        {"dir": "out", "frame": {"command": "devices/list", "message_id": "1", "args": {}}},
        {"dir": "in", "frame": {"message_id": "1", "result": {"configured": [], "importable": []}}},
    ]
    async with (
        serve(transcript) as (url, fake),
        aiohttp.ClientSession() as session,
        device_builder.connect(
                session, url, username="admin", password="secret"
        ) as client,
    ):
        await client.list_configurations()
    assert fake.mismatches == []


async def test_rejected_credentials_raise_authentication_failed() -> None:
    transcript = [
        server_info(requires_auth=True),
        _login("admin", "wrong"),
        {"dir": "in", "frame": {"message_id": "1", "error_code": "not_authenticated",
                                "details": "Invalid credentials"}},
    ]
    async with serve(transcript) as (url, _), aiohttp.ClientSession() as session:
        with pytest.raises(device_builder.AuthenticationFailed, match="Invalid credentials"):
            async with device_builder.connect(session, url, username="admin", password="wrong"):
                pass


async def test_missing_credentials_raise_authentication_failed() -> None:
    async with (
        serve([server_info(requires_auth=True)]) as (url, _),
        aiohttp.ClientSession() as session,
    ):
        with pytest.raises(device_builder.AuthenticationFailed, match="requires a username"):
            async with device_builder.connect(session, url):
                pass


async def test_a_recorder_captures_a_transcript_the_fake_server_can_replay() -> None:
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "devices/get_config", "message_id": "1",
                                 "args": {"configuration": "b2500.yaml"}}},
        {"dir": "in", "frame": {"message_id": "1", "result": "esphome:\n  name: b2500\n"}},
    ]
    recorded: list[dict[str, object]] = []
    async with (
        serve(transcript) as (url, _),
        aiohttp.ClientSession() as session,
        device_builder.connect(
                session, url, recorder=lambda d, f: recorded.append({"dir": d, "frame": f})
        ) as client,
    ):
        await client.get_config("b2500.yaml")
    assert recorded == transcript


class _Recorder:
    """An InstallObserver that keeps what it is told."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []
        self.reported: list[tuple[device_builder.ActiveJob, ...]] = []

    def output(self, stage: str, line: str) -> None:
        self.lines.append((stage, line))

    async def jobs(self, jobs: tuple[device_builder.ActiveJob, ...]) -> None:
        self.reported.append(jobs)

    def progress(self, update: device_builder.JobProgress) -> None:
        pass


async def _install(
    fixture: str | Transcript, observer: _Recorder | None = None, **kwargs: object
) -> tuple[device_builder.InstallResult, list[tuple[str, str]], FakeDeviceBuilder]:
    """Install against the fixture named *fixture*, or against a transcript."""
    observer = observer or _Recorder()
    transcript = load_fixture(fixture) if isinstance(fixture, str) else fixture
    async with (
        serve(transcript) as (url, fake),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        result = await client.install(
            kwargs.pop("configuration"),  # type: ignore[arg-type]
            observer=observer,
            **kwargs,  # type: ignore[arg-type]
        )
    assert fake.mismatches == []
    assert fake.finished
    return result, observer.lines, fake


async def test_install_on_an_offline_device_is_refused_as_deferred() -> None:
    result, _, _ = await _install("install_deferred_at_enqueue", configuration="offline-node.yaml")
    assert result.outcome is device_builder.InstallOutcome.DEFERRED
    assert result.stage == "compile"
    [job] = result.jobs
    assert (job.status, job.queued_update_armed) == ("cancelled", False)


async def test_a_deferred_compile_that_finished_before_the_cancel_is_still_disarmed() -> None:
    # A cached compile can finish before firmware/cancel arrives; the cancel
    # is then refused, and the armed queued update must still be cleared.
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "firmware/install", "message_id": "1",
                                 "args": {"configuration": "b2500.yaml", "port": "OTA"}}},
        {"dir": "in", "frame": {"message_id": "1", "result": {
            "job_id": "c1", "job_type": "compile", "status": "running", "is_deferred_install": True}}},
        {"dir": "out", "frame": {"command": "firmware/cancel", "message_id": "2",
                                 "args": {"job_id": "c1"}}},
        {"dir": "in", "frame": {"message_id": "2", "error_code": "invalid_args",
                                "details": "Cannot cancel a completed job"}},
        {"dir": "out", "frame": {"command": "firmware/clear_queued_update", "message_id": "3",
                                 "args": {"configuration": "b2500.yaml"}}},
        {"dir": "in", "frame": {"message_id": "3", "result": None}},
        {"dir": "out", "frame": {"command": "firmware/follow_job", "message_id": "4",
                                 "args": {"job_id": "c1"}}},
        {"dir": "in", "frame": {"message_id": "4", "event": "result", "data": {
            "status": "completed", "exit_code": 0, "error": None, "queued_update_armed": True}}},
        {"dir": "in", "frame": {"message_id": "4", "result": None}},
    ]
    async with (
        serve(transcript) as (url, fake),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        result = await client.install("b2500.yaml")
    assert fake.finished and fake.mismatches == []
    assert result.outcome is device_builder.InstallOutcome.DEFERRED


# --- Replays of transcripts captured from a real Device Builder 1.26.0 -------


async def test_captured_list() -> None:
    async with (
        serve(load_fixture("list")) as (url, fake),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        configurations = await client.list_configurations()
    assert [c.configuration for c in configurations] == [
        "broken-node.yaml", "host-node.yaml", "offline-node.yaml",
    ]
    assert fake.finished


async def test_captured_login_with_a_password() -> None:
    async with (
        serve(load_fixture("auth_login_list")) as (url, fake),
        aiohttp.ClientSession() as session,
        device_builder.connect(
                session, url, username="admin", password="spike-password"
        ) as client,
    ):
        assert [c.name for c in await client.list_configurations()] == ["host-node"]
    assert fake.mismatches == []


async def test_captured_login_with_a_wrong_password() -> None:
    async with (
        serve(load_fixture("auth_login_rejected")) as (url, _),
        aiohttp.ClientSession() as session,
    ):
        with pytest.raises(device_builder.AuthenticationFailed, match="Invalid credentials"):
            async with device_builder.connect(session, url, username="admin", password="x"):
                pass


async def test_captured_successful_clean_install() -> None:
    result, output, _ = await _install(
        with_clean_jobs(load_fixture("install_clean_success")), configuration="host-node.yaml", port="127.0.0.1", clean_build=True
    )
    assert result.outcome is device_builder.InstallOutcome.COMPLETED
    assert result.error is None
    assert [(job.stage, job.status, job.exit_code) for job in result.jobs] == [
        ("clean", "completed", 0), ("compile", "completed", 0), ("upload", "completed", 0),
    ]
    assert {stage for stage, _ in output} == {"clean", "compile", "upload"}
    assert ("upload", "\\033[32mINFO OTA successful\\033[0m") in output


async def test_captured_failed_compile() -> None:
    result, output, _ = await _install("install_compile_failed", configuration="broken-node.yaml")
    assert result.outcome is device_builder.InstallOutcome.FAILED
    assert result.stage == "compile"
    assert result.error == "compile failed (exit code 1)"
    assert output[-1] == ("compile", "\\033[31mERROR Host build failed\\033[0m")


async def test_captured_failed_upload() -> None:
    result, _, _ = await _install(
        "install_upload_failed", configuration="host-node.yaml", port="127.0.0.1"
    )
    assert result.outcome is device_builder.InstallOutcome.FAILED
    assert result.stage == "upload"
    assert [job.status for job in result.jobs] == ["completed", "failed"]


async def test_captured_follow_job_reattaches_to_a_running_job() -> None:
    lines: list[str] = []
    async with (
        serve(load_fixture("follow_job_reattach")) as (url, fake),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        job = await client.follow_job(
            "ed1483200901", device_builder.Stage.COMPILE, lambda stage, line: lines.append(line)
        )
    assert fake.mismatches == []
    assert job.succeeded
    assert len(lines) == 56


async def test_a_compile_that_arms_a_queued_update_is_refused_as_deferred() -> None:
    # The device went offline during the build: the compile completes with
    # queued_update_armed (frame shape from deferred_compile_armed_then_cleared)
    # and the Device Builder cancels the held upload.
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "firmware/install", "message_id": "1",
                                 "args": {"configuration": "b2500.yaml", "port": "OTA"}}},
        {"dir": "in", "frame": {"message_id": "1", "result": {
            "job_id": "c1", "job_type": "compile", "status": "queued", "is_deferred_install": False}}},
        {"dir": "out", "frame": {"command": "firmware/get_jobs", "message_id": "2",
                                 "args": {"configuration": "b2500.yaml"}}},
        {"dir": "in", "frame": {"message_id": "2", "result": [
            {"job_id": "c1", "job_type": "compile", "depends_on": ""},
            {"job_id": "u1", "job_type": "upload", "depends_on": "c1"}]}},
        {"dir": "out", "frame": {"command": "firmware/follow_job", "message_id": "3",
                                 "args": {"job_id": "c1"}}},
        {"dir": "in", "frame": {"message_id": "3", "event": "result", "data": {
            "status": "completed", "exit_code": 0, "error": None, "queued_update_armed": True}}},
        {"dir": "in", "frame": {"message_id": "3", "result": None}},
        {"dir": "out", "frame": {"command": "firmware/clear_queued_update", "message_id": "4",
                                 "args": {"configuration": "b2500.yaml"}}},
        {"dir": "in", "frame": {"message_id": "4", "result": None}},
    ]
    async with (
        serve(transcript) as (url, fake),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        result = await client.install("b2500.yaml")
    assert fake.finished and fake.mismatches == []
    assert result.outcome is device_builder.InstallOutcome.DEFERRED
    assert result.error == "device offline"


# --- Replays of transcripts captured from the real HA add-on (Device Builder 1.25.0) ---


async def test_addon_successful_clean_install() -> None:
    # The add-on is paired with two remote build servers, so `firmware/clean`
    # queued a clean job for each of them besides the local one.
    remote = {"84dcb8482426": "completed", "bef9160385f1": "completed"}
    result, output, _ = await _install(
        with_clean_jobs(load_fixture("addon_success"), remote),
        configuration="b2500.yaml", clean_build=True,
    )
    assert result.outcome is device_builder.InstallOutcome.COMPLETED
    assert [(job.job_id, job.stage, job.status) for job in result.jobs] == [
        ("e9cfd4b2b27c", "clean", "completed"),
        ("84dcb8482426", "clean", "completed"),
        ("bef9160385f1", "clean", "completed"),
        ("3f1da4fb8667", "compile", "completed"),
        ("d57f9bbbbc5e", "upload", "completed"),
    ]
    assert {stage for stage, _ in output} == {"clean", "compile", "upload"}


async def test_a_remote_clean_that_fails_fails_the_install_before_it_compiles() -> None:
    remote = {"84dcb8482426": "completed", "bef9160385f1": "failed"}
    result, _, _ = await _install(
        with_clean_jobs(load_fixture("addon_success"), remote),
        configuration="b2500.yaml", clean_build=True,
    )
    assert result.outcome is device_builder.InstallOutcome.FAILED
    assert result.stage == "clean"
    assert result.jobs[-1].job_id == "bef9160385f1"


async def test_a_remote_clean_that_failed_before_the_jobs_were_listed_still_fails_the_install() -> None:
    remote = {"84dcb8482426": "completed", "bef9160385f1": "failed"}
    result, _, _ = await _install(
        with_clean_jobs(load_fixture("addon_success"), remote, ended=("bef9160385f1",)),
        configuration="b2500.yaml", clean_build=True,
    )
    assert result.outcome is device_builder.InstallOutcome.FAILED
    assert result.stage == "clean"
    assert result.jobs[-1].job_id == "bef9160385f1"


async def test_addon_install_on_an_offline_device_is_refused_as_deferred() -> None:
    result, _, _ = await _install("addon_deferred", configuration="offline.yaml")
    assert result.outcome is device_builder.InstallOutcome.DEFERRED
    [job] = result.jobs
    assert (job.stage, job.status, job.queued_update_armed) == ("compile", "cancelled", False)


# --- Re-attaching to an Install after a restart -------------------------------


def _follow(message_id: str, job_id: str, status: str, *, armed: bool = False) -> list[dict[str, object]]:
    """Frames of one `firmware/follow_job` that ends with *status*."""
    return [
        {"dir": "out", "frame": {"command": "firmware/follow_job", "message_id": message_id,
                                 "args": {"job_id": job_id}}},
        {"dir": "in", "frame": {"message_id": message_id, "event": "result", "data": {
            "status": status, "exit_code": 0 if status == "completed" else 1, "error": None,
            "queued_update_armed": armed}}},
        {"dir": "in", "frame": {"message_id": message_id, "result": None}},
    ]


async def test_following_a_job_the_device_builder_no_longer_knows_raises_job_not_found() -> None:
    # What the Device Builder answers for an unknown or pruned job id.
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "firmware/follow_job", "message_id": "1",
                                 "args": {"job_id": "gone"}}},
        {"dir": "in", "frame": {"message_id": "1", "error_code": "internal_error",
                                "details": "Command failed: firmware/follow_job"}},
    ]
    async with (
        serve(transcript) as (url, fake),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        with pytest.raises(device_builder.JobNotFound) as raised:
            await client.follow_job("gone", device_builder.Stage.COMPILE)
    assert fake.mismatches == []
    assert raised.value.job_id == "gone"


async def test_install_reports_the_jobs_still_to_follow_before_following_each() -> None:
    observer = _Recorder()

    await _install(
        with_clean_jobs(load_fixture("install_clean_success")), observer,
        configuration="host-node.yaml", port="127.0.0.1", clean_build=True,
    )
    ActiveJob = device_builder.ActiveJob
    assert observer.reported == [
        (ActiveJob("98d512a9c2fd", device_builder.Stage.CLEAN),),
        (ActiveJob("368f07c5f6ff", device_builder.Stage.COMPILE), ActiveJob("58fd489cd24d", device_builder.Stage.UPLOAD)),
        (ActiveJob("58fd489cd24d", device_builder.Stage.UPLOAD),),
    ]


async def _resume(
    transcript: list[dict[str, object]], jobs: tuple[device_builder.ActiveJob, ...]
) -> tuple[device_builder.InstallResult, list[tuple[device_builder.ActiveJob, ...]]]:
    observer = _Recorder()
    async with (
        serve([server_info(), *transcript]) as (url, fake),
        aiohttp.ClientSession() as session,
        device_builder.connect(session, url) as client,
    ):
        result = await client.resume_install("b2500.yaml", jobs, observer=observer)
    assert fake.finished and fake.mismatches == []
    return result, observer.reported


async def test_resuming_an_install_follows_its_jobs_to_the_end() -> None:
    ActiveJob = device_builder.ActiveJob
    result, reported = await _resume(
        [*_follow("1", "c1", "completed"), *_follow("2", "u1", "completed")],
        (ActiveJob("c1", device_builder.Stage.COMPILE), ActiveJob("u1", device_builder.Stage.UPLOAD)),
    )
    assert result.outcome is device_builder.InstallOutcome.COMPLETED
    assert [(job.job_id, job.stage) for job in result.jobs] == [("c1", "compile"), ("u1", "upload")]
    assert reported == [
        (ActiveJob("c1", device_builder.Stage.COMPILE), ActiveJob("u1", device_builder.Stage.UPLOAD)), (ActiveJob("u1", device_builder.Stage.UPLOAD),),
    ]


async def test_resuming_an_install_that_had_only_got_to_its_clean_build_goes_on_to_compile() -> None:
    ActiveJob = device_builder.ActiveJob
    result, reported = await _resume(
        [
            *_follow("1", "k1", "completed"),
            {"dir": "out", "frame": {"command": "firmware/install", "message_id": "2",
                                     "args": {"configuration": "b2500.yaml", "port": "OTA"}}},
            {"dir": "in", "frame": {"message_id": "2", "result": {
                "job_id": "c1", "job_type": "compile", "is_deferred_install": False}}},
            {"dir": "out", "frame": {"command": "firmware/get_jobs", "message_id": "3",
                                     "args": {"configuration": "b2500.yaml"}}},
            {"dir": "in", "frame": {"message_id": "3", "result": [
                {"job_id": "u1", "job_type": "upload", "depends_on": "c1"}]}},
            *_follow("4", "c1", "completed"),
            *_follow("5", "u1", "completed"),
        ],
        (ActiveJob("k1", device_builder.Stage.CLEAN),),
    )
    assert result.outcome is device_builder.InstallOutcome.COMPLETED
    assert [job.stage for job in result.jobs] == ["clean", "compile", "upload"]
    assert reported == [
        (ActiveJob("k1", device_builder.Stage.CLEAN),),
        (ActiveJob("c1", device_builder.Stage.COMPILE), ActiveJob("u1", device_builder.Stage.UPLOAD)),
        (ActiveJob("u1", device_builder.Stage.UPLOAD),),
    ]


async def test_a_resumed_compile_that_armed_a_queued_update_is_refused_as_deferred() -> None:
    ActiveJob = device_builder.ActiveJob
    result, _ = await _resume(
        [
            *_follow("1", "c1", "completed", armed=True),
            {"dir": "out", "frame": {"command": "firmware/clear_queued_update", "message_id": "2",
                                     "args": {"configuration": "b2500.yaml"}}},
            {"dir": "in", "frame": {"message_id": "2", "result": None}},
        ],
        (ActiveJob("c1", device_builder.Stage.COMPILE), ActiveJob("u1", device_builder.Stage.UPLOAD)),
    )
    assert result.outcome is device_builder.InstallOutcome.DEFERRED
