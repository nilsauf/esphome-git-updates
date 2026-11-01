"""The one update entity of a Tracked Config (ADR-0005)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NoReturn

from homeassistant.components import persistent_notification
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .connection import CONNECTION_ERRORS, connect_to_device_builder
from .const import CONF_CONFIGURATION, DOMAIN
from .coordinator import (
    ActiveInstall,
    EsphomeGitUpdatesConfigEntry,
    EsphomeVersionCoordinator,
    TrackedConfigStore,
)
from .device_builder import (
    ActiveJob,
    DeviceBuilderClient,
    InstallOutcome,
    InstallResult,
    JobNotFound,
    JobsCallback,
)
from .git_coordinator import GitSourcesCoordinator
from .install_log import (
    DEVICE_OFFLINE_TEXT,
    DEVICE_UNREACHABLE,
    OUTCOME_UNKNOWN_TEXT,
    InstallLog,
)
from .versions import SourceCommits, Versions


# Starts or resumes an Install's jobs on a connected client.
_StartJobs = Callable[[DeviceBuilderClient, InstallLog, JobsCallback], Awaitable[InstallResult]]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EsphomeGitUpdatesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the Tracked Config's one update entity."""
    async_add_entities([TrackedConfigUpdate(entry)])


class TrackedConfigUpdate(CoordinatorEntity[EsphomeVersionCoordinator], UpdateEntity):
    """Offers an update when any Source differs from what is installed.

    The Sources are the ESPHome version (polled by the coordinator) and every
    Git Source on a Floating Ref (polled by the git coordinator). Until a
    Linked Device is supported, it sits on a device of its own.
    """

    _attr_has_entity_name = True
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    # PROGRESS so that in_progress is the entity's own: a re-attached Install
    # runs outside async_install. There is no update_percentage yet (ticket 14).
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS

    def __init__(self, entry: EsphomeGitUpdatesConfigEntry) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._notification_id = f"{DOMAIN}_{entry.entry_id}_install"
        self._store: TrackedConfigStore = entry.runtime_data.store
        self._git: GitSourcesCoordinator = entry.runtime_data.git_coordinator
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            model=entry.data[CONF_CONFIGURATION],
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._git.async_add_listener(self._handle_coordinator_update))
        if (active := self._store.active_install) is not None:
            self._reattach(active)

    @property
    def installed_version(self) -> str | None:
        return self._versions().installed

    @property
    def latest_version(self) -> str | None:
        return self._versions().latest

    @property
    def release_summary(self) -> str | None:
        return self._versions().release_summary

    def _versions(self) -> Versions:
        return Versions(
            installed_esphome=self._store.installed_esphome_version,
            available_esphome=self.coordinator.data,
            git_sources=tuple(
                SourceCommits(
                    tracked.source.name, self._store.installed_commit(key), tracked.latest_commit
                )
                for key, tracked in (self._git.data or {}).items()
            ),
        )

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        # "Newer" means "differs": a Device Builder can also move back a version.
        return latest_version != installed_version

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Run an Install: compile, then upload over the air.

        The outcome is told through a persistent notification; the full
        output stays in the Device Builder, nothing is written to /config.
        """
        configuration = self._entry.data[CONF_CONFIGURATION]
        await self._check_before_install()
        # What gets recorded is what the check just read, not what the remote
        # holds once the build is done: a push during the build stays an update.
        commits = self._git.latest_commits()
        task = self._start(
            commits,
            "compile",
            lambda client, log, on_jobs: client.install(
                configuration, on_output=log, on_jobs=on_jobs
            ),
            reattached=False,
        )
        # Not awaited directly: cancelling the action must not cancel the Install.
        await asyncio.wait([task])
        if task.cancelled():
            raise HomeAssistantError(
                f"Install of {self._entry.title} was interrupted; "
                "it is picked up again when the integration is set up"
            )
        task.result()

    async def _check_before_install(self) -> None:
        """Read the YAML again and refuse an Install it cannot take over the air.

        The Install counts as in progress from here, so a second press while
        this check runs is refused rather than starting a second Install.
        """
        self._attr_in_progress = True
        self.async_write_ha_state()
        try:
            await self._git.async_refresh()
            check = self._git.pre_install_check
            if not self._git.last_update_success or check is None:
                raise HomeAssistantError(
                    f"Install of {self._entry.title} refused: cannot read its YAML to check "
                    f"it before installing: {self._git.last_exception}"
                )
            if (refusal := check.refusal(self._entry.title)) is not None:
                raise HomeAssistantError(refusal)
        except BaseException:
            self._attr_in_progress = False
            self.async_write_ha_state()
            raise

    def _reattach(self, active: ActiveInstall) -> None:
        """Finish the Install that was running when Home Assistant stopped (ADR-0002)."""
        configuration = self._entry.data[CONF_CONFIGURATION]
        self._start(
            active.commits,
            active.jobs[0].stage,
            lambda client, log, on_jobs: client.resume_install(
                configuration, active.jobs, on_output=log, on_jobs=on_jobs
            ),
            reattached=True,
        )

    def _start(
        self, commits: Mapping[str, str], stage: str, run: _StartJobs, *, reattached: bool
    ) -> asyncio.Task[None]:
        """Run an Install as a task of the entry.

        Unloading the entry, as stopping Home Assistant does, cancels the task
        but leaves its jobs in the store, to be re-attached on the next setup.
        """
        self._attr_in_progress = True
        self.async_write_ha_state()
        return self._entry.async_create_background_task(
            self.hass,
            self._install(commits, stage, run, reattached),
            f"{DOMAIN} install {self._entry.title}",
        )

    async def _install(
        self, commits: Mapping[str, str], stage: str, run: _StartJobs, reattached: bool
    ) -> None:
        cancelled = False
        try:
            await self._follow_install(commits, stage, run, reattached)
        except asyncio.CancelledError:
            # It stays in progress, and is re-attached on the next setup.
            cancelled = True
            raise
        except HomeAssistantError:
            # Nobody waits for a re-attached Install: its notification says how it went.
            if not reattached:
                raise
        finally:
            if not cancelled:
                self._attr_in_progress = False
                self.async_write_ha_state()

    async def _follow_install(
        self, commits: Mapping[str, str], stage: str, run: _StartJobs, reattached: bool
    ) -> None:
        log = InstallLog()

        async def on_jobs(jobs: tuple[ActiveJob, ...]) -> None:
            await self._store.async_set_active_install(ActiveInstall(jobs, commits))

        try:
            async with connect_to_device_builder(
                self.hass, self._entry.data, long_running=True
            ) as client:
                # The first job's output names its stage; until then, it is *stage*.
                log.stage = stage
                result = await run(client, log, on_jobs)
                esphome_version = client.esphome_version
        except JobNotFound as err:
            await self._store.async_clear_active_install()
            if not reattached:
                self._fail(log.stage, str(err), log)
            # Its jobs were pruned while Home Assistant was down.
            self._notify("Install outcome unknown", OUTCOME_UNKNOWN_TEXT)
            raise HomeAssistantError(
                f"The outcome of the Install of {self._entry.title} is unknown"
            ) from None
        except CONNECTION_ERRORS as err:
            await self._store.async_clear_active_install()
            self._fail(log.stage, str(err), log)
        if result.outcome is not InstallOutcome.COMPLETED:
            await self._store.async_clear_active_install()
            if result.outcome is InstallOutcome.DEFERRED:
                self._notify("Install failed: device offline", DEVICE_OFFLINE_TEXT)
                raise HomeAssistantError(f"Install of {self._entry.title} failed: device offline")
            if result.stage == "upload" and log.device_unreachable:
                self._fail(result.stage, DEVICE_UNREACHABLE, log)
            self._fail(result.stage, result.error or result.outcome.value, log)
        await self._store.async_record_install(esphome_version, commits)
        if log.warnings:
            self._notify("Installed with warnings", log.warnings_text())
        else:
            persistent_notification.async_dismiss(self.hass, self._notification_id)

    def _fail(self, stage: str, error: str, log: InstallLog) -> NoReturn:
        """Tell the user why the Install failed, then fail the action."""
        self._notify("Install failed", log.failure_text(stage, error))
        raise HomeAssistantError(f"Install of {self._entry.title} failed at {stage}: {error}")

    def _notify(self, headline: str, message: str) -> None:
        persistent_notification.async_create(
            self.hass,
            message,
            title=f"{self._entry.title}: {headline}",
            notification_id=self._notification_id,
        )
