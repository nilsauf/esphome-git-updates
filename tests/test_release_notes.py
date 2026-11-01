"""Release notes: what changed in each Source since it was installed.

A GitHub Source's remote is a real repository on disk that git reaches
through `url.<path>.insteadOf`, and the fake GitHub API compares its commits.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.esphome_git_updates import github
from fake_device_builder import FakeDeviceBuilder, serve, server_info
from fake_github import FakeGitHub, serve_github
from git_repo import GitRepo
from tracked_config import CONFIGURATION, ENTITY_ID, OTA, set_up, tick

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

GITHUB_URL = "https://github.com/nilsauf/marsrelay.git"


def _config(url: str) -> str:
    return (
        f"esphome:\n  name: host-node\n{OTA}external_components:\n"
        f"  - source:\n      type: git\n      url: {url}\n    refresh: always\n"
    )


@pytest.fixture
def on_github(marsrelay: GitRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Let git reach marsrelay at its GitHub URL."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{marsrelay.url}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", GITHUB_URL)


@pytest.fixture
async def fake_github(
    marsrelay: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[FakeGitHub]:
    async with serve_github({"nilsauf/marsrelay": marsrelay}) as fake:
        monkeypatch.setattr(github, "GITHUB_API", fake.url)
        yield fake


async def _builder(url: str) -> AsyncIterator[FakeDeviceBuilder]:
    async with serve(
        [server_info(esphome_version="2026.10.1")], {CONFIGURATION: _config(url)}
    ) as (_, fake):
        yield fake


@pytest.fixture
async def builder(on_github: None) -> AsyncIterator[FakeDeviceBuilder]:
    """A Device Builder whose config tracks marsrelay's main on GitHub."""
    async for fake in _builder(GITHUB_URL):
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
    fake_github: FakeGitHub,
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
    # Unchanged at setup, so only asked once, after the push.
    assert fake_github.authorizations == [None]


async def test_the_github_token_option_is_sent_with_the_compare_request(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
) -> None:
    await set_up(hass, builder, options={"github_token": "ghp_secret"})
    marsrelay.push()

    await tick(hass, freezer, 30.1)

    assert fake_github.authorizations == ["Bearer ghp_secret"]


async def test_a_failing_github_api_falls_back_to_the_sha_change_and_is_asked_again(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WebSocketGenerator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    installed = marsrelay.latest_commit()
    await set_up(hass, builder)
    latest = marsrelay.push(message="Fix debounce")
    fake_github.failure = 403

    await tick(hass, freezer, 30.1)

    assert _summary(hass) == f"marsrelay {installed[:7]} → {latest[:7]}"
    assert await _release_notes(hass, hass_ws_client) == (
        f"### marsrelay\n{installed[:7]} → {latest[:7]}"
    )
    assert "API rate limit exceeded" in caplog.text

    fake_github.failure = None
    notes = await _release_notes(hass, hass_ws_client)

    assert notes is not None and notes.endswith(") Fix debounce")
    assert _summary(hass) == f"marsrelay {installed[:7]} → {latest[:7]} (1 commit)"


async def test_github_that_cannot_be_reached_falls_back_to_the_sha_change(
    hass: HomeAssistant,
    builder: FakeDeviceBuilder,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WebSocketGenerator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing listens on port 1.
    monkeypatch.setattr(github, "GITHUB_API", "http://127.0.0.1:1")
    installed = marsrelay.latest_commit()
    await set_up(hass, builder)
    latest = marsrelay.push()

    await tick(hass, freezer, 30.1)

    assert await _release_notes(hass, hass_ws_client) == (
        f"### marsrelay\n{installed[:7]} → {latest[:7]}"
    )


async def test_a_remote_not_on_github_shows_the_sha_change_without_asking_github(
    hass: HomeAssistant,
    fake_github: FakeGitHub,
    marsrelay: GitRepo,
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WebSocketGenerator,
) -> None:
    installed = marsrelay.latest_commit()
    async for builder in _builder(marsrelay.url):
        await set_up(hass, builder)
        latest = marsrelay.push()

        await tick(hass, freezer, 30.1)

        assert _summary(hass) == f"marsrelay {installed[:7]} → {latest[:7]}"
        assert await _release_notes(hass, hass_ws_client) == (
            f"### marsrelay\n{installed[:7]} → {latest[:7]}"
        )
    assert fake_github.authorizations == []
