"""The Installed Record over in-memory storage: no Home Assistant (ADR-0003)."""

from __future__ import annotations

from custom_components.esphome_git_updates.device_builder import ActiveJob, Stage
from custom_components.esphome_git_updates.install import ActiveInstall, InstallPlan
from custom_components.esphome_git_updates.installed_record import InstalledRecord, Seeds
from in_memory_storage import InMemoryStorage

NONE = Seeds({}, ())


async def loaded(storage: InMemoryStorage) -> InstalledRecord:
    record = InstalledRecord(storage)
    await record.async_load()
    return record


# Seeding Installed Commits and Installed Tags.


async def test_seeds_are_recorded_only_where_nothing_is_recorded_yet() -> None:
    record = await loaded(InMemoryStorage())
    await record.async_seed(Seeds({"a": "c1"}, {"a"}), Seeds({"u": "v1.0.0"}, {"u"}))

    await record.async_seed(
        Seeds({"a": "c2", "b": "s1"}, {"a", "b"}), Seeds({"u": "v2.0.0"}, {"u"})
    )

    assert record.installed_commit("a") == "c1"
    assert record.installed_commit("b") == "s1"
    assert record.installed_tag("u") == "v1.0.0"


async def test_what_is_no_longer_present_is_forgotten() -> None:
    record = await loaded(InMemoryStorage())
    await record.async_seed(Seeds({"a": "c1", "b": "s1"}, {"a", "b"}), Seeds({"u": "v1"}, {"u"}))

    await record.async_seed(Seeds({}, {"a"}), NONE)

    assert record.installed_commit("a") == "c1"
    assert record.installed_commit("b") is None
    assert record.installed_tag("u") is None


async def test_a_source_that_returns_is_seeded_afresh() -> None:
    record = await loaded(InMemoryStorage())
    await record.async_seed(Seeds({"a": "c1"}, {"a"}), Seeds({"u": "v1"}, {"u"}))
    await record.async_seed(NONE, NONE)

    await record.async_seed(Seeds({"a": "c2"}, {"a"}), Seeds({"u": "v2"}, {"u"}))

    assert record.installed_commit("a") == "c2"
    assert record.installed_tag("u") == "v2"


async def test_seeding_both_kinds_saves_once() -> None:
    storage = InMemoryStorage()
    record = await loaded(storage)

    await record.async_seed(Seeds({"a": "c1"}, {"a"}), Seeds({"u": "v1"}, {"u"}))

    assert storage.saves == 1
    reloaded = await loaded(storage)
    assert reloaded.installed_commit("a") == "c1"
    assert reloaded.installed_tag("u") == "v1"


async def test_seeding_that_changes_nothing_saves_nothing() -> None:
    storage = InMemoryStorage()
    record = await loaded(storage)
    await record.async_seed(Seeds({"a": "c1"}, {"a"}), Seeds({"u": "v1"}, {"u"}))

    await record.async_seed(Seeds({"a": "c2"}, {"a"}), Seeds({"u": "v2"}, {"u"}))
    await record.async_seed(NONE, NONE)
    await record.async_seed(NONE, NONE)

    assert storage.saves == 2


# Installs.

ACTIVE = ActiveInstall(
    (ActiveJob("j1", Stage.COMPILE), ActiveJob("j2", Stage.UPLOAD)),
    InstallPlan({"a": "c2"}, "192.168.1.20", True, {"u": "v2"}),
)


async def test_recording_an_install_merges_the_versions_and_ends_the_active_install() -> None:
    storage = InMemoryStorage()
    record = await loaded(storage)
    await record.async_seed(
        Seeds({"a": "c1", "b": "s1"}, {"a", "b"}), Seeds({"u": "v1", "w": "w1"}, {"u", "w"})
    )
    await record.async_set_active_install(ACTIVE)

    await record.async_record_install("2025.6.0", {"a": "c2"}, {"u": "v2"})

    for each in (record, await loaded(storage)):
        assert each.installed_esphome_version == "2025.6.0"
        assert each.installed_commit("a") == "c2"
        assert each.installed_commit("b") == "s1"
        assert each.installed_tag("u") == "v2"
        assert each.installed_tag("w") == "w1"
        assert each.active_install is None


async def test_the_active_install_survives_a_reload_until_cleared() -> None:
    storage = InMemoryStorage()
    record = await loaded(storage)
    assert record.active_install is None

    await record.async_set_active_install(ACTIVE)

    assert record.active_install == ACTIVE
    reloaded = await loaded(storage)
    assert reloaded.active_install == ACTIVE

    await reloaded.async_clear_active_install()

    assert reloaded.active_install is None
    assert (await loaded(storage)).active_install is None


async def test_clearing_no_active_install_saves_nothing() -> None:
    storage = InMemoryStorage()
    record = await loaded(storage)

    await record.async_clear_active_install()

    assert storage.saves == 0
