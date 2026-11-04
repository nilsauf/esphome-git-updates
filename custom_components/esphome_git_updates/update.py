"""The one update entity of a Tracked Config (ADR-0005)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NoReturn

from homeassistant.components import persistent_notification
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo, EventDeviceRegistryUpdatedData
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_device_registry_updated_event
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
    AuthenticationFailed,
    DeviceBuilderClient,
    InstallOutcome,
    InstallResult,
    JobNotFound,
    JobProgress,
    JobsCallback,
    ProgressCallback,
)
from .git_coordinator import GitSourcesCoordinator
from .install_log import (
    DEVICE_OFFLINE_TEXT,
    DEVICE_UNREACHABLE,
    OUTCOME_UNKNOWN_TEXT,
    InstallLog,
)
from .linked_device import LinkedDevice
from .options import Options
from .progress import InstallProgress
from .versions import SourceCommits, Versions

_LOGGER = logging.getLogger(__name__)

# Starts or resumes an Install's jobs on a connected client.
_StartJobs = Callable[
    [DeviceBuilderClient, InstallLog, JobsCallback, ProgressCallback],
    Awaitable[InstallResult],
]


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
    Git Source on a Floating Ref (polled by the git coordinator). It sits on
    the Linked Device if there is one, else on a device of its own (ADR-0005).
    """

    _attr_has_entity_name = True
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    # PROGRESS so that in_progress is the entity's own: a re-attached Install
    # runs outside async_install, and both set update_percentage (ticket 14).
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, entry: EsphomeGitUpdatesConfigEntry) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._notification_id = f"{DOMAIN}_{entry.entry_id}_install"
        self._store: TrackedConfigStore = entry.runtime_data.store
        self._git: GitSourcesCoordinator = entry.runtime_data.git_coordinator
        self._linked: LinkedDevice | None = entry.runtime_data.linked_device
        self._reauth = entry.runtime_data.reauth
        # Whether the unknown Installed ESPHome Version has been warned about.
        self._warned_unknown = False
        self._attr_unique_id = entry.entry_id
        if self._linked is not None:
            # Set explicitly: HA no longer merges devices across config entries.
            self.device_entry = self._linked.device_entry
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)},
                name=entry.title,
                model=entry.data[CONF_CONFIGURATION],
            )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._git.async_add_listener(self._handle_coordinator_update))
        if self._linked is not None:
            self.async_on_remove(
                async_track_device_registry_updated_event(
                    self.hass, self._linked.device_id, self._linked_device_updated
                )
            )
            self._warn_if_installed_unknown()
        if (active := self._store.active_install) is not None:
            self._reattach(active)

    @callback
    def _linked_device_updated(self, event: Event[EventDeviceRegistryUpdatedData]) -> None:
        if event.data["action"] == "update" and "sw_version" in event.data["changes"]:
            self._warn_if_installed_unknown()
            self.async_write_ha_state()

    def _installed_esphome(self) -> str | None:
        """What the Linked Device reports, else what the last Install recorded (ADR-0003)."""
        reported = self._linked.esphome_version if self._linked is not None else None
        return reported or self._store.installed_esphome_version

    def _warn_if_installed_unknown(self) -> None:
        """Warn once while the Installed ESPHome Version is unknown, so no update shows."""
        if self._installed_esphome() is not None:
            self._warned_unknown = False
            return
        if not self._warned_unknown:
            _LOGGER.warning(
                "%s: the installed ESPHome version is unknown, so no update is shown: "
                "its Linked Device reports sw_version %r, and no Install has succeeded yet",
                self._entry.title,
                self._linked.sw_version if self._linked is not None else None,
            )
            self._warned_unknown = True

    @property
    def installed_version(self) -> str | None:
        return self._versions().installed

    @property
    def latest_version(self) -> str | None:
        return self._versions().latest

    @property
    def release_summary(self) -> str | None:
        return self._versions().release_summary

    async def async_release_notes(self) -> str | None:
        """What changed in each Source; GitHub Sources list their commits."""
        await self._git.async_compare_changed()
        return self._versions().release_notes

    def _versions(self) -> Versions:
        return Versions(
            installed_esphome=self._installed_esphome(),
            available_esphome=self.coordinator.data,
            git_sources=tuple(
                SourceCommits(
                    tracked.source.name,
                    installed := self._store.installed_commit(key),
                    tracked.latest_commit,
                    self._git.comparisons.get(key, installed, tracked.latest_commit),
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
        options = Options.of(self._entry.options)
        await self._check_before_install()
        # What gets recorded is what the check just read, not what the remote
        # holds once the build is done: a push during the build stays an update.
        commits = self._git.latest_commits()
        task = self._start(
            commits,
            options.upload_address,
            "clean" if options.clean_build else "compile",
            options.clean_build,
            lambda client, log, on_jobs, on_progress: client.install(
                configuration,
                port=options.upload_address,
                clean_build=options.clean_build,
                on_output=log,
                on_jobs=on_jobs,
                on_progress=on_progress,
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
        A Linked Device that looks offline is refused first: the Device Builder
        would only defer the Install.
        """
        if self._linked is not None and self._linked.is_offline(self.entity_id):
            raise HomeAssistantError(
                f"Install of {self._entry.title} refused: device offline "
                "(every entity of its Linked Device is unavailable)"
            )
        # The bar is 0 until the first progress arrives.
        self._set_state(True, 0)
        try:
            await self._git.async_check_before_install()
        except BaseException:
            self._set_state(False, None)
            raise

    def _set_state(self, in_progress: bool, percentage: int | None) -> None:
        """Write both Install attributes in one state change."""
        self._attr_in_progress = in_progress
        self._attr_update_percentage = percentage
        self.async_write_ha_state()

    def _set_percentage(self, percentage: int) -> None:
        self._set_state(True, percentage)

    def _reattach(self, active: ActiveInstall) -> None:
        """Finish the Install that was running when Home Assistant stopped (ADR-0002)."""
        configuration = self._entry.data[CONF_CONFIGURATION]
        self._start(
            active.commits,
            active.port,
            active.jobs[0].stage,
            active.clean_build,
            lambda client, log, on_jobs, on_progress: client.resume_install(
                configuration,
                active.jobs,
                port=active.port,
                on_output=log,
                on_jobs=on_jobs,
                on_progress=on_progress,
            ),
            reattached=True,
        )

    def _start(
        self,
        commits: Mapping[str, str],
        port: str,
        stage: str,
        clean_build: bool,
        run: _StartJobs,
        *,
        reattached: bool,
    ) -> asyncio.Task[None]:
        """Run an Install as a task of the entry.

        Unloading the entry, as stopping Home Assistant does, cancels the task
        but leaves its jobs in the store, to be re-attached on the next setup.
        """
        self._set_state(True, 0)
        return self._entry.async_create_background_task(
            self.hass,
            self._install(commits, port, stage, clean_build, run, reattached),
            f"{DOMAIN} install {self._entry.title}",
        )

    async def _install(
        self,
        commits: Mapping[str, str],
        port: str,
        stage: str,
        clean_build: bool,
        run: _StartJobs,
        reattached: bool,
    ) -> None:
        cancelled = False
        try:
            await self._follow_install(commits, port, stage, clean_build, run, reattached)
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
                self._set_state(False, None)

    async def _follow_install(
        self,
        commits: Mapping[str, str],
        port: str,
        stage: str,
        clean_build: bool,
        run: _StartJobs,
        reattached: bool,
    ) -> None:
        log = InstallLog()
        progress = InstallProgress(clean_build)

        def on_progress(update: JobProgress) -> None:
            if progress.report(update):
                self._set_percentage(progress.percentage)

        async def on_jobs(jobs: tuple[ActiveJob, ...]) -> None:
            await self._store.async_set_active_install(
                ActiveInstall(jobs, commits, port, clean_build)
            )

        try:
            async with connect_to_device_builder(
                self.hass, self._entry.data, long_running=True
            ) as client:
                # The first job's output names its stage; until then, it is *stage*.
                log.stage = stage
                result = await run(client, log, on_jobs, on_progress)
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
        except AuthenticationFailed as err:
            self._reauth.async_require(err)
            await self._store.async_clear_active_install()
            self._fail(log.stage, str(err), log)
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
