"""Repository persistence: GitHub upserts, filter compilation, read models.

This is the only module that writes the ``repos`` table from GitHub data, and the only one
that turns ``RepoFilters`` into SQL -- the library listing and every search mode share it.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ..api.schemas import (
    CategoryRef,
    CollectionRef,
    Facets,
    FacetValue,
    NameChange,
    RepoDetail,
    RepoFilters,
    RepoListRequest,
    RepoPage,
    RepoSummary,
)
from ..db import Database, dumps, loads, placeholders, utcnow

MAINTENANCE_LABELS = {
    "active": "Active (pushed within 90 days)",
    "maintained": "Maintained (within a year)",
    "slowing": "Slowing (1–2 years)",
    "stale": "Stale (over 2 years)",
    "archived": "Archived",
    "unknown": "Unknown",
}


def maintenance_status(pushed_at: str | None, archived: bool, now: datetime | None = None) -> str:
    if archived:
        return "archived"
    if not pushed_at:
        return "unknown"
    try:
        pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    age = (now or datetime.now(UTC)) - pushed
    if age <= timedelta(days=90):
        return "active"
    if age <= timedelta(days=365):
        return "maintained"
    if age <= timedelta(days=730):
        return "slowing"
    return "stale"


def _cutoff(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------- upsert


@dataclass
class UpsertResult:
    repo_id: int
    outcome: str            # added | updated | unchanged
    renamed: bool = False
    restarred: bool = False
    pushed_changed: bool = False
    old_full_name: str | None = None


_META_FIELDS = (
    "full_name", "description", "homepage", "topics", "language", "stars", "forks", "watchers", "open_issues",
    "license_spdx", "default_branch", "size_kb", "is_fork", "is_archived", "is_disabled", "is_private",
    "pushed_at", "updated_at",
)


def github_to_row(repo: dict[str, Any]) -> dict[str, Any]:
    """Map a GitHub REST repository object to our column values."""
    owner = repo.get("owner") or {}
    license_ = repo.get("license") or {}
    spdx = license_.get("spdx_id")
    if spdx in ("NOASSERTION", ""):
        spdx = None
    full_name = repo.get("full_name") or f"{owner.get('login', '')}/{repo.get('name', '')}"
    parent = repo.get("parent") or {}
    return {
        "id": int(repo["id"]),
        "node_id": repo.get("node_id"),
        "full_name": full_name,
        "owner": owner.get("login") or full_name.split("/")[0],
        "name": repo.get("name") or full_name.split("/")[-1],
        "owner_type": owner.get("type"),
        "owner_avatar_url": owner.get("avatar_url"),
        "description": repo.get("description"),
        "homepage": repo.get("homepage") or None,
        "html_url": repo.get("html_url") or f"https://github.com/{full_name}",
        "clone_url": repo.get("clone_url"),
        "topics": dumps(sorted(set(repo.get("topics") or []))),
        "language": repo.get("language"),
        "stars": int(repo.get("stargazers_count") or 0),
        "forks": int(repo.get("forks_count") or 0),
        "watchers": int(repo.get("subscribers_count") or repo.get("watchers_count") or 0),
        "open_issues": int(repo.get("open_issues_count") or 0),
        "license_spdx": spdx,
        "license_name": license_.get("name"),
        "default_branch": repo.get("default_branch"),
        "size_kb": int(repo.get("size") or 0),
        "is_fork": int(bool(repo.get("fork"))),
        "is_archived": int(bool(repo.get("archived"))),
        "is_disabled": int(bool(repo.get("disabled"))),
        "is_private": int(bool(repo.get("private"))),
        "is_template": int(bool(repo.get("is_template"))),
        "parent_full_name": parent.get("full_name"),
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
    }


def _meta_hash(row: dict[str, Any]) -> str:
    h = hashlib.sha1()
    for f in _META_FIELDS:
        h.update(repr(row.get(f)).encode())
        h.update(b"\x1f")
    return h.hexdigest()


def upsert_from_github(
    c: sqlite3.Connection,
    repo: dict[str, Any],
    *,
    starred_at: str | None,
    run_id: int | None,
    default_index_level: str = "docs",
    mark_starred: bool = True,
) -> UpsertResult:
    """Insert or update one repository by its immutable id. Never touches annotation tables."""
    row = github_to_row(repo)
    now = utcnow()
    mhash = _meta_hash(row)
    existing = c.execute(
        "SELECT full_name, metadata_hash, star_state, pushed_at, starred_at, parent_full_name FROM repos WHERE id=?",
        (row["id"],),
    ).fetchone()
    if existing is None:
        cols = {
            **row,
            "starred_at": starred_at,
            "star_state": "starred" if mark_starred else "unstarred",
            "first_seen_at": now,
            "last_seen_at": now,
            "last_seen_run": run_id,
            "metadata_hash": mhash,
            "index_level": default_index_level,
            "index_status": "pending",
        }
        names = list(cols)
        c.execute(
            f"INSERT INTO repos({', '.join(names)}) VALUES ({placeholders(len(names))})",
            [cols[n] for n in names],
        )
        return UpsertResult(row["id"], "added")

    result = UpsertResult(row["id"], "unchanged")
    if existing["full_name"] != row["full_name"]:
        result.renamed = True
        result.old_full_name = existing["full_name"]
        c.execute(
            "INSERT INTO repo_name_history(repo_id, old_full_name, new_full_name, changed_at) VALUES (?,?,?,?)",
            (row["id"], existing["full_name"], row["full_name"], now),
        )
    if mark_starred and existing["star_state"] != "starred":
        result.restarred = True
    result.pushed_changed = existing["pushed_at"] != row["pushed_at"]
    if existing["metadata_hash"] != mhash or result.renamed:
        result.outcome = "updated"
    if row["parent_full_name"] is None:       # list endpoints omit `parent`; keep what we know
        row["parent_full_name"] = existing["parent_full_name"]

    sets = {k: v for k, v in row.items() if k != "id"}
    sets.update({"last_seen_at": now, "metadata_hash": mhash, "access_state": "ok", "access_checked_at": now})
    if run_id is not None:
        sets["last_seen_run"] = run_id
    if mark_starred:
        sets["star_state"] = "starred"
        sets["unstarred_at"] = None
        if starred_at:
            sets["starred_at"] = starred_at
    names = list(sets)
    c.execute(
        f"UPDATE repos SET {', '.join(f'{n}=?' for n in names)} WHERE id=?",
        [*[sets[n] for n in names], row["id"]],
    )
    return result


# ---------------------------------------------------------------------------------- filters


@dataclass
class CompiledFilter:
    where: str               # SQL boolean expression over alias `r` ("1=1" when empty)
    params: list[Any]
    restricted: bool         # True when anything beyond the default star_state filter applies

    def ids(self, db: Database) -> list[int]:
        return [r[0] for r in db.all(f"SELECT r.id FROM repos r WHERE {self.where}", self.params)]


def compile_filters(
    filters: RepoFilters,
    *,
    text: str = "",
    smart_resolver: Callable[[int], list[int] | None] | None = None,
    skip: Iterable[str] = (),
) -> CompiledFilter:
    """Translate RepoFilters into SQL. Values within one facet are OR-ed; facets are AND-ed.

    ``skip`` omits facets by name (used to compute facet counts that ignore their own selection).
    """
    skip = set(skip)
    clauses: list[str] = []
    params: list[Any] = []
    restricted = False

    def add(clause: str, *values: Any) -> None:
        nonlocal restricted
        clauses.append(clause)
        params.extend(values)
        restricted = True

    f = filters
    if f.star_state != "all":
        clauses.append("r.star_state=?")
        params.append(f.star_state)
        if f.star_state != "starred":
            restricted = True
    if f.languages and "languages" not in skip:
        add(f"lower(COALESCE(r.language,'')) IN ({placeholders(len(f.languages))})", *[v.lower() for v in f.languages])
    if f.topics and "topics" not in skip:
        add(
            f"EXISTS (SELECT 1 FROM json_each(r.topics) WHERE lower(json_each.value) IN ({placeholders(len(f.topics))}))",
            *[v.lower() for v in f.topics],
        )
    if f.categories and "categories" not in skip:
        add(
            "EXISTS (SELECT 1 FROM repo_categories rc JOIN categories cat ON cat.id=rc.category_id "
            f"WHERE rc.repo_id=r.id AND rc.excluded=0 AND cat.slug IN ({placeholders(len(f.categories))}))",
            *f.categories,
        )
    if f.collections and "collections" not in skip:
        manual: list[int] = []
        smart_ids: set[int] = set()
        any_smart = False
        for cid in f.collections:
            resolved = smart_resolver(cid) if smart_resolver else None
            if resolved is None:
                manual.append(cid)
            else:
                any_smart = True
                smart_ids.update(resolved)
        parts = []
        if manual:
            parts.append(f"EXISTS (SELECT 1 FROM collection_repos cr WHERE cr.repo_id=r.id AND cr.collection_id IN ({placeholders(len(manual))}))")
        if any_smart:
            parts.append(f"r.id IN ({','.join(str(int(i)) for i in smart_ids) or 'NULL'})")
        add("(" + " OR ".join(parts) + ")", *manual)
    if f.tags and "tags" not in skip:
        add(
            "EXISTS (SELECT 1 FROM repo_tags rt JOIN tags t ON t.id=rt.tag_id "
            f"WHERE rt.repo_id=r.id AND lower(t.name) IN ({placeholders(len(f.tags))}))",
            *[v.lower() for v in f.tags],
        )
    if f.licenses and "licenses" not in skip:
        named = [v.lower() for v in f.licenses if v.lower() != "none"]
        parts = []
        if named:
            parts.append(f"lower(COALESCE(r.license_spdx,'')) IN ({placeholders(len(named))})")
        if len(named) != len(f.licenses):
            parts.append("r.license_spdx IS NULL")
        add("(" + " OR ".join(parts) + ")", *named)
    if f.owners and "owners" not in skip:
        add(f"lower(r.owner) IN ({placeholders(len(f.owners))})", *[v.lower() for v in f.owners])
    if f.min_stars is not None:
        add("r.stars>=?", f.min_stars)
    if f.max_stars is not None:
        add("r.stars<=?", f.max_stars)
    if f.min_size_kb is not None:
        add("r.size_kb>=?", f.min_size_kb)
    if f.max_size_kb is not None:
        add("r.size_kb<=?", f.max_size_kb)
    if f.archived is not None:
        add("r.is_archived=?", int(f.archived))
    if f.fork is not None:
        add("r.is_fork=?", int(f.fork))
    if f.favorite is not None:
        add(
            ("EXISTS" if f.favorite else "NOT EXISTS")
            + " (SELECT 1 FROM repo_annotations a WHERE a.repo_id=r.id AND a.favorite=1)"
        )
    if f.has_note is not None:
        add(
            ("EXISTS" if f.has_note else "NOT EXISTS")
            + " (SELECT 1 FROM repo_annotations a WHERE a.repo_id=r.id AND a.note<>'')"
        )
    if f.maintenance and "maintenance" not in skip:
        parts = []
        mparams: list[Any] = []
        for m in f.maintenance:
            if m == "archived":
                parts.append("r.is_archived=1")
            elif m == "active":
                parts.append("(r.is_archived=0 AND r.pushed_at>=?)")
                mparams.append(_cutoff(90))
            elif m == "maintained":
                parts.append("(r.is_archived=0 AND r.pushed_at<? AND r.pushed_at>=?)")
                mparams += [_cutoff(90), _cutoff(365)]
            elif m == "slowing":
                parts.append("(r.is_archived=0 AND r.pushed_at<? AND r.pushed_at>=?)")
                mparams += [_cutoff(365), _cutoff(730)]
            elif m == "stale":
                parts.append("(r.is_archived=0 AND r.pushed_at<?)")
                mparams.append(_cutoff(730))
            elif m == "unknown":
                parts.append("(r.is_archived=0 AND r.pushed_at IS NULL)")
        if parts:
            add("(" + " OR ".join(parts) + ")", *mparams)
    if f.index_status and "index_status" not in skip:
        add(f"r.index_status IN ({placeholders(len(f.index_status))})", *f.index_status)
    if f.indexed_level and "indexed_level" not in skip:
        add(f"COALESCE(r.indexed_level,'none') IN ({placeholders(len(f.indexed_level))})", *f.indexed_level)
    if f.pushed_after:
        add("r.pushed_at>=?", f.pushed_after)
    if f.pushed_before:
        add("r.pushed_at<=?", f.pushed_before + ("T23:59:59Z" if len(f.pushed_before) == 10 else ""))
    if f.starred_after:
        add("r.starred_at>=?", f.starred_after)
    if f.starred_before:
        add("r.starred_at<=?", f.starred_before + ("T23:59:59Z" if len(f.starred_before) == 10 else ""))
    if f.repo_ids:
        add(f"r.id IN ({','.join(str(int(i)) for i in f.repo_ids)})")
    if text.strip():
        like = f"%{_escape_like(text.strip().lower())}%"
        add(
            "(lower(r.full_name) LIKE ? ESCAPE '\\' OR lower(COALESCE(r.description,'')) LIKE ? ESCAPE '\\' "
            "OR lower(r.topics) LIKE ? ESCAPE '\\')",
            like, like, like,
        )
    return CompiledFilter(" AND ".join(clauses) or "1=1", params, restricted)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------------- read models


_SUMMARY_COLS = (
    "r.id, r.full_name, r.owner, r.name, r.owner_avatar_url, r.description, r.html_url, r.homepage, r.topics, "
    "r.language, r.stars, r.forks, r.license_spdx, r.is_fork, r.is_archived, r.is_private, r.pushed_at, "
    "r.updated_at, r.created_at, r.starred_at, r.star_state, r.access_state, r.size_kb, r.index_level, "
    "r.indexed_level, r.index_status, r.indexed_at"
)


def _summary_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "full_name": row["full_name"],
        "owner": row["owner"],
        "name": row["name"],
        "owner_avatar_url": row["owner_avatar_url"],
        "description": row["description"],
        "html_url": row["html_url"],
        "homepage": row["homepage"],
        "topics": loads(row["topics"], []),
        "language": row["language"],
        "stars": row["stars"],
        "forks": row["forks"],
        "license_spdx": row["license_spdx"],
        "is_fork": bool(row["is_fork"]),
        "is_archived": bool(row["is_archived"]),
        "is_private": bool(row["is_private"]),
        "pushed_at": row["pushed_at"],
        "updated_at": row["updated_at"],
        "created_at": row["created_at"],
        "starred_at": row["starred_at"],
        "star_state": row["star_state"],
        "access_state": row["access_state"],
        "size_kb": row["size_kb"],
        "index_level": row["index_level"],
        "indexed_level": row["indexed_level"],
        "index_status": row["index_status"],
        "indexed_at": row["indexed_at"],
        "maintenance": maintenance_status(row["pushed_at"], bool(row["is_archived"])),
    }


def _attach_relations(db: Database, items: dict[int, dict[str, Any]]) -> None:
    if not items:
        return
    ids = list(items)
    for chunk_start in range(0, len(ids), 500):
        part = ids[chunk_start : chunk_start + 500]
        ph = placeholders(len(part))
        for row in db.all(
            f"SELECT rc.repo_id, c.id, c.slug, c.name, rc.source, rc.confidence FROM repo_categories rc "
            f"JOIN categories c ON c.id=rc.category_id WHERE rc.excluded=0 AND rc.repo_id IN ({ph}) "
            f"ORDER BY rc.confidence DESC, c.sort_order",
            part,
        ):
            items[row["repo_id"]].setdefault("categories", []).append(
                CategoryRef(id=row["id"], slug=row["slug"], name=row["name"], source=row["source"], confidence=row["confidence"])
            )
        for row in db.all(
            f"SELECT rt.repo_id, t.name FROM repo_tags rt JOIN tags t ON t.id=rt.tag_id WHERE rt.repo_id IN ({ph}) ORDER BY t.name",
            part,
        ):
            items[row["repo_id"]].setdefault("tags", []).append(row["name"])
        for row in db.all(
            f"SELECT cr.repo_id, c.id, c.name FROM collection_repos cr JOIN collections c ON c.id=cr.collection_id "
            f"WHERE cr.repo_id IN ({ph}) ORDER BY c.name",
            part,
        ):
            items[row["repo_id"]].setdefault("collections", []).append(CollectionRef(id=row["id"], name=row["name"]))
        for row in db.all(
            f"SELECT repo_id, favorite, priority, note<>'' AS has_note FROM repo_annotations WHERE repo_id IN ({ph})", part
        ):
            items[row["repo_id"]].update(
                favorite=bool(row["favorite"]), priority=row["priority"], has_note=bool(row["has_note"])
            )


def get_summaries(db: Database, repo_ids: Iterable[int]) -> dict[int, RepoSummary]:
    """Batch-load repository cards, keyed by id."""
    ids = list(dict.fromkeys(int(i) for i in repo_ids))
    items: dict[int, dict[str, Any]] = {}
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        for row in db.all(f"SELECT {_SUMMARY_COLS} FROM repos r WHERE r.id IN ({placeholders(len(part))})", part):
            items[row["id"]] = _summary_from_row(row)
    _attach_relations(db, items)
    return {i: RepoSummary(**data) for i, data in items.items()}


def ordered_summaries(db: Database, repo_ids: Iterable[int]) -> list[RepoSummary]:
    ids = list(repo_ids)
    found = get_summaries(db, ids)
    return [found[i] for i in ids if i in found]


def get_detail(db: Database, repo_id: int) -> RepoDetail | None:
    row = db.one("SELECT * FROM repos r WHERE r.id=?", (repo_id,))
    if row is None:
        return None
    data = _summary_from_row(row)
    items = {repo_id: data}
    _attach_relations(db, items)
    ann = db.one("SELECT note, last_opened_at, open_count FROM repo_annotations WHERE repo_id=?", (repo_id,))
    history = [
        NameChange(old_full_name=h["old_full_name"], new_full_name=h["new_full_name"], changed_at=h["changed_at"])
        for h in db.all("SELECT * FROM repo_name_history WHERE repo_id=? ORDER BY id DESC", (repo_id,))
    ]
    embedded = db.scalar("SELECT count(*) FROM chunks WHERE repo_id=? AND embedded_model_key IS NOT NULL", (repo_id,), 0)
    return RepoDetail(
        **data,
        node_id=row["node_id"],
        clone_url=row["clone_url"],
        watchers=row["watchers"],
        open_issues=row["open_issues"],
        license_name=row["license_name"],
        default_branch=row["default_branch"],
        is_template=bool(row["is_template"]),
        is_disabled=bool(row["is_disabled"]),
        parent_full_name=row["parent_full_name"],
        languages=loads(row["languages"], {}),
        unstarred_at=row["unstarred_at"],
        index_error=row["index_error"],
        indexed_commit_sha=row["indexed_commit_sha"],
        tree_truncated=bool(row["tree_truncated"]),
        doc_count=row["doc_count"],
        chunk_count=row["chunk_count"],
        embedded_chunk_count=embedded,
        note=ann["note"] if ann else "",
        last_opened_at=ann["last_opened_at"] if ann else None,
        open_count=ann["open_count"] if ann else 0,
        name_history=history,
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
    )


def find_by_name(db: Database, full_name: str) -> int | None:
    """Resolve owner/name to a repository id, following recorded renames."""
    row = db.one("SELECT id FROM repos WHERE full_name=? COLLATE NOCASE ORDER BY star_state='starred' DESC LIMIT 1", (full_name,))
    if row:
        return row["id"]
    row = db.one(
        "SELECT repo_id FROM repo_name_history WHERE old_full_name=? COLLATE NOCASE ORDER BY id DESC LIMIT 1", (full_name,)
    )
    return row["repo_id"] if row else None


_SORTS = {
    "starred_at": "r.starred_at",
    "stars": "r.stars",
    "pushed_at": "r.pushed_at",
    "name": "lower(r.full_name)",
    "updated_at": "r.updated_at",
    "size": "r.size_kb",
    "last_opened": "(SELECT a.last_opened_at FROM repo_annotations a WHERE a.repo_id=r.id)",
    "priority": "(SELECT a.priority*10 + a.favorite FROM repo_annotations a WHERE a.repo_id=r.id)",
}


def list_repos(db: Database, req: RepoListRequest, *, smart_resolver: Callable[[int], list[int] | None] | None = None) -> RepoPage:
    cf = compile_filters(req.filters, text=req.text, smart_resolver=smart_resolver)
    total = db.scalar(f"SELECT count(*) FROM repos r WHERE {cf.where}", cf.params, 0)
    order = "ASC" if req.order == "asc" else "DESC"
    sort = _SORTS.get(req.sort, "r.starred_at")
    rows = db.all(
        f"SELECT r.id FROM repos r WHERE {cf.where} ORDER BY {sort} IS NULL, {sort} {order}, r.id DESC LIMIT ? OFFSET ?",
        (*cf.params, req.limit, req.offset),
    )
    return RepoPage(items=ordered_summaries(db, [r["id"] for r in rows]), total=total, limit=req.limit, offset=req.offset)


def compute_facets(
    db: Database,
    filters: RepoFilters,
    *,
    text: str = "",
    restrict_ids: list[int] | None = None,
    smart_resolver: Callable[[int], list[int] | None] | None = None,
    top: int = 40,
) -> Facets:
    """Facet counts. Each facet ignores its own selection so alternatives stay visible."""

    def base(skip: str) -> tuple[str, list[Any]]:
        cf = compile_filters(filters, text=text, smart_resolver=smart_resolver, skip=[skip])
        where, params = cf.where, list(cf.params)
        if restrict_ids is not None:
            where += f" AND r.id IN ({','.join(str(int(i)) for i in restrict_ids) or 'NULL'})"
        return where, params

    def simple(skip: str, expr: str, limit: int = top) -> list[FacetValue]:
        where, params = base(skip)
        rows = db.all(
            f"SELECT {expr} AS v, count(*) AS n FROM repos r WHERE {where} GROUP BY v HAVING v IS NOT NULL AND v<>'' ORDER BY n DESC, v LIMIT ?",
            (*params, limit),
        )
        return [FacetValue(value=str(r["v"]), count=r["n"]) for r in rows]

    out = Facets()
    out.languages = simple("languages", "r.language")
    out.owners = simple("owners", "r.owner", 25)
    out.index_status = simple("index_status", "r.index_status")
    out.indexed_level = simple("indexed_level", "COALESCE(r.indexed_level,'none')")

    where, params = base("licenses")
    rows = db.all(
        f"SELECT COALESCE(r.license_spdx,'none') AS v, count(*) AS n FROM repos r WHERE {where} GROUP BY v ORDER BY n DESC LIMIT ?",
        (*params, top),
    )
    out.licenses = [FacetValue(value=r["v"], label="No license" if r["v"] == "none" else r["v"], count=r["n"]) for r in rows]

    where, params = base("topics")
    rows = db.all(
        f"SELECT lower(j.value) AS v, count(*) AS n FROM repos r, json_each(r.topics) j WHERE {where} GROUP BY v ORDER BY n DESC, v LIMIT ?",
        (*params, 60),
    )
    out.topics = [FacetValue(value=r["v"], count=r["n"]) for r in rows]

    where, params = base("categories")
    rows = db.all(
        f"SELECT cat.slug AS v, cat.name AS label, count(*) AS n FROM repos r JOIN repo_categories rc ON rc.repo_id=r.id AND rc.excluded=0 "
        f"JOIN categories cat ON cat.id=rc.category_id WHERE {where} GROUP BY cat.id ORDER BY n DESC",
        params,
    )
    out.categories = [FacetValue(value=r["v"], label=r["label"], count=r["n"]) for r in rows]

    where, params = base("tags")
    rows = db.all(
        f"SELECT t.name AS v, count(*) AS n FROM repos r JOIN repo_tags rt ON rt.repo_id=r.id JOIN tags t ON t.id=rt.tag_id "
        f"WHERE {where} GROUP BY t.id ORDER BY n DESC, v LIMIT ?",
        (*params, top),
    )
    out.tags = [FacetValue(value=r["v"], count=r["n"]) for r in rows]

    where, params = base("collections")
    rows = db.all(
        f"SELECT c.id AS v, c.name AS label, count(*) AS n FROM repos r JOIN collection_repos cr ON cr.repo_id=r.id "
        f"JOIN collections c ON c.id=cr.collection_id WHERE {where} GROUP BY c.id ORDER BY n DESC",
        params,
    )
    out.collections = [FacetValue(value=str(r["v"]), label=r["label"], count=r["n"]) for r in rows]

    where, params = base("maintenance")
    row = db.one(
        "SELECT sum(r.is_archived=1) AS archived, "
        "sum(r.is_archived=0 AND r.pushed_at>=?) AS active, "
        "sum(r.is_archived=0 AND r.pushed_at<? AND r.pushed_at>=?) AS maintained, "
        "sum(r.is_archived=0 AND r.pushed_at<? AND r.pushed_at>=?) AS slowing, "
        "sum(r.is_archived=0 AND r.pushed_at<?) AS stale "
        f"FROM repos r WHERE {where}",
        (_cutoff(90), _cutoff(90), _cutoff(365), _cutoff(365), _cutoff(730), _cutoff(730), *params),
    )
    if row:
        out.maintenance = [
            FacetValue(value=k, label=MAINTENANCE_LABELS[k], count=row[k] or 0)
            for k in ("active", "maintained", "slowing", "stale", "archived")
            if row[k]
        ]
    return out


# ---------------------------------------------------------------------------------- deletion


def delete_repo(db: Database, repo_id: int) -> None:
    """Remove a repository and everything derived from it (cascades to annotations)."""
    with db.tx() as c:
        c.execute("DELETE FROM repo_fts WHERE rowid=?", (repo_id,))
        c.execute("DELETE FROM repo_name_trgm WHERE rowid=?", (repo_id,))
        c.execute("DELETE FROM repos WHERE id=?", (repo_id,))


def has_personal_data(db: Database, repo_id: int) -> bool:
    """True when the user attached anything to this repository (notes, tags, collections, reports)."""
    if db.one("SELECT 1 FROM repo_annotations WHERE repo_id=? AND (note<>'' OR favorite=1 OR priority>0)", (repo_id,)):
        return True
    if db.one("SELECT 1 FROM repo_tags WHERE repo_id=? LIMIT 1", (repo_id,)):
        return True
    if db.one("SELECT 1 FROM collection_repos WHERE repo_id=? LIMIT 1", (repo_id,)):
        return True
    if db.one("SELECT 1 FROM repo_categories WHERE repo_id=? AND source='user' LIMIT 1", (repo_id,)):
        return True
    return bool(
        db.one(
            "SELECT 1 FROM reports, json_each(reports.repo_ids) j WHERE reports.saved=1 AND j.value=? LIMIT 1", (repo_id,)
        )
    )
