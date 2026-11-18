"""A Git Source with no ref follows Releases, else Version Tags, else Latest Commit; an
Install that moves it to a version writes the ref into the YAML (ADR-0007).

Each Git Source's remote is a real repository on disk; on GitHub, git reaches
it at its GitHub URL and the fake GitHub API lists the Releases a test publishes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import issue_registry as ir

from custom_components.esphome_git_updates.const import DOMAIN
from fake_device_builder import FakeDeviceBuilder, serve, server_info
from fake_github import GITHUB_URL, FakeGitHub
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


async def test_an_unset_ref_without_versions_follows_commits_and_needs_refresh_always(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
) -> None:
    # A tag that is no Version Tag does not make it follow versions.
    marsrelay.tag("stable")
    seeded = marsrelay.latest_commit()
    await set_up(hass, builder)
    pushed = marsrelay.push()
    await tick(hass, freezer, 30.1)

    state = _state(hass)
    assert state.state == STATE_ON
    assert state.attributes["installed_version"] == f"2026.10.1 (marsrelay {seeded[:7]})"
    assert state.attributes["latest_version"] == f"2026.10.1 (marsrelay {pushed[:7]})"
    assert _refresh_policy_issue(issue_registry) is not None


async def test_a_first_version_tag_switches_to_versions_without_offering_it(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
) -> None:
    await set_up(hass, builder)
    assert _refresh_policy_issue(issue_registry) is not None

    marsrelay.push()
    marsrelay.tag("v1.4.0")
    await tick(hass, freezer, 30.1)

    state = _state(hass)
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1 (marsrelay v1.4.0)"
    assert _refresh_policy_issue(issue_registry) is None
    assert builder.files[CONFIGURATION] == _config(marsrelay.url)

    marsrelay.push()
    marsrelay.tag("v1.5.0")
    await tick(hass, freezer, 30.1)

    state = _state(hass)
    assert state.state == STATE_ON
    assert state.attributes["release_summary"] == "marsrelay v1.4.0 → v1.5.0"


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


async def test_an_install_without_a_newer_version_leaves_an_unset_ref_unwritten(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    hass_storage: dict[str, Any],
) -> None:
    marsrelay.tag("v1.4.0")
    # Only ESPHome has an update.
    hass_storage[f"{DOMAIN}.tracked"] = {
        "version": 1,
        "key": f"{DOMAIN}.tracked",
        "data": {"installed_esphome_version": "2026.10.0"},
    }
    await set_up(hass, builder)
    assert _state(hass).state == STATE_ON

    await install(hass, builder, "install_clean_success")

    assert builder.writes == []
    assert builder.files[CONFIGURATION] == _config(marsrelay.url)
    assert _state(hass).attributes["installed_version"] == "2026.10.1 (marsrelay v1.4.0)"


@pytest.fixture
async def github_builder(on_github: None) -> AsyncIterator[FakeDeviceBuilder]:
    """A Device Builder whose config follows marsrelay on GitHub by its shorthand."""
    async with serve(
        [server_info(esphome_version="2026.10.1")], {CONFIGURATION: _shorthand()}
    ) as (_, fake):
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


async def test_an_existing_unset_ref_with_releases_offers_nothing_until_the_next_release(
    hass: HomeAssistant,
    github_builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    # It followed commits before the upgrade, and was installed at an older one.
    installed = marsrelay.latest_commit()
    marsrelay.push()
    marsrelay.tag("v1.4.0")
    marsrelay.push()
    marsrelay.tag("v1.5.0")
    marsrelay.push()
    fake_github.release(REPO, "v1.4.0")
    fake_github.release(REPO, "v1.5.0")
    hass_storage[f"{DOMAIN}.tracked"] = {
        "version": 1,
        "key": f"{DOMAIN}.tracked",
        "data": {"installed_commits": {f"{GITHUB_URL}@HEAD": installed}},
    }

    await set_up(hass, github_builder)

    state = _state(hass)
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1 (marsrelay v1.5.0)"

    marsrelay.tag("v1.6.0")
    fake_github.release(REPO, "v1.6.0")
    await tick(hass, freezer, 30.1)
    assert _state(hass).attributes["release_summary"].startswith("marsrelay v1.5.0 → v1.6.0")


async def test_an_unset_ref_on_github_shows_no_version_until_github_answers(
    hass: HomeAssistant,
    github_builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    marsrelay.tag("v1.4.0")
    fake_github.failure = 403
    await set_up(hass, github_builder)
    assert _state(hass).state == STATE_OFF

    marsrelay.push()
    marsrelay.tag("v1.5.0")
    fake_github.failure = None
    await tick(hass, freezer, 30.1)

    # GitHub answered "no Releases" at last: it follows Version Tags from v1.5.0 on.
    state = _state(hass)
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1 (marsrelay v1.5.0)"


async def test_a_flow_style_unset_ref_keeps_following_commits_and_says_why(
    hass: HomeAssistant,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marsrelay.tag("v1.4.0")
    config = (
        f"esphome:\n  name: host-node\n{OTA}"
        f"external_components:\n  - source: {{type: git, url: {marsrelay.url}}}\n"
        "    refresh: always\n"
    )
    async with serve(
        [server_info(esphome_version="2026.10.1")], {CONFIGURATION: config}
    ) as (_, fake):
        await set_up(hass, fake)
        pushed = marsrelay.push()
        marsrelay.tag("v1.5.0")
        await tick(hass, freezer, 30.1)

        state = _state(hass)
        assert state.attributes["latest_version"] == f"2026.10.1 (marsrelay {pushed[:7]})"
        assert "cannot be tracked" in caplog.text
        assert "flow-style" in caplog.text

        await install(hass, fake, "install_clean_success")
        assert fake.files[CONFIGURATION] == config
