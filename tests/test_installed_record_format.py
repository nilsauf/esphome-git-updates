"""The on-disk format of an Installed Record, storage version 1, read and written
through Home Assistant's store. The only test that names a stored field: a change of
the layout breaks this test and no other."""

from __future__ import annotations

import copy
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.esphome_git_updates.device_builder import OTA, ActiveJob, Stage
from custom_components.esphome_git_updates.install import ActiveInstall, InstallPlan
from stored_record import stored_record

# Written by an older version of the integration: its running Install has no upload
# address, Clean Build or Installed Tags.
ON_DISK: dict[str, Any] = {
    "version": 1,
    "minor_version": 1,
    "key": "esphome_git_updates.tracked",
    "data": {
        "installed_esphome_version": "2026.10.0",
        "installed_commits": {"https://github.com/example/marsrelay@HEAD": "c0ffee1"},
        "installed_tags": {"https://github.com/example/lunarlink": "v1.2.0"},
        "active_install": {
            "jobs": [{"job_id": "c1", "stage": "compile"}, {"job_id": "u1", "stage": "upload"}],
            "commits": {"https://github.com/example/marsrelay@HEAD": "c0ffee2"},
        },
    },
}


async def test_a_stored_record_reads_as_before_and_saves_the_same_layout(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass_storage["esphome_git_updates.tracked"] = copy.deepcopy(ON_DISK)

    record = await stored_record(hass, "tracked")

    assert record.installed_esphome_version(None) == "2026.10.0"
    assert record.installed_commit("https://github.com/example/marsrelay@HEAD") == "c0ffee1"
    assert record.installed_tag("https://github.com/example/lunarlink") == "v1.2.0"
    assert record.active_install == ActiveInstall(
        (ActiveJob("c1", Stage.COMPILE), ActiveJob("u1", Stage.UPLOAD)),
        InstallPlan({"https://github.com/example/marsrelay@HEAD": "c0ffee2"}, OTA, False, {}),
    )

    await record.async_set_active_install(
        ActiveInstall(
            (ActiveJob("k1", Stage.CLEAN),),
            InstallPlan(
                {"https://github.com/example/marsrelay@HEAD": "c0ffee3"},
                "192.168.1.42",
                True,
                {"https://github.com/example/lunarlink": "v1.3.0"},
            ),
        )
    )

    assert hass_storage["esphome_git_updates.tracked"] == {
        **ON_DISK,
        "data": {
            **ON_DISK["data"],
            "active_install": {
                "jobs": [{"job_id": "k1", "stage": "clean"}],
                "commits": {"https://github.com/example/marsrelay@HEAD": "c0ffee3"},
                "port": "192.168.1.42",
                "clean_build": True,
                "tags": {"https://github.com/example/lunarlink": "v1.3.0"},
            },
        },
    }
