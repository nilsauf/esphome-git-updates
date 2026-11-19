"""The GitHub adapter, asked over a real HTTP session against the fake GitHub: no Home
Assistant."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
import pytest

from custom_components.esphome_git_updates import github
from custom_components.esphome_git_updates.github import GitHub, GitHubError
from fake_github import GITHUB_URL, FakeGitHub
from git_repo import GitRepo

REPO = "nilsauf/marsrelay"
TITLE = "Mars Relay"


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session


def _github(session: aiohttp.ClientSession, token: str | None = None) -> GitHub:
    return GitHub(session, lambda: token, lambda: TITLE)


async def test_releases_are_asked_for_one_page_when_it_holds_a_release(
    session: aiohttp.ClientSession, fake_github: FakeGitHub
) -> None:
    fake_github.release(REPO, "v1.4.0", title="Four")
    for build in range(150):
        fake_github.release(REPO, f"v1.5.0-nightly{build}", prerelease=True)
    fake_github.release(REPO, "v1.5.0", title="Five")

    releases = await _github(session).releases(GITHUB_URL)

    assert [release.tag for release in releases] == ["v1.5.0"]
    assert fake_github.releases_pages == [1]


async def test_a_full_page_without_a_release_is_followed_by_the_next_page(
    session: aiohttp.ClientSession, fake_github: FakeGitHub
) -> None:
    fake_github.release(REPO, "v1.5.0", title="Five")
    # GitHub's first page of 100 holds only nightly prereleases and drafts.
    for build in range(99):
        fake_github.release(REPO, f"v1.6.0-nightly{build}", prerelease=True)
    fake_github.release(REPO, "v1.6.0", draft=True)

    releases = await _github(session).releases(GITHUB_URL)

    assert [release.tag for release in releases] == ["v1.5.0"]
    assert fake_github.releases_pages == [1, 2]


async def test_pages_without_a_release_are_asked_for_at_most_five_times_and_warned_about_once(
    session: aiohttp.ClientSession,
    fake_github: FakeGitHub,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_github.release(REPO, "v1.4.0", title="Four")
    for build in range(500):
        fake_github.release(REPO, f"v1.5.0-nightly{build}", prerelease=True)
    github = _github(session)

    assert await github.releases(GITHUB_URL) == ()
    assert fake_github.releases_pages == [1, 2, 3, 4, 5]
    assert caplog.text.count("all drafts or prereleases") == 1
    assert TITLE in caplog.text

    assert await github.releases(GITHUB_URL) == ()
    assert caplog.text.count("all drafts or prereleases") == 1


async def test_drafts_prereleases_and_tags_that_are_no_version_are_no_releases(
    session: aiohttp.ClientSession, fake_github: FakeGitHub
) -> None:
    fake_github.release(REPO, "v1.4.0", title="Four", body="The fourth.")
    fake_github.release(REPO, "nightly")
    fake_github.release(REPO, "v1.5.0-rc1")
    fake_github.release(REPO, "v1.5.0", prerelease=True)
    fake_github.release(REPO, "v1.6.0", draft=True)

    releases = await _github(session).releases(GITHUB_URL)

    assert [(release.tag, release.title, release.body) for release in releases] == [
        ("v1.4.0", "Four", "The fourth.")
    ]


async def test_compare_lists_the_commits_oldest_first_with_their_total(
    session: aiohttp.ClientSession, fake_github: FakeGitHub, marsrelay: GitRepo
) -> None:
    base = marsrelay.latest_commit()
    first = marsrelay.push(message="Add the relay\n\nIt switches.")
    head = marsrelay.push(message="Fix the relay")

    comparison = await _github(session).compare(GITHUB_URL, base, head)

    assert comparison is not None
    assert comparison.total_commits == 2
    assert [(commit.sha, commit.title) for commit in comparison.commits] == [
        (first, "Add the relay"),
        (head, "Fix the relay"),
    ]
    assert comparison.commits[0].url == f"https://github.com/{REPO}/commit/{first}"


async def test_the_token_read_at_each_request_is_sent(
    session: aiohttp.ClientSession, fake_github: FakeGitHub, marsrelay: GitRepo
) -> None:
    tokens: list[str | None] = [None]
    adapter = GitHub(session, lambda: tokens[-1], lambda: TITLE)
    commit = marsrelay.latest_commit()

    await adapter.releases(GITHUB_URL)
    await adapter.compare(GITHUB_URL, commit, commit)
    tokens.append("first-token")
    await adapter.releases(GITHUB_URL)
    await adapter.compare(GITHUB_URL, commit, commit)
    tokens.append("second-token")
    await adapter.releases(GITHUB_URL)
    await adapter.compare(GITHUB_URL, commit, commit)

    expected = [None, "Bearer first-token", "Bearer second-token"]
    assert fake_github.releases_authorizations == expected
    assert fake_github.authorizations == expected


async def test_a_url_not_on_github_is_never_asked_about(
    session: aiohttp.ClientSession, fake_github: FakeGitHub
) -> None:
    adapter = _github(session)
    url = "https://gitlab.com/nilsauf/marsrelay.git"

    assert await adapter.releases(url) == ()
    assert await adapter.compare(url, "a" * 40, "b" * 40) is None
    assert fake_github.releases_pages == []
    assert fake_github.authorizations == []


@pytest.mark.parametrize("status", [403, 429, 404, 500])
async def test_a_refusal_is_a_github_error(
    session: aiohttp.ClientSession, fake_github: FakeGitHub, marsrelay: GitRepo, status: int
) -> None:
    adapter = _github(session)
    commit = marsrelay.latest_commit()
    fake_github.failure = status

    with pytest.raises(GitHubError, match=f"HTTP {status}"):
        await adapter.releases(GITHUB_URL)
    with pytest.raises(GitHubError, match=f"HTTP {status}"):
        await adapter.compare(GITHUB_URL, commit, commit)


async def test_an_unreachable_github_is_a_github_error(
    session: aiohttp.ClientSession, unused_tcp_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(github, "GITHUB_API", f"http://127.0.0.1:{unused_tcp_port}")
    adapter = _github(session)

    with pytest.raises(GitHubError):
        await adapter.releases(GITHUB_URL)
    with pytest.raises(GitHubError):
        await adapter.compare(GITHUB_URL, "a" * 40, "b" * 40)
