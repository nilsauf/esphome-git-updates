"""Device Builder client, tested against a fake `/ws` server that replays transcripts."""

from __future__ import annotations

import aiohttp
import pytest

import device_builder
from fake_device_builder import FakeDeviceBuilder, load_fixture, server_info, serve


async def test_connect_reads_server_info() -> None:
    async with serve([server_info("1.26.0", "2026.10.1")]) as (url, _):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
                assert client.server_version == "1.26.0"
                assert client.esphome_version == "2026.10.1"


@pytest.mark.parametrize(
    "version", ["0.1.0b118", "1.24.0", "1.25.0b1", "1.26.0-junk", "2.0.0", "garbage"]
)
async def test_connect_rejects_unsupported_server_version(version: str) -> None:
    async with serve([server_info(version)]) as (url, _):
        async with aiohttp.ClientSession() as session:
            with pytest.raises(device_builder.UnsupportedServerVersion) as err:
                async with device_builder.connect(session, url):
                    pass
    assert err.value.server_version == version


@pytest.mark.parametrize(
    "version", ["1.25.0", "1.25.1b1", "1.26.0", "1.26.1b5", "1.26.1rc2", "1.40.2"]
)
async def test_connect_accepts_supported_server_version(version: str) -> None:
    async with serve([server_info(version)]) as (url, _):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
                assert client.server_version == version


async def test_connect_rejects_a_handshake_that_is_not_server_info() -> None:
    async with serve([{"dir": "in", "frame": {"hello": "world"}}]) as (url, _):
        async with aiohttp.ClientSession() as session:
            with pytest.raises(device_builder.UnexpectedFrame):
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
    async with serve(transcript) as (url, fake):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
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
    async with serve(transcript) as (url, _):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
                assert await client.get_config("b2500.yaml") == "esphome:\n  name: b2500\n"


async def test_a_command_error_raises_command_failed_with_the_server_code() -> None:
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "devices/get_config", "message_id": "1",
                                 "args": {"configuration": "nope.yaml"}}},
        {"dir": "in", "frame": {"message_id": "1", "error_code": "not_found",
                                "details": "Device 'nope.yaml' not found"}},
    ]
    async with serve(transcript) as (url, _):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
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
    async with serve(transcript) as (url, _):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
                with pytest.raises(device_builder.UnexpectedFrame):
                    await client.list_configurations()


async def test_a_malformed_result_raises_unexpected_frame() -> None:
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "devices/list", "message_id": "1", "args": {}}},
        {"dir": "in", "frame": {"message_id": "1", "result": {"devices": []}}},
    ]
    async with serve(transcript) as (url, _):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
                with pytest.raises(device_builder.UnexpectedFrame):
                    await client.list_configurations()


async def test_a_dropped_connection_raises_connection_lost() -> None:
    transcript = [
        server_info(),
        {"dir": "out", "frame": {"command": "devices/list", "message_id": "1", "args": {}}},
        {"dir": "close"},
    ]
    async with serve(transcript) as (url, _):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
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
    async with serve(transcript) as (url, fake):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(
                session, url, username="admin", password="secret"
            ) as client:
                assert await client.list_configurations() == []
    assert fake.mismatches == []


async def test_connect_skips_login_when_the_server_does_not_require_auth() -> None:
    transcript = [
        server_info(requires_auth=False),
        {"dir": "out", "frame": {"command": "devices/list", "message_id": "1", "args": {}}},
        {"dir": "in", "frame": {"message_id": "1", "result": {"configured": [], "importable": []}}},
    ]
    async with serve(transcript) as (url, fake):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(
                session, url, username="admin", password="secret"
            ) as client:
                await client.list_configurations()
    assert fake.mismatches == []


async def test_rejected_credentials_raise_authentication_failed() -> None:
    transcript = [
        server_info(requires_auth=True),
        _login("admin", "wrong"),
        {"dir": "in", "frame": {"message_id": "1", "error_code": "not_authenticated",
                                "details": "Invalid credentials"}},
    ]
    async with serve(transcript) as (url, _):
        async with aiohttp.ClientSession() as session:
            with pytest.raises(device_builder.AuthenticationFailed, match="Invalid credentials"):
                async with device_builder.connect(session, url, username="admin", password="wrong"):
                    pass


async def test_missing_credentials_raise_authentication_failed() -> None:
    async with serve([server_info(requires_auth=True)]) as (url, _):
        async with aiohttp.ClientSession() as session:
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
    async with serve(transcript) as (url, _):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(
                session, url, recorder=lambda d, f: recorded.append({"dir": d, "frame": f})
            ) as client:
                await client.get_config("b2500.yaml")
    assert recorded == transcript


async def _install(name: str, **kwargs: object) -> tuple[device_builder.InstallResult, list[tuple[str, str]], FakeDeviceBuilder]:
    output: list[tuple[str, str]] = []
    async with serve(load_fixture(name)) as (url, fake):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
                result = await client.install(
                    kwargs.pop("configuration"),  # type: ignore[arg-type]
                    on_output=lambda stage, line: output.append((stage, line)),
                    **kwargs,  # type: ignore[arg-type]
                )
    assert fake.mismatches == []
    assert fake.finished
    return result, output, fake


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
    async with serve(transcript) as (url, fake):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
                result = await client.install("b2500.yaml")
    assert fake.finished and fake.mismatches == []
    assert result.outcome is device_builder.InstallOutcome.DEFERRED


# --- Replays of transcripts captured from a real Device Builder 1.26.0 -------


async def test_captured_list() -> None:
    async with serve(load_fixture("list")) as (url, fake):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
                configurations = await client.list_configurations()
    assert [c.configuration for c in configurations] == [
        "broken-node.yaml", "host-node.yaml", "offline-node.yaml",
    ]
    assert fake.finished


async def test_captured_login_with_a_password() -> None:
    async with serve(load_fixture("auth_login_list")) as (url, fake):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(
                session, url, username="admin", password="spike-password"
            ) as client:
                assert [c.name for c in await client.list_configurations()] == ["host-node"]
    assert fake.mismatches == []


async def test_captured_login_with_a_wrong_password() -> None:
    async with serve(load_fixture("auth_login_rejected")) as (url, _):
        async with aiohttp.ClientSession() as session:
            with pytest.raises(device_builder.AuthenticationFailed, match="Invalid credentials"):
                async with device_builder.connect(session, url, username="admin", password="x"):
                    pass


async def test_captured_successful_clean_install() -> None:
    result, output, _ = await _install(
        "install_clean_success", configuration="host-node.yaml", port="127.0.0.1", clean_build=True
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
    async with serve(load_fixture("follow_job_reattach")) as (url, fake):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
                job = await client.follow_job(
                    "ed1483200901", "compile", lambda stage, line: lines.append(line)
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
    async with serve(transcript) as (url, fake):
        async with aiohttp.ClientSession() as session:
            async with device_builder.connect(session, url) as client:
                result = await client.install("b2500.yaml")
    assert fake.finished and fake.mismatches == []
    assert result.outcome is device_builder.InstallOutcome.DEFERRED
    assert result.error == "device offline"
