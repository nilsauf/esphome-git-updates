"""What the update entity shows: one version string across all of a Tracked Config's Sources."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .github import Comparison

# Home Assistant drops a longer release_summary.
_MAX_SUMMARY = 255

# An ESPHome version: `<year>.<month>.<patch>`, maybe with a suffix (`b1`, `-dev`).
_ESPHOME_VERSION = r"\d{4}\.\d{1,2}\.\d+[0-9A-Za-z.+-]*"
# `<project_version> (ESPHome <ver>)`, when the YAML sets `esphome: project:`.
_PROJECT_SW_VERSION = re.compile(rf"\S.* \(ESPHome (?P<esphome>{_ESPHOME_VERSION})\)")
# `<ver> (config hash 0x…)` over MQTT, `<ver> (<compile time>)` or `<ver>` over the native API.
_PLAIN_SW_VERSION = re.compile(rf"(?P<esphome>{_ESPHOME_VERSION})(?: \(.*\))?")


def parse_sw_version(sw_version: str | None) -> str | None:
    """The ESPHome version in a Linked Device's registry `sw_version`, or None (ADR-0003)."""
    if not sw_version:
        return None
    for pattern in (_PROJECT_SW_VERSION, _PLAIN_SW_VERSION):
        if (match := pattern.fullmatch(sw_version.strip())) is not None:
            return match.group("esphome")
    return None


@dataclass(frozen=True)
class SourceCommits:
    """A tracked Git Source's Installed and Latest Commit."""

    name: str
    installed: str | None
    latest: str
    # The commits from installed to latest, for a GitHub Source whose compare succeeded.
    comparison: Comparison | None = None

    @property
    def changed(self) -> bool:
        return self.installed != self.latest

    @property
    def commit_change(self) -> str:
        """`<installed> → <latest>`, short, with the commit count when known."""
        change = f"{_short(self.installed or '')} → {_short(self.latest)}"
        if self.comparison is None:
            return change
        count = self.comparison.total_commits
        return f"{change} ({count} commit{'' if count == 1 else 's'})"


@dataclass(frozen=True)
class Versions:
    """Every Source of a Tracked Config, installed against latest.

    "Newer" means "differs", so comparing the composite strings is enough:
    they differ exactly when some Source differs.
    """

    installed_esphome: str | None
    available_esphome: str | None
    git_sources: tuple[SourceCommits, ...]

    @property
    def installed(self) -> str | None:
        """Unknown when any Source's installed version is."""
        if self.installed_esphome is None or any(
            source.installed is None for source in self.git_sources
        ):
            return None
        return _composite(
            self.installed_esphome,
            [(source.name, source.installed or "") for source in self.git_sources],
        )

    @property
    def latest(self) -> str | None:
        if self.available_esphome is None:
            return None
        return _composite(
            self.available_esphome,
            [(source.name, source.latest) for source in self.git_sources],
        )

    @property
    def _esphome_changed(self) -> bool:
        return (
            self.installed_esphome is not None
            and self.available_esphome is not None
            and self.installed_esphome != self.available_esphome
        )

    @property
    def release_summary(self) -> str | None:
        """One `<source> <installed> → <latest>` line per changed Source."""
        lines = []
        if self._esphome_changed:
            lines.append(f"ESPHome {self.installed_esphome} → {self.available_esphome}")
        lines.extend(
            f"{source.name} {source.commit_change}"
            for source in self.git_sources
            if source.installed is not None and source.changed
        )
        if not lines:
            return None
        summary = "; ".join(lines)
        return summary if len(summary) <= _MAX_SUMMARY else summary[: _MAX_SUMMARY - 1] + "…"

    @property
    def release_notes(self) -> str | None:
        """A Markdown section per changed Source; a compared one lists its commits."""
        sections = []
        if self._esphome_changed:
            sections.append(f"### ESPHome\n{self.installed_esphome} → {self.available_esphome}")
        sections.extend(
            f"### {source.name}\n{_source_notes(source)}"
            for source in self.git_sources
            if source.changed
        )
        return "\n\n".join(sections) or None


def _source_notes(source: SourceCommits) -> str:
    if source.installed is None:
        return f"Latest commit {_short(source.latest)}"
    if source.comparison is None:
        return source.commit_change
    lines = [
        f"- [`{_short(commit.sha)}`]({commit.url}) {commit.title}"
        for commit in source.comparison.commits
    ]
    if (unlisted := source.comparison.total_commits - len(source.comparison.commits)) > 0:
        lines.append(f"- … and {unlisted} more")
    return f"{source.commit_change}\n\n" + "\n".join(lines)


def _composite(esphome: str, commits: list[tuple[str, str]]) -> str:
    if not commits:
        return esphome
    return f"{esphome} ({', '.join(f'{name} {_short(sha)}' for name, sha in commits)})"


def _short(sha: str) -> str:
    return sha[:7]
