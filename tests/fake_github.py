"""A fake GitHub API: compares real repositories on disk, and lists the Releases it is given."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiohttp import web
from aiohttp.test_utils import TestServer

from git_repo import GitRepo

# marsrelay's URL on GitHub; the `on_github` fixture lets git reach it there.
GITHUB_URL = "https://github.com/nilsauf/marsrelay.git"


class FakeGitHub:
    """Serves, for *repos* ("owner/repo"), `GET /repos/{owner}/{repo}/compare/{base}...{head}`
    and `GET /repos/{owner}/{repo}/releases`.

    A repository has no Releases until `release` publishes one.
    """

    def __init__(self, repos: dict[str, GitRepo]) -> None:
        self.repos = repos
        self.url = ""
        # The Authorization header of each compare request, None when there was none.
        self.authorizations: list[str | None] = []
        # The Authorization header of each Releases request, None when there was none.
        self.releases_authorizations: list[str | None] = []
        # The `page` asked for by each Releases request.
        self.releases_pages: list[int] = []
        # When set, every request is answered with this status, as a rate limit would be.
        self.failure: int | None = None
        # Each repository's releases, newest published first, as GitHub lists them.
        self._releases: dict[str, list[dict[str, object]]] = {}

    def release(
        self,
        repo: str,
        tag: str,
        *,
        title: str | None = None,
        body: str = "",
        draft: bool = False,
        prerelease: bool = False,
    ) -> None:
        """Publish a release of *repo* ("owner/repo") on *tag*."""
        self._releases.setdefault(repo, []).insert(
            0,
            {
                "tag_name": tag,
                "name": title,
                "body": body,
                "draft": draft,
                "prerelease": prerelease,
                "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
            },
        )

    async def releases(self, request: web.Request) -> web.Response:
        self.releases_authorizations.append(request.headers.get("Authorization"))
        self.releases_pages.append(int(request.query.get("page", "1")))
        if self.failure is not None:
            return web.json_response({"message": "API rate limit exceeded"}, status=self.failure)
        name = f"{request.match_info['owner']}/{request.match_info['repo']}"
        if name not in self.repos:
            return web.json_response({"message": "Not Found"}, status=404)
        per_page = int(request.query.get("per_page", "30"))
        page = int(request.query.get("page", "1"))
        start = (page - 1) * per_page
        return web.json_response(self._releases.get(name, [])[start : start + per_page])

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
    app.router.add_get("/repos/{owner}/{repo}/releases", fake.releases)
    server = TestServer(app)
    await server.start_server()
    fake.url = str(server.make_url("")).rstrip("/")
    try:
        yield fake
    finally:
        await server.close()
