"""Find the commit a Git Source's ref points to on its remote, with `git ls-remote`.

No Home Assistant imports: this runs `git` and reads its output, nothing more.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

# A remote that has not answered by then is taken to be unreachable.
LS_REMOTE_TIMEOUT = 60


class GitRemoteError(Exception):
    """The remote could not be asked, or does not have the ref."""


class RemoteRefKind(StrEnum):
    BRANCH = "branch"
    TAG = "tag"


@dataclass(frozen=True)
class RemoteRef:
    kind: RemoteRefKind
    commit: str


@dataclass(frozen=True)
class RemoteTags:
    """What a ref points to on a remote, and every tag the remote has."""

    # None: the remote has no branch or tag of that name.
    ref: RemoteRef | None
    # Each tag's name (without `refs/tags/`) and the commit it points to.
    tags: Mapping[str, str]


_TAGS = "refs/tags/"


async def resolve_ref(url: str, ref: str | None) -> RemoteRef:
    """Ask the remote at *url* what *ref* (None: its default branch) points to."""
    found = (await _resolve(url, ref, tags=False)).ref
    if found is None:
        raise GitRemoteError(f"{url} has no branch or tag {ref}")
    return found


class LsRemote:
    """Asks each remote with `git ls-remote`: the remote port of `FollowedSources`."""

    async def resolve(self, url: str, ref: str | None, *, tags: bool) -> RemoteTags:
        """What *ref* (None: the default branch) points to at *url*, with every tag
        listed if *tags*, else at most *ref*'s, in one call."""
        return await _resolve(url, ref, tags=tags)


async def _resolve(url: str, ref: str | None, *, tags: bool) -> RemoteTags:
    """What *ref* points to, with every tag listed if *tags*, else at most *ref*'s."""
    _check_url(url)
    all_tags = (f"{_TAGS}*",) if tags else ()
    found: RemoteRef | None
    if ref is None:
        refs = await _ls_remote(url, "HEAD", *all_tags)
        if "HEAD" not in refs:
            raise GitRemoteError(f"{url} has no default branch")
        found = RemoteRef(RemoteRefKind.BRANCH, refs["HEAD"])
    else:
        branch, tag = _branch_and_tag(ref)
        refs = await _ls_remote(url, branch, *(all_tags or (tag,)))
        found = _found(refs, branch, tag)
    listed = {
        name.removeprefix(_TAGS): _peeled(refs, name)
        for name in refs
        if name.startswith(_TAGS) and not name.endswith("^{}")
    }
    return RemoteTags(found, listed)


def _check_url(url: str) -> None:
    if url.startswith("-"):
        # git would read it as an option, such as --upload-pack=<command>.
        raise GitRemoteError(f"not a git URL: {url}")


def _branch_and_tag(ref: str) -> tuple[str, str]:
    branch = ref if ref.startswith("refs/heads/") else f"refs/heads/{ref}"
    tag = ref if ref.startswith(_TAGS) else f"{_TAGS}{ref}"
    return branch, tag


def _found(refs: Mapping[str, str], branch: str, tag: str) -> RemoteRef | None:
    """The branch if listed, else the tag, else None: a ref that is both is a branch."""
    if branch in refs:
        return RemoteRef(RemoteRefKind.BRANCH, refs[branch])
    if tag in refs:
        return RemoteRef(RemoteRefKind.TAG, _peeled(refs, tag))
    return None


def _peeled(refs: Mapping[str, str], tag: str) -> str:
    """An annotated tag is also listed peeled, as the commit it points to."""
    return refs.get(f"{tag}^{{}}", refs[tag])


async def _ls_remote(url: str, *patterns: str) -> dict[str, str]:
    """Run `git ls-remote`; return each ref name it lists with its commit."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "ls-remote",
            url,
            *patterns,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Never wait for a password or a host key prompt.
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
            },
        )
    except OSError as err:
        raise GitRemoteError(f"cannot run git ls-remote: {err}") from err
    try:
        async with asyncio.timeout(LS_REMOTE_TIMEOUT):
            stdout, stderr = await process.communicate()
    except TimeoutError as err:
        process.kill()
        await process.wait()
        raise GitRemoteError(f"git ls-remote {url} timed out") from err
    if process.returncode != 0:
        message = stderr.decode(errors="replace").strip()
        raise GitRemoteError(f"git ls-remote {url} failed: {message}")
    refs: dict[str, str] = {}
    for line in stdout.decode().splitlines():
        commit, _, name = line.partition("\t")
        refs[name] = commit
    return refs
