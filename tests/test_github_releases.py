"""A Tag Ref on GitHub follows the repository's Releases, and its Version Tags only once
GitHub answers that it has none.

marsrelay's remote is a real repository on disk that git reaches at its GitHub
URL, and the fake GitHub API lists the Releases a test publishes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from fake_device_builder import FakeDeviceBuilder, serve, server_info
from fake_github import GITHUB_URL, FakeGitHub
from git_repo import GitRepo
from tracked_config import CONFIGURATION, ENTITY_ID, OTA, install, set_up, tick

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

REPO = "nilsauf/marsrelay"


def _config_on_tag(tag: str, url: str = GITHUB_URL) -> str:
    return (
        f"esphome:\n  name: host-node\n{OTA}external_components:\n"
        f"  - source:\n      type: git\n      url: {url}\n      ref: {tag}\n"
    )


@pytest.fixture
async def builder(on_github: None) -> AsyncIterator[FakeDeviceBuilder]:
    """A Device Builder whose config follows marsrelay on GitHub from `v1.4.0`."""
    async with serve(
        [server_info(esphome_version="2026.10.1")], {CONFIGURATION: _config_on_tag("v1.4.0")}
    ) as (_, fake):
        yield fake


def _state(hass: HomeAssistant) -> State:
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    return state


async def _release_notes(hass: HomeAssistant, ws: WebSocketGenerator) -> str | None:
    client = await ws(hass)
    await client.send_json_auto_id({"type": "update/release_notes", "entity_id": ENTITY_ID})
    answer: dict[str, Any] = await client.receive_json()
    assert answer["success"], answer
    notes: str | None = answer["result"]
    return notes


async def test_a_tag_ref_follows_the_newest_release_not_newer_tags_or_prereleases(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
) -> None:
    marsrelay.tag("v1.4.0")
    marsrelay.push()
    marsrelay.tag("v1.5.0")
    marsrelay.push()
    marsrelay.tag("v1.6.0-rc1")
    marsrelay.push()
    # A Version Tag without a Release.
    marsrelay.tag("v1.7.0")
    fake_github.release(REPO, "v1.4.0")
    fake_github.release(REPO, "v1.5.0")
    fake_github.release(REPO, "v1.6.0-rc1", prerelease=True)

    await set_up(hass, builder)

    state = _state(hass)
    assert state.state == STATE_ON
    assert state.attributes["latest_version"] == "2026.10.1 (marsrelay v1.5.0)"


async def test_install_moves_the_tag_ref_to_the_newest_release_not_a_newer_tag(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    marsrelay.tag("v1.4.0")
    marsrelay.push()
    marsrelay.tag("v1.5.0")
    marsrelay.push()
    # A Version Tag without a Release.
    marsrelay.tag("v1.7.0")
    fake_github.release(REPO, "v1.4.0")
    fake_github.release(REPO, "v1.5.0")
    await set_up(hass, builder)

    await install(hass, builder, "install_clean_success")

    assert builder.files[CONFIGURATION] == _config_on_tag("v1.5.0")
    state = _state(hass)
    assert state.state == STATE_OFF
    assert state.attributes["installed_version"] == "2026.10.1 (marsrelay v1.5.0)"
    await tick(hass, freezer, 30.1)
    assert _state(hass).state == STATE_OFF


async def test_release_notes_show_each_newer_release_newest_first(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    hass_ws_client: WebSocketGenerator,
) -> None:
    for tag in ("v1.4.0", "v1.10.0", "v1.5.0", "v1.11.0"):
        marsrelay.push()
        marsrelay.tag(tag)
    fake_github.release(REPO, "v1.4.0", title="Installed", body="Not shown.")
    # Published after v1.10.0, but older.
    fake_github.release(REPO, "v1.10.0", title="Ten", body="- Fix debounce")
    fake_github.release(REPO, "v1.5.0", title="Five", body="- Add relay")
    fake_github.release(REPO, "v1.11.0", title="Eleven", body="Not released.", draft=True)
    fake_github.release(REPO, "test-foo", title="Test", body="Not a version.")

    await set_up(hass, builder)

    # The Releases' text replaces the commit list.
    assert await _release_notes(hass, hass_ws_client) == (
        "### marsrelay\nv1.4.0 → v1.10.0 (1 commit)"
        "\n\n#### [Ten](https://github.com/nilsauf/marsrelay/releases/tag/v1.10.0)\n"
        "- Fix debounce\n\n"
        "#### [Five](https://github.com/nilsauf/marsrelay/releases/tag/v1.5.0)\n"
        "- Add relay"
    )


async def test_a_repository_publishing_its_first_release_is_followed_by_releases_next_poll(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    marsrelay.tag("v1.4.0")
    marsrelay.push()
    marsrelay.tag("v1.5.0")
    marsrelay.push()
    marsrelay.tag("v1.6.0")
    await set_up(hass, builder)
    # No Releases: it follows the Version Tags.
    assert _state(hass).attributes["latest_version"] == "2026.10.1 (marsrelay v1.6.0)"

    fake_github.release(REPO, "v1.5.0")
    await tick(hass, freezer, 30.1)

    assert _state(hass).attributes["latest_version"] == "2026.10.1 (marsrelay v1.5.0)"


async def test_a_failing_github_keeps_the_last_answer_and_warns_once(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marsrelay.tag("v1.4.0")
    marsrelay.push()
    marsrelay.tag("v1.5.0")
    marsrelay.push()
    marsrelay.tag("v1.6.0")
    fake_github.release(REPO, "v1.5.0")
    await set_up(hass, builder)

    # Refused, rate-limited, gone or down: none of them is "no Releases".
    for failure in (403, 429, 404, 500):
        fake_github.failure = failure
        await tick(hass, freezer, 30.1)
        assert _state(hass).attributes["latest_version"] == "2026.10.1 (marsrelay v1.5.0)"

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelname == "WARNING" and "Releases" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert "API rate limit exceeded" in warnings[0]

    fake_github.failure = None
    await tick(hass, freezer, 30.1)

    assert [
        record.getMessage()
        for record in caplog.records
        if record.levelname == "INFO" and "Releases" in record.getMessage()
    ] == [f"host-node: GitHub lists the Releases of {GITHUB_URL} again"]


async def test_a_source_github_never_answered_shows_no_update_until_it_answers(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    marsrelay.tag("v1.4.0")
    marsrelay.push()
    marsrelay.tag("v1.5.0")
    fake_github.failure = 403
    await set_up(hass, builder)

    state = _state(hass)
    assert state.state == STATE_OFF
    assert state.attributes["latest_version"] == "2026.10.1 (marsrelay v1.4.0)"

    # It answers that there are no Releases: the Version Tags are followed.
    fake_github.failure = None
    await tick(hass, freezer, 30.1)

    state = _state(hass)
    assert state.state == STATE_ON
    assert state.attributes["latest_version"] == "2026.10.1 (marsrelay v1.5.0)"


async def test_the_github_token_option_is_sent_with_the_releases_request(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
) -> None:
    marsrelay.tag("v1.4.0")

    await set_up(hass, builder, options={"github_token": "ghp_secret"})

    assert fake_github.releases_authorizations == ["Bearer ghp_secret"]


async def test_a_tag_ref_off_github_follows_version_tags_without_asking_github(
    hass: HomeAssistant,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
) -> None:
    marsrelay.tag("v1.4.0")
    marsrelay.push()
    marsrelay.tag("v1.5.0")
    fake_github.release(REPO, "v1.4.0")
    async with serve(
        [server_info(esphome_version="2026.10.1")],
        {CONFIGURATION: _config_on_tag("v1.4.0", url=marsrelay.url)},
    ) as (_, builder):
        await set_up(hass, builder)

        assert _state(hass).attributes["latest_version"] == "2026.10.1 (marsrelay v1.5.0)"
    assert fake_github.releases_authorizations == []
