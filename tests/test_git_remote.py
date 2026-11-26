"""Finding a Git Source's Latest Commit with `git ls-remote`."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.esphome_git_updates import git_remote
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


def _fake_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    """Put a `git` that runs *script* first on the PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text(f"#!/bin/sh\n{script}\n")
    git.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))


@pytest.fixture
def empty(tmp_path: Path) -> Path:
    """A repository with no commits, so no branch at all."""
    path = tmp_path / "empty"
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    return path


async def test_a_remote_with_no_commits_has_no_default_branch(empty: Path) -> None:
    with pytest.raises(GitRemoteError, match="no default branch"):
        await LsRemote().resolve_ref(empty.as_uri(), None)


async def test_git_exiting_with_an_error_is_an_error_with_its_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_git(tmp_path, monkeypatch, "echo 'fatal: repository not found' >&2; exit 128")

    with pytest.raises(GitRemoteError, match="failed: fatal: repository not found"):
        await LsRemote().resolve_ref("https://example.invalid/marsrelay.git", "main")


async def test_git_that_cannot_be_run_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(GitRemoteError, match="cannot run git ls-remote"):
        await LsRemote().resolve_ref("https://example.invalid/marsrelay.git", None)


async def test_a_remote_that_does_not_answer_in_time_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_git(tmp_path, monkeypatch, f"exec {shutil.which('sleep')} 30")
    monkeypatch.setattr(git_remote, "LS_REMOTE_TIMEOUT", 0.1)

    with pytest.raises(GitRemoteError, match="timed out"):
        await LsRemote().resolve_ref_and_tags("https://example.invalid/marsrelay.git", None)
