"""Finding a Git Source's Latest Commit with `git ls-remote`."""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.esphome_git_updates.git_remote import (
    GitRemoteError,
    LsRemote,
    RemoteRef,
    RemoteRefKind,
)
from git_repo import GitRepo


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    return GitRepo(tmp_path / "marsrelay")


async def test_no_ref_resolves_to_the_default_branch(repo: GitRepo) -> None:
    latest = repo.push()

    assert await LsRemote().resolve_ref(repo.url, None) == RemoteRef(RemoteRefKind.BRANCH, latest)


async def test_a_branch_is_a_floating_ref_at_its_latest_commit(repo: GitRepo) -> None:
    dev = repo.push("dev")
    repo.push("main")

    assert await LsRemote().resolve_ref(repo.url, "dev") == RemoteRef(RemoteRefKind.BRANCH, dev)


async def test_a_tag_is_a_pinned_ref(repo: GitRepo) -> None:
    tagged = repo.tag("v1.0.0")
    repo.push()

    assert await LsRemote().resolve_ref(repo.url, "v1.0.0") == RemoteRef(RemoteRefKind.TAG, tagged)


async def test_a_ref_the_remote_does_not_have_is_an_error(repo: GitRepo) -> None:
    with pytest.raises(GitRemoteError, match="no-such-branch"):
        await LsRemote().resolve_ref(repo.url, "no-such-branch")


async def test_an_unreachable_remote_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(GitRemoteError, match="ls-remote"):
        await LsRemote().resolve_ref((tmp_path / "gone").as_uri(), None)


async def test_a_url_that_would_be_read_as_an_option_is_refused(tmp_path: Path) -> None:
    marker = tmp_path / "ran"

    with pytest.raises(GitRemoteError, match="not a git URL"):
        await LsRemote().resolve_ref(f"--upload-pack=touch {marker}", None)
    assert not marker.exists()
