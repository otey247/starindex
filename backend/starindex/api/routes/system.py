"""Status, diagnostics, settings and storage."""

from __future__ import annotations

import platform
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import ValidationError

from ... import __version__
from ...context import AppContext
from ...db import utcnow
from ...github import auth as gh_auth
from ...settings_store import AppSettings
from ...storage import export_library, list_backups, restore_backup, storage_info
from ..deps import ApiError, get_ctx
from ..schemas import (
    BackupInfo,
    Counts,
    DiagnosticCheck,
    Diagnostics,
    EmbeddingStatus,
    Job,
    Ok,
    RestoreRequest,
    SettingsPatch,
    StorageInfo,
    SystemStatus,
)
from .jobs import job_to_schema, worker_info

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


def embedding_status(ctx: AppContext) -> EmbeddingStatus:
    e = ctx.settings.embedding
    emb = ctx.embeddings
    if e.backend == "none":
        return EmbeddingStatus(backend="none", model="", ready=False, detail="Semantic search is turned off.")
    if emb.loaded():
        return EmbeddingStatus(backend=e.backend, model=e.model, model_key=emb.model_key, dim=emb.known_dim(), ready=True, detail="Model loaded.")
    err = emb.last_error()
    return EmbeddingStatus(
        backend=e.backend, model=e.model, model_key=emb.model_key, dim=emb.known_dim(), ready=err is None,
        detail=err or "Loads on first use (the first run downloads the model).",
    )


@router.get("/system/status", response_model=SystemStatus)
def status(ctx: AppContext = Depends(get_ctx)) -> SystemStatus:
    db, settings = ctx.db, ctx.settings
    key = ctx.embeddings.model_key
    counts = Counts(
        repos=db.scalar("SELECT count(*) FROM repos", default=0),
        starred=db.scalar("SELECT count(*) FROM repos WHERE star_state='starred'", default=0),
        unstarred=db.scalar("SELECT count(*) FROM repos WHERE star_state='unstarred'", default=0),
        documents=db.scalar("SELECT count(*) FROM documents", default=0),
        chunks=db.scalar("SELECT count(*) FROM chunks", default=0),
        chunks_embedded=db.scalar("SELECT count(*) FROM chunks WHERE embedded_model_key=?", (key,), 0),
        collections=db.scalar("SELECT count(*) FROM collections", default=0),
        reports=db.scalar("SELECT count(*) FROM reports WHERE saved=1", default=0),
    )
    vector_ok, backend = True, "qdrant-embedded"
    try:
        backend = f"qdrant-{ctx.vectors.mode}"
    except Exception:
        vector_ok = False
    return SystemStatus(
        version=__version__, data_dir=str(ctx.config.paths.data_dir),
        first_run=counts.repos == 0 and not settings.onboarding_completed, onboarding_completed=settings.onboarding_completed,
        github=gh_auth.auth_status(ctx, verify=False), counts=counts, embedding=embedding_status(ctx), vector_backend=backend,
        vector_ok=vector_ok, llm=ctx.router.active("answer"), worker=worker_info(ctx), secret_backend=ctx.secrets.backend_name,
        cloud_allowed=settings.privacy.allow_cloud_models, offline_mode=settings.privacy.offline_mode,
    )


@router.get("/system/diagnostics", response_model=Diagnostics)
def diagnostics(ctx: AppContext = Depends(get_ctx)) -> Diagnostics:
    checks: list[DiagnosticCheck] = []

    def add(name: str, status: Literal["ok", "warn", "fail", "info"], detail: str, hint: str = "") -> None:
        checks.append(DiagnosticCheck(name=name, status=status, detail=detail, hint=hint))

    add("Python", "ok", f"{platform.python_version()} on {platform.system()} {platform.release()}")
    add("SQLite", "ok", f"{sqlite3.sqlite_version}; schema {ctx.db.schema_version()}")
    try:
        ctx.db.execute("SELECT 1 FROM chunk_fts LIMIT 1")
        add("Full-text search (FTS5)", "ok", "Available.")
    except sqlite3.Error as exc:
        add("Full-text search (FTS5)", "fail", str(exc), "Use a Python build whose SQLite includes FTS5 (python.org installers do).")
    add("Data directory", "ok", str(ctx.config.paths.data_dir))
    add("Secret storage", "ok" if ctx.secrets.backend_name.startswith("keyring") else "warn", ctx.secrets.backend_name,
        "" if ctx.secrets.backend_name.startswith("keyring") else "No OS keyring was found; secrets are kept in a file readable only by your user.")
    gh = gh_auth.auth_status(ctx)
    add("GitHub", "ok" if gh.ok else ("info" if gh.method == "none" else "fail"), gh.detail, "" if gh.ok else "Open Settings → GitHub.")
    emb = embedding_status(ctx)
    add("Embedding model", "ok" if emb.ready else "warn", f"{emb.backend}:{emb.model} — {emb.detail}")
    try:
        index = ctx.vectors
        add("Vector index", "ok", f"Qdrant ({index.mode}) at {index.location}; collections: {len(index.collections())}")
    except Exception as exc:
        add("Vector index", "fail", str(exc), "Close other StarIndex processes or set STARINDEX_QDRANT_URL to a Qdrant server.")
    ok, detail = ctx.reranker.status()
    add("Reranker", "ok" if ok else "info", detail)
    worker = ctx.jobs.worker_status()
    add("Background worker", "ok" if worker["alive"] else "fail", f"mode: {worker['mode']}; last seen {worker['last_seen'] or 'never'}",
        "" if worker["alive"] else "Start StarIndex with its normal launcher, or run `starindex worker`.")
    llm = ctx.router.active("answer")
    add("Language model", "ok" if llm.configured else "info",
        f"{llm.provider_name} / {llm.model}" if llm.configured else "No model configured. Search works without one; Ask and Research need one.",
        "" if llm.configured else "Open Settings → Models.")
    settings = ctx.settings
    add("Privacy", "info", f"Cloud models {'allowed' if settings.privacy.allow_cloud_models else 'blocked'}; offline mode {'on' if settings.privacy.offline_mode else 'off'}.")
    add("Network binding", "ok" if ctx.config.is_loopback else "warn", f"{ctx.config.host}:{ctx.config.port}",
        "" if ctx.config.is_loopback else "Listening beyond loopback; access requires STARINDEX_ACCESS_TOKEN.")
    return Diagnostics(checks=checks, generated_at=utcnow())


@router.get("/settings", response_model=AppSettings)
def get_settings(ctx: AppContext = Depends(get_ctx)) -> AppSettings:
    return ctx.settings


@router.patch("/settings", response_model=AppSettings)
def patch_settings(body: SettingsPatch, ctx: AppContext = Depends(get_ctx)) -> AppSettings:
    before = ctx.settings
    if "routing" in body.patch:
        raise ApiError(400, "validation", "Model routing is changed through /api/routing.")
    try:
        after = ctx.settings_store.patch(body.patch)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
        raise ApiError(422, "validation", f"Invalid settings: {problems}") from exc
    if before.embedding != after.embedding:
        # A different vector space: build the new index in the background, keep the old one until cleanup.
        ctx.reset_service("embeddings")
        with ctx.db.tx() as c:
            c.execute("UPDATE chunks SET embedded_model_key=NULL WHERE embedded_model_key IS NOT ?", (ctx.embeddings.model_key,))
            c.execute("UPDATE repos SET embedded_model_key=NULL WHERE embedded_model_key IS NOT ?", (ctx.embeddings.model_key,))
        if after.embedding.backend != "none":
            ctx.jobs.enqueue("embed_pending", dedupe_key="embed_pending")
    if before.rerank != after.rerank:
        ctx.reset_service("reranker")
    if before.privacy != after.privacy:
        ctx.router.invalidate()
    if before.github != after.github:
        gh_auth.invalidate()
    return after


@router.get("/storage", response_model=StorageInfo)
def storage(ctx: AppContext = Depends(get_ctx)) -> StorageInfo:
    return StorageInfo(**storage_info(ctx))


@router.get("/storage/backups", response_model=list[BackupInfo])
def backups(ctx: AppContext = Depends(get_ctx)) -> list[BackupInfo]:
    return [BackupInfo(**b) for b in list_backups(ctx)]


@router.post("/storage/backups", response_model=Job, status_code=201)
def backup_now(ctx: AppContext = Depends(get_ctx)) -> Job:
    job_id = ctx.jobs.enqueue("backup", dedupe_key="backup", priority=5)
    return job_to_schema(ctx, ctx.jobs.get(job_id))  # type: ignore[arg-type]


@router.post("/storage/restore", response_model=Ok)
def restore(body: RestoreRequest, ctx: AppContext = Depends(get_ctx)) -> Ok:
    if not body.confirm:
        raise ApiError(400, "confirmation_required", "Restoring replaces the current library. Confirm to continue.")
    try:
        restore_backup(ctx, body.name)
    except FileNotFoundError as exc:
        raise ApiError(404, "not_found", "That backup no longer exists.") from exc
    except ValueError as exc:
        raise ApiError(400, "validation", str(exc)) from exc
    return Ok(message="Backup restored. The search index is being rebuilt in the background.")


@router.get("/export")
def export(format: Literal["json", "csv"] = Query("json"), ctx: AppContext = Depends(get_ctx)) -> Response:
    name, media, body = export_library(ctx, format)
    return Response(body, media_type=f"{media}; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})
