"""Discover a Tracked Config's Git Sources from its raw YAML (ADR-0006).

This module has no Home Assistant imports. Files are read through a callable,
which the integration backs with the Device Builder's `devices/get_config`.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, NamedTuple

import yaml
from yarl import URL

from .version_tags import is_version_tag

# Reads one file under the Device Builder's config directory.
Reader = Callable[[str], Awaitable[str]]

# ESPHome's `github://owner/repo[/path][@ref]` shorthand.
_GITHUB_SHORTHAND = re.compile(r"github://([^/@]+)/([^/@]+)(/[^@]*)?(?:@(.+))?")

# A ref naming a commit, full or abbreviated: a Pinned Ref.
_COMMIT_SHA = re.compile(r"[0-9a-f]{7,40}")

# A Refresh Policy of zero time: ESPHome fetches the Source on every build (ADR-0004).
_ZERO_REFRESH = re.compile(r"0+(\.0+)?\s*[a-z]*")

# Includes nested deeper than this are taken to be a cycle.
_MAX_INCLUDE_DEPTH = 20

# ESPHome's substitution syntax: `$name` or `${name}`.
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


class RefKind(Enum):
    """What a Git Source's ref says should be followed, as far as its name tells.

    Pinned Refs (commits) are never Git Sources of a Discovery. Only the
    remote can tell a branch from a tag, and a ref that is both is a branch:
    so a Tag Ref that turns out to be a branch is a Floating Ref after all.
    """

    # A branch, or a tag that is not a Version Tag.
    FLOATING = "floating"
    # A Version Tag.
    TAG = "tag"
    # No ref: the remote's default branch.
    UNSET = "unset"


@dataclass(frozen=True)
class RefLocation:
    """Where a Git Source's ref is written: a span of one file's text, which an
    Install rewrites to move the ref to another tag (ADR-0007)."""

    # The file, as the reader names it.
    file: str
    # The span of the ref's text in that file, quotes excluded; empty where an
    # Unset Ref has no ref yet, and one is inserted.
    start: int
    end: int
    # What an inserted ref is written after: a new `ref:` line, or a shorthand's `@`.
    prefix: str = ""
    # Whether the span is a whole unquoted YAML scalar, which a tag is written to
    # quoted if YAML would read it as something else, such as the number `1.10`.
    plain: bool = False


def rewritten(text: str, refs: Iterable[tuple[RefLocation, str]]) -> str:
    """*text* with each ref located in it replaced by its tag; every other byte is kept.

    Each location must be one found in *text*, read from that location's file.
    """
    for location, tag in sorted(refs, key=lambda edit: edit[0].start, reverse=True):
        written = _scalar(tag) if location.plain else tag
        text = text[: location.start] + location.prefix + written + text[location.end :]
    return text


# The key an inserted ref line starts with.
_REF_KEY = "ref: "


def _scalar(tag: str) -> str:
    """*tag* as a YAML scalar that reads back as that string: `1.10` is a number unquoted."""
    return tag if yaml.safe_load(tag) == tag else f'"{tag}"'


@dataclass(frozen=True)
class GitSource:
    """An `external_components` or remote `packages:` entry that points at git."""

    name: str
    url: str
    # None: the remote's default branch.
    ref: str | None
    # Each place the ref's text is written, all rewritten together; for an Unset
    # Ref, where one is inserted. Discovery reports a Tag Ref untrackable when its
    # text cannot be rewritten safely, so every Tag Ref it returns has one or more.
    written_at: tuple[RefLocation, ...] = field(default=(), compare=False)

    @property
    def kind(self) -> RefKind:
        if self.ref is None:
            return RefKind.UNSET
        return RefKind.TAG if is_version_tag(self.ref) else RefKind.FLOATING

    @property
    def key(self) -> str:
        """What identifies the Source: its remote and ref, not its display name."""
        return f"{self.url}@{self.ref or 'HEAD'}"


class OtaStatus(Enum):
    """Whether the YAML lets the device be updated over the air."""

    # An `ota:` entry with one of OTA_PLATFORMS.
    PRESENT = "present"
    MISSING = "missing"
    # None in the files read, but a remote package, which is never read, may add one.
    UNKNOWN = "unknown"


# The `ota:` platforms the Device Builder can upload through.
OTA_PLATFORMS = ("esphome", "web_server")


@dataclass(frozen=True)
class Discovery:
    sources: tuple[GitSource, ...]
    # One line per Git Source that could not be tracked, saying why.
    untrackable: tuple[str, ...]
    ota: OtaStatus
    # The Sources whose Refresh Policy lets ESPHome build a stale cached clone.
    wrong_refresh_policy: tuple[GitSource, ...]


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


class _Written(str):
    """A string scalar as written in a file, with where it is written."""

    location: RefLocation
    # For a `url:` key: where a `ref:` line would be inserted after its entry,
    # and whether its mapping is flow-style, where none can be inserted safely.
    ref_insertion: RefLocation | None = None
    in_flow_style: bool = False


class _FromVars(str):
    """A string an include's `vars:` were substituted into."""


class _Loader(yaml.SafeLoader):
    """A loader that accepts ESPHome's custom tags, and keeps where strings are written."""

    def __init__(self, text: str, path: str) -> None:
        super().__init__(text)
        self.path = path

    def flatten_mapping(self, node: yaml.MappingNode) -> None:
        # PyYAML can only merge mappings it has already loaded; an included
        # one is read later, so keep `<<: !include` as a plain key for now.
        for key_node, value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge" and value_node.tag == "!include":
                key_node.tag = "tag:yaml.org,2002:str"
        super().flatten_mapping(node)

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        mapping = super().construct_mapping(node, deep)
        for key_node, value_node in node.value:
            key = self.constructed_objects.get(key_node)
            if key == "url" and isinstance(key, _Written):
                key.in_flow_style = bool(node.flow_style)
                if not node.flow_style and value_node.end_mark.line == key_node.start_mark.line:
                    key.ref_insertion = self._line_after(
                        value_node.end_mark.index, key_node.start_mark.column
                    )
        return mapping

    def _line_after(self, index: int, indent: int) -> RefLocation:
        """Where a `ref:` line indented by *indent* goes after the line holding *index*."""
        text = self.buffer.rstrip("\0")
        end = text.find("\n", index)
        if end == -1:
            return RefLocation(
                self.path, len(text), len(text), f"\n{' ' * indent}{_REF_KEY}", plain=True
            )
        newline = "\n"
        if end > 0 and text[end - 1] == "\r":
            end, newline = end - 1, "\r\n"
        return RefLocation(self.path, end, end, f"{newline}{' ' * indent}{_REF_KEY}", plain=True)


def _construct_str(loader: _Loader, node: yaml.ScalarNode) -> str:
    """A string scalar, with where it is written if it is written as is."""
    value = str(loader.construct_scalar(node))
    start, end = node.start_mark.index, node.end_mark.index
    if node.style in ("'", '"'):
        start, end = start + 1, end - 1
    # Only a scalar written exactly as its value can be rewritten in place:
    # not one with escapes, or folded over several lines.
    if node.style not in (None, "'", '"') or loader.buffer[start:end] != value:
        return value
    written = _Written(value)
    written.location = RefLocation(loader.path, start, end, plain=node.style is None)
    return written


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


_Loader.add_constructor("tag:yaml.org,2002:str", _construct_str)
# Numbers are kept as their text, so that a ref like `1.10` is not read as 1.1.
_Loader.add_constructor("tag:yaml.org,2002:int", _construct_str)
_Loader.add_constructor("tag:yaml.org,2002:float", _construct_str)
_Loader.add_constructor("!secret", _construct_secret)
_Loader.add_constructor("!include", _construct_include)
_Loader.add_multi_constructor("!", _construct_tagged)


def without_userinfo(url: str) -> str:
    """*url* without any username and password, as shown to a user; a URL without
    a scheme, such as a Git Source in scp-like form, as it is."""
    if "://" not in url:
        return url
    return str(URL(url).with_user(None))


class DiscoveryError(Exception):
    """The YAML could not be parsed or its includes form a cycle."""


async def discover_git_sources(configuration: str, read: Reader) -> Discovery:
    """Find the Git Sources of *configuration* (a file name the reader understands)."""
    config = await _load(configuration, read, 0)
    gathered = _Gathered()
    gathered.add(config)
    untrackable = list(gathered.untrackable)
    parsed: list[tuple[_Candidate, GitSource]] = []
    for candidate in gathered.candidates:
        value = _substitute(candidate.source, gathered.substitutions)
        secret = _find_secret(value)
        if secret is not None:
            untrackable.append(
                f"{candidate.where} source depends on !secret {secret.name}: {value!r}"
            )
            continue
        source = _parse_source(value)
        if source is not None and source.ref is None and _in_flow_style(value):
            untrackable.append(
                f"{candidate.where} source {source.url} is a flow-style mapping, where "
                "no ref can be inserted, so it cannot follow versions; it follows its "
                "Latest Commit"
            )
        if source is not None and not _is_pinned_to_a_commit(source):
            parsed.append((candidate, source))
    refs = _refs_by_repository(source for _candidate, source in parsed)
    uses = _variable_uses(config)
    # Where each Tag Ref candidate is written, by its index in parsed, and the
    # repositories with a Tag Ref written somewhere it cannot be rewritten.
    located: dict[int, RefLocation] = {}
    unlocated: set[str] = set()
    for index, (candidate, source) in enumerate(parsed):
        if source.kind is RefKind.TAG:
            location, reason = _locate_tag_ref(candidate, source, refs, uses, gathered)
            if location is None:
                untrackable.append(
                    f"{candidate.where} source {source.name} at {source.ref!r}: {reason}"
                )
                unlocated.add(source.url)
            else:
                located[index] = location
    sources: list[GitSource] = []
    stale: set[str] = set()
    # Repositories of Unset Refs that cannot follow versions, noted once each.
    shared: set[str] = set()
    for index, (candidate, source) in enumerate(parsed):
        if source.kind is RefKind.TAG:
            if index not in located:
                continue
            if source.url in unlocated:
                # Rewriting only the other places would leave it at two refs.
                untrackable.append(
                    f"{candidate.where} source {source.name} at {source.ref!r}: its ref "
                    "is also written where it cannot be rewritten"
                )
                continue
            source = replace(source, written_at=(located[index],))
        elif source.kind is RefKind.UNSET and source.written_at and len(refs[source.url]) > 1:
            # A ref inserted in one place only would leave the repository at two refs.
            if source.url not in shared:
                untrackable.append(
                    f"{candidate.where} source {source.name} is referred to in several "
                    "places, so it cannot follow versions; it follows its Latest Commit"
                )
                shared.add(source.url)
            source = replace(source, written_at=())
        sources.append(source)
        if not _refreshes_always(_substitute(candidate.refresh, gathered.substitutions)):
            stale.add(source.key)
    distinct = _distinct(sources)
    return Discovery(
        distinct,
        tuple(untrackable),
        _ota_status(gathered),
        tuple(source for source in distinct if source.key in stale),
    )


def _refreshes_always(refresh: Any) -> bool:
    """Whether *refresh* is `always` or zero; missing, it is ESPHome's default of 1d."""
    if refresh is None:
        return False
    policy = str(refresh).strip().lower()
    return policy == "always" or _ZERO_REFRESH.fullmatch(policy) is not None


def _ota_status(gathered: _Gathered) -> OtaStatus:
    platforms = {
        _substitute(platform, gathered.substitutions) for platform in gathered.ota_platforms
    }
    if not platforms.isdisjoint(OTA_PLATFORMS):
        return OtaStatus.PRESENT
    return OtaStatus.UNKNOWN if gathered.remote_packages else OtaStatus.MISSING


def _distinct(sources: list[GitSource]) -> tuple[GitSource, ...]:
    """*sources* without duplicates, each written wherever its duplicates are, with a
    ref added to names that would be ambiguous."""
    unique: dict[str, GitSource] = {}
    for source in sources:
        kept = unique.setdefault(source.key, source)
        written_at = kept.written_at + tuple(
            location for location in source.written_at if location not in kept.written_at
        )
        unique[source.key] = replace(kept, written_at=written_at)
    names = Counter(source.name for source in unique.values())
    return tuple(
        replace(source, name=f"{source.name}@{source.ref or 'HEAD'}")
        if names[source.name] > 1
        else source
        for source in unique.values()
    )


def _refs_by_repository(sources: Iterable[GitSource]) -> dict[str, list[str | None]]:
    """The ref of each place a repository is referred to, by its URL."""
    refs: dict[str, list[str | None]] = {}
    for source in sources:
        refs.setdefault(source.url, []).append(source.ref)
    return refs


def _locate_tag_ref(
    candidate: _Candidate,
    source: GitSource,
    refs: dict[str, list[str | None]],
    uses: Counter[str],
    gathered: _Gathered,
) -> tuple[RefLocation | None, str]:
    """Where the Tag Ref of *candidate* is written, so that rewriting it there moves
    only that ref; or None, and why it cannot be rewritten safely."""
    if len(set(refs[source.url])) > 1:
        return None, "its repository is referred to at several refs"
    written, raw = _raw_ref(candidate.source)
    if isinstance(raw, _FromVars):
        return None, "ref comes from an include's vars"
    if not isinstance(raw, str) or _VARIABLE.search(raw) is None:
        return written, "ref is not written as is in the YAML"
    variable = _VARIABLE.fullmatch(raw)
    if variable is None:
        return None, f"ref is built from several parts: {raw!r}"
    name = variable.group(1) or variable.group(2)
    if gathered.definitions[name] > 1:
        return None, f"substitution {name} is defined more than once"
    if uses[name] > 1:
        return None, f"substitution {name} is also used elsewhere"
    value = gathered.substitutions.get(name)
    if isinstance(value, str) and _VARIABLE.search(value) is not None:
        return None, f"ref is built from several parts: {value!r}"
    if not isinstance(value, _Written):
        return None, f"substitution {name} is not written as is in the YAML"
    return value.location, ""


def _raw_ref(source: Any) -> tuple[RefLocation | None, Any]:
    """The ref of a raw `source:` or remote package, before substitutions, and where
    it is written if it is written as is."""
    if isinstance(source, dict):
        ref = source.get("ref")
        return (ref.location if isinstance(ref, _Written) else None), ref
    if isinstance(source, str) and (match := _GITHUB_SHORTHAND.fullmatch(source)):
        ref = match.group(4)
        if isinstance(source, _FromVars):
            ref = _FromVars(ref)
        elif isinstance(source, _Written):
            # The ref is the end of the shorthand's text.
            whole = source.location
            return RefLocation(whole.file, whole.end - len(ref), whole.end), ref
        return None, ref
    return None, None


def _variable_uses(value: Any) -> Counter[str]:
    """How often each substitution is used in *value*, keys included."""
    uses: Counter[str] = Counter()
    if isinstance(value, str):
        for match in _VARIABLE.finditer(value):
            uses[match.group(1) or match.group(2)] += 1
    elif isinstance(value, dict):
        for key, item in value.items():
            uses += _variable_uses(key)
            uses += _variable_uses(item)
    elif isinstance(value, list):
        for item in value:
            uses += _variable_uses(item)
    elif isinstance(value, _Tagged):
        uses += _variable_uses(value.value)
    return uses


class _Candidate(NamedTuple):
    """A possible Git Source, as written, before substitutions."""

    # The key it was declared under: `external_components` or `packages`.
    where: str
    # The raw `source:` or remote package.
    source: Any
    # The raw `refresh:`, its Refresh Policy.
    refresh: Any


@dataclass
class _Gathered:
    """The possible Git Sources of a config and of its local packages."""

    candidates: list[_Candidate] = field(default_factory=list)
    substitutions: dict[str, Any] = field(default_factory=dict)
    # How many times each substitution is defined.
    definitions: Counter[str] = field(default_factory=Counter)
    untrackable: list[str] = field(default_factory=list)
    # The raw `platform:` of every `ota:` entry.
    ota_platforms: list[Any] = field(default_factory=list)
    # Whether any remote package was declared; its files are never read.
    remote_packages: bool = False

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
                self.definitions[str(name)] += 1
        # In the order they appear, so Sources are listed as the YAML reads.
        for key, value in config.items():
            if key == "external_components":
                for entry in self._items(key, value):
                    if isinstance(entry, dict):
                        self.candidates.append(
                            _Candidate(key, entry.get("source"), entry.get("refresh"))
                        )
            elif key == "packages":
                for package in self._items(key, value):
                    self._add_package(package)
            elif key == "ota":
                # A list of platforms, or a single one as a mapping.
                entries = [value] if isinstance(value, dict) and "platform" in value else value
                for entry in self._items(key, entries):
                    if isinstance(entry, dict):
                        self.ota_platforms.append(entry.get("platform"))

    def _add_package(self, package: Any) -> None:
        if isinstance(package, str):
            # Only remote packages are strings: the `github://` shorthand.
            self.remote_packages = True
            self.candidates.append(_Candidate("packages", package, None))
        elif isinstance(package, dict) and "url" in package:
            # A remote package is tracked itself, but its files are not read (ADR-0006).
            self.remote_packages = True
            self.candidates.append(
                _Candidate("packages", {**package, "type": "git"}, package.get("refresh"))
            )
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
        loader = _Loader(await read(path), path)
        try:
            document = loader.get_single_data()
        finally:
            loader.dispose()
    except yaml.YAMLError as err:
        raise DiscoveryError(f"cannot parse {path}: {err}") from err
    return await _resolve_includes(document, posixpath.dirname(path), read, depth)


async def _resolve_includes(value: Any, directory: str, read: Reader, depth: int) -> Any:
    if isinstance(value, _Include):
        path = posixpath.normpath(posixpath.join(directory, value.file))
        included = await _load(path, read, depth + 1)
        # An include's vars are substituted into the included content only.
        return _substitute_vars(included, value.vars) if value.vars else included
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


def _substitute_vars(value: Any, variables: dict[str, Any]) -> Any:
    """*value* with an include's *variables* substituted, each string they changed
    marked as coming from them."""
    if isinstance(value, str):
        substituted = _substitute(value, variables)
        if isinstance(substituted, str) and substituted != value:
            return _FromVars(substituted)
        return substituted
    if isinstance(value, dict):
        return {key: _substitute_vars(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_vars(item, variables) for item in value]
    return value


def _substitute(value: Any, substitutions: dict[str, Any]) -> Any:
    """*value* with every known `$name` / `${name}` replaced; unknown ones stay.

    A string that takes in a `!secret` substitution becomes that secret: its
    value cannot be known.
    """
    if isinstance(value, str):
        if _VARIABLE.search(value) is None:
            # Kept as is, with where it is written.
            return value
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
    children = (
        value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    )
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
        appended = (
            (RefLocation(value.location.file, value.location.end, value.location.end, "@"),)
            if ref is None and isinstance(value, _Written)
            else ()
        )
        return GitSource(repo, f"https://github.com/{owner}/{repo}.git", ref, appended)
    if isinstance(value, dict) and value.get("type") == "git":
        url = str(value["url"])
        ref = value.get("ref")
        # A Tag Ref is located later; an empty `ref:` must not get a second one.
        insertion = None if "ref" in value else _url_key(value).ref_insertion
        return GitSource(
            _repo_name(url),
            url,
            None if ref is None else str(ref),
            () if insertion is None else (insertion,),
        )
    return None


def _url_key(source: dict[Any, Any]) -> _Written:
    """The `url` key of *source* as written, or a stand-in that is located nowhere."""
    key = next(key for key in source if key == "url")
    return key if isinstance(key, _Written) else _Written(key)


def _in_flow_style(source: Any) -> bool:
    """Whether *source* is a mapping written flow-style, such as `{type: git, url: ...}`."""
    return isinstance(source, dict) and "url" in source and _url_key(source).in_flow_style


def _is_pinned_to_a_commit(source: GitSource) -> bool:
    return source.ref is not None and _COMMIT_SHA.fullmatch(source.ref) is not None


def _repo_name(url: str) -> str:
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return name.removesuffix(".git") or url
