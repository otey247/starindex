"""Personal knowledge: notes, favorites, tags, collections, categories.

These tables are written only here (and by the user through the API). GitHub
synchronisation never touches them.
"""

from __future__ import annotations

import re
from typing import Any

from ..api.schemas import (
    AnnotationPatch,
    Category,
    Collection,
    CollectionCreate,
    CollectionDetail,
    CollectionPatch,
    SearchRequest,
    Tag,
)
from ..db import Database, dumps, loads, placeholders, utcnow
from ..search.lexical import refresh_repo_docs
from . import repos as repo_store


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "category"


# ---------------------------------------------------------------------------------- notes / favorites


def patch_annotation(db: Database, repo_id: int, patch: AnnotationPatch) -> None:
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO repo_annotations(repo_id, updated_at) VALUES (?,?)", (repo_id, utcnow()))
        sets: dict[str, Any] = {"updated_at": utcnow()}
        if patch.note is not None:
            sets["note"] = patch.note
        if patch.favorite is not None:
            sets["favorite"] = int(patch.favorite)
        if patch.priority is not None:
            sets["priority"] = patch.priority
        c.execute(f"UPDATE repo_annotations SET {', '.join(f'{k}=?' for k in sets)} WHERE repo_id=?", (*sets.values(), repo_id))
        if patch.note is not None:
            refresh_repo_docs(c, [repo_id])


def record_opened(db: Database, repo_id: int) -> None:
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO repo_annotations(repo_id, updated_at) VALUES (?,?)", (repo_id, utcnow()))
        c.execute("UPDATE repo_annotations SET last_opened_at=?, open_count=open_count+1 WHERE repo_id=?", (utcnow(), repo_id))


# ---------------------------------------------------------------------------------- tags


def set_tags(db: Database, repo_id: int, tags: list[str]) -> None:
    clean = list(dict.fromkeys(t.strip()[:60] for t in tags if t.strip()))
    with db.tx() as c:
        c.execute("DELETE FROM repo_tags WHERE repo_id=?", (repo_id,))
        for name in clean:
            c.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
            tag_id = c.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()["id"]
            c.execute("INSERT OR IGNORE INTO repo_tags(repo_id, tag_id) VALUES (?,?)", (repo_id, tag_id))
        c.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM repo_tags)")
        refresh_repo_docs(c, [repo_id])


def add_tag(db: Database, repo_ids: list[int], tag: str) -> None:
    name = tag.strip()[:60]
    if not name:
        return
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
        tag_id = c.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()["id"]
        for rid in repo_ids:
            c.execute("INSERT OR IGNORE INTO repo_tags(repo_id, tag_id) VALUES (?,?)", (rid, tag_id))
        refresh_repo_docs(c, repo_ids)


def rename_tag(db: Database, old: str, new: str) -> bool:
    """Rename a tag; merging into an existing tag of the new name."""
    new = new.strip()[:60]
    with db.tx() as c:
        row = c.execute("SELECT id FROM tags WHERE name=?", (old,)).fetchone()
        if row is None:
            return False
        target = c.execute("SELECT id FROM tags WHERE name=?", (new,)).fetchone()
        repo_ids = [r["repo_id"] for r in c.execute("SELECT repo_id FROM repo_tags WHERE tag_id=?", (row["id"],))]
        if target and target["id"] != row["id"]:
            for rid in repo_ids:
                c.execute("INSERT OR IGNORE INTO repo_tags(repo_id, tag_id) VALUES (?,?)", (rid, target["id"]))
            c.execute("DELETE FROM tags WHERE id=?", (row["id"],))
        else:
            c.execute("UPDATE tags SET name=? WHERE id=?", (new, row["id"]))
        refresh_repo_docs(c, repo_ids)
    return True


def delete_tag(db: Database, name: str) -> bool:
    with db.tx() as c:
        row = c.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        if row is None:
            return False
        repo_ids = [r["repo_id"] for r in c.execute("SELECT repo_id FROM repo_tags WHERE tag_id=?", (row["id"],))]
        c.execute("DELETE FROM tags WHERE id=?", (row["id"],))
        refresh_repo_docs(c, repo_ids)
    return True


def list_tags(db: Database) -> list[Tag]:
    return [
        Tag(name=r["name"], count=r["n"])
        for r in db.all("SELECT t.name, count(rt.repo_id) n FROM tags t LEFT JOIN repo_tags rt ON rt.tag_id=t.id GROUP BY t.id ORDER BY n DESC, t.name")
    ]


# ---------------------------------------------------------------------------------- categories


def list_categories(db: Database) -> list[Category]:
    return [
        Category(
            id=r["id"], slug=r["slug"], name=r["name"], description=r["description"], keywords=loads(r["keywords"], []),
            is_builtin=bool(r["is_builtin"]), repo_count=r["n"],
        )
        for r in db.all(
            "SELECT c.*, (SELECT count(*) FROM repo_categories rc JOIN repos r ON r.id=rc.repo_id "
            "WHERE rc.category_id=c.id AND rc.excluded=0 AND r.star_state='starred') n FROM categories c ORDER BY c.sort_order, c.name"
        )
    ]


def create_category(db: Database, name: str, description: str, keywords: list[str]) -> int:
    base = _slug(name)
    slug, n = base, 2
    while db.one("SELECT 1 FROM categories WHERE slug=?", (slug,)):
        slug, n = f"{base}-{n}", n + 1
    with db.tx() as c:
        order = (c.execute("SELECT max(sort_order) FROM categories").fetchone()[0] or 0) + 1
        return int(
            c.execute(
                "INSERT INTO categories(slug, name, description, keywords, is_builtin, sort_order) VALUES (?,?,?,?,0,?)",
                (slug, name.strip(), description, dumps([k.strip() for k in keywords if k.strip()]), order),
            ).lastrowid
        )


def update_category(db: Database, category_id: int, *, name: str | None, description: str | None, keywords: list[str] | None) -> None:
    sets: dict[str, Any] = {}
    if name is not None:
        sets["name"] = name.strip()
    if description is not None:
        sets["description"] = description
    if keywords is not None:
        sets["keywords"] = dumps([k.strip() for k in keywords if k.strip()])
    if sets:
        with db.tx() as c:
            c.execute(f"UPDATE categories SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), category_id))


def delete_category(db: Database, category_id: int) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM categories WHERE id=?", (category_id,))


def set_repo_categories(db: Database, repo_id: int, category_ids: list[int]) -> None:
    """User edit: chosen categories become ``user`` rows; removed automatic ones are remembered as excluded."""
    wanted = set(category_ids)
    now = utcnow()
    with db.tx() as c:
        current = {r["category_id"]: r for r in c.execute("SELECT category_id, source, excluded FROM repo_categories WHERE repo_id=?", (repo_id,))}
        for cid, row in current.items():
            if cid not in wanted and not row["excluded"]:
                c.execute("UPDATE repo_categories SET excluded=1, source='user', updated_at=? WHERE repo_id=? AND category_id=?", (now, repo_id, cid))
        for cid in wanted:
            if not c.execute("SELECT 1 FROM categories WHERE id=?", (cid,)).fetchone():
                continue
            c.execute(
                "INSERT INTO repo_categories(repo_id, category_id, source, confidence, excluded, updated_at) VALUES (?,?,'user',1.0,0,?) "
                "ON CONFLICT(repo_id, category_id) DO UPDATE SET source='user', confidence=1.0, excluded=0, updated_at=excluded.updated_at",
                (repo_id, cid, now),
            )
        refresh_repo_docs(c, [repo_id])


# ---------------------------------------------------------------------------------- collections


def _collection(db: Database, row, *, smart_ids=None, preview: int = 4) -> Collection:
    smart = None
    if row["kind"] == "smart" and row["smart_query"]:
        try:
            smart = SearchRequest.model_validate(loads(row["smart_query"], {}))
        except Exception:
            smart = None
    if row["kind"] == "smart":
        ids = smart_ids(row["id"]) or [] if smart_ids else []
    else:
        ids = [r["repo_id"] for r in db.all("SELECT repo_id FROM collection_repos WHERE collection_id=? ORDER BY added_at DESC", (row["id"],))]
    return Collection(
        id=row["id"], name=row["name"], description=row["description"], kind=row["kind"], smart_query=smart, repo_count=len(ids),
        report_count=db.scalar("SELECT count(*) FROM collection_reports WHERE collection_id=?", (row["id"],), 0),
        created_at=row["created_at"], updated_at=row["updated_at"], preview=repo_store.ordered_summaries(db, ids[:preview]),
    )


def list_collections(db: Database, smart_ids=None) -> list[Collection]:
    return [_collection(db, r, smart_ids=smart_ids) for r in db.all("SELECT * FROM collections ORDER BY lower(name)")]


def get_collection(db: Database, collection_id: int, smart_ids=None) -> CollectionDetail | None:
    from .reports import list_reports

    row = db.one("SELECT * FROM collections WHERE id=?", (collection_id,))
    if row is None:
        return None
    base = _collection(db, row, smart_ids=smart_ids, preview=0)
    if row["kind"] == "smart":
        ids = smart_ids(collection_id) or [] if smart_ids else []
    else:
        ids = [r["repo_id"] for r in db.all("SELECT repo_id FROM collection_repos WHERE collection_id=? ORDER BY added_at DESC", (collection_id,))]
    report_ids = [r["report_id"] for r in db.all("SELECT report_id FROM collection_reports WHERE collection_id=?", (collection_id,))]
    return CollectionDetail(
        **base.model_dump(exclude={"preview"}), preview=[], repos=repo_store.ordered_summaries(db, ids),
        reports=[r for r in list_reports(db, limit=500) if r.id in set(report_ids)],
    )


def create_collection(db: Database, body: CollectionCreate) -> int:
    now = utcnow()
    with db.tx() as c:
        cid = int(
            c.execute(
                "INSERT INTO collections(name, description, kind, smart_query, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (body.name.strip(), body.description, body.kind,
                 dumps(body.smart_query.model_dump(exclude_defaults=True)) if body.smart_query and body.kind == "smart" else None, now, now),
            ).lastrowid
        )
        if body.kind == "manual":
            for rid in dict.fromkeys(body.repo_ids):
                if c.execute("SELECT 1 FROM repos WHERE id=?", (rid,)).fetchone():
                    c.execute("INSERT OR IGNORE INTO collection_repos(collection_id, repo_id, added_at) VALUES (?,?,?)", (cid, rid, now))
    return cid


def patch_collection(db: Database, collection_id: int, patch: CollectionPatch) -> None:
    sets: dict[str, Any] = {"updated_at": utcnow()}
    if patch.name is not None:
        sets["name"] = patch.name.strip()
    if patch.description is not None:
        sets["description"] = patch.description
    if patch.smart_query is not None:
        sets["smart_query"] = dumps(patch.smart_query.model_dump(exclude_defaults=True))
    with db.tx() as c:
        c.execute(f"UPDATE collections SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), collection_id))


def delete_collection(db: Database, collection_id: int) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM collections WHERE id=?", (collection_id,))


def add_repos(db: Database, collection_id: int, repo_ids: list[int]) -> None:
    now = utcnow()
    with db.tx() as c:
        for rid in dict.fromkeys(repo_ids):
            if c.execute("SELECT 1 FROM repos WHERE id=?", (rid,)).fetchone():
                c.execute("INSERT OR IGNORE INTO collection_repos(collection_id, repo_id, added_at) VALUES (?,?,?)", (collection_id, rid, now))
        c.execute("UPDATE collections SET updated_at=? WHERE id=?", (now, collection_id))


def remove_repos(db: Database, collection_id: int, repo_ids: list[int]) -> None:
    if not repo_ids:
        return
    with db.tx() as c:
        c.execute(
            f"DELETE FROM collection_repos WHERE collection_id=? AND repo_id IN ({placeholders(len(repo_ids))})", (collection_id, *repo_ids)
        )
        c.execute("UPDATE collections SET updated_at=? WHERE id=?", (utcnow(), collection_id))
