"""A fake GitHub API that answers the compare endpoint from real repositories on disk."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiohttp import web
from aiohttp.test_utils import TestServer

from git_repo import GitRepo


class FakeGitHub:
    """Serves `GET /repos/{owner}/{repo}/compare/{base}...{head}` for *repos* ("owner/repo")."""

    def __init__(self, repos: dict[str, GitRepo]) -> None:
        self.repos = repos
        self.url = ""
        # The Authorization header of each compare request, None when there was none.
        self.authorizations: list[str | None] = []
        # When set, every request is answered with this status, as a rate limit would be.
        self.failure: int | None = None

    async def compare(self, request: web.Request) -> web.Response:
        self.authorizations.append(request.headers.get("Authorization"))
        if self.failure is not None:
            return web.json_response({"message": "API rate limit exceeded"}, status=self.failure)
        name = f"{request.match_info['owner']}/{request.match_info['repo']}"
        repo = self.repos.get(name)
        base, _, head = request.match_info["basehead"].partition("...")
        if repo is None or not head:
            return web.json_response({"message": "Not Found"}, status=404)
        commits = repo.commits(base, head)
        return web.json_response(
            {
                "status": "ahead",
                "ahead_by": len(commits),
                "behind_by": 0,
                "total_commits": len(commits),
                "commits": [
                    {
                        "sha": sha,
                        "html_url": f"https://github.com/{name}/commit/{sha}",
                        "commit": {"message": message},
                    }
                    for sha, message in commits
                ],
            }
        )


@asynccontextmanager
async def serve_github(repos: dict[str, GitRepo]) -> AsyncIterator[FakeGitHub]:
    fake = FakeGitHub(repos)
    app = web.Application()
    app.router.add_get("/repos/{owner}/{repo}/compare/{basehead}", fake.compare)
    server = TestServer(app)
    await server.start_server()
    fake.url = str(server.make_url("")).rstrip("/")
    try:
        yield fake
    finally:
        await server.close()
