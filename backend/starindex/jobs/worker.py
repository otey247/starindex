"""Background worker: bounded pool of threads over the persistent queue, plus the scheduler.

Runs embedded in the API process (default; required with the embedded vector index, which is
single-process) or standalone via ``starindex worker`` when Qdrant runs as a server.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ..db import parse_ts, utcnow
from .handle import JobHandle
from .handlers import HANDLERS
from .queue import JobCancelled, LeaseLost, RetryLater, WorkerStopping

if TYPE_CHECKING:
    from ..context import AppContext

log = logging.getLogger(__name__)

# Seeded once; afterwards the schedules table is the source of truth (editable in the UI).
DEFAULT_SCHEDULES = [
    ("incremental_sync", "sync_incremental", lambda s: s.sync.incremental_interval_minutes),
    ("full_sync", "sync_full", lambda s: s.sync.full_interval_hours * 60),
    ("backup", "backup", lambda s: s.storage.backup_interval_hours * 60),
    ("cleanup", "cleanup", lambda s: 24 * 60),
]
SYNC_JOBS = {"sync_full", "sync_incremental"}
DEDUPE = {"sync_full": "sync", "sync_incremental": "sync"}


def ensure_schedules(ctx: AppContext) -> None:
    settings = ctx.settings
    with ctx.db.tx() as c:
        have = {r["name"] for r in c.execute("SELECT name FROM schedules")}
        for name, job_type, interval in DEFAULT_SCHEDULES:
            if name not in have:
                minutes = int(interval(settings))
                next_run = (datetime.now(UTC) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
                c.execute(
                    "INSERT INTO schedules(name, job_type, interval_minutes, enabled, next_run_at) VALUES (?,?,?,1,?)",
                    (name, job_type, minutes, next_run),
                )


def run_due_schedules(ctx: AppContext) -> list[str]:
    """Enqueue jobs whose schedule is due. Returns the names that fired."""
    settings = ctx.settings
    fired: list[str] = []
    now = datetime.now(UTC)
    for row in ctx.db.all("SELECT * FROM schedules WHERE enabled=1"):
        due = parse_ts(row["next_run_at"])
        if due is not None and due > now:
            continue
        job_type = row["job_type"]
        skip = False
        if job_type in SYNC_JOBS:
            skip = (
                not settings.sync.scheduled_sync_enabled
                or settings.privacy.offline_mode
                or settings.github.auth_method == "none"
            )
        if job_type == "backup" and not settings.storage.backup_enabled:
            skip = True
        if not skip:
            ctx.jobs.enqueue(job_type, dedupe_key=DEDUPE.get(job_type, job_type))
            fired.append(row["name"])
        nxt = (now + timedelta(minutes=row["interval_minutes"])).strftime("%Y-%m-%dT%H:%M:%SZ")
        with ctx.db.tx() as c:
            c.execute(
                "UPDATE schedules SET next_run_at=?, last_run_at=CASE WHEN ? THEN ? ELSE last_run_at END WHERE name=?",
                (nxt, int(not skip), utcnow(), row["name"]),
            )
    return fired


class Worker:
    def __init__(self, ctx: AppContext, *, mode: str = "embedded", concurrency: int | None = None, poll_interval: float = 1.0):
        self.ctx = ctx
        self.mode = mode
        self.concurrency = concurrency or ctx.settings.indexing.worker_concurrency
        self.poll_interval = poll_interval
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{int(time.time())}"
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._running: dict[int, str] = {}
        self._lock = threading.Lock()

    # -- lifecycle --------------------------------------------------------------------------

    def start(self) -> None:
        ensure_schedules(self.ctx)
        self.ctx.worker_mode = self.mode
        self.ctx.jobs.worker_heartbeat(self.worker_id, {"mode": self.mode, "concurrency": self.concurrency})
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, name=f"starindex-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        t = threading.Thread(target=self._maintenance, name="starindex-worker-maint", daemon=True)
        t.start()
        self._threads.append(t)
        log.info("worker started (%s, %d threads)", self.mode, self.concurrency)

    def stop(self, timeout: float = 10.0) -> None:
        """Stop taking work. In-flight jobs keep their checkpoint and resume on next start."""
        self._stop.set()
        deadline = time.monotonic() + timeout
        for t in self._threads:
            t.join(max(0.0, deadline - time.monotonic()))
        self.ctx.worker_mode = "none"

    def run_forever(self) -> None:
        self.start()
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # -- loops ------------------------------------------------------------------------------

    def _maintenance(self) -> None:
        last_schedule = 0.0
        while not self._stop.is_set():
            try:
                self.ctx.jobs.worker_heartbeat(self.worker_id, {"mode": self.mode, "concurrency": self.concurrency})
                with self._lock:
                    running = list(self._running)
                for job_id in running:
                    self.ctx.jobs.heartbeat(job_id, self.worker_id)      # keeps the lease during long steps
                if time.monotonic() - last_schedule > 20:
                    last_schedule = time.monotonic()
                    run_due_schedules(self.ctx)
            except Exception:
                log.exception("worker maintenance error")
            self._stop.wait(10)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                did = self.run_one()
            except Exception:
                log.exception("worker loop error")
                did = False
            if not did:
                self._stop.wait(self.poll_interval)

    def run_one(self) -> bool:
        """Claim and execute a single job. Returns False when nothing was runnable."""
        queue = self.ctx.jobs
        record = queue.claim(self.worker_id)
        if record is None:
            return False
        handler = HANDLERS.get(record.type)
        if handler is None:
            queue.fail(record.id, f"No handler for job type {record.type!r}.", retryable=False)
            return True
        handle = JobHandle(queue, record, self.worker_id, stopping=self._stop)
        with self._lock:
            self._running[record.id] = record.type
        try:
            result = handler(self.ctx, handle)
            if queue.owns(record.id, self.worker_id):
                queue.succeed(record.id, result)
        except JobCancelled:
            if queue.owns(record.id, self.worker_id):
                queue.mark_cancelled(record.id)
        except LeaseLost:
            log.warning("job %s (%s): lease taken over by another worker; abandoning", record.id, record.type)
        except WorkerStopping:
            handle.save_state()
            queue.release(record.id, self.worker_id)
            log.info("job %s (%s) returned to the queue for shutdown", record.id, record.type)
        except RetryLater as exc:
            handle.save_state()
            queue.defer(record.id, exc.delay_s, exc.message or "Waiting before retrying")
            log.info("job %s (%s) deferred %.0fs: %s", record.id, record.type, exc.delay_s, exc.message)
        except Exception as exc:
            if not queue.owns(record.id, self.worker_id):
                log.warning("job %s (%s) failed after its lease was lost; ignoring", record.id, record.type)
                return True
            handle.save_state()
            message = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
            retryable = getattr(exc, "kind", None) not in ("auth", "invalid", "offline")
            status = queue.fail(record.id, str(message), retryable=retryable)
            log.warning("job %s (%s) failed -> %s: %s", record.id, record.type, status, message)
            log.debug("%s", traceback.format_exc())
        finally:
            with self._lock:
                self._running.pop(record.id, None)
        return True

    def drain(self, *, max_jobs: int = 100_000, include_deferred: bool = False) -> int:
        """Run jobs on the calling thread until the queue is empty (CLI and tests)."""
        done = 0
        while done < max_jobs:
            if not self.run_one():
                if include_deferred and self.ctx.db.one("SELECT 1 FROM jobs WHERE status='queued' LIMIT 1"):
                    with self.ctx.db.tx() as c:
                        c.execute("UPDATE jobs SET run_after=NULL WHERE status='queued'")
                    continue
                break
            done += 1
        return done
