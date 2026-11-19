"""The Releases of a Git Source's repository on GitHub: the Releases port of
`FollowedSources`, which keeps GitHub's last answer while it cannot be asked.
"""

from __future__ import annotations

import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .github import RELEASES_PER_PAGE, Release, github_repo, releases
from .options import Options
from .version_tags import is_version_tag

_LOGGER = logging.getLogger(__name__)

# Pages of releases asked for at most, while each holds drafts and prereleases only.
_MAX_PAGES = 5


class GitHubReleases:
    """Asks GitHub for the Releases of a repository, with the entry's token if any."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        # Repositories whose pages asked for held no Release, warned about once each.
        self._unpaged: set[str] = set()

    async def releases(self, url: str) -> tuple[Release, ...]:
        """The Releases of the repository at *url*: empty when it has none or is not on
        GitHub. Raises GitHubError if GitHub cannot be asked."""
        if (repo := github_repo(url)) is None:
            return ()
        session = async_get_clientsession(self._hass)
        token = Options.of(self._entry.options).github_token
        return await self._releases(session, url, repo, token)

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


def _is_release(release: Release) -> bool:
    """A Release is neither a draft nor a prerelease, and its tag is a Version Tag."""
    return not release.draft and not release.prerelease and is_version_tag(release.tag)
