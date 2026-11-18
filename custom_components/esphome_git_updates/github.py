"""What GitHub's API knows of a repository: the commits between two shas, for release
notes, and its releases.

No Home Assistant imports: this asks GitHub's API, nothing more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import aiohttp

GITHUB_API = "https://api.github.com"

# GitHub that has not answered by then is taken to be unreachable.
COMPARE_TIMEOUT = 30

# A GitHub remote, over HTTPS or SSH: its owner and repository.
_GITHUB_REMOTE = re.compile(
    r"(?:https?://(?:[^@/]+@)?|ssh://git@|git@)github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?"
)


class GitHubError(Exception):
    """GitHub could not be asked, refused (as a rate limit does), or answered unexpectedly."""


@dataclass(frozen=True)
class Commit:
    sha: str
    message: str
    # The commit's page on GitHub.
    url: str

    @property
    def title(self) -> str:
        """The message's first line."""
        return self.message.split("\n", 1)[0]


@dataclass(frozen=True)
class Comparison:
    """The commits from one sha up to another, oldest first."""

    # GitHub lists at most 250 commits, but counts them all.
    total_commits: int
    commits: tuple[Commit, ...]


def github_repo(url: str) -> str | None:
    """`owner/repo` when the remote at *url* is on GitHub, else None."""
    match = _GITHUB_REMOTE.fullmatch(url)
    return None if match is None else f"{match.group(1)}/{match.group(2)}"


async def compare(
    session: aiohttp.ClientSession, repo: str, base: str, head: str, token: str | None
) -> Comparison:
    """The commits of *repo* (`owner/repo`) after *base* up to *head*.

    A *token* raises GitHub's rate limit; without one, GitHub allows 60
    requests an hour from an address.
    """
    url = f"{GITHUB_API}/repos/{repo}/compare/{base}...{head}"
    try:
        async with session.get(
            url, headers=_headers(token), timeout=aiohttp.ClientTimeout(total=COMPARE_TIMEOUT)
        ) as response:
            status = response.status
            body = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise GitHubError(f"cannot compare {repo} {base[:7]}...{head[:7]}: {err!r}") from err
    if status != 200:
        message = body.get("message") if isinstance(body, dict) else None
        raise GitHubError(f"GitHub refused to compare {repo}: HTTP {status}: {message}")
    try:
        return _comparison(body)
    except (KeyError, TypeError, ValueError) as err:
        raise GitHubError(f"unexpected compare answer for {repo}: {err!r}") from err


def _headers(token: str | None) -> dict[str, str]:
    """A *token* raises GitHub's rate limit; without one, GitHub allows 60 requests an
    hour from an address."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _comparison(body: Any) -> Comparison:
    commits = tuple(
        Commit(str(commit["sha"]), str(commit["commit"]["message"]), str(commit["html_url"]))
        for commit in body["commits"]
    )
    return Comparison(int(body["total_commits"]), commits)


@dataclass(frozen=True)
class Release:
    """A GitHub release, as GitHub lists it: drafts and prereleases included."""

    tag: str
    title: str
    body: str
    # The release's page on GitHub.
    url: str
    draft: bool
    prerelease: bool


async def releases(
    session: aiohttp.ClientSession, repo: str, token: str | None
) -> tuple[Release, ...]:
    """The releases of *repo* (`owner/repo`), newest published first; empty when it has none.

    Only GitHub's first page is asked for, the 100 newest releases. A refusal,
    a rate limit, a 404 or a timeout raise GitHubError: GitHub could not be
    asked, which is not the same as having no releases.
    """
    url = f"{GITHUB_API}/repos/{repo}/releases?per_page=100"
    try:
        async with session.get(
            url, headers=_headers(token), timeout=aiohttp.ClientTimeout(total=COMPARE_TIMEOUT)
        ) as response:
            status = response.status
            body = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise GitHubError(f"cannot list the releases of {repo}: {err!r}") from err
    if status != 200:
        message = body.get("message") if isinstance(body, dict) else None
        raise GitHubError(
            f"GitHub refused to list the releases of {repo}: HTTP {status}: {message}"
        )
    try:
        return tuple(_release(release) for release in body)
    except (KeyError, TypeError, ValueError) as err:
        raise GitHubError(f"unexpected releases answer for {repo}: {err!r}") from err


def _release(release: Any) -> Release:
    tag = str(release["tag_name"])
    return Release(
        tag=tag,
        title=str(release.get("name") or tag),
        body=str(release.get("body") or ""),
        url=str(release["html_url"]),
        draft=bool(release["draft"]),
        prerelease=bool(release["prerelease"]),
    )
