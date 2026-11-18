"""The Releases of each Git Source on GitHub that follows versions.

GitHub that cannot be asked changes nothing: the last answer is kept, so a
Source does not flip between following Releases and Version Tags.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .git_sources import version_of
from .github import GitHubError, Release, github_repo, releases
from .options import Options

_LOGGER = logging.getLogger(__name__)


class GitHubReleases:
    """Asks GitHub for the Releases of each repository on every poll, keeping the last
    answer while GitHub cannot be asked."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        # Each repository's Releases at GitHub's last answer, by url.
        self._answers: dict[str, tuple[Release, ...]] = {}
        self._failing: set[str] = set()

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
            *(releases(session, repo, token) for repo in wanted.values()),
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
                answers[url] = tuple(release for release in result if _is_release(release))
        self._answers = answers
        self._failing &= set(wanted)

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
