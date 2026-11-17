"""Persistent queue, worker restarts, scheduling, backup and restore."""

from __future__ import annotations

import threading
import time

import pytest

from starindex.api.schemas import AnnotationPatch, SearchRequest
from starindex.jobs.handle import JobHandle
from starindex.jobs.handlers import HANDLERS
from starindex.jobs.queue import JobQueue, RetryLater
from starindex.jobs.worker import Worker, ensure_schedules, run_due_schedules
from starindex.library import annotations as ann
from starindex.storage import create_backup, export_library, list_backups, restore_backup

from .conftest import run_jobs


def test_queue_dedupe_priority_and_retry(ctx):
    q: JobQueue = ctx.jobs
    a = q.enqueue("index_repo", {"repo_id": 1}, dedupe_key="index:1")
    assert q.enqueue("index_repo", {"repo_id": 1}, dedupe_key="index:1") == a          # one live job per unit of work
    q.enqueue("cleanup")
    sync = q.enqueue("sync_full", dedupe_key="sync")
    first = q.claim("w1")
    assert first.id == sync                                                             # sync outranks indexing and cleanup
    assert q.fail(first.id, "boom") == "queued" and q.get(first.id).run_after           # retried with backoff
    with ctx.db.tx() as c:
        c.execute("UPDATE jobs SET run_after=NULL, attempts=max_attempts WHERE id=?", (sync,))
    again = q.claim("w1")
    assert again.id == sync and q.fail(again.id, "boom again") == "failed"
    assert q.retry_failed() == 1 and q.get(sync).status == "queued"


def test_expired_lease_is_taken_over_and_old_worker_cannot_clobber(ctx):
    q = ctx.jobs
    job_id = q.enqueue("cleanup")
    rec = q.claim("dead-worker")
    assert q.claim("other") is None                                                     # lease still valid
    with ctx.db.tx() as c:
        c.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00Z' WHERE id=?", (job_id,))
    taken = q.claim("new-worker")
    assert taken.id == job_id and taken.attempts == rec.attempts                        # recovery is not a new attempt
    assert q.heartbeat(job_id, "dead-worker") == "lost"
    assert q.owns(job_id, "new-worker") and not q.owns(job_id, "dead-worker")


def test_embeddings_are_not_starved_by_documentation_backlog(ctx):
    for i in range(100):
        ctx.jobs.enqueue("index_repo", {"repo_id": i})
    embeddings = ctx.jobs.enqueue("embed_pending")
    sync = ctx.jobs.enqueue("sync_full")
    assert ctx.jobs.claim("discovery").id == sync
    assert ctx.jobs.claim("semantic-search").id == embeddings
    assert ctx.jobs.claim("documentation").type == "index_repo"


def test_handler_checkpoint_survives_a_crash(ctx):
    seen: list[dict] = []

    def flaky(context, job: JobHandle):
        seen.append(dict(job.state))
        job.state["step"] = job.state.get("step", 0) + 1
        job.save_state()
        if job.state["step"] < 3:
            raise RuntimeError("crash")
        return {"steps": job.state["step"]}

    HANDLERS["_flaky"] = flaky
    try:
        job_id = ctx.jobs.enqueue("_flaky")
        run_jobs(ctx)
        rec = ctx.jobs.get(job_id)
        assert rec.status == "succeeded" and rec.result == {"steps": 3}
        assert seen == [{}, {"step": 1}, {"step": 2}]
    finally:
        HANDLERS.pop("_flaky")


def test_retry_later_does_not_consume_attempts(ctx):
    calls = []

    def limited(context, job):
        calls.append(1)
        if len(calls) < 6:
            raise RetryLater(30, "rate limited")
        return {}

    HANDLERS["_limited"] = limited
    try:
        job_id = ctx.jobs.enqueue("_limited", max_attempts=2)
        run_jobs(ctx)
        assert ctx.jobs.get(job_id).status == "succeeded" and len(calls) == 6
    finally:
        HANDLERS.pop("_limited")


def test_cancel_running_job_and_shutdown_releases_work(ctx):
    started, release = threading.Event(), threading.Event()

    def slow(context, job):
        started.set()
        for _ in range(400):
            time.sleep(0.01)
            job._last_beat = 0                       # check on every iteration in this test
            job.check_cancelled()
        return {}

    HANDLERS["_slow"] = slow
    try:
        worker = Worker(ctx, mode="embedded", concurrency=1, poll_interval=0.05)
        cancelled = ctx.jobs.enqueue("_slow")
        worker.start()
        assert started.wait(5)
        ctx.jobs.cancel(cancelled)
        deadline = time.time() + 5
        while ctx.jobs.get(cancelled).status != "cancelled" and time.time() < deadline:
            time.sleep(0.02)
        assert ctx.jobs.get(cancelled).status == "cancelled"

        started.clear()
        resumed = ctx.jobs.enqueue("_slow")
        assert started.wait(5)
        worker.stop(timeout=5)                       # shutdown mid-job
        rec = ctx.jobs.get(resumed)
        assert rec.status == "queued" and rec.attempts == 0      # back in the queue, no attempt lost
        assert ctx.jobs.worker_status()["worker_id"]
    finally:
        HANDLERS.pop("_slow")
        release.set()


def test_interrupted_indexing_resumes_after_restart(ctx, fake):
    """Kill the worker mid-way through indexing; a new worker finishes without duplicates."""
    ctx.jobs.enqueue("sync_full", dedupe_key="sync")
    first = Worker(ctx, mode="embedded")
    first.drain(max_jobs=6)                          # sync + five repositories, then the process "dies"
    with ctx.db.tx() as c:                           # a job that was running when it died
        c.execute("UPDATE jobs SET status='running', lease_owner='dead', lease_expires_at='2000-01-01T00:00:00Z' "
                  "WHERE id=(SELECT id FROM jobs WHERE status='queued' AND type='index_repo' LIMIT 1)")
    indexed_before = ctx.db.scalar("SELECT count(*) FROM repos WHERE index_status='indexed'")
    assert 0 < indexed_before < len(fake.repos)
    run_jobs(ctx)                                    # the "restarted" worker
    assert ctx.db.scalar("SELECT count(*) FROM repos WHERE index_status='indexed'") == len(fake.repos)
    assert ctx.db.scalar("SELECT count(*) FROM jobs WHERE status IN ('queued','running','failed')") == 0
    dupes = ctx.db.all("SELECT repo_id, path, count(*) n FROM documents GROUP BY repo_id, path HAVING n>1")
    assert not dupes
    assert ctx.db.scalar("SELECT count(*) FROM chunks WHERE embedded_model_key IS NULL") == 0


def test_schedules_fire_only_when_due_and_configured(ctx):
    ensure_schedules(ctx)
    assert {r["name"] for r in ctx.db.all("SELECT name FROM schedules")} == {"incremental_sync", "full_sync", "backup", "cleanup"}
    assert run_due_schedules(ctx) == []                                           # nothing is due yet
    with ctx.db.tx() as c:
        c.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00Z'")
    fired = run_due_schedules(ctx)
    assert set(fired) == {"incremental_sync", "full_sync", "backup", "cleanup"}
    assert ctx.db.scalar("SELECT count(*) FROM jobs WHERE type LIKE 'sync_%' AND status='queued'") == 1     # deduplicated
    assert run_due_schedules(ctx) == []
    ctx.settings_store.patch({"github": {"auth_method": "none"}})
    with ctx.db.tx() as c:
        c.execute("DELETE FROM jobs")
        c.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00Z'")
    assert "incremental_sync" not in run_due_schedules(ctx)                       # no sync without a GitHub connection


def test_data_survives_reopening_the_application(synced, tmp_path):
    from starindex.context import AppContext

    ann.patch_annotation(synced.db, 101, AnnotationPatch(note="remember me", favorite=True))
    before = [r.repo.full_name for r in synced.search.search(SearchRequest(query="vector similarity", record=False)).results][:5]
    counts = (synced.db.scalar("SELECT count(*) FROM repos"), synced.db.scalar("SELECT count(*) FROM chunks"))
    synced.close()

    reopened = AppContext()
    try:
        assert (reopened.db.scalar("SELECT count(*) FROM repos"), reopened.db.scalar("SELECT count(*) FROM chunks")) == counts
        assert reopened.db.one("SELECT note, favorite FROM repo_annotations WHERE repo_id=101")["note"] == "remember me"
        after = reopened.search.search(SearchRequest(query="vector similarity", record=False))
        assert after.mode == "hybrid" and [r.repo.full_name for r in after.results][:5] == before    # vector index persisted too
    finally:
        reopened.close()


def test_backup_restore_and_export(synced):
    ann.patch_annotation(synced.db, 104, AnnotationPatch(note="before backup"))
    path = create_backup(synced)
    assert path.is_file() and list_backups(synced)[0]["name"] == path.name
    ann.patch_annotation(synced.db, 104, AnnotationPatch(note="after backup"))
    with synced.db.tx() as c:
        c.execute("DELETE FROM repos WHERE id=101")
    restore_backup(synced, path.name)
    assert synced.db.one("SELECT note FROM repo_annotations WHERE repo_id=104")["note"] == "before backup"
    assert synced.db.one("SELECT 1 FROM repos WHERE id=101")
    run_jobs(synced)                                                              # vector index rebuilt from cached embeddings
    assert synced.db.scalar("SELECT count(*) FROM chunks WHERE embedded_model_key IS NULL") == 0
    with pytest.raises(ValueError):
        restore_backup(synced, "../../etc/passwd")
    name, media, body = export_library(synced, "csv")
    assert name.endswith(".csv") and "graphflow/agentgraph" in body and "before backup" in body
    assert '"note": "before backup"' in export_library(synced, "json")[2]


def test_rebuild_vector_index_uses_cached_embeddings(synced):
    calls = []
    backend = synced.embeddings.backend()
    original = backend.embed
    backend.embed = lambda texts: (calls.append(len(texts)), original(texts))[1]
    synced.jobs.enqueue("rebuild_vector_index")
    run_jobs(synced)
    assert calls == []                                                            # nothing was re-embedded
    assert synced.db.scalar("SELECT count(*) FROM chunks WHERE embedded_model_key IS NULL") == 0
    assert synced.search.search(SearchRequest(query="vector similarity", mode="semantic", record=False)).results


def test_migrations_are_recorded_and_idempotent(ctx):
    names = [r["name"] for r in ctx.db.all("SELECT name FROM schema_migrations ORDER BY name")]
    assert names[0] == "0001_init.sql" and len(names) >= 2
    assert ctx.db.migrate() == []
