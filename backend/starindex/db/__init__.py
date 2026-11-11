"""SQLite access: one connection per thread, WAL mode, forward-only numbered migrations."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def utcnow() -> str:
    """ISO-8601 UTC timestamp, second precision, 'Z' suffix (matches GitHub's format)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class Database:
    """Thread-local SQLite connections against a single database file.

    SQLite in WAL mode supports one writer and many readers across threads and processes,
    which is what the API process and the worker need. Writes are short transactions.
    """

    def __init__(self, path: Path | str):
        self.path = str(path)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA temp_store=MEMORY")
            self._local.conn = c
            with self._lock:
                self._all.append(c)
        return c

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Write transaction. Nested use joins the outer transaction."""
        c = self.conn()
        if c.in_transaction:
            yield c
            return
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")

    def execute(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        return self.conn().execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        return self.conn().executemany(sql, rows)

    def one(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        return self.conn().execute(sql, params).fetchone()

    def all(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, params).fetchall()

    def scalar(self, sql: str, params: Iterable[Any] | dict[str, Any] = (), default: Any = None) -> Any:
        row = self.conn().execute(sql, params).fetchone()
        return default if row is None or row[0] is None else row[0]

    def close(self) -> None:
        with self._lock:
            for c in self._all:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
            self._all.clear()
        self._local = threading.local()

    # -- migrations ---------------------------------------------------------------------

    def migrate(self) -> list[str]:
        """Apply pending migrations in filename order. Returns the names applied."""
        c = self.conn()
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        done = {r[0] for r in c.execute("SELECT name FROM schema_migrations")}
        applied: list[str] = []
        for file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if file.name in done:
                continue
            sql = file.read_text(encoding="utf-8")
            try:
                c.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
            except sqlite3.Error:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise
            c.execute("INSERT INTO schema_migrations(name, applied_at) VALUES (?, ?)", (file.name, utcnow()))
            applied.append(file.name)
        return applied

    def schema_version(self) -> str | None:
        try:
            return self.scalar("SELECT max(name) FROM schema_migrations")
        except sqlite3.Error:
            return None


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def placeholders(n: int) -> str:
    return ",".join("?" * n)
