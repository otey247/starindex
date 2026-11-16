"""Job handlers: the mapping from job type to the code that does the work."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..db import utcnow
from .handle import JobHandle

if TYPE_CHECKING:
    from ..context import AppContext

log = logging.getLogger(__name__)
Handler = Callable[["AppContext", JobHandle], dict[str, Any] | None]


def _after_sync(ctx: AppContext, outcome, job: JobHandle) -> None:
    """Follow-up work once the starred list is known: vocabulary, categories, indexing, embedding."""
    from ..indexing.classify import classify_repo
    from ..indexing.pipeline import enqueue_index
    from ..search.lexical import rebuild_terms

    rebuild_terms(ctx.db)
    for repo_id in outcome.new_repo_ids:
        classify_repo(ctx, repo_id)
    settings = ctx.settings
    if settings.indexing.auto_index_new:
        # New repositories, repositories with new commits, and anything left pending by an earlier run.
        wanted = set(outcome.new_repo_ids) | set(outcome.changed_repo_ids)
        wanted |= {r["id"] for r in ctx.db.all("SELECT id FROM repos WHERE star_state='starred' AND index_status IN ('pending','queued')")}
        for repo_id in sorted(wanted):
            enqueue_index(ctx, repo_id)
    with ctx.db.tx() as c:
        c.execute("UPDATE repos SET embedded_model_key=NULL WHERE id IN (%s)" % (",".join(str(int(i)) for i in outcome.new_repo_ids + outcome.changed_repo_ids) or "NULL"))
    ctx.jobs.enqueue("embed_pending", dedupe_key="embed_pending", delay_s=2)


def sync_full(ctx: AppContext, job: JobHandle) -> dict[str, Any]:
    from ..github.sync import StarSync

    outcome = StarSync(ctx).run_full(job)
    _after_sync(ctx, outcome, job)
    return outcome.to_dict()


def sync_incremental(ctx: AppContext, job: JobHandle) -> dict[str, Any]:
    from ..github.sync import StarSync

    outcome = StarSync(ctx).run_incremental(job)
    _after_sync(ctx, outcome, job)
    if outcome.escalate_full:
        ctx.jobs.enqueue("sync_full", dedupe_key="sync")
    return outcome.to_dict()


def index_repo(ctx: AppContext, job: JobHandle) -> dict[str, Any]:
    from ..indexing.pipeline import index_repo as run

    result = run(ctx, int(job.payload["repo_id"]), force=bool(job.payload.get("force")), job=job)
    if result.status == "indexed":
        ctx.jobs.enqueue("embed_pending", dedupe_key="embed_pending", delay_s=3)
    return result.to_dict()


def embed_pending(ctx: AppContext, job: JobHandle) -> dict[str, Any]:
    from ..indexing.pipeline import embed_pending as run

    return run(ctx, job)


def classify_repos(ctx: AppContext, job: JobHandle) -> dict[str, Any]:
    """Rules + embedding similarity for everything; with ``use_llm`` also the model routed for classification.

    The model pass is optional and runs unattended, so it never confirms a cloud provider on the
    user's behalf: with the default privacy settings only a local model is used.
    """
    from ..indexing.classify import classify_all, classify_with_llm
    from ..providers.base import ProviderError

    count = classify_all(ctx, job, only_unclassified=bool(job.payload.get("only_unclassified")))
    result: dict[str, Any] = {"classified": count}
    if job.payload.get("use_llm"):
        ids = [int(i) for i in job.payload.get("repo_ids", [])] or [
            r["id"] for r in ctx.db.all("SELECT id FROM repos WHERE star_state='starred' ORDER BY starred_at DESC")
        ]
        done = 0
        for i, repo_id in enumerate(ids):
            try:
                classify_with_llm(ctx, repo_id)
                done += 1
            except ProviderError as exc:
                result["llm_stopped"] = exc.message      # stop at the first provider problem; never switch providers
                break
            job.progress(i + 1, len(ids), f"Classifying with the configured model ({i + 1}/{len(ids)})")
        result["llm_classified"] = done
    return result


def rebuild_search_index(ctx: AppContext, job: JobHandle) -> dict[str, Any]:
    from ..search.lexical import rebuild_terms, refresh_repo_docs

    ids = [r["id"] for r in ctx.db.all("SELECT id FROM repos")]
    for start in range(0, len(ids), 200):
        with ctx.db.tx() as c:
            refresh_repo_docs(c, ids[start : start + 200])
        job.progress(start, len(ids), "Rebuilding repository search documents")
    with ctx.db.tx() as c:
        c.execute("INSERT INTO chunk_fts(chunk_fts) VALUES ('rebuild')")
    return {"repos": len(ids), "terms": rebuild_terms(ctx.db)}


def rebuild_vector_index(ctx: AppContext, job: JobHandle) -> dict[str, Any]:
    from ..indexing.pipeline import rebuild_vector_index as run

    return run(ctx, job)


def refresh_metadata(ctx: AppContext, job: JobHandle) -> dict[str, Any]:
    from ..github.sync import StarSync

    sync = StarSync(ctx)
    ids = [int(i) for i in job.payload.get("repo_ids", [])]
    ok = 0
    for i, repo_id in enumerate(ids):
        ok += int(sync.refresh_repo(repo_id))
        job.progress(i + 1, len(ids))
    return {"refreshed": ok, "requested": len(ids)}


def backup(ctx: AppContext, job: JobHandle) -> dict[str, Any]:
    from ..storage import create_backup

    path = create_backup(ctx)
    return {"path": str(path), "size_bytes": path.stat().st_size}


def cleanup(ctx: AppContext, job: JobHandle) -> dict[str, Any]:
    """Housekeeping: old job rows, vectors of retired embedding models, retention, compaction."""
    removed_jobs = ctx.jobs.clear_finished(older_than_hours=72)
    active = ctx.embeddings.model_key
    dropped: list[str] = []
    try:
        index = ctx.vectors
        keep = {index.chunks_collection(active), index.repos_collection(active)}
        for name in index.collections():
            if name not in keep and name.startswith(("chunks__", "repos__")):
                index.drop(name)
                dropped.append(name)
    except Exception:
        log.debug("vector cleanup skipped", exc_info=True)
    with ctx.db.tx() as c:
        stale = c.execute("DELETE FROM embeddings WHERE model_key<>?", (active,)).rowcount if job.payload.get("purge_old_embeddings") else 0
        c.execute("DELETE FROM reports WHERE saved=0 AND created_at<datetime('now','-30 days')")
        c.execute("DELETE FROM http_cache WHERE updated_at<?", ("2000-01-01",))
    ctx.db.execute("PRAGMA optimize")
    ctx.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {"jobs_removed": removed_jobs, "collections_dropped": dropped, "embeddings_removed": stale, "at": utcnow()}


def evaluate_search(ctx: AppContext, job: JobHandle) -> dict[str, Any]:
    from ..search.evaluation import run_personal_eval

    run = run_personal_eval(ctx, job)
    return {"run_id": run["id"], "queries": run["queries"], "best_config": run["best_config"]}


HANDLERS: dict[str, Handler] = {
    "evaluate_search": evaluate_search,
    "sync_full": sync_full,
    "sync_incremental": sync_incremental,
    "index_repo": index_repo,
    "embed_pending": embed_pending,
    "classify_repos": classify_repos,
    "rebuild_search_index": rebuild_search_index,
    "rebuild_vector_index": rebuild_vector_index,
    "refresh_metadata": refresh_metadata,
    "backup": backup,
    "cleanup": cleanup,
}
