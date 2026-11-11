"""Derived views over the library: related repositories, duplicates, dependencies, overview."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from ..api.schemas import Dependency, DuplicateGroup, FacetValue, LibraryOverview, RelatedRepo, RelationReason
from ..db import loads, placeholders
from . import repos as repo_store

if TYPE_CHECKING:
    from ..context import AppContext

GENERIC_TOPICS = {"hacktoberfest", "awesome", "python", "javascript", "typescript", "rust", "go", "golang", "java", "open-source", "opensource"}


def dependencies(ctx: AppContext, repo_id: int) -> list[Dependency]:
    rows = ctx.db.all(
        "SELECT d.ecosystem, d.name, d.version_spec, d.scope, d.manifest_path, "
        "(SELECT count(DISTINCT o.repo_id) FROM repo_dependencies o JOIN repos r ON r.id=o.repo_id "
        " WHERE o.ecosystem=d.ecosystem AND o.name=d.name AND o.repo_id<>d.repo_id AND r.star_state='starred') AS shared "
        "FROM repo_dependencies d WHERE d.repo_id=? ORDER BY d.scope='runtime' DESC, shared DESC, d.name",
        (repo_id,),
    )
    return [
        Dependency(ecosystem=r["ecosystem"], name=r["name"], version_spec=r["version_spec"], scope=r["scope"],
                   manifest_path=r["manifest_path"], shared_with=r["shared"])
        for r in rows
    ]


def related(ctx: AppContext, repo_id: int, *, limit: int = 12) -> list[RelatedRepo]:
    """Related starred repositories from semantic similarity plus explicit relationship evidence."""
    db = ctx.db
    base = db.one("SELECT full_name, owner, topics, parent_full_name, language FROM repos WHERE id=?", (repo_id,))
    if base is None:
        return []
    scores: dict[int, float] = {}
    reasons: dict[int, list[RelationReason]] = {}

    def add(rid: int, weight: float, kind: str, detail: str) -> None:
        if rid == repo_id:
            return
        scores[rid] = scores.get(rid, 0.0) + weight
        reasons.setdefault(rid, []).append(RelationReason(kind=kind, detail=detail, weight=round(weight, 3)))  # type: ignore[arg-type]

    for rid, sim in ctx.search.similar_repos(repo_id, limit=30):
        if sim >= 0.45:
            add(rid, 0.6 * sim, "semantic", f"Similar description and README (similarity {sim:.2f})")

    topics = {t.lower() for t in loads(base["topics"], [])} - GENERIC_TOPICS
    if topics:
        rows = db.all(
            f"SELECT r.id, group_concat(lower(j.value), '|') AS shared, count(*) AS n, json_array_length(r.topics) AS total "
            f"FROM repos r, json_each(r.topics) j WHERE r.star_state='starred' AND r.id<>? AND lower(j.value) IN ({placeholders(len(topics))}) "
            f"GROUP BY r.id ORDER BY n DESC LIMIT 40",
            (repo_id, *topics),
        )
        for r in rows:
            jaccard = r["n"] / max(1, len(topics) + (r["total"] or 0) - r["n"])
            shared = sorted(set(r["shared"].split("|")))
            add(r["id"], 0.35 * jaccard + 0.03 * min(r["n"], 4), "topics", "Shared topics: " + ", ".join(shared[:6]))

    deps = db.all("SELECT ecosystem, name FROM repo_dependencies WHERE repo_id=? AND scope IN ('runtime','peer')", (repo_id,))
    if deps:
        total_repos = max(2, db.scalar("SELECT count(DISTINCT repo_id) FROM repo_dependencies", default=2))
        overlap: dict[int, list[tuple[str, float]]] = {}
        for d in deps[:150]:
            others = db.all(
                "SELECT DISTINCT o.repo_id FROM repo_dependencies o JOIN repos r ON r.id=o.repo_id WHERE o.ecosystem=? AND o.name=? AND o.repo_id<>? AND r.star_state='starred'",
                (d["ecosystem"], d["name"], repo_id),
            )
            if not others or len(others) > total_repos * 0.5:      # ubiquitous dependencies say nothing
                continue
            rarity = math.log(total_repos / len(others)) / math.log(total_repos)
            for o in others:
                overlap.setdefault(o["repo_id"], []).append((d["name"], rarity))
        for rid, items in overlap.items():
            weight = min(0.3, 0.08 * sum(w for _, w in items))
            names = [n for n, _ in sorted(items, key=lambda it: it[1], reverse=True)[:5]]
            add(rid, weight, "dependencies", "Shared dependencies: " + ", ".join(names))

    cats = [r["category_id"] for r in db.all("SELECT category_id FROM repo_categories WHERE repo_id=? AND excluded=0", (repo_id,))]
    if cats:
        for r in db.all(
            f"SELECT rc.repo_id, group_concat(c.name, ', ') AS names FROM repo_categories rc JOIN categories c ON c.id=rc.category_id "
            f"WHERE rc.excluded=0 AND rc.category_id IN ({placeholders(len(cats))}) AND rc.repo_id<>? GROUP BY rc.repo_id",
            (*cats, repo_id),
        ):
            if r["repo_id"] in scores:      # category alone is too weak to introduce a candidate
                add(r["repo_id"], 0.05, "category", f"Same category: {r['names']}")

    for r in db.all("SELECT id FROM repos WHERE owner=? AND id<>? AND star_state='starred' LIMIT 20", (base["owner"], repo_id)):
        add(r["id"], 0.08, "owner", f"Same owner: {base['owner']}")
    for r in db.all(
        "SELECT id, full_name FROM repos WHERE id<>? AND (full_name=? COLLATE NOCASE OR parent_full_name=? COLLATE NOCASE)",
        (repo_id, base["parent_full_name"] or "\x00", base["full_name"]),
    ):
        add(r["id"], 0.5, "fork", "Fork relationship")

    top = sorted(scores, key=lambda rid: scores[rid], reverse=True)[: limit * 2]
    summaries = repo_store.get_summaries(db, top)
    out = [
        RelatedRepo(repo=summaries[rid], score=round(scores[rid], 4), reasons=sorted(reasons[rid], key=lambda x: x.weight, reverse=True))
        for rid in top if rid in summaries and summaries[rid].star_state == "starred"
    ]
    return out[:limit]


def duplicates(ctx: AppContext, *, threshold: float = 0.93) -> list[DuplicateGroup]:
    db = ctx.db
    groups: list[DuplicateGroup] = []
    used: set[frozenset[int]] = set()

    def emit(reason: str, detail: str, ids: list[int]) -> None:
        key = frozenset(ids)
        if len(key) < 2 or key in used:
            return
        used.add(key)
        repos = repo_store.ordered_summaries(db, ids)
        if len(repos) >= 2:
            groups.append(DuplicateGroup(reason=reason, detail=detail, repos=repos))  # type: ignore[arg-type]

    for r in db.all(
        "SELECT f.id AS fork_id, p.id AS parent_id, p.full_name FROM repos f JOIN repos p ON p.full_name=f.parent_full_name COLLATE NOCASE "
        "WHERE f.star_state='starred' AND p.star_state='starred'"
    ):
        emit("fork", f"Fork and its upstream {r['full_name']} are both starred", [r["parent_id"], r["fork_id"]])
    for r in db.all(
        "SELECT lower(name) AS n, group_concat(id) AS ids FROM repos WHERE star_state='starred' GROUP BY lower(name) HAVING count(*)>1"
    ):
        emit("same_name", f"Several repositories are named “{r['n']}”", [int(i) for i in r["ids"].split(",")])

    key = ctx.embeddings.model_key
    rows = db.all(
        "SELECT r.id, e.vector FROM repos r JOIN embeddings e ON e.content_hash=r.embed_text_hash AND e.model_key=? "
        "WHERE r.star_state='starred' AND r.embedded_model_key=? LIMIT 6000",
        (key, key),
    )
    if len(rows) >= 2:
        ids = [r["id"] for r in rows]
        mat = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        sims = mat @ mat.T
        np.fill_diagonal(sims, 0.0)
        pairs = np.argwhere(np.triu(sims >= threshold))
        for a, b in pairs[:200]:
            emit("near_duplicate", f"Nearly identical description and README (similarity {sims[a, b]:.2f})", [ids[a], ids[b]])
    return groups


def overview(ctx: AppContext) -> LibraryOverview:
    db = ctx.db

    def ids(sql: str, params: tuple = ()) -> list[int]:
        return [r[0] for r in db.all(sql, params)]

    return LibraryOverview(
        total_starred=db.scalar("SELECT count(*) FROM repos WHERE star_state='starred'", default=0),
        total_unstarred_retained=db.scalar("SELECT count(*) FROM repos WHERE star_state='unstarred'", default=0),
        recently_starred=repo_store.ordered_summaries(db, ids("SELECT id FROM repos WHERE star_state='starred' ORDER BY starred_at DESC LIMIT 8")),
        recently_rediscovered=repo_store.ordered_summaries(
            db, ids("SELECT repo_id FROM repo_annotations WHERE last_opened_at IS NOT NULL ORDER BY last_opened_at DESC LIMIT 8")
        ),
        favorites=repo_store.ordered_summaries(db, ids("SELECT repo_id FROM repo_annotations WHERE favorite=1 ORDER BY priority DESC, updated_at DESC LIMIT 8")),
        needs_attention=repo_store.ordered_summaries(
            db, ids("SELECT id FROM repos WHERE star_state='starred' AND (is_archived=1 OR access_state<>'ok') ORDER BY stars DESC LIMIT 12")
        ),
        category_counts=[
            FacetValue(value=r["slug"], label=r["name"], count=r["n"])
            for r in db.all(
                "SELECT c.slug, c.name, count(*) n FROM repo_categories rc JOIN categories c ON c.id=rc.category_id JOIN repos r ON r.id=rc.repo_id "
                "WHERE rc.excluded=0 AND r.star_state='starred' GROUP BY c.id ORDER BY n DESC"
            )
        ],
    )
