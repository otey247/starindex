"""In-memory GitHub REST API used by tests, the evaluation harness and browser tests.

Implements the subset StarIndex calls, faithfully enough to exercise pagination (Link
headers), star timestamps (star+json media type), conditional requests (ETag / 304), primary
and secondary rate limits, renames, deletions, unstarring and injected failures. It can be
used as an ``httpx`` transport (no sockets) or served over HTTP as an ASGI application.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx


@dataclass
class FakeRepo:
    data: dict[str, Any]
    files: dict[str, bytes] = field(default_factory=dict)
    languages: dict[str, int] = field(default_factory=dict)
    deleted: bool = False
    blocked: bool = False
    revision: int = 0

    @property
    def id(self) -> int:
        return int(self.data["id"])

    @property
    def full_name(self) -> str:
        return self.data["full_name"]

    @property
    def sha(self) -> str:
        h = hashlib.sha1(f"{self.id}:{self.revision}".encode())
        for path in sorted(self.files):
            h.update(path.encode())
            h.update(hashlib.sha1(self.files[path]).digest())
        return h.hexdigest()


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def make_repo(
    repo_id: int,
    full_name: str,
    *,
    description: str = "",
    topics: list[str] | None = None,
    language: str | None = "Python",
    stars: int = 100,
    license_spdx: str | None = "MIT",
    archived: bool = False,
    fork: bool = False,
    pushed_at: str = "2026-10-01T00:00:00Z",
    size_kb: int = 500,
    private: bool = False,
    default_branch: str = "main",
) -> dict[str, Any]:
    owner, name = full_name.split("/", 1)
    return {
        "id": repo_id,
        "node_id": f"R_{repo_id}",
        "name": name,
        "full_name": full_name,
        "private": private,
        "owner": {"login": owner, "type": "Organization", "avatar_url": f"https://avatars.githubusercontent.com/{owner}"},
        "html_url": f"https://github.com/{full_name}",
        "clone_url": f"https://github.com/{full_name}.git",
        "description": description,
        "fork": fork,
        "homepage": None,
        "size": size_kb,
        "stargazers_count": stars,
        "watchers_count": stars,
        "forks_count": max(1, stars // 10),
        "open_issues_count": 3,
        "language": language,
        "license": {"spdx_id": license_spdx, "name": f"{license_spdx} License"} if license_spdx else None,
        "topics": topics or [],
        "archived": archived,
        "disabled": False,
        "is_template": False,
        "default_branch": default_branch,
        "created_at": "2022-01-01T00:00:00Z",
        "updated_at": pushed_at,
        "pushed_at": pushed_at,
    }


class FakeGitHub:
    def __init__(self, *, login: str = "octo", token: str | None = "test-token", page_cap: int = 100):
        self.login = login
        self.token = token                 # None = accept anonymous requests only for public endpoints
        self.page_cap = page_cap
        self.repos: dict[int, FakeRepo] = {}
        self.stars: list[tuple[int, str]] = []      # (repo id, starred_at), any order; listing sorts by time desc
        self.requests: list[tuple[str, str]] = []   # (method, path?query)
        self.rate_limit = 5000
        self.rate_remaining = 5000
        self.rate_reset = int(time.time()) + 3600
        self.secondary_limit_next: int = 0          # next N API calls return 403 + Retry-After
        self.retry_after = 1
        self._fail_plan: list[tuple[Callable[[str], bool], int | str, int]] = []
        self.ignore_star_media_type = False

    # -- fixture building -------------------------------------------------------------------

    def add(self, repo: dict[str, Any], *, files: dict[str, str | bytes] | None = None, starred_at: str | None = None,
            languages: dict[str, int] | None = None) -> FakeRepo:
        fr = FakeRepo(dict(repo), {p: (c.encode() if isinstance(c, str) else c) for p, c in (files or {}).items()}, languages or {})
        if not fr.languages and repo.get("language"):
            fr.languages = {repo["language"]: 10_000}
        self.repos[fr.id] = fr
        if starred_at:
            self.star(fr.id, starred_at)
        return fr

    def star(self, repo_id: int, at: str) -> None:
        self.stars = [(i, t) for i, t in self.stars if i != repo_id] + [(repo_id, at)]

    def unstar(self, repo_id: int) -> None:
        self.stars = [(i, t) for i, t in self.stars if i != repo_id]

    def rename(self, repo_id: int, new_full_name: str) -> None:
        repo = self.repos[repo_id]
        owner, name = new_full_name.split("/", 1)
        repo.data.update(full_name=new_full_name, name=name, html_url=f"https://github.com/{new_full_name}")
        repo.data["owner"] = {**repo.data["owner"], "login": owner}

    def delete(self, repo_id: int) -> None:
        self.repos[repo_id].deleted = True
        self.unstar(repo_id)

    def push(self, repo_id: int, files: dict[str, str | bytes] | None = None, pushed_at: str = "2026-11-01T00:00:00Z") -> None:
        repo = self.repos[repo_id]
        repo.revision += 1
        repo.data["pushed_at"] = pushed_at
        for path, content in (files or {}).items():
            repo.files[path] = content.encode() if isinstance(content, str) else content

    def fail(self, match: str | Callable[[str], bool], *, status: int | str = 502, times: int = 1) -> None:
        """Make the next ``times`` matching requests fail. ``status`` may be 'network' to drop the connection."""
        pred = (lambda path, m=match: m in path) if isinstance(match, str) else match
        self._fail_plan.append((pred, status, times))

    def count(self, fragment: str) -> int:
        return sum(1 for _, path in self.requests if fragment in path)

    # -- request handling -------------------------------------------------------------------

    def _starred_ids(self) -> list[tuple[int, str]]:
        live = [(i, t) for i, t in self.stars if i in self.repos and not self.repos[i].deleted and not self.repos[i].blocked]
        return sorted(live, key=lambda it: (it[1], it[0]), reverse=True)

    def _rl_headers(self) -> dict[str, str]:
        return {
            "x-ratelimit-limit": str(self.rate_limit),
            "x-ratelimit-remaining": str(max(0, self.rate_remaining)),
            "x-ratelimit-reset": str(self.rate_reset),
            "x-ratelimit-resource": "core",
        }

    def _json(self, status: int, body: Any, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        return status, {"content-type": "application/json", **self._rl_headers(), **(headers or {})}, json.dumps(body).encode()

    def _find(self, owner: str, name: str) -> FakeRepo | None:
        full = f"{owner}/{name}".lower()
        for repo in self.repos.values():
            if repo.full_name.lower() == full:
                return repo
        return None

    def handle(self, method: str, url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        parts = urlsplit(url)
        path = parts.path
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.requests.append((method, path + (f"?{parts.query}" if parts.query else "")))
        headers = {k.lower(): v for k, v in headers.items()}

        for i, (pred, status, times) in enumerate(self._fail_plan):
            if pred(path):
                if times <= 1:
                    self._fail_plan.pop(i)
                else:
                    self._fail_plan[i] = (pred, status, times - 1)
                if status == "network":
                    raise httpx.ConnectError("connection dropped (injected)")
                return self._json(int(status), {"message": "injected failure"})

        if path.startswith("/raw/"):
            return self._raw(path[len("/raw/"):])

        # ---- API surface ----
        if self.secondary_limit_next > 0:
            self.secondary_limit_next -= 1
            return self._json(403, {"message": "You have exceeded a secondary rate limit."}, {"retry-after": str(self.retry_after)})
        if self.rate_remaining <= 0:
            return self._json(403, {"message": "API rate limit exceeded"})
        self.rate_remaining -= 1

        auth = headers.get("authorization", "")
        authed = bool(self.token) and auth == f"Bearer {self.token}"
        if auth and not authed:
            return self._json(401, {"message": "Bad credentials"})

        if path == "/rate_limit":
            core = {"limit": self.rate_limit, "remaining": self.rate_remaining, "reset": self.rate_reset}
            return self._json(200, {"resources": {"core": core}, "rate": core})
        if path == "/user":
            if not authed:
                return self._json(401, {"message": "Requires authentication"})
            return self._json(200, {"login": self.login, "id": 1}, {"x-oauth-scopes": "read:user"})

        m = re.fullmatch(r"/user/starred|/users/([^/]+)/starred", path)
        if m:
            if path == "/user/starred" and not authed:
                return self._json(401, {"message": "Requires authentication"})
            if m.group(1) and m.group(1).lower() != self.login.lower():
                return self._json(404, {"message": "Not Found"})
            return self._starred(url, query, headers)

        m = re.fullmatch(r"/user/starred/([^/]+)/([^/]+)", path)
        if m:
            if not authed:
                return self._json(401, {"message": "Requires authentication"})
            repo = self._find(m.group(1), m.group(2))
            starred = repo is not None and any(i == repo.id for i, _ in self._starred_ids())
            return (204, self._rl_headers(), b"") if starred else self._json(404, {"message": "Not Found"})

        m = re.fullmatch(r"/repositories/(\d+)", path)
        if m:
            repo = self.repos.get(int(m.group(1)))
            return self._repo_or_error(repo) or self._json(200, repo.data)  # type: ignore[union-attr]

        m = re.fullmatch(r"/repos/([^/]+)/([^/]+)(/.*)?", path)
        if m:
            repo = self._find(m.group(1), m.group(2))
            err = self._repo_or_error(repo)
            if err:
                return err
            assert repo is not None
            rest = m.group(3) or ""
            if rest == "":
                return self._json(200, repo.data)
            if rest == "/languages":
                return self._json(200, repo.languages)
            cm = re.fullmatch(r"/commits/(.+)", rest)
            if cm:
                if not repo.files:
                    return self._json(409, {"message": "Git Repository is empty."})
                etag = f'"{repo.sha}"'
                if headers.get("if-none-match") == etag:
                    self.rate_remaining += 1      # 304s do not count against the limit
                    return 304, {**self._rl_headers(), "etag": etag}, b""
                return 200, {"content-type": "application/vnd.github.sha", "etag": etag, **self._rl_headers()}, repo.sha.encode()
            tm = re.fullmatch(r"/git/trees/([0-9a-f]+)", rest)
            if tm:
                tree = [{"path": p, "type": "blob", "size": len(c), "sha": blob_sha(c)} for p, c in sorted(repo.files.items())]
                truncated = bool(repo.data.get("_tree_truncated"))
                if truncated:
                    tree = tree[: max(1, len(tree) // 2)]
                return self._json(200, {"sha": tm.group(1), "tree": tree, "truncated": truncated})
            if re.fullmatch(r"/tarball/[0-9a-f]+", rest):
                return 200, {"content-type": "application/x-gzip", **self._rl_headers()}, self._tarball(repo)
            fm = re.fullmatch(r"/contents/(.+)", rest)
            if fm:
                data = repo.files.get(fm.group(1))
                if data is None:
                    return self._json(404, {"message": "Not Found"})
                return 200, {"content-type": "application/vnd.github.raw", **self._rl_headers()}, data
        return self._json(404, {"message": "Not Found"})

    def _repo_or_error(self, repo: FakeRepo | None) -> tuple[int, dict[str, str], bytes] | None:
        if repo is None or repo.deleted:
            return self._json(404, {"message": "Not Found"})
        if repo.blocked:
            return self._json(451, {"message": "Repository access blocked"})
        return None

    def _starred(self, url: str, query: dict[str, str], headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        per_page = max(1, min(int(query.get("per_page", 30)), self.page_cap))
        page = max(1, int(query.get("page", 1)))
        items = self._starred_ids()
        if query.get("direction") == "asc":
            items = list(reversed(items))
        start = (page - 1) * per_page
        chunk = items[start : start + per_page]
        with_times = "star+json" in headers.get("accept", "") and not self.ignore_star_media_type
        body: list[Any] = []
        for repo_id, at in chunk:
            data = {k: v for k, v in self.repos[repo_id].data.items() if not k.startswith("_")}
            body.append({"starred_at": at, "repo": data} if with_times else data)
        last = max(1, -(-len(items) // per_page))
        base = url.split("?")[0]

        def link(p: int) -> str:
            q = {**query, "per_page": str(per_page), "page": str(p)}
            return f"<{base}?{'&'.join(f'{k}={v}' for k, v in q.items())}>"

        links = []
        if page < last:
            links += [f'{link(page + 1)}; rel="next"', f'{link(last)}; rel="last"']
        if page > 1:
            links += [f'{link(page - 1)}; rel="prev"', f'{link(1)}; rel="first"']
        payload = json.dumps(body).encode()
        etag = '"' + hashlib.sha1(payload).hexdigest() + '"'
        extra = {"etag": etag}
        if links:
            extra["link"] = ", ".join(links)
        if headers.get("if-none-match") == etag:
            self.rate_remaining += 1
            return 304, {**self._rl_headers(), **extra}, b""
        return 200, {"content-type": "application/json", **self._rl_headers(), **extra}, payload

    def _raw(self, rest: str) -> tuple[int, dict[str, str], bytes]:
        m = re.fullmatch(r"([^/]+)/([^/]+)/([0-9a-f]{40})/(.+)", rest)
        if not m:
            return 404, {}, b"not found"
        repo = self._find(m.group(1), m.group(2))
        if repo is None or repo.deleted or repo.blocked or repo.data.get("private"):
            return 404, {}, b"not found"
        data = repo.files.get(m.group(4))
        if data is None:
            return 404, {}, b"not found"
        return 200, {"content-type": "text/plain", "content-length": str(len(data))}, data

    def _tarball(self, repo: FakeRepo) -> bytes:
        buf = io.BytesIO()
        root = f"{repo.full_name.replace('/', '-')}-{repo.sha[:7]}"
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
            for path, data in sorted(repo.files.items()):
                info = tarfile.TarInfo(f"{root}/{path}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            for name, target in (repo.data.get("_symlinks") or {}).items():
                info = tarfile.TarInfo(f"{root}/{name}")
                info.type = tarfile.SYMTYPE
                info.linkname = target
                tar.addfile(info)
            for name in repo.data.get("_evil_members") or []:
                data = b"should never be read"
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    # -- adapters ---------------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            status, headers, body = self.handle(request.method, str(request.url), dict(request.headers))
            return httpx.Response(status, headers=headers, content=body)

        return httpx.MockTransport(handler)

    def asgi(self):
        """ASGI app serving the same API over real HTTP (API at /, raw content at /raw)."""
        fake = self

        async def app(scope, receive, send):
            if scope["type"] != "http":
                return
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            host = headers.get("host", "127.0.0.1")
            query = scope.get("query_string", b"").decode()
            url = f"http://{host}{scope['path']}" + (f"?{query}" if query else "")
            try:
                status, out_headers, body = fake.handle(scope["method"], url, headers)
            except httpx.ConnectError:
                status, out_headers, body = 502, {}, b"injected network failure"
            await send({
                "type": "http.response.start", "status": status,
                "headers": [(k.encode(), v.encode()) for k, v in out_headers.items() if k != "content-length"],
            })
            await send({"type": "http.response.body", "body": body})

        return app
