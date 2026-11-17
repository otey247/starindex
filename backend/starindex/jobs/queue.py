"""Persistent job queue on SQLite.

Jobs are claimed with a lease. A worker that dies simply stops renewing its lease; the job
becomes claimable again and its handler resumes from the checkpoint stored in ``state``.
Handlers must therefore be idempotent. ``dedupe_key`` guarantees at most one queued/running
job per logical unit of work (e.g. one index job per repository).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ..db import Database, dumps, loads, utcnow

LEASE_SECONDS = 120
# Lower number = runs first.
PRIORITY = {
    "sync_full": 10,
    "sync_incremental": 10,
    "refresh_metadata": 20,
    "index_repo": 50,
    # Let semantic search become usable while a large documentation import is still running.
    "embed_pending": 45,
    "classify_repos": 70,
    "rebuild_search_index": 30,
    "rebuild_vector_index": 40,
    "summarize_repo": 80,
    "backup": 90,
    "cleanup": 95,
}


class JobCancelled(BaseException):
    """Raised inside a handler when the user cancelled the job.

    Control-flow exceptions derive from BaseException so handler code that catches Exception
    (to record a failure) never mistakes them for one.
    """


class LeaseLost(BaseException):
    """Another worker took the job over (this worker was presumed dead). Abandon it untouched."""


class WorkerStopping(BaseException):
    """The worker is shutting down; the job goes back to the queue and resumes from its checkpoint."""


class RetryLater(Exception):
    """Handler asks to be re-run after ``delay_s`` without counting as a failure (rate limits)."""

    def __init__(self, delay_s: float, message: str = ""):
        super().__init__(message or f"retry in {delay_s:.0f}s")
        self.delay_s = delay_s
        self.message = message


@dataclass
class JobRecord:
    id: int
    type: str
    payload: dict[str, Any]
    state: dict[str, Any]
    status: str
    priority: int
    attempts: int
    max_attempts: int
    progress_current: int
    progress_total: int
    progress_message: str
    error: str | None
    result: dict[str, Any] | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    run_after: str | None
    cancel_requested: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> JobRecord:
        return cls(
            id=row["id"],
            type=row["type"],
            payload=loads(row["payload"], {}),
            state=loads(row["state"], {}),
            status=row["status"],
            priority=row["priority"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            progress_current=row["progress_current"],
            progress_total=row["progress_total"],
            progress_message=row["progress_message"],
            error=row["error"],
            result=loads(row["result"], None),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            run_after=row["run_after"],
            cancel_requested=bool(row["cancel_requested"]),
        )


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class JobQueue:
    def __init__(self, db: Database):
        self.db = db

    # -- producing ------------------------------------------------------------------------

    def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
        *,
        dedupe_key: str | None = None,
        priority: int | None = None,
        max_attempts: int = 4,
        delay_s: float = 0,
    ) -> int:
        """Add a job; returns the id of the new job or of the live job with the same dedupe key."""
        run_after = _ts(datetime.now(UTC) + timedelta(seconds=delay_s)) if delay_s > 0 else None
        prio = PRIORITY.get(job_type, 100) if priority is None else priority
        with self.db.tx() as c:
            if dedupe_key:
                row = c.execute(
                    "SELECT id FROM jobs WHERE dedupe_key=? AND status IN ('queued','running')", (dedupe_key,)
                ).fetchone()
                if row:
                    if priority is not None:
                        # An interactive request can promote work already queued by a bulk sync.
                        c.execute("UPDATE jobs SET priority=min(priority, ?) WHERE id=? AND status='queued'",
                                  (prio, row["id"]))
                    return row["id"]
            cur = c.execute(
                "INSERT INTO jobs(type, payload, priority, dedupe_key, max_attempts, run_after, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (job_type, dumps(payload or {}), prio, dedupe_key, max_attempts, run_after, utcnow()),
            )
            return int(cur.lastrowid)

    def enqueue_many(self, job_type: str, items: list[tuple[dict[str, Any], str | None]], **kw: Any) -> list[int]:
        with self.db.tx():
            return [self.enqueue(job_type, payload, dedupe_key=key, **kw) for payload, key in items]

    # -- consuming ------------------------------------------------------------------------

    def claim(self, worker_id: str, *, exclude_types: set[str] | None = None) -> JobRecord | None:
        """Atomically take the next runnable job (queued, or running with an expired lease)."""
        now = utcnow()
        lease = _ts(datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS))
        exclude = sorted(exclude_types or ())
        type_clause = f" AND type NOT IN ({','.join('?' * len(exclude))})" if exclude else ""
        with self.db.tx() as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE ("
                "  (status='queued' AND (run_after IS NULL OR run_after<=?))"
                "  OR (status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at<?)"
                f"){type_clause} ORDER BY priority, id LIMIT 1",
                (now, now, *exclude),
            ).fetchone()
            if row is None:
                return None
            if row["cancel_requested"]:
                c.execute(
                    "UPDATE jobs SET status='cancelled', finished_at=?, lease_owner=NULL, lease_expires_at=NULL WHERE id=?",
                    (now, row["id"]),
                )
                return None
            recovered = row["status"] == "running"   # previous worker died mid-job
            c.execute(
                "UPDATE jobs SET status='running', lease_owner=?, lease_expires_at=?, "
                "attempts=attempts+?, started_at=COALESCE(started_at, ?), error=NULL WHERE id=?",
                (worker_id, lease, 0 if recovered else 1, now, row["id"]),
            )
            fresh = c.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
            return JobRecord.from_row(fresh)

    def heartbeat(self, job_id: int, worker_id: str) -> str:
        """Extend the lease. Returns 'ok', 'cancelled' (user asked) or 'lost' (another worker owns it)."""
        lease = _ts(datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS))
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE jobs SET lease_expires_at=? WHERE id=? AND lease_owner=? AND status='running'",
                (lease, job_id, worker_id),
            )
            if cur.rowcount == 0:
                return "lost"
            row = c.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
            return "cancelled" if row["cancel_requested"] else "ok"

    def owns(self, job_id: int, worker_id: str) -> bool:
        return bool(self.db.one("SELECT 1 FROM jobs WHERE id=? AND lease_owner=? AND status='running'", (job_id, worker_id)))

    def release(self, job_id: int, worker_id: str) -> None:
        """Give a running job back (worker shutdown). It keeps its checkpoint and does not lose an attempt."""
        with self.db.tx() as c:
            c.execute(
                "UPDATE jobs SET status='queued', attempts=max(attempts-1,0), run_after=NULL, lease_owner=NULL, lease_expires_at=NULL "
                "WHERE id=? AND lease_owner=? AND status='running'",
                (job_id, worker_id),
            )

    def progress(self, job_id: int, current: int | None = None, total: int | None = None, message: str | None = None) -> None:
        sets, params = [], []
        if current is not None:
            sets.append("progress_current=?")
            params.append(current)
        if total is not None:
            sets.append("progress_total=?")
            params.append(total)
        if message is not None:
            sets.append("progress_message=?")
            params.append(message[:500])
        if not sets:
            return
        with self.db.tx() as c:
            c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", (*params, job_id))

    def save_state(self, job_id: int, state: dict[str, Any]) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE jobs SET state=? WHERE id=?", (dumps(state), job_id))

    def succeed(self, job_id: int, result: dict[str, Any] | None = None) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE jobs SET status='succeeded', finished_at=?, result=?, lease_owner=NULL, lease_expires_at=NULL, error=NULL WHERE id=?",
                (utcnow(), dumps(result) if result is not None else None, job_id),
            )

    def fail(self, job_id: int, error: str, *, retryable: bool = True) -> str:
        """Record a failure. Retries with exponential backoff until max_attempts. Returns new status."""
        with self.db.tx() as c:
            row = c.execute("SELECT attempts, max_attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return "missing"
            if retryable and row["attempts"] < row["max_attempts"]:
                delay = min(900, 15 * (2 ** (row["attempts"] - 1)))
                c.execute(
                    "UPDATE jobs SET status='queued', run_after=?, error=?, lease_owner=NULL, lease_expires_at=NULL WHERE id=?",
                    (_ts(datetime.now(UTC) + timedelta(seconds=delay)), error[:4000], job_id),
                )
                return "queued"
            c.execute(
                "UPDATE jobs SET status='failed', finished_at=?, error=?, lease_owner=NULL, lease_expires_at=NULL WHERE id=?",
                (utcnow(), error[:4000], job_id),
            )
            return "failed"

    def defer(self, job_id: int, delay_s: float, message: str = "") -> None:
        """Put a running job back in the queue without consuming an attempt (rate-limit waits)."""
        with self.db.tx() as c:
            c.execute(
                "UPDATE jobs SET status='queued', run_after=?, attempts=max(attempts-1,0), progress_message=?, "
                "lease_owner=NULL, lease_expires_at=NULL WHERE id=?",
                (_ts(datetime.now(UTC) + timedelta(seconds=delay_s)), message[:500], job_id),
            )

    def mark_cancelled(self, job_id: int) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE jobs SET status='cancelled', finished_at=?, lease_owner=NULL, lease_expires_at=NULL WHERE id=?",
                (utcnow(), job_id),
            )

    # -- user actions ---------------------------------------------------------------------

    def cancel(self, job_id: int) -> None:
        with self.db.tx() as c:
            row = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return
            if row["status"] == "queued":
                c.execute("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?", (utcnow(), job_id))
            elif row["status"] == "running":
                c.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))

    def cancel_type(self, job_type: str) -> int:
        with self.db.tx() as c:
            n = c.execute(
                "UPDATE jobs SET status='cancelled', finished_at=? WHERE type=? AND status='queued'", (utcnow(), job_type)
            ).rowcount
            n += c.execute(
                "UPDATE jobs SET cancel_requested=1 WHERE type=? AND status='running'", (job_type,)
            ).rowcount
            return n

    def retry(self, job_id: int) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE jobs SET status='queued', attempts=0, run_after=NULL, error=NULL, finished_at=NULL, "
                "cancel_requested=0, lease_owner=NULL, lease_expires_at=NULL WHERE id=? AND status IN ('failed','cancelled')",
                (job_id,),
            )

    def retry_failed(self) -> int:
        """Re-queue failed jobs, skipping any whose unit of work is already queued again."""
        n = 0
        with self.db.tx() as c:
            for row in c.execute("SELECT id, dedupe_key FROM jobs WHERE status='failed' ORDER BY id").fetchall():
                if row["dedupe_key"] and c.execute(
                    "SELECT 1 FROM jobs WHERE dedupe_key=? AND status IN ('queued','running')", (row["dedupe_key"],)
                ).fetchone():
                    continue
                c.execute(
                    "UPDATE jobs SET status='queued', attempts=0, run_after=NULL, error=NULL, finished_at=NULL, cancel_requested=0 WHERE id=?",
                    (row["id"],),
                )
                n += 1
        return n

    def clear_finished(self, older_than_hours: float = 0) -> int:
        cutoff = _ts(datetime.now(UTC) - timedelta(hours=older_than_hours))
        with self.db.tx() as c:
            return c.execute(
                "DELETE FROM jobs WHERE status IN ('succeeded','cancelled') AND COALESCE(finished_at, created_at)<=?",
                (cutoff,),
            ).rowcount

    # -- introspection --------------------------------------------------------------------

    def get(self, job_id: int) -> JobRecord | None:
        row = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        return JobRecord.from_row(row) if row else None

    def list(self, *, status: str | None = None, job_type: str | None = None, limit: int = 100, offset: int = 0) -> tuple[list[JobRecord], int]:
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status)
        if job_type:
            where.append("type=?")
            params.append(job_type)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.db.scalar(f"SELECT count(*) FROM jobs {clause}", params, 0)
        rows = self.db.all(
            f"SELECT * FROM jobs {clause} ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'failed' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END, "
            f"COALESCE(finished_at, started_at, created_at) DESC, id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [JobRecord.from_row(r) for r in rows], total

    def has_active(self, job_type: str) -> bool:
        return bool(self.db.one("SELECT 1 FROM jobs WHERE type=? AND status IN ('queued','running') LIMIT 1", (job_type,)))

    def summary(self) -> dict[str, Any]:
        by_type: dict[str, dict[str, int]] = {}
        totals = {"queued": 0, "running": 0, "failed": 0}
        for row in self.db.all("SELECT type, status, count(*) n FROM jobs GROUP BY type, status"):
            by_type.setdefault(row["type"], {})[row["status"]] = row["n"]
            if row["status"] in totals:
                totals[row["status"]] += row["n"]
        hour_ago = _ts(datetime.now(UTC) - timedelta(hours=1))
        ten_min = _ts(datetime.now(UTC) - timedelta(minutes=10))
        done_hour = self.db.scalar("SELECT count(*) FROM jobs WHERE status='succeeded' AND finished_at>=?", (hour_ago,), 0)
        done_10 = self.db.scalar("SELECT count(*) FROM jobs WHERE status='succeeded' AND finished_at>=?", (ten_min,), 0)
        first_10 = self.db.scalar("SELECT min(finished_at) FROM jobs WHERE status='succeeded' AND finished_at>=?", (ten_min,))
        throughput = 0.0
        if done_10 and first_10:
            from ..db import parse_ts

            span = max(30.0, (datetime.now(UTC) - (parse_ts(first_10) or datetime.now(UTC))).total_seconds())
            throughput = round(done_10 / (span / 60.0), 2)
        pending = totals["queued"] + totals["running"]
        eta = int(pending / throughput * 60) if throughput > 0 and pending else None
        return {
            **totals,
            "succeeded_last_hour": done_hour,
            "throughput_per_min": throughput,
            "by_type": by_type,
            "eta_seconds": eta,
        }

    # -- worker liveness ------------------------------------------------------------------

    def worker_heartbeat(self, worker_id: str, info: dict[str, Any] | None = None) -> None:
        now = utcnow()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO worker_heartbeats(worker_id, started_at, last_seen, info) VALUES (?,?,?,?) "
                "ON CONFLICT(worker_id) DO UPDATE SET last_seen=excluded.last_seen, info=excluded.info",
                (worker_id, now, now, dumps(info or {})),
            )
            c.execute("DELETE FROM worker_heartbeats WHERE last_seen<?", (_ts(datetime.now(UTC) - timedelta(hours=1)),))

    def worker_status(self) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM worker_heartbeats ORDER BY last_seen DESC LIMIT 1")
        if row is None:
            return {"alive": False, "worker_id": None, "last_seen": None, "mode": "none"}
        from ..db import parse_ts

        seen = parse_ts(row["last_seen"])
        alive = bool(seen and (datetime.now(UTC) - seen).total_seconds() < 45)
        info = loads(row["info"], {})
        return {"alive": alive, "worker_id": row["worker_id"], "last_seen": row["last_seen"], "mode": info.get("mode", "none") if alive else "none"}
