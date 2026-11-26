"""Release notes: what changed in each Source since it was installed, wired to GitHub.

A GitHub Source's remote is a real repository on disk that git reaches
through `url.<path>.insteadOf`, and the fake GitHub API compares its commits and
lists the Releases a test publishes. Paging, the token and GitHub's failures are
tested against the GitHub adapter in `test_github.py`; comparison caching and
keeping GitHub's last answer against `FollowedSources` in `test_following.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from fake_device_builder import FakeDeviceBuilder, serve, server_info
from fake_github import GITHUB_URL, FakeGitHub
from git_repo import GitRepo
from tracked_config import CONFIGURATION, ENTITY_ID, OTA, set_up, tick

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

REPO = "nilsauf/marsrelay"


def _config(ref: str | None = None) -> str:
    """A Tracked Config following marsrelay on GitHub at *ref* (None: its main)."""
    follow = f"  ref: {ref}\n" if ref is not None else "refresh: always\n"
    return (
        f"esphome:\n  name: host-node\n{OTA}external_components:\n"
        f"  - source:\n      type: git\n      url: {GITHUB_URL}\n    {follow}"
    )


@pytest.fixture
async def builder(on_github: None, fake_github: FakeGitHub) -> AsyncIterator[FakeDeviceBuilder]:
    """A Device Builder whose config tracks marsrelay's main on GitHub, which the fake
    GitHub answers for."""
    async with serve([server_info(esphome_version="2026.10.1")], {CONFIGURATION: _config()}) as (
        _,
        fake,
    ):
        yield fake


async def _release_notes(hass: HomeAssistant, ws: WebSocketGenerator) -> str | None:
    client = await ws(hass)
    await client.send_json_auto_id({"type": "update/release_notes", "entity_id": ENTITY_ID})
    answer: dict[str, Any] = await client.receive_json()
    assert answer["success"], answer
    notes: str | None = answer["result"]
    return notes


def _summary(hass: HomeAssistant) -> str | None:
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    summary: str | None = state.attributes["release_summary"]
    return summary


async def test_a_github_source_lists_its_commits_since_the_installed_one(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WebSocketGenerator,
) -> None:
    installed = marsrelay.latest_commit()
    await set_up(hass, builder)
    assert await _release_notes(hass, hass_ws_client) is None
    timer = marsrelay.push(message="Add relay timer\n\nLonger body.")
    debounce = marsrelay.push(message="Fix debounce")
    latest = marsrelay.push(message="Bump version")

    await tick(hass, freezer, 30.1)

    assert _summary(hass) == f"marsrelay {installed[:7]} → {latest[:7]} (3 commits)"
    commit_url = "https://github.com/nilsauf/marsrelay/commit/"
    assert await _release_notes(hass, hass_ws_client) == (
        f"### marsrelay\n{installed[:7]} → {latest[:7]} (3 commits)\n\n"
        f"- [`{timer[:7]}`]({commit_url}{timer}) Add relay timer\n"
        f"- [`{debounce[:7]}`]({commit_url}{debounce}) Fix debounce\n"
        f"- [`{latest[:7]}`]({commit_url}{latest}) Bump version"
    )


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
    builder.files[CONFIGURATION] = _config("v1.4.0")

    await set_up(hass, builder)

    # The Releases' text replaces the commit list.
    assert await _release_notes(hass, hass_ws_client) == (
        "### marsrelay\nv1.4.0 → v1.10.0 (1 commit)"
        "\n\n#### [Ten](https://github.com/nilsauf/marsrelay/releases/tag/v1.10.0)\n"
        "- Fix debounce\n\n"
        "#### [Five](https://github.com/nilsauf/marsrelay/releases/tag/v1.5.0)\n"
        "- Add relay"
    )


async def test_a_github_token_set_without_reloading_the_entry_is_sent_with_the_next_request(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = await set_up(hass, builder)
    marsrelay.push()
    await tick(hass, freezer, 30.1)
    assert fake_github.authorizations == [None]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "clean_build": False,
            "git_poll_interval": 30,
            "upload_address": "OTA",
            "github_token": "ghp_secret",
        },
    )
    await hass.async_block_till_done()
    marsrelay.push()
    await tick(hass, freezer, 30.1)

    assert fake_github.authorizations == [None, "Bearer ghp_secret"]


async def test_a_github_token_saved_with_a_new_git_poll_interval_is_sent_with_the_poll_right_away(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = await set_up(hass, builder)
    marsrelay.push()
    await tick(hass, freezer, 30.1)
    assert fake_github.authorizations == [None]
    marsrelay.push()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "clean_build": False,
            "git_poll_interval": 5,
            "upload_address": "OTA",
            "github_token": "ghp_secret",
        },
    )
    await hass.async_block_till_done()

    assert fake_github.authorizations == [None, "Bearer ghp_secret"]


async def test_opening_release_notes_asks_github_again_for_a_missing_comparison(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WebSocketGenerator,
) -> None:
    installed = marsrelay.latest_commit()
    await set_up(hass, builder)
    latest = marsrelay.push(message="Fix debounce")
    fake_github.failure = 403
    await tick(hass, freezer, 30.1)
    assert await _release_notes(hass, hass_ws_client) == (
        f"### marsrelay\n{installed[:7]} → {latest[:7]}"
    )

    fake_github.failure = None
    notes = await _release_notes(hass, hass_ws_client)

    assert notes is not None and notes.endswith(") Fix debounce")
    # The entity is refreshed with the comparison that arrived.
    assert _summary(hass) == f"marsrelay {installed[:7]} → {latest[:7]} (1 commit)"
