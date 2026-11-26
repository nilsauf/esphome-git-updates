"""The Installed Record of one Tracked Config: what the integration remembers of its
last successful Install, the seeds that stand in for it until then, and the Install
still running (ADR-0002, ADR-0003, ADR-0007).

No Home Assistant imports: the storage is a port handed in. Home Assistant's
`Store`, built by the integration's `record_store`, is the production adapter.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from typing import Any, NamedTuple, NotRequired, Protocol, TypedDict

from .const import DOMAIN
from .device_builder import OTA, ActiveJob, Stage
from .git_sources import without_userinfo
from .install import ActiveInstall, InstallPlan
from .versions import parse_sw_version

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


def storage_key(entry_id: str) -> str:
    """The key the Installed Record of the config entry *entry_id* is stored under."""
    return f"{DOMAIN}.{entry_id}"


class _StoredJob(TypedDict):
    job_id: str
    stage: str


class _StoredInstall(TypedDict):
    # The jobs still to follow, in order.
    jobs: list[_StoredJob]
    # The commits to record if the Install succeeds.
    commits: dict[str, str]
    # Where the Install uploads; absent in stores written before ticket 08.
    port: NotRequired[str]
    # Whether it started with a Clean Build; absent in stores written before
    # ticket 14, where the progress bands did not need it.
    clean_build: NotRequired[bool]
    # The Installed Tags to record if it succeeds, by repository URL.
    tags: NotRequired[dict[str, str]]


class StoredData(TypedDict, total=False):
    """The on-disk layout of an Installed Record, storage version 1."""

    installed_esphome_version: str
    # Git Source key -> Installed Commit.
    installed_commits: dict[str, str]
    # Repository URL of a Git Source following versions -> Installed Tag. Keyed
    # without the ref, so it survives Install rewriting the ref (ADR-0007).
    installed_tags: dict[str, str]
    # The Install running on the Device Builder, if any (ADR-0002).
    active_install: _StoredInstall


class Storage(Protocol):
    """Where an Installed Record is kept."""

    async def async_load(self) -> StoredData | None:
        """What was last saved, or None when nothing was."""
        ...

    async def async_save(self, data: StoredData) -> None: ...

    async def async_remove(self) -> None: ...


class Seeds(NamedTuple):
    """What new entries of one kind start from, by key, and every key still present."""

    new: Mapping[str, str]
    present: Collection[str]


def _seeded(recorded: Mapping[str, str], seeds: Seeds) -> dict[str, str]:
    """*recorded* without the keys no longer present, and with the new ones of *seeds* where
    nothing is recorded yet."""
    kept = {key: value for key, value in recorded.items() if key in seeds.present}
    for key, value in seeds.new.items():
        kept.setdefault(key, value)
    return kept


class InstalledRecord:
    """What the integration remembers about one Tracked Config (ADR-0003)."""

    def __init__(self, storage: Storage, *, linked_device: bool, title: str) -> None:
        """*linked_device*: whether the entry has a Linked Device, even one since gone.
        *title*: the entry's title, for the log."""
        self._storage = storage
        self._linked_device = linked_device
        self._title = title
        self._data: StoredData = {}
        # Whether the unknown Installed ESPHome Version has been warned about.
        self._warned_unknown = False

    async def async_load(self) -> None:
        self._data = await self._storage.async_load() or {}

    def installed_esphome_version(self, sw_version: str | None) -> str | None:
        """The Installed ESPHome Version, given the Linked Device's raw *sw_version*
        (None without one): what it reports, else what the last Install recorded
        (ADR-0003). None while neither gives one, so no update shows."""
        return parse_sw_version(sw_version) or self._data.get("installed_esphome_version")

    def warn_if_esphome_version_unknown(self, sw_version: str | None) -> None:
        """Warn while the Installed ESPHome Version, given the Linked Device's raw
        *sw_version*, is unknown: once, and again only after a version was known in
        between."""
        if self.installed_esphome_version(sw_version) is not None:
            self._warned_unknown = False
            return
        if not self._warned_unknown:
            _LOGGER.warning(
                "%s: the installed ESPHome version is unknown, so no update is shown: "
                "its Linked Device reports sw_version %r, and no Install has succeeded yet",
                self._title,
                sw_version,
            )
            self._warned_unknown = True

    async def async_seed_esphome_version(self, available: str) -> None:
        """Seed the Installed ESPHome Version with the *available* one: a new entry
        assumes the device already runs it, so it shows no update until the Device
        Builder's ESPHome changes. An entry with a Linked Device is never seeded, even
        when that device has since gone, and a recorded version is kept (ADR-0003)."""
        if self._linked_device or "installed_esphome_version" in self._data:
            return
        self._data["installed_esphome_version"] = available
        await self._storage.async_save(self._data)

    def installed_commit(self, source_key: str) -> str | None:
        """The Installed Commit of the Git Source with *source_key*, if any."""
        return self._data.get("installed_commits", {}).get(source_key)

    def installed_tag(self, url: str) -> str | None:
        """The Installed Tag of the Git Source following versions at *url*, if any."""
        return self._data.get("installed_tags", {}).get(url)

    async def async_seed(self, commits: Seeds, tags: Seeds) -> None:
        """Seed Installed Commits (by Source key) and Installed Tags (by repository URL)
        where none is recorded yet, and forget those no longer present, so a Source
        that comes back is seeded afresh (ADR-0003). Saves once, if anything changed."""
        changed = False
        recorded_commits = self._data.get("installed_commits", {})
        if (seeded := _seeded(recorded_commits, commits)) != recorded_commits:
            self._data["installed_commits"] = seeded
            changed = True
        recorded_tags = self._data.get("installed_tags", {})
        if (seeded := _seeded(recorded_tags, tags)) != recorded_tags:
            self._data["installed_tags"] = seeded
            changed = True
        if changed:
            await self._storage.async_save(self._data)

    def diagnostics(self) -> dict[str, Any]:
        """What the record holds, for a diagnostics download: the Installed ESPHome
        Version, Installed Commits and Installed Tags, and the active Install, if any.

        Every Git Source URL is cut free of the credentials it may carry.
        """
        active = self.active_install
        return {
            "installed_esphome_version": self._data.get("installed_esphome_version"),
            "installed_commits": _by_url(self._data.get("installed_commits", {})),
            "installed_tags": _by_url(self._data.get("installed_tags", {})),
            "active_install": None
            if active is None
            else {
                "jobs": [{"job_id": job.job_id, "stage": str(job.stage)} for job in active.jobs],
                "commits": _by_url(active.plan.commits),
                "port": active.plan.port,
                "clean_build": active.plan.clean_build,
                "tags": _by_url(active.plan.tags),
            },
        }

    @property
    def active_install(self) -> ActiveInstall | None:
        """The Install left running when Home Assistant last stopped, if any."""
        stored = self._data.get("active_install")
        if stored is None:
            return None
        return ActiveInstall(
            tuple(ActiveJob(job["job_id"], Stage(job["stage"])) for job in stored["jobs"]),
            InstallPlan(
                stored["commits"],
                stored.get("port", OTA),
                stored.get("clean_build", False),
                stored.get("tags", {}),
            ),
        )

    async def async_set_active_install(self, active: ActiveInstall) -> None:
        """Remember the jobs an Install still has to follow, until it ends."""
        self._data["active_install"] = {
            "jobs": [{"job_id": job.job_id, "stage": str(job.stage)} for job in active.jobs],
            "commits": dict(active.plan.commits),
            "port": active.plan.port,
            "clean_build": active.plan.clean_build,
            "tags": dict(active.plan.tags),
        }
        await self._storage.async_save(self._data)

    async def async_clear_active_install(self) -> None:
        """Forget the active Install: it ended without success."""
        if self._data.pop("active_install", None) is not None:
            await self._storage.async_save(self._data)

    async def async_record_install(
        self, esphome_version: str, commits: Mapping[str, str], tags: Mapping[str, str]
    ) -> None:
        """Record a successful Install: the version compiled with, and the commits and
        versions seen when Install was pressed (ADR-0003, ADR-0007). The Install is no
        longer active."""
        self._data["installed_esphome_version"] = esphome_version
        self._data["installed_commits"] = {**self._data.get("installed_commits", {}), **commits}
        self._data["installed_tags"] = {**self._data.get("installed_tags", {}), **tags}
        self._data.pop("active_install", None)
        await self._storage.async_save(self._data)


def _by_url(values: Mapping[str, str]) -> dict[str, str]:
    """*values*, keyed by Git Source URLs (or Source keys), with those cut free of credentials."""
    return {without_userinfo(key): value for key, value in values.items()}
