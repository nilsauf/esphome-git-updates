"""Find the commit a Git Source's ref points to on its remote, with `git ls-remote`.

No Home Assistant imports: this runs `git` and reads its output, nothing more.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from enum import StrEnum

# A remote that has not answered by then is taken to be unreachable.
LS_REMOTE_TIMEOUT = 60


class GitRemoteError(Exception):
    """The remote could not be asked, or does not have the ref."""


class RefKind(StrEnum):
    # A Floating Ref: tracked.
    BRANCH = "branch"
    # A Pinned Ref: never produces an update.
    TAG = "tag"


@dataclass(frozen=True)
class RemoteRef:
    kind: RefKind
    commit: str


async def resolve_ref(url: str, ref: str | None) -> RemoteRef:
    """Ask the remote at *url* what *ref* (None: its default branch) points to."""
    if url.startswith("-"):
        # git would read it as an option, such as --upload-pack=<command>.
        raise GitRemoteError(f"not a git URL: {url}")
    if ref is None:
        refs = await _ls_remote(url, "HEAD")
        if "HEAD" not in refs:
            raise GitRemoteError(f"{url} has no default branch")
        return RemoteRef(RefKind.BRANCH, refs["HEAD"])
    branch = ref if ref.startswith("refs/heads/") else f"refs/heads/{ref}"
    tag = ref if ref.startswith("refs/tags/") else f"refs/tags/{ref}"
    refs = await _ls_remote(url, branch, tag)
    if branch in refs:
        return RemoteRef(RefKind.BRANCH, refs[branch])
    if tag in refs:
        # An annotated tag is also listed peeled, as the commit it points to.
        return RemoteRef(RefKind.TAG, refs.get(f"{tag}^{{}}", refs[tag]))
    raise GitRemoteError(f"{url} has no branch or tag {ref}")


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
