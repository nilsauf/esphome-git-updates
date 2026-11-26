"""One Install of a Tracked Config: run it on the Device Builder, and say how it ended.

Nothing here knows Home Assistant. The update entity hands an `InstallRunner`
what it depends on, and shows the `InstallEnding` it gets back.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from typing import Protocol

from .device_builder import (
    ActiveJob,
    CommandFailed,
    DeviceBuilderClient,
    DeviceBuilderUnavailable,
    InstallObserver,
    InstallOutcome,
    InstallResult,
    JobNotFound,
    JobProgress,
    Stage,
)
from .install_log import (
    DEVICE_OFFLINE_TEXT,
    DEVICE_UNREACHABLE,
    OUTCOME_UNKNOWN_TEXT,
    UPDATE_YAML,
    InstallLog,
    InstallStage,
    warnings_text,
)
from .progress import InstallProgress

# The name the Install reports protocol drift under (ADR-0002).
DRIFT_NAME = "install"


@dataclass(frozen=True)
class YamlRewrite:
    """One file of the Tracked Config to write before compiling (ADR-0007)."""

    file: str
    content: str
    # The text read before the Install: a file changed since is not overwritten.
    expected: str


@dataclass(frozen=True)
class InstallPlan:
    """What an Install is asked to do."""

    # The commits to record if it succeeds: those seen when Install was pressed (ADR-0003).
    commits: Mapping[str, str]
    # The upload address.
    port: str
    clean_build: bool
    # The Installed Tags to record if it succeeds: the versions shown when
    # Install was pressed, by repository URL.
    tags: Mapping[str, str] = field(default_factory=dict)
    # The files to write first, in order, to move Tag Refs to those versions.
    rewrites: tuple[YamlRewrite, ...] = ()


@dataclass(frozen=True)
class ActiveInstall:
    """An Install whose jobs run on the Device Builder, enough to re-attach to it."""

    jobs: tuple[ActiveJob, ...]
    # The plan it was started with, its rewrites already written. A re-attached
    # Install still uploads to its port if the option changed meanwhile, and its
    # Clean Build tells its progress bands even after the clean step.
    plan: InstallPlan


# A notification's headline and message.
Notification = tuple[str, str]


@dataclass(frozen=True)
class Installed:
    """The Install succeeded and was recorded."""

    # ESPHome's distinct warnings, in the order they first appeared.
    warnings: tuple[str, ...] = ()

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
    def notification(self) -> Notification:
        return ("Install failed", self.message)


@dataclass(frozen=True)
class DeviceOffline:
    """The Device Builder would have deferred the Install; it was refused (spec step 4)."""

    @property
    def notification(self) -> Notification:
        return ("Install failed: device offline", DEVICE_OFFLINE_TEXT)


@dataclass(frozen=True)
class OutcomeUnknown:
    """A re-attached Install whose jobs the Device Builder no longer knows; nothing was recorded."""

    @property
    def notification(self) -> Notification:
        return ("Install outcome unknown", OUTCOME_UNKNOWN_TEXT)


InstallEnding = Installed | Failed | DeviceOffline | OutcomeUnknown


class InstallBookkeeping(Protocol):
    """What the integration remembers about Installs (ADR-0002, ADR-0003)."""

    async def async_set_active_install(self, active: ActiveInstall) -> None: ...

    async def async_clear_active_install(self) -> None: ...

    async def async_record_install(
        self, esphome_version: str, commits: Mapping[str, str], tags: Mapping[str, str]
    ) -> None: ...


class Session(Protocol):
    """The entry's Device Builder session (`connection.DeviceBuilderSession`).

    It holds back the Install while new credentials are awaited, and reports
    or clears protocol drift under the Install's name: drift clears only when
    the block exits cleanly. What it cannot get past comes out as one
    `DeviceBuilderUnavailable`.
    """

    def connect(
        self, name: str, *, long_running: bool = False
    ) -> AbstractAsyncContextManager[DeviceBuilderClient]: ...


class _Observer:
    """Follows one Install for its runner: log, progress bar and the jobs to re-attach to."""

    def __init__(
        self,
        bookkeeping: InstallBookkeeping,
        plan: InstallPlan,
        on_percentage: Callable[[int], None],
    ) -> None:
        self.log = InstallLog()
        self._bookkeeping = bookkeeping
        self._plan = plan
        self._bar = InstallProgress(plan.clean_build)
        self._on_percentage = on_percentage

    def output(self, stage: Stage, line: str) -> None:
        self.log(stage, line)

    async def jobs(self, jobs: tuple[ActiveJob, ...]) -> None:
        # Its rewrites are written before its jobs are queued.
        written = replace(self._plan, rewrites=())
        await self._bookkeeping.async_set_active_install(ActiveInstall(jobs, written))

    def progress(self, update: JobProgress) -> None:
        if self._bar.report(update):
            self._on_percentage(self._bar.percentage)


class InstallRunner:
    """Runs a Tracked Config's Installs, fresh or re-attached."""

    def __init__(
        self,
        configuration: str,
        session: Session,
        bookkeeping: InstallBookkeeping,
    ) -> None:
        self._configuration = configuration
        self._session = session
        self._bookkeeping = bookkeeping

    async def run(self, plan: InstallPlan, on_percentage: Callable[[int], None]) -> InstallEnding:
        """Run an Install: write the plan's rewrites, then an optional Clean Build,
        then compile, then upload."""

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
                self._configuration, active.jobs, port=active.plan.port, observer=observer
            )

        first_stage = active.jobs[0].stage
        return await self._follow(active.plan, first_stage, start, on_percentage, reattached=True)

    async def _follow(
        self,
        plan: InstallPlan,
        first_stage: Stage,
        start: Callable[[DeviceBuilderClient, InstallObserver], Awaitable[InstallResult]],
        on_percentage: Callable[[int], None],
        *,
        reattached: bool,
    ) -> InstallEnding:
        observer = _Observer(self._bookkeeping, plan, on_percentage)
        log = observer.log
        try:
            async with self._session.connect(DRIFT_NAME, long_running=True) as client:
                log.stage = UPDATE_YAML
                for rewrite in plan.rewrites:
                    await client.update_config(
                        rewrite.file, rewrite.content, expected=rewrite.expected
                    )
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
        except (DeviceBuilderUnavailable, CommandFailed) as err:
            return await self._failed(log, log.stage, str(err))
        if result.outcome is InstallOutcome.DEFERRED:
            await self._bookkeeping.async_clear_active_install()
            return DeviceOffline()
        if result.outcome is not InstallOutcome.COMPLETED:
            if result.stage == Stage.UPLOAD and log.device_unreachable:
                return await self._failed(log, result.stage, DEVICE_UNREACHABLE)
            return await self._failed(log, result.stage, result.error or result.outcome.value)
        await self._bookkeeping.async_record_install(esphome_version, plan.commits, plan.tags)
        return Installed(tuple(log.warnings))

    async def _failed(self, log: InstallLog, stage: InstallStage, error: str) -> Failed:
        await self._bookkeeping.async_clear_active_install()
        return Failed(stage, error, log.failure_text(stage, error))
