"""Version Tags: which tag names read as a version, and how those versions order.

No Home Assistant imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A Version Tag: an optional `v`/`V`, then one to four dot-separated integers.
_VERSION_TAG = re.compile(r"[vV]?([0-9]+(?:\.[0-9]+){0,3})")
# Versions are compared padded with zeros to this many parts, so `v1.2` equals `1.2.0`.
_VERSION_PARTS = 4


@dataclass(frozen=True, order=True)
class Version:
    """The version a Version Tag names, ordered by number, never by date."""

    parts: tuple[int, ...]

    @classmethod
    def of(cls, tag: str) -> Version | None:
        """The version *tag* names, or None if it is no Version Tag."""
        match = _VERSION_TAG.fullmatch(tag)
        if match is None:
            return None
        parts = tuple(int(part) for part in match.group(1).split("."))
        return cls(parts + (0,) * (_VERSION_PARTS - len(parts)))

    @classmethod
    def key(cls, tag: str) -> Version:
        """*tag*'s version for ordering tags; a tag that is no Version Tag sorts lowest."""
        return cls.of(tag) or _LOWEST


_LOWEST = Version(())


def is_version_tag(tag: str) -> bool:
    return Version.of(tag) is not None
