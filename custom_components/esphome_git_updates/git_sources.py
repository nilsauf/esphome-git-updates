"""Discover a Tracked Config's Git Sources from its raw YAML (ADR-0006).

This module has no Home Assistant imports. Files are read through a callable,
which the integration backs with the Device Builder's `devices/get_config`.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

import yaml

# Reads one file under the Device Builder's config directory.
Reader = Callable[[str], Awaitable[str]]

# ESPHome's `github://owner/repo[/path][@ref]` shorthand.
_GITHUB_SHORTHAND = re.compile(r"github://([^/@]+)/([^/@]+)(/[^@]*)?(?:@(.+))?")

# A ref naming a commit, full or abbreviated: a Pinned Ref.
_COMMIT_SHA = re.compile(r"[0-9a-f]{7,40}")

# Includes nested deeper than this are taken to be a cycle.
_MAX_INCLUDE_DEPTH = 20

# ESPHome's substitution syntax: `$name` or `${name}`.
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


@dataclass(frozen=True)
class GitSource:
    """An `external_components` or remote `packages:` entry that points at git."""

    name: str
    url: str
    # None: the remote's default branch.
    ref: str | None

    @property
    def key(self) -> str:
        """What identifies the Source: its remote and ref, not its display name."""
        return f"{self.url}@{self.ref or 'HEAD'}"


@dataclass(frozen=True)
class Discovery:
    sources: tuple[GitSource, ...]
    # One line per Git Source that could not be tracked, saying why.
    untrackable: tuple[str, ...]


@dataclass(frozen=True)
class _Secret:
    """A `!secret` value. `secrets.yaml` is never read, so its value stays unknown."""

    name: str


@dataclass(frozen=True)
class _Include:
    """An `!include`, resolved after loading because files are read asynchronously."""

    file: str
    vars: dict[str, Any]


@dataclass(frozen=True)
class _Tagged:
    """Any other ESPHome tag (`!lambda`, `!extend`, `!include_dir_named`, ...), kept opaque."""

    tag: str
    value: Any

    def __str__(self) -> str:
        return f"{self.tag} {self.value}"


# A merge key whose value is an `!include` is kept as this key until the
# include is read, then merged in.
_MERGE_KEY = "<<"

# Substitutions that refer to other substitutions are expanded this many times at most.
_MAX_SUBSTITUTION_DEPTH = 10


class _Loader(yaml.SafeLoader):
    """A loader that accepts ESPHome's custom tags."""

    def flatten_mapping(self, node: yaml.MappingNode) -> None:
        # PyYAML can only merge mappings it has already loaded; an included
        # one is read later, so keep `<<: !include` as a plain key for now.
        for key_node, value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge" and value_node.tag == "!include":
                key_node.tag = "tag:yaml.org,2002:str"
        super().flatten_mapping(node)


def _construct_secret(loader: _Loader, node: yaml.Node) -> _Secret:
    return _Secret(str(loader.construct_scalar(node)))  # type: ignore[arg-type]


def _construct_include(loader: _Loader, node: yaml.Node) -> _Include:
    if isinstance(node, yaml.MappingNode):
        spec = loader.construct_mapping(node, deep=True)
        return _Include(str(spec["file"]), dict(spec.get("vars") or {}))
    return _Include(str(loader.construct_scalar(node)), {})  # type: ignore[arg-type]


def _construct_tagged(loader: _Loader, suffix: str, node: yaml.Node) -> _Tagged:
    value: Any
    if isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node, deep=True)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_scalar(node)  # type: ignore[arg-type]
    return _Tagged(f"!{suffix}", value)


_Loader.add_constructor("!secret", _construct_secret)
_Loader.add_constructor("!include", _construct_include)
_Loader.add_multi_constructor("!", _construct_tagged)


class DiscoveryError(Exception):
    """The YAML could not be parsed or its includes form a cycle."""


async def discover_git_sources(configuration: str, read: Reader) -> Discovery:
    """Find the Git Sources of *configuration* (a file name the reader understands)."""
    config = await _load(configuration, read, 0)
    gathered = _Gathered()
    gathered.add(config)
    sources: list[GitSource] = []
    untrackable = list(gathered.untrackable)
    for where, raw in gathered.candidates:
        value = _substitute(raw, gathered.substitutions)
        secret = _find_secret(value)
        if secret is not None:
            untrackable.append(f"{where} source depends on !secret {secret.name}: {value!r}")
            continue
        source = _parse_source(value)
        if source is not None and not _is_pinned_to_a_commit(source):
            sources.append(source)
    return Discovery(_distinct(sources), tuple(untrackable))


def _distinct(sources: list[GitSource]) -> tuple[GitSource, ...]:
    """*sources* without duplicates, with a ref added to names that would be ambiguous."""
    unique: dict[str, GitSource] = {}
    for source in sources:
        unique.setdefault(source.key, source)
    names = Counter(source.name for source in unique.values())
    return tuple(
        replace(source, name=f"{source.name}@{source.ref or 'HEAD'}")
        if names[source.name] > 1
        else source
        for source in unique.values()
    )


@dataclass
class _Gathered:
    """The possible Git Sources of a config and of its local packages."""

    # (where it was declared, the raw source or remote package)
    candidates: list[tuple[str, Any]] = field(default_factory=list)
    substitutions: dict[str, Any] = field(default_factory=dict)
    untrackable: list[str] = field(default_factory=list)

    def add(self, config: Any) -> None:
        """Gather from *config*, then from its local packages.

        Substitutions come from the config and its packages; the outermost
        definition wins, as in ESPHome, where the main config overrides packages.
        """
        if not isinstance(config, dict):
            return
        substitutions = config.get("substitutions")
        if isinstance(substitutions, dict):
            for name, value in substitutions.items():
                self.substitutions.setdefault(str(name), value)
        # In the order they appear, so Sources are listed as the YAML reads.
        for key, value in config.items():
            if key == "external_components":
                for entry in self._items(key, value):
                    if isinstance(entry, dict):
                        self.candidates.append((key, entry.get("source")))
            elif key == "packages":
                for package in self._items(key, value):
                    self._add_package(package)

    def _add_package(self, package: Any) -> None:
        if isinstance(package, str):
            # Only remote packages are strings: the `github://` shorthand.
            self.candidates.append(("packages", package))
        elif isinstance(package, dict) and "url" in package:
            # A remote package is tracked itself, but its files are not read (ADR-0006).
            self.candidates.append(("packages", {**package, "type": "git"}))
        else:
            self.add(package)

    def _items(self, where: str, value: Any) -> list[Any]:
        """The entries of a YAML list, or the values of a mapping (as `packages:` allows)."""
        if isinstance(value, dict):
            items = list(value.values())
        elif isinstance(value, list):
            items = value
        else:
            items = [] if value is None else [value]
        for item in items:
            if isinstance(item, _Tagged) and item.tag.startswith("!include_dir"):
                # A directory cannot be listed over the Device Builder's API.
                self.untrackable.append(f"{where}: cannot follow {item}")
        return [item for item in items if not isinstance(item, _Tagged)]


async def _load(path: str, read: Reader, depth: int) -> Any:
    """Load the YAML file at *path*, with its includes resolved."""
    if depth > _MAX_INCLUDE_DEPTH:
        raise DiscoveryError(f"includes nested too deeply at {path}")
    try:
        document = yaml.load(await read(path), _Loader)
    except yaml.YAMLError as err:
        raise DiscoveryError(f"cannot parse {path}: {err}") from err
    return await _resolve_includes(document, posixpath.dirname(path), read, depth)


async def _resolve_includes(value: Any, directory: str, read: Reader, depth: int) -> Any:
    if isinstance(value, _Include):
        path = posixpath.normpath(posixpath.join(directory, value.file))
        included = await _load(path, read, depth + 1)
        # An include's vars are substituted into the included content only.
        return _substitute(included, value.vars) if value.vars else included
    if isinstance(value, dict):
        resolved = {
            key: await _resolve_includes(item, directory, read, depth)
            for key, item in value.items()
        }
        merged = resolved.pop(_MERGE_KEY, None)
        # As with a YAML merge key, the mapping's own keys win.
        return {**merged, **resolved} if isinstance(merged, dict) else resolved
    if isinstance(value, list):
        return [await _resolve_includes(item, directory, read, depth) for item in value]
    return value


def _substitute(value: Any, substitutions: dict[str, Any]) -> Any:
    """*value* with every known `$name` / `${name}` replaced; unknown ones stay.

    A string that takes in a `!secret` substitution becomes that secret: its
    value cannot be known.
    """
    if isinstance(value, str):
        secrets: list[_Secret] = []

        def replace(match: re.Match[str]) -> str:
            name = match.group(1) or match.group(2)
            if name not in substitutions:
                return match.group(0)
            if isinstance(substitutions[name], _Secret):
                secrets.append(substitutions[name])
            return str(substitutions[name])

        # A substitution may refer to another one.
        substituted = value
        for _ in range(_MAX_SUBSTITUTION_DEPTH):
            expanded = _VARIABLE.sub(replace, substituted)
            if expanded == substituted:
                break
            substituted = expanded
        return secrets[0] if secrets else substituted
    if isinstance(value, dict):
        return {key: _substitute(item, substitutions) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, substitutions) for item in value]
    return value


def _find_secret(value: Any) -> _Secret | None:
    if isinstance(value, _Secret):
        return value
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    for child in children:
        if (secret := _find_secret(child)) is not None:
            return secret
    return None


def _parse_source(value: Any) -> GitSource | None:
    if isinstance(value, str):
        match = _GITHUB_SHORTHAND.fullmatch(value)
        if match is None:
            return None
        owner, repo, _path, ref = match.groups()
        return GitSource(repo, f"https://github.com/{owner}/{repo}.git", ref)
    if isinstance(value, dict) and value.get("type") == "git":
        url = str(value["url"])
        ref = value.get("ref")
        return GitSource(_repo_name(url), url, None if ref is None else str(ref))
    return None


def _is_pinned_to_a_commit(source: GitSource) -> bool:
    return source.ref is not None and _COMMIT_SHA.fullmatch(source.ref) is not None


def _repo_name(url: str) -> str:
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return name.removesuffix(".git") or url
