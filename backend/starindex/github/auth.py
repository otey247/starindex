"""GitHub connection status for the UI (never exposes the token)."""

from __future__ import annotations

import hashlib
import threading
import time
from typing import TYPE_CHECKING, Any

from ..api.schemas import GitHubAuthStatus, RateLimitInfo
from ..secrets import GITHUB_TOKEN
from .client import GitHubError, gh_cli_path, gh_cli_token

if TYPE_CHECKING:
    from ..context import AppContext

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()
CACHE_SECONDS = 120


def invalidate() -> None:
    with _lock:
        _cache.clear()


def auth_status(ctx: AppContext, *, verify: bool = True) -> GitHubAuthStatus:
    """Current connection state. ``verify`` performs (and caches) one authenticated request."""
    settings = ctx.settings
    method = settings.github.auth_method
    gh_available = gh_cli_path() is not None
    token_stored = bool(ctx.secrets.get(GITHUB_TOKEN))
    status = GitHubAuthStatus(
        method=method, configured=method != "none", username=settings.github.username, gh_cli_available=gh_available,
        token_stored=token_stored,
    )
    stored = ctx.settings_store.get_value("github.rate_limit")
    if isinstance(stored, dict):
        status.rate_limit = RateLimitInfo(**{k: stored.get(k) for k in ("limit", "remaining", "reset_at", "resource")})
    if method == "none":
        status.detail = "GitHub is not connected."
        return status
    if settings.privacy.offline_mode:
        status.detail = "Offline mode is on; GitHub is not contacted."
        status.ok = True
        return status
    if method == "public":
        status.ok = bool(settings.github.username)
        status.login = settings.github.username
        status.detail = (
            f"Reading public stars of {settings.github.username} without a token (60 requests/hour; private stars are not visible)."
            if status.ok else "Enter the GitHub username whose public stars should be imported."
        )
        return status

    token = gh_cli_token() if method == "gh_cli" else ctx.secrets.get(GITHUB_TOKEN)
    status.gh_cli_authenticated = method == "gh_cli" and bool(token)
    if not token:
        status.detail = (
            "GitHub CLI is not signed in. Run `gh auth login` and try again." if method == "gh_cli" else "No access token is stored."
        )
        return status
    if not verify:
        status.ok = True
        return status
    key = hashlib.sha256(f"{method}:{token}".encode()).hexdigest()
    with _lock:
        hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        data = hit[1]
    else:
        data = {}
        try:
            resolver = getattr(ctx.github, "token_resolver", None)
            if resolver is not None:
                resolver.invalidate()
            resp = ctx.github.request("user")
            data = {
                "ok": True, "login": (resp.data or {}).get("login"),
                "scopes": [s.strip() for s in resp.headers.get("x-oauth-scopes", "").split(",") if s.strip()],
                "detail": "Connected.",
            }
        except GitHubError as exc:
            data = {"ok": False, "detail": exc.message}
        with _lock:
            _cache[key] = (time.monotonic(), data)
    status.ok = bool(data.get("ok"))
    status.login = data.get("login")
    status.scopes = data.get("scopes", [])
    status.detail = data.get("detail", "")
    rl = ctx.github.rate_limit
    if rl.limit:
        status.rate_limit = RateLimitInfo(**rl.to_dict())
    write_scopes = [s for s in status.scopes if s in ("repo", "public_repo", "delete_repo", "admin:org", "workflow", "write:packages")]
    if status.ok and write_scopes:
        status.detail = (
            f"Connected as {status.login}. This token carries write scopes ({', '.join(write_scopes)}); StarIndex only reads. "
            "A fine-grained token with read-only access is safer."
        )
    elif status.ok:
        status.detail = f"Connected as {status.login}."
    return status
