"""Backups, restore, export and storage accounting. Everything stays on the local disk."""

from __future__ import annotations

import csv
import io
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .db import loads, utcnow

if TYPE_CHECKING:
    from .context import AppContext

BACKUP_PREFIX = "starindex-"


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def storage_info(ctx: AppContext) -> dict[str, Any]:
    paths = ctx.config.paths
    db_bytes = sum(_dir_size(Path(str(paths.db_path) + suffix)) for suffix in ("", "-wal", "-shm"))
    try:
        free = shutil.disk_usage(paths.data_dir).free
    except OSError:
        free = None
    return {
        "data_dir": str(paths.data_dir),
        "database_bytes": db_bytes,
        "vector_bytes": _dir_size(paths.vector_dir),
        "models_bytes": _dir_size(paths.models_dir),
        "backups_bytes": _dir_size(paths.backups_dir),
        "free_bytes": free,
        "embeddings_rows": ctx.db.scalar("SELECT count(*) FROM embeddings", default=0),
    }


def create_backup(ctx: AppContext) -> Path:
    """Consistent online copy of the database (includes annotations, settings and embeddings).

    The vector index is not copied: it is rebuilt from the embeddings stored in the database.
    Secrets live in the OS keyring and are never part of a backup.
    """
    paths = ctx.config.paths
    paths.backups_dir.mkdir(parents=True, exist_ok=True)
    name = f"{BACKUP_PREFIX}{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.db"
    target = paths.backups_dir / name
    dest = sqlite3.connect(str(target))
    try:
        ctx.db.conn().backup(dest)
    finally:
        dest.close()
    keep = ctx.settings.storage.backup_keep
    backups = sorted(paths.backups_dir.glob(f"{BACKUP_PREFIX}*.db"))
    for old in backups[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    return target


def list_backups(ctx: AppContext) -> list[dict[str, Any]]:
    out = []
    for p in sorted(ctx.config.paths.backups_dir.glob(f"{BACKUP_PREFIX}*.db"), reverse=True):
        st = p.stat()
        out.append({
            "name": p.name,
            "created_at": datetime.fromtimestamp(st.st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size_bytes": st.st_size,
            "path": str(p),
        })
    return out


def restore_backup(ctx: AppContext, name: str) -> None:
    """Replace the live database content with a backup, then schedule an index rebuild."""
    if "/" in name or "\\" in name or not name.startswith(BACKUP_PREFIX) or not name.endswith(".db"):
        raise ValueError("Invalid backup name.")
    source_path = ctx.config.paths.backups_dir / name
    if not source_path.is_file():
        raise FileNotFoundError(name)
    src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        if not src.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone():
            raise ValueError("That file is not a StarIndex backup.")
        create_backup(ctx)            # safety copy of the current state first
        src.backup(ctx.db.conn())
    finally:
        src.close()
    ctx.db.migrate()
    with ctx.db.tx() as c:
        # Vectors must be re-derived for the restored content; running jobs belong to the old state.
        c.execute("UPDATE chunks SET embedded_model_key=NULL")
        c.execute("UPDATE repos SET embedded_model_key=NULL")
        c.execute("UPDATE jobs SET status='cancelled', finished_at=? WHERE status IN ('queued','running')", (utcnow(),))
    ctx.jobs.enqueue("rebuild_vector_index", dedupe_key="rebuild_vector_index")


def export_library(ctx: AppContext, fmt: str = "json") -> tuple[str, str, str]:
    """Portable export of repositories with personal annotations. Returns (filename, media type, body)."""
    rows = ctx.db.all(
        "SELECT r.*, a.note, a.favorite, a.priority FROM repos r LEFT JOIN repo_annotations a ON a.repo_id=r.id ORDER BY r.starred_at DESC"
    )
    items = []
    for r in rows:
        rid = r["id"]
        items.append({
            "id": rid, "full_name": r["full_name"], "html_url": r["html_url"], "description": r["description"],
            "language": r["language"], "topics": loads(r["topics"], []), "stars": r["stars"], "license": r["license_spdx"],
            "starred_at": r["starred_at"], "pushed_at": r["pushed_at"], "archived": bool(r["is_archived"]),
            "star_state": r["star_state"], "note": r["note"] or "", "favorite": bool(r["favorite"] or 0), "priority": r["priority"] or 0,
            "tags": [t["name"] for t in ctx.db.all("SELECT t.name FROM repo_tags rt JOIN tags t ON t.id=rt.tag_id WHERE rt.repo_id=?", (rid,))],
            "collections": [c["name"] for c in ctx.db.all("SELECT c.name FROM collection_repos cr JOIN collections c ON c.id=cr.collection_id WHERE cr.repo_id=?", (rid,))],
            "categories": [c["name"] for c in ctx.db.all("SELECT c.name FROM repo_categories rc JOIN categories c ON c.id=rc.category_id WHERE rc.repo_id=? AND rc.excluded=0", (rid,))],
        })
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    if fmt == "csv":
        buf = io.StringIO()
        cols = ["id", "full_name", "html_url", "description", "language", "topics", "stars", "license", "starred_at", "pushed_at",
                "archived", "star_state", "favorite", "priority", "tags", "collections", "categories", "note"]
        writer = csv.DictWriter(buf, fieldnames=cols)
        writer.writeheader()
        for it in items:
            writer.writerow({k: "; ".join(it[k]) if isinstance(it[k], list) else it[k] for k in cols})
        return f"starindex-library-{stamp}.csv", "text/csv", buf.getvalue()
    collections = [dict(r) for r in ctx.db.all("SELECT id, name, description, kind, smart_query FROM collections")]
    reports = [dict(r) for r in ctx.db.all("SELECT id, kind, title, question, answer_md, created_at FROM reports WHERE saved=1")]
    body = json.dumps({"exported_at": utcnow(), "repositories": items, "collections": collections, "reports": reports}, indent=2, ensure_ascii=False)
    return f"starindex-library-{stamp}.json", "application/json", body
