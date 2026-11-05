"""An Install run, driven against a fake Device Builder without Home Assistant."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

import aiohttp

from custom_components.esphome_git_updates import device_builder
from custom_components.esphome_git_updates.device_builder import DeviceBuilderUnavailable
from custom_components.esphome_git_updates.install import (
    ActiveInstall,
    DeviceOffline,
    Failed,
    InstallEnding,
    Installed,
    InstallPlan,
    InstallRunner,
    OutcomeUnknown,
)
from fake_device_builder import (
    FakeDeviceBuilder,
    Transcript,
    install_transcript,
    job_events,
    queue_install,
    serve,
    server_info,
)

CONFIGURATION = "host-node.yaml"
COMMITS = {"marsrelay": "c0ffee"}
PLAN = InstallPlan(COMMITS, port="OTA", clean_build=False)


@dataclass
class FakeBookkeeping:
    """The store's Install bookkeeping, in memory."""

    active: ActiveInstall | None = None
    # Every ActiveInstall set, in order.
    activity: list[ActiveInstall] = field(default_factory=list)
    recorded: tuple[str, Mapping[str, str]] | None = None

    async def async_set_active_install(self, active: ActiveInstall) -> None:
        self.active = active
        self.activity.append(active)

    async def async_clear_active_install(self) -> None:
        self.active = None

    async def async_record_install(self, esphome_version: str, commits: Mapping[str, str]) -> None:
        self.recorded = (esphome_version, dict(commits))
        self.active = None


@dataclass
class FakeSession:
    """The entry's Device Builder session as the Install sees it.

    It connects to the fake Device Builder, and turns what the real session
    handles into DeviceBuilderUnavailable; `CommandFailed` and `JobNotFound`
    pass through. The reauth gate and the Repair issue are the real session's,
    tested in test_connection.py.
    """

    url: str | None
    credentials: tuple[str, str] | None = None
    # (name, long_running) of every connection.
    connections: list[tuple[str, bool]] = field(default_factory=list)
    # The names whose block exited without an exception.
    clean_exits: list[str] = field(default_factory=list)

    @asynccontextmanager
    async def connect(
        self, name: str, *, long_running: bool = False
    ) -> AsyncIterator[device_builder.DeviceBuilderClient]:
        self.connections.append((name, long_running))
        if self.url is None:
            raise DeviceBuilderUnavailable("cannot reach the Device Builder: connection refused")
        username, password = self.credentials or (None, None)
        try:
            async with (
                aiohttp.ClientSession() as http,
                device_builder.connect(
                    http, self.url, username=username, password=password
                ) as client,
            ):
                yield client
        except (device_builder.CommandFailed, device_builder.JobNotFound):
            raise
        except (device_builder.DeviceBuilderError, aiohttp.ClientError, OSError) as err:
            raise DeviceBuilderUnavailable(str(err)) from err
        self.clean_exits.append(name)


@dataclass
class Run:
    ending: InstallEnding
    fake: FakeDeviceBuilder
    bookkeeping: FakeBookkeeping
    session: FakeSession
    percentages: list[int]


async def _run(
    transcript: Transcript,
    plan: InstallPlan = PLAN,
    *,
    jobs: dict[str, list[dict[str, Any]]] | None = None,
    reattach: ActiveInstall | None = None,
) -> Run:
    bookkeeping = FakeBookkeeping()
    percentages: list[int] = []
    async with serve(transcript) as (url, fake):
        fake.jobs = jobs or {}
        session = FakeSession(url)
        runner = InstallRunner(CONFIGURATION, session, bookkeeping)
        if reattach is None:
            ending = await runner.run(plan, percentages.append)
        else:
            ending = await runner.reattach(reattach, percentages.append)
    return Run(ending, fake, bookkeeping, session, percentages)


def _with_answer(transcript: Transcript, command: str, result: Any) -> Transcript:
    """*transcript* with the Device Builder's answer to *command* replaced by *result*."""
    at = next(
        i
        for i, step in enumerate(transcript)
        if step["dir"] == "out" and step["frame"]["command"] == command
    )
    answer = transcript[at + 1]
    return [
        *transcript[: at + 1],
        {**answer, "frame": {"message_id": answer["frame"]["message_id"], "result": result}},
        *transcript[at + 2 :],
    ]


async def test_a_device_builder_that_answers_an_install_unexpectedly_fails_it() -> None:
    transcript = _with_answer(
        install_transcript("install_clean_success"), "firmware/install", {"job": "?"}
    )

    run = await _run(transcript)

    assert isinstance(run.ending, Failed)
    assert run.ending.stage == "compile"
    assert "unexpected frame" in run.ending.error
    assert run.bookkeeping.recorded is None
    assert run.bookkeeping.active is None
    # The session saw the drift, so it stays reported.
    assert run.session.clean_exits == []


async def test_a_refused_install_command_fails_at_its_stage_and_clears_no_drift() -> None:
    transcript = install_transcript("install_clean_success")
    at = next(
        i
        for i, step in enumerate(transcript)
        if step["dir"] == "out" and step["frame"]["command"] == "firmware/install"
    )
    message_id = transcript[at + 1]["frame"]["message_id"]
    refused = {"message_id": message_id, "error_code": "busy", "details": "already compiling"}

    run = await _run([*transcript[: at + 1], {"dir": "in", "frame": refused}])

    assert isinstance(run.ending, Failed)
    assert (run.ending.stage, run.ending.error) == (
        "compile",
        "firmware/install failed: busy: already compiling",
    )
    assert run.bookkeeping.active is None
    assert run.session.clean_exits == []


async def test_a_successful_install_records_the_version_and_the_commits_seen_when_it_started() -> None:
    run = await _run(install_transcript("install_clean_success"))

    assert run.fake.finished and run.fake.mismatches == []
    assert run.ending == Installed(warnings=())
    assert run.ending.notification is None
    assert run.bookkeeping.recorded == ("2026.10.1", COMMITS)
    assert run.bookkeeping.active is None
    # A long-running connection, whose clean exit clears the Install's drift.
    assert run.session.connections == [("install", True)]
    assert run.session.clean_exits == ["install"]


async def test_a_failed_compile_fails_at_compile_with_the_log_tail_and_exits_the_session_cleanly() -> None:
    run = await _run(install_transcript("install_compile_failed"))

    assert isinstance(run.ending, Failed)
    assert run.ending.stage == "compile"
    headline, message = run.ending.notification
    assert headline == "Install failed"
    assert "Install failed at stage **compile**" in message
    assert "[26/30] CXX obj/src/main.cpp.o" in message
    assert run.bookkeeping.recorded is None
    assert run.bookkeeping.active is None
    # The Install got a result from the Device Builder, so its drift is over.
    assert run.session.clean_exits == ["install"]


async def test_an_install_the_device_builder_defers_ends_as_device_offline() -> None:
    # Captured: the deferred compile is cancelled and the queued update cleared.
    run = await _run(install_transcript("install_deferred_at_enqueue"))

    assert run.fake.finished and run.fake.mismatches == []
    assert run.ending == DeviceOffline()
    headline, message = run.ending.notification
    assert headline == "Install failed: device offline"
    assert "queued update was cleared" in message
    assert run.bookkeeping.recorded is None
    assert run.bookkeeping.active is None


async def test_an_upload_that_cannot_reach_the_device_fails_as_device_unreachable() -> None:
    """With its web UI closed the Device Builder does not know the device is
    offline: the compile succeeds and the upload cannot connect."""
    run = await _run(install_transcript("install_upload_failed"))

    assert isinstance(run.ending, Failed)
    assert (run.ending.stage, run.ending.error) == ("upload", "device offline / unreachable")
    assert "Connection refused" in run.ending.message
    assert run.bookkeeping.recorded is None


async def test_an_upload_that_fails_for_another_reason_is_not_called_unreachable() -> None:
    transcript = [
        step
        for step in install_transcript("install_upload_failed")
        if "Connection refused" not in str(step["frame"].get("data", ""))
    ]

    run = await _run(transcript)

    assert isinstance(run.ending, Failed)
    assert run.ending.stage == "upload"
    assert "unreachable" not in run.ending.error


async def test_a_connection_lost_mid_compile_fails_at_the_stage_reached_and_forgets_the_install() -> None:
    transcript = install_transcript("install_compile_failed")
    lost_at = next(
        i
        for i, step in enumerate(transcript)
        if step["frame"].get("data", "").startswith("[26/30]")
    )

    run = await _run([*transcript[: lost_at + 1], {"dir": "close"}])

    assert isinstance(run.ending, Failed)
    assert run.ending.stage == "compile"
    assert "websocket closed" in run.ending.error
    assert "[26/30] CXX obj/src/main.cpp.o" in run.ending.message
    # Its jobs may still run, but it is not followed again (ticket 16).
    assert run.bookkeeping.active is None
    assert run.bookkeeping.recorded is None
    assert run.session.clean_exits == []


async def test_the_jobs_still_to_follow_are_kept_until_the_install_ends() -> None:
    plan = InstallPlan(COMMITS, port="127.0.0.1", clean_build=True)

    run = await _run(install_transcript("install_clean_success", clean={}, port="127.0.0.1"), plan)

    assert run.fake.finished and run.fake.mismatches == []
    ActiveJob, Stage = device_builder.ActiveJob, device_builder.Stage
    assert [active.jobs for active in run.bookkeeping.activity] == [
        (ActiveJob("98d512a9c2fd", Stage.CLEAN),),
        (ActiveJob("368f07c5f6ff", Stage.COMPILE), ActiveJob("58fd489cd24d", Stage.UPLOAD)),
        (ActiveJob("58fd489cd24d", Stage.UPLOAD),),
    ]
    assert all(
        (a.commits, a.port, a.clean_build) == (COMMITS, "127.0.0.1", True)
        for a in run.bookkeeping.activity
    )
    assert isinstance(run.ending, Installed)
    assert run.bookkeeping.active is None


async def test_a_connection_lost_before_any_output_fails_at_the_first_stage() -> None:
    clean = InstallPlan(COMMITS, port="OTA", clean_build=True)

    compile_run = await _run([server_info(), {"dir": "close"}])
    clean_run = await _run([server_info(), {"dir": "close"}], clean)

    assert isinstance(compile_run.ending, Failed) and isinstance(clean_run.ending, Failed)
    assert (compile_run.ending.stage, clean_run.ending.stage) == ("compile", "clean")


async def test_an_unavailable_device_builder_fails_the_install_at_connect() -> None:
    bookkeeping = FakeBookkeeping()
    runner = InstallRunner(CONFIGURATION, FakeSession(None), bookkeeping)
    ending = await runner.run(PLAN, lambda percentage: None)

    assert isinstance(ending, Failed)
    assert (ending.stage, ending.error) == (
        "connect",
        "cannot reach the Device Builder: connection refused",
    )
    assert bookkeeping.active is None


async def test_progress_moves_through_the_compile_and_upload_bands() -> None:
    run = await _run(
        queue_install("c1", "u1"),
        # The remote compile's ninja output tops out at 99; the upload reaches 100.
        jobs={
            "c1": job_events("[1057/1061] Linking firmware.elf", status="completed"),
            "u1": job_events("Uploading: [==========] 100% Done...", status="completed"),
        },
    )

    assert run.fake.mismatches == []
    # compile 0-90: 99% of the band is 89, then its end fills it to 90; upload 90-100.
    assert run.percentages == [89, 90, 100]


async def test_a_failed_compile_holds_the_bar_below_its_band_end() -> None:
    run = await _run(
        queue_install("c1", "u1"), jobs={"c1": job_events("[99/100] Compiling", status="failed")}
    )

    assert isinstance(run.ending, Failed)
    assert run.percentages == [89]


async def test_a_clean_build_progresses_through_the_clean_band_before_compiling() -> None:
    plan = InstallPlan(COMMITS, port="OTA", clean_build=True)

    run = await _run(install_transcript("install_clean_success", clean={}), plan)

    # The clean job reports no progress of its own; its band fills when it ends.
    assert run.percentages[0] == 5
    assert run.percentages[-1] == 100


async def test_a_clean_build_whose_clean_fails_ends_at_clean_and_records_nothing() -> None:
    plan = InstallPlan(COMMITS, port="OTA", clean_build=True)
    # A remote build server's clean fails; the Install stops before compiling.
    transcript = install_transcript("install_clean_success", clean={"k2": "failed"})

    run = await _run(transcript, plan)

    assert run.fake.mismatches == []
    assert isinstance(run.ending, Failed)
    assert run.ending.stage == "clean"
    assert "Install failed at stage **clean**" in run.ending.message
    assert run.bookkeeping.recorded is None
    assert run.bookkeeping.active is None


# An Install that was running when Home Assistant stopped.
LEFT_RUNNING = ActiveInstall(
    (
        device_builder.ActiveJob("c1", device_builder.Stage.COMPILE),
        device_builder.ActiveJob("u1", device_builder.Stage.UPLOAD),
    ),
    COMMITS,
    port="OTA",
)


async def test_a_reattached_install_follows_its_jobs_and_records_its_starting_commits() -> None:
    run = await _run(
        [server_info()],
        jobs={"c1": job_events("INFO Compiling"), "u1": job_events("INFO OTA successful")},
        reattach=LEFT_RUNNING,
    )

    assert run.fake.followed == ["c1", "u1"]
    assert run.ending == Installed()
    assert run.bookkeeping.recorded == ("2026.10.1", COMMITS)
    assert run.percentages[-1] == 100


async def test_a_reattached_install_whose_jobs_are_gone_ends_with_its_outcome_unknown() -> None:
    run = await _run([server_info()], reattach=LEFT_RUNNING)

    assert run.ending == OutcomeUnknown()
    headline, message = run.ending.notification
    assert "unknown" in headline.lower()
    assert "restarted" in message
    assert run.bookkeeping.active is None
    assert run.bookkeeping.recorded is None


async def test_a_reattached_install_that_fails_ends_like_any_failed_install() -> None:
    run = await _run(
        [server_info()],
        jobs={
            "c1": job_events("INFO Successfully compiled program."),
            "u1": job_events(
                "ERROR Connecting to 192.168.1.50 port 3232 failed: [Errno 113] No route to host",
                status="failed",
            ),
        },
        reattach=LEFT_RUNNING,
    )

    assert isinstance(run.ending, Failed)
    assert (run.ending.stage, run.ending.error) == ("upload", "device offline / unreachable")


async def test_a_fresh_install_whose_job_vanishes_fails_without_claiming_a_restart() -> None:
    # No jobs: the Device Builder no longer knows c1.
    run = await _run(queue_install("c1", "u1"))

    assert isinstance(run.ending, Failed)
    assert run.ending.stage == "compile"
    assert "restarted" not in run.ending.message
    assert run.bookkeeping.active is None


async def test_esphome_warnings_reach_the_ending_without_colour_codes() -> None:
    warning = "\\033[33mWARNING GPIO2 is a strapping PIN and should only be used for I/O with care.\\033[0m\n"
    run = await _run(
        queue_install("c1", "u1"),
        jobs={
            "c1": job_events(warning, "INFO Successfully compiled program."),
            "u1": job_events("INFO OTA successful"),
        },
    )

    assert run.ending == Installed(
        ("WARNING GPIO2 is a strapping PIN and should only be used for I/O with care.",)
    )
    assert run.ending.notification is not None
    headline, message = run.ending.notification
    assert headline == "Installed with warnings"
    assert "WARNING GPIO2" in message
    assert run.bookkeeping.recorded == ("2026.10.1", COMMITS)


async def test_compiler_warnings_and_the_remote_build_secrets_notice_are_not_warnings() -> None:
    """The add-on's real Install: a remote compile full of compiler warnings."""
    run = await _run(install_transcript("addon_success"))

    assert run.fake.mismatches == []
    assert run.ending == Installed()


async def test_a_failed_progress_poll_is_logged_and_the_install_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=device_builder.__name__)
    transcript = queue_install("c1", "u1")
    jobs = {
        "c1": job_events("[1057/1061] Linking firmware.elf", status="completed"),
        "u1": job_events("Uploading: [==========] 100% Done...", status="completed"),
    }
    bookkeeping = FakeBookkeeping()
    async with serve(transcript) as (url, fake):
        fake.jobs = jobs
        fake.progress_errors = {"c1": 1}
        runner = InstallRunner(CONFIGURATION, FakeSession(url), bookkeeping)
        ending = await runner.run(PLAN, lambda percentage: None)

    assert ending == Installed()
    assert "progress poll for job c1 failed" in caplog.text


async def test_a_malformed_progress_poll_is_logged_and_the_install_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=device_builder.__name__)
    transcript = queue_install("c1", "u1")
    async with serve(transcript) as (url, fake):
        fake.jobs = {
            "c1": job_events("[1057/1061] Linking firmware.elf", status="completed"),
            "u1": job_events("Uploading: [==========] 100% Done...", status="completed"),
        }
        fake.progress_malformed = {"c1": 1}
        runner = InstallRunner(CONFIGURATION, FakeSession(url), FakeBookkeeping())
        ending = await runner.run(PLAN, lambda percentage: None)

    assert ending == Installed()
    assert "malformed job progress" in caplog.text
