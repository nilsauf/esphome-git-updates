"""The commits each changed Git Source on GitHub gained since its Installed Commit.

For release notes only: GitHub that cannot be asked fails nothing, and the
release notes show only the commit change instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import NamedTuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .github import Comparison, GitHubError, compare, github_repo
from .options import Options

_LOGGER = logging.getLogger(__name__)


class SourceChange(NamedTuple):
    """A Git Source whose Latest Commit differs from its Installed Commit."""

    key: str
    url: str
    installed: str
    latest: str


@dataclass(frozen=True)
class _CachedComparison:
    installed: str
    latest: str
    comparison: Comparison


class GitHubComparisons:
    """Asks GitHub's compare API once per change, and again only after a failure."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._cached: dict[str, _CachedComparison] = {}
        self._failing = False

    def get(self, key: str, installed: str | None, latest: str) -> Comparison | None:
        """The commits from *installed* to *latest* of the Source with *key*, if GitHub
        listed them."""
        cached = self._cached.get(key)
        if cached is None or (cached.installed, cached.latest) != (installed, latest):
            return None
        return cached.comparison

    async def async_update(self, changes: Iterable[SourceChange]) -> bool:
        """Compare each change on GitHub not compared yet; return whether any was.

        Only the comparisons of *changes* are kept, so a change is compared
        once, and one that failed is tried again on the next update.
        """
        kept: dict[str, _CachedComparison] = {}
        wanted: list[tuple[SourceChange, str]] = []
        for change in changes:
            if (repo := github_repo(change.url)) is None:
                continue
            if self.get(change.key, change.installed, change.latest) is not None:
                kept[change.key] = self._cached[change.key]
            else:
                wanted.append((change, repo))
        session = async_get_clientsession(self._hass)
        token = Options.of(self._entry.options).github_token
        results = await asyncio.gather(
            *(
                compare(session, repo, change.installed, change.latest, token)
                for change, repo in wanted
            ),
            return_exceptions=True,
        )
        failed = False
        for (change, _), result in zip(wanted, results, strict=True):
            if isinstance(result, GitHubError):
                failed = True
                self._log_failure(result)
            elif isinstance(result, BaseException):
                raise result
            else:
                kept[change.key] = _CachedComparison(change.installed, change.latest, result)
        if wanted and not failed and self._failing:
            _LOGGER.info("%s: GitHub answers again", self._entry.title)
            self._failing = False
        self._cached = kept
        return any(change.key in kept for change, _ in wanted)

    def _log_failure(self, err: GitHubError) -> None:
        if not self._failing:
            _LOGGER.warning(
                "%s: release notes show only commit changes while GitHub cannot be asked: %s",
                self._entry.title,
                err,
            )
        self._failing = True
