"""What GitHub's API knows of a repository: the commits between two shas, for release
notes, and its releases. `GitHub` asks both by a Git Source's url.

No Home Assistant imports: this asks GitHub's API, nothing more.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

from .version_tags import is_version_tag

_LOGGER = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# GitHub that has not answered by then is taken to be unreachable.
REQUEST_TIMEOUT = 30

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
    return await _get(
        session,
        f"/repos/{repo}/compare/{base}...{head}",
        token,
        f"compare {repo} {base[:7]}...{head[:7]}",
        _comparison,
    )


async def _get[T](
    session: aiohttp.ClientSession,
    path: str,
    token: str | None,
    asking: str,
    read: Callable[[Any], T],
) -> T:
    """GitHub's answer at *path*, read by *read*; GitHubError if GitHub cannot be
    asked, refuses, or answers what *read* cannot read. *asking* completes "cannot ..."."""
    try:
        async with session.get(
            f"{GITHUB_API}{path}",
            headers=_headers(token),
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as response:
            status = response.status
            body = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise GitHubError(f"cannot {asking}: {err!r}") from err
    if status != 200:
        message = body.get("message") if isinstance(body, dict) else None
        raise GitHubError(f"GitHub refused to {asking}: HTTP {status}: {message}")
    try:
        return read(body)
    except (KeyError, TypeError, ValueError) as err:
        raise GitHubError(f"unexpected answer when asked to {asking}: {err!r}") from err


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


# GitHub lists at most this many releases a page.
RELEASES_PER_PAGE = 100


async def releases(
    session: aiohttp.ClientSession, repo: str, token: str | None, page: int = 1
) -> tuple[Release, ...]:
    """One *page* of the releases of *repo* (`owner/repo`), newest published first:
    fewer than RELEASES_PER_PAGE on the last page, empty when it has none.

    A refusal, a rate limit, a 404 or a timeout raise GitHubError: GitHub could
    not be asked, which is not the same as having no releases.
    """
    return await _get(
        session,
        f"/repos/{repo}/releases?per_page={RELEASES_PER_PAGE}&page={page}",
        token,
        f"list the releases of {repo}",
        lambda body: tuple(_release(release) for release in body),
    )


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


# Pages of releases asked for at most, while each holds drafts and prereleases only.
_MAX_PAGES = 5


class GitHub:
    """Asks GitHub about the repository of a Git Source, by its url.

    It caches nothing: what to keep of GitHub's answers, and for how long, is
    the caller's to decide.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: Callable[[], str | None],
        title: Callable[[], str],
    ) -> None:
        """*token* is read at each request, so a token set later reaches the next one;
        *title* names the Tracked Config in the paging warning."""
        self._session = session
        self._token = token
        self._title = title
        # Repositories whose pages asked for held no Release, warned about once each.
        self._unpaged: set[str] = set()

    async def releases(self, url: str) -> tuple[Release, ...]:
        """The Releases of the repository at *url*, newest first: empty when it has none
        or is not on GitHub. Raises GitHubError if GitHub cannot be asked.

        The first page of 100 is enough unless it holds drafts and prereleases
        only; then the next is asked for, up to _MAX_PAGES, which keeps a
        repository full of nightly prereleases from eating the rate limit.
        """
        if (repo := github_repo(url)) is None:
            return ()
        for page in range(1, _MAX_PAGES + 1):
            listed = await releases(self._session, repo, self._token(), page)
            found = tuple(release for release in listed if _is_release(release))
            if found or len(listed) < RELEASES_PER_PAGE:
                self._unpaged.discard(url)
                return found
        if url in self._unpaged:
            return ()
        self._unpaged.add(url)
        _LOGGER.warning(
            "%s: GitHub's %d newest releases of %s are all drafts or prereleases; "
            "following its Version Tags",
            self._title(),
            _MAX_PAGES * RELEASES_PER_PAGE,
            url,
        )
        return ()

    async def compare(self, url: str, base: str, head: str) -> Comparison | None:
        """The commits of the repository at *url* after *base* up to *head*: None, without
        a request, when it is not on GitHub. Raises GitHubError if GitHub cannot be asked."""
        if (repo := github_repo(url)) is None:
            return None
        return await compare(self._session, repo, base, head, self._token())


def _is_release(release: Release) -> bool:
    """A Release is neither a draft nor a prerelease, and its tag is a Version Tag."""
    return not release.draft and not release.prerelease and is_version_tag(release.tag)
