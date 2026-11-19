"""A Git Source with no ref follows Releases, else Version Tags, else Latest Commit; an
Install that moves it to a version writes the ref into the YAML (ADR-0007).

Each Git Source's remote is a real repository on disk; on GitHub, git reaches
it at its GitHub URL and the fake GitHub API lists the Releases a test publishes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import issue_registry as ir

from custom_components.esphome_git_updates.const import DOMAIN
from fake_device_builder import FakeDeviceBuilder, serve, server_info
from fake_github import FakeGitHub
from git_repo import GitRepo
from tracked_config import CONFIGURATION, ENTITY_ID, OTA, install, set_up, tick

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

REPO = "nilsauf/marsrelay"


def _config(url: str) -> str:
    """A Tracked Config with one external component from *url* with no ref and no
    Refresh Policy, with comments and spacing an Install must keep."""
    return (
        "# host-node\n"
        f"esphome:\n  name: host-node\n{OTA}"
        "external_components:\n"
        "  - source:\n"
        "      type: git\n"
        f"      url: {url}   # no ref\n"
        "    components: [marsrelay]\n"
    )


def _with_ref(url: str, tag: str) -> str:
    """`_config(url)` with only a `ref:` line inserted after its `url:`."""
    return _config(url).replace("# no ref\n", f"# no ref\n      ref: {tag}\n")


def _shorthand(ref: str = "") -> str:
    return (
        f"esphome:\n  name: host-node\n{OTA}"
        f'external_components:\n  - source: "github://{REPO}{ref}"  # marsrelay\n'
    )


@pytest.fixture
async def builder(marsrelay: GitRepo) -> AsyncIterator[FakeDeviceBuilder]:
    """A Device Builder whose config follows marsrelay with no ref."""
    async with serve(
        [server_info(esphome_version="2026.10.1")], {CONFIGURATION: _config(marsrelay.url)}
    ) as (_, fake):
        yield fake


def _state(hass: HomeAssistant) -> State:
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    return state


def _refresh_policy_issue(issue_registry: ir.IssueRegistry) -> ir.IssueEntry | None:
    return issue_registry.async_get_issue(DOMAIN, "refresh_policy_tracked")


async def test_install_inserts_the_ref_after_the_url_and_then_follows_it_as_a_tag_ref(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
) -> None:
    marsrelay.tag("v1.4.0")
    await set_up(hass, builder)
    marsrelay.push()
    marsrelay.tag("v1.5.0")
    await tick(hass, freezer, 30.1)
    assert _state(hass).state == STATE_ON

    await install(hass, builder, "install_clean_success")

    assert builder.files[CONFIGURATION] == _with_ref(marsrelay.url, "v1.5.0")
    state = _state(hass)
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1 (marsrelay v1.5.0)"
    await tick(hass, freezer, 30.1)
    assert _state(hass).state == STATE_OFF
    assert _refresh_policy_issue(issue_registry) is None

    marsrelay.push()
    marsrelay.tag("v1.6.0")
    await tick(hass, freezer, 30.1)
    assert _state(hass).attributes["release_summary"] == "marsrelay v1.5.0 → v1.6.0"


@pytest.fixture
async def github_builder(on_github: None) -> AsyncIterator[FakeDeviceBuilder]:
    """A Device Builder whose config follows marsrelay on GitHub by its shorthand."""
    async with serve([server_info(esphome_version="2026.10.1")], {CONFIGURATION: _shorthand()}) as (
        _,
        fake,
    ):
        yield fake


async def test_install_appends_the_release_to_a_shorthand(
    hass: HomeAssistant,
    github_builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    marsrelay.tag("v1.4.0")
    fake_github.release(REPO, "v1.4.0")
    await set_up(hass, github_builder)
    assert _state(hass).state == STATE_OFF
    marsrelay.push()
    marsrelay.tag("v1.5.0")
    fake_github.release(REPO, "v1.5.0")
    await tick(hass, freezer, 30.1)
    assert _state(hass).attributes["release_summary"] == "marsrelay v1.4.0 → v1.5.0 (1 commit)"

    await install(hass, github_builder, "install_clean_success")

    assert github_builder.files[CONFIGURATION] == _shorthand("@v1.5.0")
    state = _state(hass)
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1 (marsrelay v1.5.0)"
    await tick(hass, freezer, 30.1)
    assert _state(hass).state == STATE_OFF
