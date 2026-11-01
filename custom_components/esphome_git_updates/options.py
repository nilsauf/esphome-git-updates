"""The options of a Tracked Config: how it is installed and polled, and its release notes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from .device_builder import OTA

CONF_CLEAN_BUILD: Final = "clean_build"
CONF_GIT_POLL_INTERVAL: Final = "git_poll_interval"
CONF_GITHUB_TOKEN: Final = "github_token"
CONF_UPLOAD_ADDRESS: Final = "upload_address"

DEFAULT_CLEAN_BUILD: Final = False
# Minutes.
DEFAULT_GIT_POLL_INTERVAL: Final = 30
DEFAULT_UPLOAD_ADDRESS: Final = OTA


@dataclass(frozen=True)
class Options:
    """A Tracked Config's options, with the defaults for those never set."""

    # Run `firmware/clean` before compiling (ADR-0004: it does not re-fetch Git Sources).
    clean_build: bool
    git_poll_interval: timedelta
    # Where the firmware is uploaded: the `port` of `firmware/install`.
    upload_address: str
    # Used only to ask GitHub for release notes, at a higher rate limit.
    github_token: str | None

    @classmethod
    def of(cls, options: Mapping[str, Any]) -> Options:
        """Read an entry's *options*."""
        return cls(
            clean_build=options.get(CONF_CLEAN_BUILD, DEFAULT_CLEAN_BUILD),
            git_poll_interval=timedelta(
                minutes=options.get(CONF_GIT_POLL_INTERVAL, DEFAULT_GIT_POLL_INTERVAL)
            ),
            upload_address=options.get(CONF_UPLOAD_ADDRESS) or DEFAULT_UPLOAD_ADDRESS,
            github_token=options.get(CONF_GITHUB_TOKEN) or None,
        )
