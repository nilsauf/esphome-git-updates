"""The Releases of each Git Source on GitHub that follows versions.

GitHub that cannot be asked changes nothing: the last answer is kept, so a
Source does not flip between following Releases and Version Tags.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .git_sources import version_of
from .github import RELEASES_PER_PAGE, GitHubError, Release, github_repo, releases
from .options import Options

_LOGGER = logging.getLogger(__name__)

# Pages of releases asked for at most, while each holds drafts and prereleases only.
_MAX_PAGES = 5


class GitHubReleases:
    """Asks GitHub for the Releases of each repository on every poll, keeping the last
    answer while GitHub cannot be asked."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        # Each repository's Releases at GitHub's last answer, by url.
        self._answers: dict[str, tuple[Release, ...]] = {}
        self._failing: set[str] = set()
        # Repositories whose pages asked for held no Release, warned about once each.
        self._unpaged: set[str] = set()

    def get(self, url: str) -> tuple[Release, ...] | None:
        """The Releases of the repository at *url*: empty when it has none or is not on
        GitHub, None while GitHub has never answered."""
        if github_repo(url) is None:
            return ()
        return self._answers.get(url)

    async def async_update(self, urls: Iterable[str]) -> None:
        """Ask GitHub for the Releases of each repository at *urls*; forget any other."""
        wanted = {url: repo for url in urls if (repo := github_repo(url)) is not None}
        session = async_get_clientsession(self._hass)
        token = Options.of(self._entry.options).github_token
        results = await asyncio.gather(
            *(self._releases(session, url, repo, token) for url, repo in wanted.items()),
            return_exceptions=True,
        )
        answers = {url: self._answers[url] for url in wanted if url in self._answers}
        for url, result in zip(wanted, results, strict=True):
            if isinstance(result, GitHubError):
                self._log_failure(url, result)
            elif isinstance(result, BaseException):
                raise result
            else:
                self._log_recovery(url)
                answers[url] = result
        self._answers = answers
        self._failing &= set(wanted)

    async def _releases(
        self, session: aiohttp.ClientSession, url: str, repo: str, token: str | None
    ) -> tuple[Release, ...]:
        """The Releases of *repo* on GitHub's first page that holds any.

        The first page of 100 is enough unless it holds drafts and prereleases
        only; then the next is asked for, up to _MAX_PAGES, which keeps a
        repository full of nightly prereleases from eating the rate limit.
        """
        for page in range(1, _MAX_PAGES + 1):
            listed = await releases(session, repo, token, page)
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
            self._entry.title,
            _MAX_PAGES * RELEASES_PER_PAGE,
            url,
        )
        return ()

    def _log_failure(self, url: str, err: GitHubError) -> None:
        if url not in self._failing:
            _LOGGER.warning(
                "%s: cannot ask GitHub for the Releases of %s, keeping the last answer: %s",
                self._entry.title,
                url,
                err,
            )
        self._failing.add(url)

    def _log_recovery(self, url: str) -> None:
        if url in self._failing:
            _LOGGER.info("%s: GitHub lists the Releases of %s again", self._entry.title, url)
        self._failing.discard(url)


def _is_release(release: Release) -> bool:
    """A Release is neither a draft nor a prerelease, and its tag is a Version Tag."""
    return not release.draft and not release.prerelease and version_of(release.tag) is not None
