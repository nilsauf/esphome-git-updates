"""Install rewrites a Tag Ref wherever its text is written: an included file or a
substitution used only for it (ADR-0007). A ref whose text is shared is untrackable.

The Device Builder is the fake `/ws` server, whose files map is what
`devices/update_config` writes to; each Git Source's remote is a real
repository on disk.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from fake_device_builder import FakeDeviceBuilder, serve, server_info
from git_repo import GitRepo
from tracked_config import CONFIGURATION, ENTITY_ID, OTA, install, set_up, tick

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

_INCLUDED = "common/marsrelay.yaml"
_UNTOUCHED = "common/wifi.yaml"


def _included(url: str, ref: str) -> str:
    return (
        "# marsrelay, shared by my nodes\n"
        "external_components:\n"
        "  - source:\n"
        "      type: git\n"
        f"      url: {url}\n"
        f"      ref: {ref}  # keep this comment\n"
    )


def _main(url: str, fw_ver: str) -> str:
    return (
        "substitutions:\n"
        f"  sensors_ver: {fw_ver}   # sensors' version\n"
        f"esphome:\n  name: host-node\n{OTA}"
        "packages:\n"
        f"  marsrelay: !include {_INCLUDED}\n"
        f"  wifi: !include {_UNTOUCHED}\n"
        "external_components:\n"
        "  - source:\n"
        "      type: git\n"
        f"      url: {url}\n"
        "      ref: ${sensors_ver}\n"
    )


@pytest.fixture
def sensors(tmp_path: Path) -> GitRepo:
    """A second repository, at v2.0 with v2.1 newer."""
    repo = GitRepo(tmp_path / "sensors")
    repo.tag("v2.0")
    repo.push()
    repo.tag("v2.1")
    return repo


@pytest.fixture
async def builder(marsrelay: GitRepo, sensors: GitRepo) -> AsyncIterator[FakeDeviceBuilder]:
    """A Device Builder whose config includes marsrelay at v1.4.0, which has v1.5.0,
    and takes the sensors' ref from a substitution."""
    marsrelay.tag("v1.4.0")
    marsrelay.push()
    marsrelay.tag("v1.5.0")
    async with serve(
        [server_info(esphome_version="2026.10.1")],
        {
            CONFIGURATION: _main(sensors.url, "v2.0"),
            _INCLUDED: _included(marsrelay.url, "v1.4.0"),
            _UNTOUCHED: "wifi:\n  ssid: home  # v1.4.0\n",
        },
    ) as (_, fake):
        yield fake


async def test_install_rewrites_an_included_file_and_a_single_use_substitution(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    sensors: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    untouched = builder.files[_UNTOUCHED]
    await set_up(hass, builder)
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_ON

    await install(hass, builder, "install_clean_success")

    assert sorted(builder.writes) == sorted([CONFIGURATION, _INCLUDED])
    assert builder.files == {
        CONFIGURATION: _main(sensors.url, "v2.1"),
        _INCLUDED: _included(marsrelay.url, "v1.5.0"),
        _UNTOUCHED: untouched,
    }
    await tick(hass, freezer, 30.1)
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == (
        "2026.10.1 (marsrelay v1.5.0, sensors v2.1)"
    )


async def test_install_rewrites_a_tag_ref_in_every_place_it_is_written(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A remote package and an external component from the same repository."""

    def main(ref: str) -> str:
        return (
            f"esphome:\n  name: host-node\n{OTA}"
            "packages:\n"
            f"  marsrelay: !include {_INCLUDED}\n"
            "  base:\n"
            f"    url: {marsrelay.url}\n"
            f"    ref: {ref}  # with the component\n"
            "    files: [base.yaml]\n"
        )

    builder.files = {
        CONFIGURATION: main("v1.4.0"),
        _INCLUDED: _included(marsrelay.url, "v1.4.0"),
    }
    await set_up(hass, builder)
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_ON

    await install(hass, builder, "install_clean_success")

    assert builder.files == {
        CONFIGURATION: main("v1.5.0"),
        _INCLUDED: _included(marsrelay.url, "v1.5.0"),
    }
    await tick(hass, freezer, 30.1)
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1 (marsrelay v1.5.0)"


async def test_a_substitution_also_used_as_the_project_version_is_untrackable(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    sensors: GitRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    builder.files[CONFIGURATION] = _main(sensors.url, "v2.0").replace(
        "  name: host-node\n",
        "  name: host-node\n  project:\n    name: me.node\n    version: ${sensors_ver}\n",
    )
    builder.files[_INCLUDED] = _included(marsrelay.url, "v1.5.0")

    with caplog.at_level(logging.WARNING):
        await set_up(hass, builder)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1 (marsrelay v1.5.0)"
    assert "substitution sensors_ver is also used elsewhere" in caplog.text
