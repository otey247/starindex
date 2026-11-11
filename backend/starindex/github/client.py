"""GitHub REST client: authentication, conditional requests, rate limits, retries.

Only the configured API host, raw-content host and GitHub's own download hosts are ever
contacted; redirects to any other host are refused. URLs found inside repository content or
metadata (homepages, links) are never fetched.
"""

from __future__ import annotations

import logging
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urljoin, urlsplit

import httpx

from ..db import Database, utcnow
from ..secrets import GITHUB_TOKEN

if TYPE_CHECKING:
    from ..context import AppContext

log = logging.getLogger(__name__)

API_VERSION = "2022-11-28"
USER_AGENT = "StarIndex/0.1 (local application)"
STAR_MEDIA_TYPE = "application/vnd.github.star+json"
DOWNLOAD_HOSTS = {"codeload.github.com", "objects.githubusercontent.com", "github.com"}
MAX_INLINE_WAIT_S = 60.0
_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="([^"]+)"')
_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubError(Exception):
    def __init__(self, kind: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.kind = kind          # auth | not_found | forbidden | blocked | rate_limit | network | server | offline | too_large | invalid
        self.message = message
        self.status = status


class RateLimited(GitHubError):
    def __init__(self, wait_s: float, resource: str | None, message: str):
        super().__init__("rate_limit", message, status=429)
        self.wait_s = wait_s
        self.resource = resource


@dataclass
class RateLimit:
    limit: int | None = None
    remaining: int | None = None
    reset_epoch: int | None = None
    resource: str | None = None

    def to_dict(self) -> dict[str, Any]:
        reset_at = None
        if self.reset_epoch:
            reset_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.reset_epoch))
        return {"limit": self.limit, "remaining": self.remaining, "reset_at": reset_at, "resource": self.resource}


@dataclass
class GHResponse:
    status: int
    headers: httpx.Headers
    data: Any = None
    text: str | None = None
    not_modified: bool = False
    links: dict[str, str] = field(default_factory=dict)

    @property
    def next_url(self) -> str | None:
        return self.links.get("next")

    @property
    def last_page(self) -> int | None:
        last = self.links.get("last")
        if not last:
            return None
        m = re.search(r"[?&]page=(\d+)", last)
        return int(m.group(1)) if m else None


@dataclass
class StarPage:
    items: list[dict[str, Any]]      # each {"starred_at": str | None, "repo": {...}}
    url: str
    next_url: str | None
    last_page: int | None
    not_modified: bool = False


def valid_full_name(full_name: str) -> bool:
    return bool(_FULL_NAME_RE.match(full_name)) and ".." not in full_name


class GitHubClient:
    def __init__(
        self,
        *,
        api_url: str,
        raw_url: str,
        token_provider: Callable[[], str | None],
        db: Database | None = None,
        username: str | Callable[[], str | None] | None = None,
        offline: Callable[[], bool] = lambda: False,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 4,
    ):
        self.api_url = api_url.rstrip("/")
        self.raw_url = raw_url.rstrip("/")
        self._token_provider = token_provider
        self.db = db
        self._username = username
        self._offline = offline
        self._sleep = sleep
        self.max_retries = max_retries
        self.rate_limit = RateLimit()
        self._lock = threading.Lock()
        self._last_rate_persist = 0.0
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(30.0, connect=15.0),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )
        self._allowed_hosts = {urlsplit(self.api_url).netloc, urlsplit(self.raw_url).netloc} | DOWNLOAD_HOSTS

    def close(self) -> None:
        self._http.close()

    @property
    def username(self) -> str | None:
        return self._username() if callable(self._username) else self._username

    # -- low level ------------------------------------------------------------------------

    @property
    def authenticated(self) -> bool:
        return bool(self._token_provider())

    def _check_host(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.netloc not in self._allowed_hosts:
            raise GitHubError("invalid", f"Refusing to contact unexpected host {parts.netloc!r}.")

    def _headers(self, url: str, accept: str | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if urlsplit(url).netloc == urlsplit(self.api_url).netloc:
            headers["Accept"] = accept or "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = API_VERSION
            token = self._token_provider()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif accept:
            headers["Accept"] = accept
        return headers

    def _note_rate_limit(self, headers: httpx.Headers) -> None:
        if "x-ratelimit-remaining" not in headers:
            return
        try:
            rl = RateLimit(
                limit=int(headers.get("x-ratelimit-limit", "0") or 0) or None,
                remaining=int(headers.get("x-ratelimit-remaining", "0") or 0),
                reset_epoch=int(headers.get("x-ratelimit-reset", "0") or 0) or None,
                resource=headers.get("x-ratelimit-resource"),
            )
        except ValueError:
            return
        if rl.resource not in (None, "core"):
            return
        self.rate_limit = rl
        now = time.monotonic()
        if self.db is not None and now - self._last_rate_persist > 5:
            self._last_rate_persist = now
            try:
                from ..db import dumps

                with self.db.tx() as c:
                    c.execute(
                        "INSERT INTO settings(key, value, updated_at) VALUES ('github.rate_limit', ?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                        (dumps(rl.to_dict()), utcnow()),
                    )
            except Exception:  # bookkeeping only; never fail a request over it
                log.debug("could not persist rate limit", exc_info=True)

    def _cached_etag(self, url: str) -> str | None:
        if self.db is None:
            return None
        return self.db.scalar("SELECT etag FROM http_cache WHERE url=?", (url,))

    def _store_etag(self, url: str, etag: str | None) -> None:
        if self.db is None or not etag:
            return
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO http_cache(url, etag, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET etag=excluded.etag, updated_at=excluded.updated_at",
                (url, etag, utcnow()),
            )

    def forget_etag(self, url: str) -> None:
        if self.db is not None:
            with self.db.tx() as c:
                c.execute("DELETE FROM http_cache WHERE url=?", (url,))

    def request(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        accept: str | None = None,
        conditional: bool = False,
        expect_json: bool = True,
        ok_statuses: tuple[int, ...] = (),
    ) -> GHResponse:
        """GET with retries. ``conditional`` sends If-None-Match from the local ETag cache."""
        if self._offline():
            raise GitHubError("offline", "Offline mode is enabled; GitHub requests are blocked.")
        if not url.startswith("http"):
            url = f"{self.api_url}/{url.lstrip('/')}"
        if params:
            url = str(httpx.URL(url, params=params))
        self._check_host(url)

        attempt = 0
        while True:
            attempt += 1
            headers = self._headers(url, accept)
            if conditional:
                etag = self._cached_etag(url)
                if etag:
                    headers["If-None-Match"] = etag
            try:
                resp = self._http.get(url, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt > self.max_retries:
                    raise GitHubError("network", f"Network error contacting GitHub: {type(exc).__name__}") from exc
                self._sleep(min(30.0, 1.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.5))
                continue

            self._note_rate_limit(resp.headers)
            status = resp.status_code
            if status == 304:
                return GHResponse(status=304, headers=resp.headers, not_modified=True)
            if status in (301, 302, 307, 308):
                target = urljoin(url, resp.headers.get("location", ""))
                self._check_host(target)
                url = target
                if attempt > 8:
                    raise GitHubError("server", "Too many redirects from GitHub.")
                continue
            if 200 <= status < 300 or status in ok_statuses:
                links = {rel: link for link, rel in _LINK_RE.findall(resp.headers.get("link", ""))}
                if conditional and status == 200:
                    self._store_etag(url, resp.headers.get("etag"))
                data = None
                text = None
                if status != 204 and 200 <= status < 300:
                    if expect_json:
                        try:
                            data = resp.json()
                        except ValueError as exc:
                            raise GitHubError("server", "GitHub returned a response that is not JSON.", status=status) from exc
                    else:
                        text = resp.text
                return GHResponse(status=status, headers=resp.headers, data=data, text=text, links=links)

            # ---- failures ----
            if status in (403, 429):
                remaining = resp.headers.get("x-ratelimit-remaining")
                retry_after = resp.headers.get("retry-after")
                reset = resp.headers.get("x-ratelimit-reset")
                wait: float | None = None
                if retry_after and retry_after.isdigit():
                    wait = float(retry_after)                       # secondary rate limit
                elif remaining == "0" and reset and reset.isdigit():
                    wait = max(1.0, float(reset) - time.time() + 1)  # primary limit exhausted
                elif status == 429 or "rate limit" in _message(resp).lower():
                    wait = min(300.0, 20.0 * attempt)
                if wait is not None:
                    if wait <= MAX_INLINE_WAIT_S and attempt <= self.max_retries:
                        log.info("github rate limited; waiting %.0fs", wait)
                        self._sleep(wait)
                        continue
                    raise RateLimited(wait, resp.headers.get("x-ratelimit-resource"), f"GitHub rate limit reached; retry in {int(wait)}s.")
                raise GitHubError("forbidden", _message(resp) or "GitHub denied access (403).", status=403)
            if status == 401:
                raise GitHubError("auth", "GitHub rejected the credentials (401). Reconnect your account in Settings.", status=401)
            if status == 404:
                raise GitHubError("not_found", "Not found on GitHub (404).", status=404)
            if status == 451:
                raise GitHubError("blocked", "Unavailable for legal reasons (451).", status=451)
            if status == 409:
                raise GitHubError("not_found", _message(resp) or "Repository is empty (409).", status=409)
            if status >= 500:
                if attempt > self.max_retries:
                    raise GitHubError("server", f"GitHub server error ({status}).", status=status)
                self._sleep(min(30.0, 1.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.5))
                continue
            raise GitHubError("invalid", _message(resp) or f"Unexpected GitHub response ({status}).", status=status)

    # -- account --------------------------------------------------------------------------

    def viewer(self) -> dict[str, Any]:
        return self.request("user").data

    def fetch_rate_limit(self) -> RateLimit:
        data = self.request("rate_limit").data
        core = (data.get("resources") or {}).get("core") or data.get("rate") or {}
        self.rate_limit = RateLimit(core.get("limit"), core.get("remaining"), core.get("reset"), "core")
        return self.rate_limit

    # -- stars ----------------------------------------------------------------------------

    def starred_base(self) -> str:
        if self.authenticated:
            return f"{self.api_url}/user/starred"
        if not self.username:
            raise GitHubError("auth", "Connect GitHub (token or GitHub CLI) or set a public username first.")
        return f"{self.api_url}/users/{quote(self.username, safe='')}/starred"

    def first_star_page_url(self, per_page: int = 100) -> str:
        return str(httpx.URL(self.starred_base(), params={"per_page": per_page, "sort": "created", "direction": "desc"}))

    def star_page(self, url: str, *, conditional: bool = False) -> StarPage:
        resp = self.request(url, accept=STAR_MEDIA_TYPE, conditional=conditional)
        if resp.not_modified:
            return StarPage(items=[], url=url, next_url=None, last_page=None, not_modified=True)
        items = []
        for entry in resp.data or []:
            if isinstance(entry, dict) and "repo" in entry:
                items.append({"starred_at": entry.get("starred_at"), "repo": entry["repo"]})
            elif isinstance(entry, dict):      # server ignored the star media type
                items.append({"starred_at": None, "repo": entry})
        return StarPage(items=items, url=url, next_url=resp.next_url, last_page=resp.last_page)

    def iter_star_pages(self, start_url: str | None = None) -> Iterator[StarPage]:
        url: str | None = start_url or self.first_star_page_url()
        while url:
            page = self.star_page(url)
            yield page
            url = page.next_url

    def starred_count(self) -> int | None:
        """Total number of starred repositories, from the pagination of a one-item page."""
        url = str(httpx.URL(self.starred_base(), params={"per_page": 1}))
        resp = self.request(url)
        if resp.last_page is not None:
            return resp.last_page
        return len(resp.data or [])

    def is_starred(self, full_name: str) -> bool | None:
        """True/False for the authenticated user; None when it cannot be determined (public mode)."""
        if not self.authenticated or not valid_full_name(full_name):
            return None
        try:
            resp = self.request(f"user/starred/{full_name}", ok_statuses=(204,))
        except GitHubError as exc:
            if exc.kind == "not_found":
                return False
            raise
        return resp.status == 204

    # -- repositories ---------------------------------------------------------------------

    def repo_by_id(self, repo_id: int) -> dict[str, Any]:
        return self.request(f"repositories/{int(repo_id)}").data

    def repo(self, full_name: str) -> dict[str, Any]:
        _require_name(full_name)
        return self.request(f"repos/{full_name}").data

    def languages(self, full_name: str) -> dict[str, int]:
        _require_name(full_name)
        return self.request(f"repos/{full_name}/languages").data or {}

    def head_commit(self, full_name: str, ref: str) -> str | None:
        """Commit SHA of a branch. Uses a conditional request; returns None when unchanged (304)."""
        _require_name(full_name)
        resp = self.request(
            f"repos/{full_name}/commits/{quote(ref, safe='')}",
            accept="application/vnd.github.sha",
            conditional=True,
            expect_json=False,
        )
        if resp.not_modified:
            return None
        sha = (resp.text or "").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise GitHubError("server", "GitHub returned an unexpected commit identifier.")
        return sha

    def tree(self, full_name: str, sha: str) -> tuple[list[dict[str, Any]], bool]:
        _require_name(full_name)
        data = self.request(f"repos/{full_name}/git/trees/{sha}", params={"recursive": "1"}).data
        return data.get("tree") or [], bool(data.get("truncated"))

    def file(self, full_name: str, sha: str, path: str, *, max_bytes: int, private: bool = False) -> bytes | None:
        """Fetch one file at a commit. Returns None when missing or larger than ``max_bytes``."""
        _require_name(full_name)
        if path.startswith("/") or ".." in path.split("/"):
            raise GitHubError("invalid", "Refusing suspicious repository path.")
        quoted = quote(path, safe="/")
        if private:
            url = f"{self.api_url}/repos/{full_name}/contents/{quoted}?ref={sha}"
            accept: str | None = "application/vnd.github.raw+json"
        else:
            url = f"{self.raw_url}/{full_name}/{sha}/{quoted}"
            accept = None
        if self._offline():
            raise GitHubError("offline", "Offline mode is enabled; GitHub requests are blocked.")
        self._check_host(url)
        for attempt in range(1, self.max_retries + 2):
            try:
                with self._http.stream("GET", url, headers=self._headers(url, accept)) as resp:
                    if resp.status_code == 404:
                        return None
                    if resp.status_code in (429, 403) or resp.status_code >= 500:
                        if attempt > self.max_retries:
                            raise GitHubError("server", f"Could not download {path} ({resp.status_code}).", status=resp.status_code)
                        self._sleep(min(30.0, 2.0 * 2 ** (attempt - 1)))
                        continue
                    if resp.status_code != 200:
                        raise GitHubError("invalid", f"Could not download {path} ({resp.status_code}).", status=resp.status_code)
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        return None
                    buf = bytearray()
                    for block in resp.iter_bytes():
                        buf.extend(block)
                        if len(buf) > max_bytes:
                            return None
                    return bytes(buf)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt > self.max_retries:
                    raise GitHubError("network", f"Network error downloading {path}.") from exc
                self._sleep(min(30.0, 1.5 * 2 ** (attempt - 1)))
        return None

    def download_tarball(self, full_name: str, sha: str, *, max_bytes: int, dest_dir: Path | None = None) -> Path:
        """Stream the repository archive for a commit to a temporary file (size-capped)."""
        _require_name(full_name)
        if self._offline():
            raise GitHubError("offline", "Offline mode is enabled; GitHub requests are blocked.")
        url = f"{self.api_url}/repos/{full_name}/tarball/{sha}"
        tmp = tempfile.NamedTemporaryFile(prefix="starindex-", suffix=".tar.gz", delete=False, dir=dest_dir)
        path = Path(tmp.name)
        try:
            for _ in range(8):
                self._check_host(url)
                with self._http.stream("GET", url, headers=self._headers(url, None), timeout=httpx.Timeout(120.0, connect=15.0)) as resp:
                    self._note_rate_limit(resp.headers)
                    if resp.status_code in (301, 302, 307, 308):
                        url = urljoin(url, resp.headers.get("location", ""))
                        continue
                    if resp.status_code == 404:
                        raise GitHubError("not_found", "Repository archive not found.", status=404)
                    if resp.status_code == 451:
                        raise GitHubError("blocked", "Unavailable for legal reasons (451).", status=451)
                    if resp.status_code in (403, 429):
                        raise RateLimited(120.0, "core", "GitHub throttled the archive download.")
                    if resp.status_code != 200:
                        raise GitHubError("server", f"Archive download failed ({resp.status_code}).", status=resp.status_code)
                    written = 0
                    for block in resp.iter_bytes(chunk_size=1 << 16):
                        written += len(block)
                        if written > max_bytes:
                            raise GitHubError("too_large", f"Repository archive exceeds the {max_bytes // (1024 * 1024)} MB limit.")
                        tmp.write(block)
                    tmp.close()
                    return path
            raise GitHubError("server", "Too many redirects downloading the archive.")
        except BaseException:
            tmp.close()
            path.unlink(missing_ok=True)
            raise


def _message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("message") or "")
    except ValueError:
        pass
    return ""


def _require_name(full_name: str) -> None:
    if not valid_full_name(full_name):
        raise GitHubError("invalid", f"Invalid repository name: {full_name!r}")


# -- authentication -------------------------------------------------------------------------


def gh_cli_path() -> str | None:
    return shutil.which("gh")


def gh_cli_token() -> str | None:
    """Token from the GitHub CLI (`gh auth token`). No shell, short timeout."""
    exe = gh_cli_path()
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "auth", "token", "--hostname", "github.com"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = out.stdout.strip()
    return token if out.returncode == 0 and token else None


class TokenResolver:
    """Resolves the GitHub token according to the configured auth method, with a short cache."""

    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self._cache: tuple[float, str | None, str] | None = None
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None

    def __call__(self) -> str | None:
        method = self.ctx.settings.github.auth_method
        with self._lock:
            if self._cache and self._cache[2] == method and time.monotonic() - self._cache[0] < 300:
                return self._cache[1]
            token: str | None = None
            if method == "gh_cli":
                token = gh_cli_token()
            elif method == "token":
                token = self.ctx.secrets.get(GITHUB_TOKEN)
            self._cache = (time.monotonic(), token, method)
            return token


def build_client(ctx: AppContext, *, transport: httpx.BaseTransport | None = None) -> GitHubClient:
    resolver = TokenResolver(ctx)
    client = GitHubClient(
        api_url=ctx.config.github_api_url,
        raw_url=ctx.config.github_raw_url,
        token_provider=resolver,
        db=ctx.db,
        username=lambda: ctx.settings.github.username,   # read lazily so settings changes apply
        offline=lambda: ctx.settings.privacy.offline_mode,
        transport=transport,
    )
    client.token_resolver = resolver  # type: ignore[attr-defined]
    return client
