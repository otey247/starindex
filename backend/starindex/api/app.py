"""FastAPI application: routes, security middleware, embedded worker, static frontend."""

from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from .. import __version__
from ..config import LOOPBACK_HOSTS, RuntimeConfig, get_config
from ..context import AppContext
from ..providers.base import ProviderError

log = logging.getLogger(__name__)
DEV_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}
COOKIE = "starindex_token"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
    "img-src 'self' data: https://avatars.githubusercontent.com; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)


def web_dir() -> Path | None:
    """Location of the built frontend: explicit override, packaged copy, or the source tree."""
    candidates = [
        os.environ.get("STARINDEX_WEB_DIR"),
        Path(__file__).resolve().parent.parent / "web",
        Path(__file__).resolve().parents[3] / "frontend" / "dist",
    ]
    for c in candidates:
        if c and (Path(c) / "index.html").is_file():
            return Path(c)
    return None


def _host_allowed(host_header: str, config: RuntimeConfig) -> bool:
    """Reject unexpected Host headers (DNS rebinding) while bound to loopback."""
    if not config.is_loopback:
        return True
    host = host_header.rsplit(":", 1)[0] if host_header.count(":") <= 1 else host_header.rsplit("]", 1)[0].lstrip("[")
    return host in LOOPBACK_HOSTS or host == "testserver"


def create_app(ctx: AppContext | None = None, *, start_worker: bool | None = None) -> FastAPI:
    config = ctx.config if ctx else get_config()
    if not config.is_loopback and not config.access_token:
        raise RuntimeError(
            f"Refusing to listen on {config.host}: set STARINDEX_ACCESS_TOKEN to enable access beyond this computer."
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        context = ctx or AppContext(config)
        app.state.ctx = context
        worker = None
        mode = os.environ.get("STARINDEX_WORKER", "embedded").lower()
        run_worker = start_worker if start_worker is not None else mode == "embedded"
        if run_worker:
            from ..jobs.worker import Worker

            # We are the only worker for an embedded index: resume interrupted jobs immediately.
            with context.db.tx() as c:
                c.execute("UPDATE jobs SET lease_expires_at='1970-01-01T00:00:00Z' WHERE status='running'")
                c.execute("UPDATE repos SET index_status='queued' WHERE index_status='indexing'")
            worker = Worker(context, mode="embedded")
            worker.start()
        app.state.worker = worker
        try:
            yield
        finally:
            if worker is not None:
                worker.stop(timeout=5)
            for name in ("router", "chatgpt"):
                svc = context.__dict__.get(name)
                if svc is not None:
                    try:
                        svc.close()
                    except Exception:
                        log.debug("closing %s failed", name, exc_info=True)
            if ctx is None:
                context.close()

    app = FastAPI(title="StarIndex", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)

    @app.middleware("http")
    async def security(request: Request, call_next):
        if not _host_allowed(request.headers.get("host", ""), config):
            return JSONResponse({"error": {"kind": "forbidden", "message": "Unexpected Host header.", "detail": {}}}, status_code=403)
        origin = request.headers.get("origin")
        if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
            same = urlsplit(origin).netloc == request.headers.get("host", "")
            if not same and origin not in DEV_ORIGINS:
                return JSONResponse({"error": {"kind": "forbidden", "message": "Cross-origin requests are not allowed.", "detail": {}}}, status_code=403)
        if config.access_token and request.url.path != "/api/health":
            supplied = request.query_params.get("token")
            header = request.headers.get("authorization", "")
            cookie = request.cookies.get(COOKIE, "")
            token = header[7:] if header.lower().startswith("bearer ") else cookie
            if supplied and hmac.compare_digest(supplied, config.access_token) and request.method == "GET":
                resp = RedirectResponse(request.url.path or "/")
                resp.set_cookie(COOKIE, config.access_token, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 30)
                return resp
            if not (token and hmac.compare_digest(token, config.access_token)):
                return JSONResponse({"error": {"kind": "unauthorized", "message": "Access token required.", "detail": {}}}, status_code=401)
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if not request.url.path.startswith("/api/"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        else:
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        detail = exc.detail
        if not (isinstance(detail, dict) and "kind" in detail):
            kind = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "error")
            detail = {"kind": kind, "message": str(detail), "detail": {}}
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        problems = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()[:10]]
        message = "; ".join(f"{p['field'] or 'body'}: {p['message']}" for p in problems[:3]) or "Invalid request."
        return JSONResponse({"error": {"kind": "validation", "message": message, "detail": {"problems": problems}}}, status_code=422)

    @app.exception_handler(ProviderError)
    async def provider_error(_: Request, exc: ProviderError):
        status = {"not_configured": 409, "cloud_disabled": 403, "confirmation_required": 409, "auth": 401}.get(exc.kind, 502)
        return JSONResponse({"error": {"kind": exc.kind, "message": exc.message, "detail": exc.to_dict()}}, status_code=status)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(
            {"error": {"kind": "internal", "message": "Something went wrong inside StarIndex. Details are in the application log.", "detail": {}}},
            status_code=500,
        )

    from .routes import agent, chatgpt, jobs, library, providers, system

    for module in (system, jobs, library, agent, providers, chatgpt):
        app.include_router(module.router, prefix="/api")

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def api_not_found(rest: str):
        return JSONResponse({"error": {"kind": "not_found", "message": f"No such endpoint: /api/{rest}", "detail": {}}}, status_code=404)

    dist = web_dir()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if dist is None:
            return JSONResponse(
                {"error": {"kind": "frontend_missing", "message": "The web interface is not built. Run `npm run build` in frontend/, or use the dev server.", "detail": {}}},
                status_code=503,
            )
        target = (dist / path).resolve()
        if path and target.is_file() and dist.resolve() in target.parents:
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in f"/{path}" else None
            return FileResponse(target, headers=headers)
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app
