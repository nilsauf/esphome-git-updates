"""A real git repository on disk, to stand in for a Git Source's remote."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitRepo:
    """A repository reachable at `url`, with `main` as its default branch."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)
        self._git("init", "--quiet", "--initial-branch=main")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        self._git("config", "commit.gpgsign", "false")
        self.push()

    @property
    def url(self) -> str:
        return self.path.as_uri()

    def push(self, branch: str = "main") -> str:
        """Add a commit to *branch* (created from the current one if new); return its sha."""
        if branch != self._git("branch", "--show-current"):
            exists = self._git("branch", "--list", branch)
            self._git("checkout", "--quiet", *([] if exists else ["-b"]), branch)
        self._git("commit", "--quiet", "--allow-empty", "-m", f"commit on {branch}")
        return self.latest_commit(branch)

    def tag(self, name: str) -> str:
        self._git("tag", name)
        return self.latest_commit("HEAD")

    def latest_commit(self, ref: str = "main") -> str:
        return self._git("rev-parse", ref)

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.path, check=True, capture_output=True, text=True
        ).stdout.strip()
