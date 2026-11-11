"""What a job handler sees of the job it is executing."""

from __future__ import annotations

import time
from typing import Any

from .queue import JobCancelled, JobQueue, JobRecord, LeaseLost, WorkerStopping


class JobHandle:
    """Checkpoint, progress and cancellation for a running job."""

    def __init__(self, queue: JobQueue | None, record: JobRecord | None, worker_id: str = "inline", stopping=None):
        self._stopping = stopping          # threading.Event set when the worker shuts down
        self._queue = queue
        self._record = record
        self._worker_id = worker_id
        self.state: dict[str, Any] = dict(record.state) if record else {}
        self.payload: dict[str, Any] = dict(record.payload) if record else {}
        self._last_beat = 0.0
        self._cancelled = False

    @property
    def id(self) -> int | None:
        return self._record.id if self._record else None

    def save_state(self) -> None:
        if self._queue and self._record:
            self._queue.save_state(self._record.id, self.state)

    def progress(self, current: int | None = None, total: int | None = None, message: str | None = None) -> None:
        if self._queue and self._record:
            self._queue.progress(self._record.id, current, total, message)
        self.check_cancelled()

    def check_cancelled(self) -> None:
        """Renew the lease (throttled). Raises when the job should stop: cancelled, lease lost, or shutdown."""
        if self._cancelled:
            raise JobCancelled()
        if self._stopping is not None and self._stopping.is_set():
            raise WorkerStopping()
        if not (self._queue and self._record):
            return
        now = time.monotonic()
        if now - self._last_beat < 5:
            return
        self._last_beat = now
        state = self._queue.heartbeat(self._record.id, self._worker_id)
        if state == "cancelled":
            self._cancelled = True
            raise JobCancelled()
        if state == "lost":
            raise LeaseLost()


def inline_job(payload: dict[str, Any] | None = None, state: dict[str, Any] | None = None) -> JobHandle:
    """A handle for running a handler directly (CLI, tests) without the queue."""
    handle = JobHandle(None, None)
    handle.payload = dict(payload or {})
    handle.state = dict(state or {})
    return handle
