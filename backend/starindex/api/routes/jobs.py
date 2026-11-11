"""Synchronisation, background jobs and schedules."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...context import AppContext
from ...db import loads
from ...github import auth as gh_auth
from ...jobs.queue import JobRecord
from ...secrets import GITHUB_TOKEN
from ..deps import ApiError, get_ctx, not_found
from ..schemas import (
    GitHubAuthStatus,
    GitHubAuthUpdate,
    IndexProgress,
    Job,
    JobEnqueue,
    JobPage,
    Ok,
    QueueSummary,
    RateLimitInfo,
    Schedule,
    SchedulePatch,
    SyncRun,
    SyncStartRequest,
    SyncStatus,
    WorkerInfo,
)

router = APIRouter(tags=["sync"])
DEDUPE = {"sync_full": "sync", "sync_incremental": "sync"}


def job_to_schema(ctx: AppContext, rec: JobRecord) -> Job:
    name = None
    repo_id = rec.payload.get("repo_id")
    if repo_id is not None:
        name = ctx.db.scalar("SELECT full_name FROM repos WHERE id=?", (repo_id,))
    return Job(
        id=rec.id, type=rec.type, status=rec.status, priority=rec.priority, attempts=rec.attempts, max_attempts=rec.max_attempts,  # type: ignore[arg-type]
        progress_current=rec.progress_current, progress_total=rec.progress_total, progress_message=rec.progress_message,
        error=rec.error, payload=rec.payload, result=rec.result, repo_full_name=name, created_at=rec.created_at,
        started_at=rec.started_at, finished_at=rec.finished_at, run_after=rec.run_after,
    )


def _run(row) -> SyncRun:
    return SyncRun(
        id=row["id"], kind=row["kind"], status=row["status"], started_at=row["started_at"], finished_at=row["finished_at"],
        pages_fetched=row["pages_fetched"], repos_seen=row["repos_seen"], added=row["added"], updated=row["updated"],
        renamed=row["renamed"], unstarred=row["unstarred"], restarred=row["restarred"], not_modified=bool(row["not_modified"]),
        total_expected=row["total_expected"], error=row["error"],
    )


def worker_info(ctx: AppContext) -> WorkerInfo:
    return WorkerInfo(**ctx.jobs.worker_status())


def index_progress(ctx: AppContext) -> IndexProgress:
    db = ctx.db
    by_status = {r["index_status"]: r["n"] for r in db.all("SELECT index_status, count(*) n FROM repos WHERE star_state='starred' GROUP BY index_status")}
    by_level = {r["lvl"]: r["n"] for r in db.all("SELECT COALESCE(indexed_level,'none') lvl, count(*) n FROM repos WHERE star_state='starred' GROUP BY lvl")}
    key = ctx.embeddings.model_key
    return IndexProgress(
        total=sum(by_status.values()), indexed=by_status.get("indexed", 0), pending=by_status.get("pending", 0),
        queued=by_status.get("queued", 0), indexing=by_status.get("indexing", 0), failed=by_status.get("failed", 0),
        skipped=by_status.get("skipped", 0), by_level=by_level,
        documents=db.scalar("SELECT count(*) FROM documents", default=0), chunks=db.scalar("SELECT count(*) FROM chunks", default=0),
        chunks_embedded=db.scalar("SELECT count(*) FROM chunks WHERE embedded_model_key=?", (key,), 0),
        repos_embedded=db.scalar("SELECT count(*) FROM repos WHERE embedded_model_key=?", (key,), 0),
    )


# ---------------------------------------------------------------------------------- GitHub connection


@router.get("/github/auth", response_model=GitHubAuthStatus)
def github_auth(ctx: AppContext = Depends(get_ctx)) -> GitHubAuthStatus:
    return gh_auth.auth_status(ctx)


@router.put("/github/auth", response_model=GitHubAuthStatus)
def set_github_auth(body: GitHubAuthUpdate, ctx: AppContext = Depends(get_ctx)) -> GitHubAuthStatus:
    patch: dict = {"auth_method": body.method}
    if body.method == "token":
        token = (body.token or "").strip()
        if token:
            if len(token) < 20 or any(ch.isspace() for ch in token):
                raise ApiError(422, "validation", "That does not look like a GitHub access token.")
            ctx.secrets.set(GITHUB_TOKEN, token)
        elif not ctx.secrets.get(GITHUB_TOKEN):
            raise ApiError(422, "validation", "Enter a GitHub access token.")
    if body.method == "public":
        username = (body.username or "").strip().lstrip("@")
        if not username or "/" in username or len(username) > 39:
            raise ApiError(422, "validation", "Enter a valid GitHub username.")
        patch["username"] = username
    ctx.settings_store.patch({"github": patch})
    gh_auth.invalidate()
    resolver = getattr(ctx.github, "token_resolver", None)
    if resolver is not None:
        resolver.invalidate()
    return gh_auth.auth_status(ctx)


@router.delete("/github/auth/token", response_model=GitHubAuthStatus)
def delete_github_token(ctx: AppContext = Depends(get_ctx)) -> GitHubAuthStatus:
    ctx.secrets.delete(GITHUB_TOKEN)
    if ctx.settings.github.auth_method == "token":
        ctx.settings_store.patch({"github": {"auth_method": "none"}})
    gh_auth.invalidate()
    resolver = getattr(ctx.github, "token_resolver", None)
    if resolver is not None:
        resolver.invalidate()
    return gh_auth.auth_status(ctx)


# ---------------------------------------------------------------------------------- sync


@router.get("/sync/status", response_model=SyncStatus)
def sync_status(ctx: AppContext = Depends(get_ctx)) -> SyncStatus:
    db = ctx.db
    current = db.one("SELECT * FROM sync_runs WHERE status='running' ORDER BY id DESC LIMIT 1")
    last_full = db.one("SELECT * FROM sync_runs WHERE kind='full' AND status<>'running' ORDER BY id DESC LIMIT 1")
    last_inc = db.one("SELECT * FROM sync_runs WHERE kind='incremental' AND status<>'running' ORDER BY id DESC LIMIT 1")
    github = gh_auth.auth_status(ctx)
    summary = ctx.jobs.summary()
    nxt = db.scalar("SELECT min(next_run_at) FROM schedules WHERE enabled=1 AND job_type IN ('sync_incremental','sync_full')")
    return SyncStatus(
        github=github, current_run=_run(current) if current else None, last_full=_run(last_full) if last_full else None,
        last_incremental=_run(last_inc) if last_inc else None, index=index_progress(ctx), queue=QueueSummary(**summary),
        worker=worker_info(ctx), next_scheduled_sync=nxt, rate_limit=github.rate_limit,
    )


@router.post("/sync/start", response_model=Job)
def sync_start(body: SyncStartRequest, ctx: AppContext = Depends(get_ctx)) -> Job:
    settings = ctx.settings
    if settings.github.auth_method == "none":
        raise ApiError(409, "github_not_connected", "Connect GitHub before synchronising.")
    if settings.privacy.offline_mode:
        raise ApiError(409, "offline", "Offline mode is on. Turn it off in Settings to synchronise with GitHub.")
    job_id = ctx.jobs.enqueue(f"sync_{body.kind}", dedupe_key="sync")
    return job_to_schema(ctx, ctx.jobs.get(job_id))  # type: ignore[arg-type]


@router.post("/sync/cancel", response_model=Ok)
def sync_cancel(ctx: AppContext = Depends(get_ctx)) -> Ok:
    n = ctx.jobs.cancel_type("sync_full") + ctx.jobs.cancel_type("sync_incremental")
    return Ok(message=f"Cancellation requested for {n} sync job(s).")


@router.get("/sync/runs", response_model=list[SyncRun])
def sync_runs(limit: int = Query(20, ge=1, le=200), ctx: AppContext = Depends(get_ctx)) -> list[SyncRun]:
    return [_run(r) for r in ctx.db.all("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------------------------- jobs


@router.get("/jobs", response_model=JobPage)
def jobs(
    status: str | None = None, type: str | None = None, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
    ctx: AppContext = Depends(get_ctx),
) -> JobPage:
    records, total = ctx.jobs.list(status=status or None, job_type=type or None, limit=limit, offset=offset)
    return JobPage(items=[job_to_schema(ctx, r) for r in records], total=total)


@router.post("/jobs", response_model=Job, status_code=201)
def enqueue(body: JobEnqueue, ctx: AppContext = Depends(get_ctx)) -> Job:
    job_id = ctx.jobs.enqueue(body.type, body.payload, dedupe_key=DEDUPE.get(body.type, body.type))
    return job_to_schema(ctx, ctx.jobs.get(job_id))  # type: ignore[arg-type]


@router.post("/jobs/retry-failed", response_model=Ok)
def retry_failed(ctx: AppContext = Depends(get_ctx)) -> Ok:
    n = ctx.jobs.retry_failed()
    with ctx.db.tx() as c:
        c.execute("UPDATE repos SET index_status='queued', index_error=NULL WHERE index_status='failed'")
    return Ok(message=f"Re-queued {n} failed job(s).")


@router.post("/jobs/clear-finished", response_model=Ok)
def clear_finished(ctx: AppContext = Depends(get_ctx)) -> Ok:
    return Ok(message=f"Removed {ctx.jobs.clear_finished()} finished job(s).")


@router.post("/jobs/{job_id}/retry", response_model=Job)
def retry(job_id: int, ctx: AppContext = Depends(get_ctx)) -> Job:
    if ctx.jobs.get(job_id) is None:
        raise not_found("Job")
    ctx.jobs.retry(job_id)
    return job_to_schema(ctx, ctx.jobs.get(job_id))  # type: ignore[arg-type]


@router.post("/jobs/{job_id}/cancel", response_model=Job)
def cancel(job_id: int, ctx: AppContext = Depends(get_ctx)) -> Job:
    if ctx.jobs.get(job_id) is None:
        raise not_found("Job")
    ctx.jobs.cancel(job_id)
    return job_to_schema(ctx, ctx.jobs.get(job_id))  # type: ignore[arg-type]


def _schedule(row) -> Schedule:
    return Schedule(name=row["name"], job_type=row["job_type"], interval_minutes=row["interval_minutes"], enabled=bool(row["enabled"]),
                    next_run_at=row["next_run_at"], last_run_at=row["last_run_at"])


@router.get("/schedules", response_model=list[Schedule])
def schedules(ctx: AppContext = Depends(get_ctx)) -> list[Schedule]:
    from ...jobs.worker import ensure_schedules

    ensure_schedules(ctx)
    return [_schedule(r) for r in ctx.db.all("SELECT * FROM schedules ORDER BY name")]


@router.patch("/schedules/{name}", response_model=Schedule)
def patch_schedule(name: str, body: SchedulePatch, ctx: AppContext = Depends(get_ctx)) -> Schedule:
    from datetime import UTC, datetime, timedelta

    row = ctx.db.one("SELECT * FROM schedules WHERE name=?", (name,))
    if row is None:
        raise not_found("Schedule")
    interval = body.interval_minutes or row["interval_minutes"]
    enabled = row["enabled"] if body.enabled is None else int(body.enabled)
    nxt = (datetime.now(UTC) + timedelta(minutes=interval)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with ctx.db.tx() as c:
        c.execute("UPDATE schedules SET interval_minutes=?, enabled=?, next_run_at=? WHERE name=?", (interval, enabled, nxt, name))
    return _schedule(ctx.db.one("SELECT * FROM schedules WHERE name=?", (name,)))
