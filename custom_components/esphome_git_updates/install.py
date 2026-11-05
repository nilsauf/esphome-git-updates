"""One Install of a Tracked Config: run it on the Device Builder, and say how it ended.

Nothing here knows Home Assistant. The update entity hands an `InstallRunner`
what it depends on, and shows the `InstallEnding` it gets back.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from .device_builder import (
    CONNECTION_ERRORS,
    ActiveJob,
    AuthenticationFailed,
    DeviceBuilderClient,
    InstallObserver,
    InstallOutcome,
    InstallResult,
    JobNotFound,
    JobProgress,
    Stage,
    UnexpectedFrame,
    UnsupportedServerVersion,
)
from .install_log import (
    DEVICE_OFFLINE_TEXT,
    DEVICE_UNREACHABLE,
    OUTCOME_UNKNOWN_TEXT,
    InstallLog,
    InstallStage,
    warnings_text,
)
from .progress import InstallProgress

# The name the Install reports protocol drift under (ADR-0002).
DRIFT_NAME = "install"


@dataclass(frozen=True)
class ActiveInstall:
    """An Install whose jobs run on the Device Builder, enough to re-attach to it."""

    jobs: tuple[ActiveJob, ...]
    commits: Mapping[str, str]
    # The upload address it was started with: it still uploads there if the
    # option changes before it is re-attached.
    port: str
    # Whether it started with a Clean Build, so its progress bands are known
    # even when it is re-attached after the clean step.
    clean_build: bool = False


@dataclass(frozen=True)
class InstallPlan:
    """What an Install is asked to do."""

    # The commits to record if it succeeds: those seen when Install was pressed (ADR-0003).
    commits: Mapping[str, str]
    # The upload address.
    port: str
    clean_build: bool


# A notification's headline and message.
Notification = tuple[str, str]

# Every ending has a `summary` that completes "Install of <title> ...". The
# entity fails the Install action with it for every ending but `Installed`.


@dataclass(frozen=True)
class Installed:
    """The Install succeeded and was recorded."""

    # ESPHome's distinct warnings, in the order they first appeared.
    warnings: tuple[str, ...] = ()

    summary = "succeeded"

    @property
    def notification(self) -> Notification | None:
        if not self.warnings:
            return None
        return ("Installed with warnings", warnings_text(self.warnings))


@dataclass(frozen=True)
class Failed:
    """The Install failed; nothing was recorded."""

    stage: InstallStage
    error: str
    # The failure notification's message, with the tail of the Install's log.
    message: str

    @property
    def summary(self) -> str:
        return f"failed at {self.stage}: {self.error}"

    @property
    def notification(self) -> Notification:
        return ("Install failed", self.message)


@dataclass(frozen=True)
class DeviceOffline:
    """The Device Builder would have deferred the Install; it was refused (spec step 4)."""

    summary = "failed: device offline"

    @property
    def notification(self) -> Notification:
        return ("Install failed: device offline", DEVICE_OFFLINE_TEXT)


@dataclass(frozen=True)
class OutcomeUnknown:
    """A re-attached Install whose jobs the Device Builder no longer knows; nothing was recorded."""

    summary = "has an unknown outcome"

    @property
    def notification(self) -> Notification:
        return ("Install outcome unknown", OUTCOME_UNKNOWN_TEXT)


InstallEnding = Installed | Failed | DeviceOffline | OutcomeUnknown


class InstallBookkeeping(Protocol):
    """What the integration remembers about Installs (ADR-0002, ADR-0003)."""

    async def async_set_active_install(self, active: ActiveInstall) -> None: ...

    async def async_clear_active_install(self) -> None: ...

    async def async_record_install(
        self, esphome_version: str, commits: Mapping[str, str]
    ) -> None: ...


class Reauth(Protocol):
    """Asks the user for new credentials once the Device Builder rejects the stored ones."""

    def async_require(self, err: AuthenticationFailed) -> None: ...


class DriftIssue(Protocol):
    """The entry's protocol-drift Repair issue, which the Install reports under its own name."""

    def report_drift(
        self, name: str, err: UnsupportedServerVersion | UnexpectedFrame
    ) -> None: ...

    def clear(self, name: str) -> None: ...


Connect = Callable[[], AbstractAsyncContextManager[DeviceBuilderClient]]


class _Observer:
    """Follows one Install for its runner: log, progress bar and the jobs to re-attach to."""

    def __init__(
        self,
        bookkeeping: InstallBookkeeping,
        commits: Mapping[str, str],
        port: str,
        clean_build: bool,
        on_percentage: Callable[[int], None],
    ) -> None:
        self.log = InstallLog()
        self._bookkeeping = bookkeeping
        self._commits = commits
        self._port = port
        self._clean_build = clean_build
        self._bar = InstallProgress(clean_build)
        self._on_percentage = on_percentage

    def output(self, stage: Stage, line: str) -> None:
        self.log(stage, line)

    async def jobs(self, jobs: tuple[ActiveJob, ...]) -> None:
        await self._bookkeeping.async_set_active_install(
            ActiveInstall(jobs, self._commits, self._port, self._clean_build)
        )

    def progress(self, update: JobProgress) -> None:
        if self._bar.report(update):
            self._on_percentage(self._bar.percentage)


class InstallRunner:
    """Runs a Tracked Config's Installs, fresh or re-attached."""

    def __init__(
        self,
        configuration: str,
        connect: Connect,
        bookkeeping: InstallBookkeeping,
        reauth: Reauth,
        issue: DriftIssue,
    ) -> None:
        self._configuration = configuration
        self._connect = connect
        self._bookkeeping = bookkeeping
        self._reauth = reauth
        self._issue = issue

    async def run(self, plan: InstallPlan, on_percentage: Callable[[int], None]) -> InstallEnding:
        """Run an Install: an optional Clean Build, then compile, then upload."""

        def start(
            client: DeviceBuilderClient, observer: InstallObserver
        ) -> Awaitable[InstallResult]:
            return client.install(
                self._configuration,
                port=plan.port,
                clean_build=plan.clean_build,
                observer=observer,
            )

        first_stage = Stage.CLEAN if plan.clean_build else Stage.COMPILE
        return await self._follow(plan, first_stage, start, on_percentage, reattached=False)

    async def reattach(
        self, active: ActiveInstall, on_percentage: Callable[[int], None]
    ) -> InstallEnding:
        """Finish the Install that was running when Home Assistant stopped (ADR-0002)."""

        def start(
            client: DeviceBuilderClient, observer: InstallObserver
        ) -> Awaitable[InstallResult]:
            return client.resume_install(
                self._configuration, active.jobs, port=active.port, observer=observer
            )

        plan = InstallPlan(active.commits, active.port, active.clean_build)
        first_stage = active.jobs[0].stage
        return await self._follow(plan, first_stage, start, on_percentage, reattached=True)

    async def _follow(
        self,
        plan: InstallPlan,
        first_stage: Stage,
        start: Callable[[DeviceBuilderClient, InstallObserver], Awaitable[InstallResult]],
        on_percentage: Callable[[int], None],
        *,
        reattached: bool,
    ) -> InstallEnding:
        observer = _Observer(
            self._bookkeeping, plan.commits, plan.port, plan.clean_build, on_percentage
        )
        log = observer.log
        try:
            async with self._connect() as client:
                # The first job's output names its stage; until then, it is the first.
                log.stage = first_stage
                result = await start(client, observer)
                esphome_version = client.esphome_version
        except JobNotFound as err:
            if reattached:
                # Its jobs were pruned while Home Assistant was down.
                await self._bookkeeping.async_clear_active_install()
                return OutcomeUnknown()
            return await self._failed(log, log.stage, str(err))
        except AuthenticationFailed as err:
            self._reauth.async_require(err)
            return await self._failed(log, log.stage, str(err))
        except (UnsupportedServerVersion, UnexpectedFrame) as err:
            self._issue.report_drift(DRIFT_NAME, err)
            return await self._failed(log, log.stage, str(err))
        except CONNECTION_ERRORS as err:
            return await self._failed(log, log.stage, str(err))
        # It reached the Device Builder without drift.
        self._issue.clear(DRIFT_NAME)
        if result.outcome is InstallOutcome.DEFERRED:
            await self._bookkeeping.async_clear_active_install()
            return DeviceOffline()
        if result.outcome is not InstallOutcome.COMPLETED:
            if result.stage == Stage.UPLOAD and log.device_unreachable:
                return await self._failed(log, result.stage, DEVICE_UNREACHABLE)
            return await self._failed(log, result.stage, result.error or result.outcome.value)
        await self._bookkeeping.async_record_install(esphome_version, plan.commits)
        return Installed(tuple(log.warnings))

    async def _failed(self, log: InstallLog, stage: InstallStage, error: str) -> Failed:
        await self._bookkeeping.async_clear_active_install()
        return Failed(stage, error, log.failure_text(stage, error))
