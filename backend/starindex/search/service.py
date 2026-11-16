"""Hybrid retrieval: lexical + dense, reciprocal rank fusion, optional reranking, bounded priors.

One entry point (``SearchService.search``) serves the UI, Ask, Research tools and smart
collections. No language model is involved anywhere in this module; when the embedding
backend or vector index is unavailable the request degrades to keyword search with a notice.
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..api.schemas import (
    Match,
    RepoFilters,
    ResultSignals,
    SearchRequest,
    SearchResponse,
    SearchResult,
    Suggestion,
)
from ..db import dumps, loads, placeholders, utcnow
from ..library import repos as repo_store
from ..library.repos import CompiledFilter, compile_filters, maintenance_status
from . import lexical
from .embeddings import EmbeddingUnavailable
from .query import Query, chunk_kinds_for, parse_query, words
from .vector import VectorIndex, VectorUnavailable

if TYPE_CHECKING:
    from ..context import AppContext

log = logging.getLogger(__name__)

CANDIDATES = 200          # per retrieval list
CHUNK_CANDIDATES = 400
MATCHES_PER_REPO = 3
# Relative weights of the four ranked lists in the fusion (multiplied by the user-facing
# lexical/semantic weights). Chosen on the evaluation set; see docs/EVALUATION.md.
W_LEX_REPO, W_LEX_CHUNK, W_SEM_REPO, W_SEM_CHUNK = 1.0, 0.5, 1.0, 0.7
# Reranking blends the cross-encoder order with the fused order (rank-based, k=RERANK_K).
# The passage shown to the cross-encoder is the repository's own summary (description, topics,
# language, README opening); "best_chunk" (the top matching passage) measured worse.
RERANK_WEIGHT, RERANK_FUSED_WEIGHT, RERANK_K = 1.0, 1.0, 20
RERANK_PASSAGE = "intro"           # intro | best_chunk
FORK_PRIOR = 0.9
EXPANSIONS = {
    "k8s": "kubernetes", "llm": "language model", "llms": "language models", "rag": "retrieval augmented generation",
    "mcp": "model context protocol", "tts": "text to speech", "stt": "speech to text", "asr": "speech recognition",
    "ocr": "optical character recognition", "orm": "object relational mapper", "ui": "user interface",
    "cli": "command line", "db": "database", "auth": "authentication", "viz": "visualization",
    "3d": "three dimensional webgl", "gpt": "language model", "ci": "continuous integration",
    "iac": "infrastructure as code", "etl": "data pipeline", "vectordb": "vector database", "sso": "single sign on",
    "api gateway": "api management proxy", "pdf": "pdf document",
}


@dataclass
class Candidate:
    repo_id: int
    fused: float = 0.0
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    rerank_score: float | None = None
    prior: float = 1.0
    name_match: bool = False
    score: float = 0.0
    chunk_scores: dict[int, tuple[float, str]] = field(default_factory=dict)   # chunk id -> (score, source)


class SearchService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.db = ctx.db
        self.typos = lexical.TypoCorrector(ctx.db)
        self._expansion_cache: dict[str, list[str]] = {}

    # ------------------------------------------------------------------ helpers

    def _smart_resolver(self, collection_id: int) -> list[int] | None:
        """Repository ids of a smart collection (None for manual collections)."""
        row = self.db.one("SELECT kind, smart_query FROM collections WHERE id=?", (collection_id,))
        if row is None or row["kind"] != "smart" or not row["smart_query"]:
            return None
        try:
            req = SearchRequest.model_validate(loads(row["smart_query"], {}))
        except Exception:
            return []
        req.filters.collections = [c for c in req.filters.collections if c != collection_id]   # no self-reference
        req.limit, req.offset, req.record, req.explain = 200, 0, False, False
        return [r.repo.id for r in self.search(req, _nested=True).results]

    def compile(self, filters: RepoFilters, query: Query | None = None) -> CompiledFilter:
        if query is not None:
            for name in query.operators.get("_collection_names", []):
                row = self.db.one("SELECT id FROM collections WHERE name=? COLLATE NOCASE", (name,))
                filters.collections = [*filters.collections, row["id"] if row else -1]
        return compile_filters(filters, smart_resolver=self._smart_resolver)

    def _vectors(self) -> VectorIndex:
        try:
            return self.ctx.vectors
        except VectorUnavailable:
            raise
        except Exception as exc:
            raise VectorUnavailable(str(exc)) from exc

    def semantic_ready(self) -> tuple[bool, str]:
        if getattr(self.ctx, "keyword_only", False):
            return False, "Semantic search needs the running StarIndex application."
        emb = self.ctx.embeddings
        if not emb.enabled:
            return False, "Semantic search is turned off in Settings."
        have = self.db.scalar(
            "SELECT count(*) FROM repos WHERE embedded_model_key=?", (emb.model_key,), 0
        )
        if not have:
            return False, "The semantic index is still being built."
        return True, ""

    # ------------------------------------------------------------------ retrieval lists

    def _lexical_lists(self, q: Query, cf: CompiledFilter, kinds: list[str] | None, *, with_repo: bool = True):
        repo_hits = lexical.search_repos(self.db, q, cf, limit=CANDIDATES) if with_repo and kinds is None else []
        chunk_hits = lexical.search_chunks(self.db, q, cf, kinds=kinds, limit=CHUNK_CANDIDATES)
        if not repo_hits and not chunk_hits and len(q.terms) > 1:
            # Conjunctive query found nothing: relax to any-term matching.
            repo_hits = lexical.search_repos(self.db, q, cf, limit=CANDIDATES, strict=False) if with_repo and kinds is None else []
            chunk_hits = lexical.search_chunks(self.db, q, cf, kinds=kinds, limit=CHUNK_CANDIDATES, strict=False)
        return repo_hits, chunk_hits

    def _semantic_lists(self, text: str, cf: CompiledFilter, kinds: list[str] | None):
        """Dense retrieval at repository and passage level, honouring the filters.

        * Nothing restricted: plain nearest-neighbour search; unstarred repositories are dropped afterwards.
        * Qdrant server: filters are pushed into the engine (indexed payload pre-filtering).
        * Embedded Qdrant with a selective filter: exact search over the vectors stored in SQLite,
          because the embedded engine evaluates ID filters point by point.
        * Embedded Qdrant with a broad filter over a very large index: over-fetch, then filter.
        """
        from . import exact

        emb = self.ctx.embeddings
        vec = emb.embed_query(text)
        index = self._vectors()
        key = emb.model_key
        repos_name, chunks_name = index.repos_collection(key), index.chunks_collection(key)
        allowed = cf.ids(self.db)
        allowed_set = set(allowed)

        def keep(hits, limit):
            return [h for h in hits if int(h.payload.get("repo_id", 0)) in allowed_set][:limit]

        if not cf.restricted:
            repo_hits = [] if kinds else keep(index.search(repos_name, vec, limit=CANDIDATES + 20), CANDIDATES)
            chunk_hits = keep(index.search(chunks_name, vec, limit=CHUNK_CANDIDATES + 40, kinds=kinds), CHUNK_CANDIDATES)
            return repo_hits, chunk_hits
        if not allowed:
            return [], []
        if index.mode == "server":
            repo_hits = [] if kinds else index.search(repos_name, vec, limit=CANDIDATES, repo_ids=allowed)
            return repo_hits, index.search(chunks_name, vec, limit=CHUNK_CANDIDATES, repo_ids=allowed, kinds=kinds)
        repo_hits = [] if kinds else exact.search_repos(self.db, key, vec, allowed, CANDIDATES)
        if exact.count_chunks(self.db, key, allowed, kinds) <= exact.MAX_EXACT_CHUNKS:
            chunk_hits = exact.search_chunks(self.db, key, vec, allowed, kinds, CHUNK_CANDIDATES)
        else:
            chunk_hits = keep(index.search(chunks_name, vec, limit=CHUNK_CANDIDATES * 8, kinds=kinds), CHUNK_CANDIDATES)
        return repo_hits, chunk_hits

    # ------------------------------------------------------------------ main entry

    def search(self, req: SearchRequest, *, _nested: bool = False, _settings=None) -> SearchResponse:
        """``_settings`` evaluates a request under alternative settings without persisting them."""
        started = time.monotonic()
        settings = _settings or self.ctx.settings
        q, filters = parse_query(req.query, req.filters)
        if settings.search.typo_tolerance and q.terms:
            self.typos.apply(q)
        cf = self.compile(filters, q)
        notices: list[str] = []
        mode = req.mode
        kinds = chunk_kinds_for(q, req.scope)

        if not q.has_text:
            return self._browse(req, q, cf, started)

        want_semantic = mode in ("semantic", "hybrid")
        if want_semantic:
            ok, why = self.semantic_ready()
            if not ok:
                notices.append(f"{why} Showing keyword results.")
                want_semantic, mode = False, "keyword"

        if settings.search.query_expansion == "auto" and mode != "semantic":
            self._expand(q)

        lex_repo, lex_chunk, sem_repo, sem_chunk = [], [], [], []
        if mode in ("keyword", "hybrid"):
            lex_repo, lex_chunk = self._lexical_lists(q, cf, kinds)
        if want_semantic:
            try:
                sem_repo, sem_chunk = self._semantic_lists(q.text or req.query, cf, kinds)
            except (EmbeddingUnavailable, VectorUnavailable) as exc:
                notices.append(f"Semantic search is unavailable ({exc}). Showing keyword results.")
                mode = "keyword"
                if not lex_repo and not lex_chunk:
                    lex_repo, lex_chunk = self._lexical_lists(q, cf, kinds)

        if req.scope in ("docs", "code"):
            return self._passage_results(req, q, mode, lex_chunk, sem_chunk, notices, started)

        cands = self._fuse(q, cf, lex_repo, lex_chunk, sem_repo, sem_chunk, settings)
        total = len(cands)
        if mode == "keyword":
            total = max(total, lexical.count_repos(self.db, q, cf))
        ordered = sorted(cands.values(), key=lambda c: (-c.fused, c.repo_id))

        reranked = False
        use_rerank = settings.rerank.enabled if req.rerank is None else req.rerank
        if use_rerank and mode != "keyword" and ordered and req.sort == "relevance":
            reranked = self._rerank(q, ordered[: settings.rerank.top_n])
        self._apply_priors(ordered, settings)
        ordered = self._sort(ordered, req.sort)
        if settings.search.max_results_per_owner:
            ordered = self._diversify(ordered, settings.search.max_results_per_owner)

        page = ordered[req.offset : req.offset + req.limit]
        results = self._build_results(page, q, explain=req.explain)
        facets = None
        if not _nested:
            facets = repo_store.compute_facets(
                self.db, filters, restrict_ids=[c.repo_id for c in ordered], smart_resolver=self._smart_resolver
            )
        took = int((time.monotonic() - started) * 1000)
        if req.record and not _nested and settings.search.save_history:
            self._record(req, len(ordered))
        return SearchResponse(
            query=req.query, mode=mode, requested_mode=req.mode, scope=req.scope, parsed=q.to_schema(), total=total,
            took_ms=took, results=results, facets=facets, notices=notices, reranked=reranked,
            embedding_model=self.ctx.embeddings.model_key if want_semantic and mode != "keyword" else None,
        )

    # ------------------------------------------------------------------ fusion and ranking

    def _fuse(self, q: Query, cf: CompiledFilter, lex_repo, lex_chunk, sem_repo, sem_chunk, settings) -> dict[int, Candidate]:
        """Weighted reciprocal rank fusion over up to four ranked lists, at repository level."""
        k = settings.search.rrf_k
        lw, sw = settings.search.lexical_weight, settings.search.semantic_weight
        cands: dict[int, Candidate] = {}

        def cand(repo_id: int) -> Candidate:
            c = cands.get(repo_id)
            if c is None:
                c = cands[repo_id] = Candidate(repo_id)
            return c

        for rank, hit in enumerate(lex_repo, start=1):
            c = cand(hit.repo_id)
            c.fused += lw * W_LEX_REPO / (k + rank)
            c.lexical_rank = rank
        seen: dict[int, int] = {}
        for hit in lex_chunk:            # best chunk per repository defines its rank in this list
            c = cand(hit.repo_id)
            c.chunk_scores.setdefault(hit.id, (hit.score, "lexical"))
            if hit.repo_id not in seen:
                seen[hit.repo_id] = len(seen) + 1
                c.fused += lw * W_LEX_CHUNK / (k + seen[hit.repo_id])
                if c.lexical_rank is None:
                    c.lexical_rank = CANDIDATES + seen[hit.repo_id]
        for rank, hit in enumerate(sem_repo, start=1):
            c = cand(int(hit.payload.get("repo_id", hit.id)))
            c.fused += sw * W_SEM_REPO / (k + rank)
            c.semantic_rank = rank
        seen = {}
        for hit in sem_chunk:
            repo_id = int(hit.payload.get("repo_id", 0))
            chunk_id = int(hit.payload.get("chunk_id", 0))
            if not repo_id:
                continue
            c = cand(repo_id)
            if chunk_id:
                prev = c.chunk_scores.get(chunk_id)
                c.chunk_scores[chunk_id] = (max(hit.score, 0.0) + (5.0 if prev else 0.0), "both" if prev else "semantic")
            if repo_id not in seen:
                seen[repo_id] = len(seen) + 1
                c.fused += sw * W_SEM_CHUNK / (k + seen[repo_id])
                if c.semantic_rank is None:
                    c.semantic_rank = CANDIDATES + seen[repo_id]

        # Exact repository-name matches are the strongest lexical signal there is.
        needle = re.sub(r"\s+", "", (q.text or "").lower())
        if needle and len(needle) >= 2:
            for row in self.db.all(
                f"SELECT r.id FROM repos r WHERE {cf.where} AND (lower(r.name)=? OR lower(r.full_name)=? OR replace(replace(lower(r.name),'-',''),'_','')=?)",
                (*cf.params, needle, needle, re.sub(r"[-_]", "", needle)),
            ):
                c = cand(row["id"])
                c.name_match = True
        return cands

    def _rerank(self, q: Query, top: list[Candidate]) -> bool:
        """Reorder the head of the list with the cross-encoder, blended with the fused order."""
        ids = [c.repo_id for c in top]
        rows = {
            r["id"]: r
            for r in self.db.all(f"SELECT id, full_name, description, topics, language FROM repos WHERE id IN ({placeholders(len(ids))})", ids)
        }
        body: dict[int, str] = {}
        if RERANK_PASSAGE == "intro":
            # What the repository says it is: the opening of its README.
            for r in self.db.all(
                f"SELECT repo_id, content FROM chunks WHERE kind='readme' AND ordinal<2 AND repo_id IN ({placeholders(len(ids))}) ORDER BY repo_id, document_id, ordinal",
                ids,
            ):
                body[r["repo_id"]] = (body.get(r["repo_id"], "") + "\n" + r["content"]).strip()
        else:
            best = {c.repo_id: max(c.chunk_scores.items(), key=lambda kv: kv[1][0])[0] for c in top if c.chunk_scores}
            if best:
                texts = {
                    r["id"]: r["content"]
                    for r in self.db.all(f"SELECT id, content FROM chunks WHERE id IN ({placeholders(len(best))})", list(best.values()))
                }
                body = {rid: texts.get(cid, "") for rid, cid in best.items()}
        passages = []
        for c in top:
            r = rows.get(c.repo_id)
            if r is None:
                passages.append("")
                continue
            topics = " ".join(loads(r["topics"], [])[:10])
            lang = f" Written in {r['language']}." if r["language"] else ""
            passages.append(f"{r['full_name']}: {r['description'] or ''}{lang}\nTopics: {topics}\n{body.get(c.repo_id, '')}".strip())
        # Judge against the query as understood (typos corrected), not as typed.
        query_text = q.text or q.raw
        for wrong, right in q.corrections.items():
            query_text = re.sub(rf"\b{re.escape(wrong)}\b", right, query_text, flags=re.IGNORECASE)
        scores = self.ctx.reranker.score(query_text, passages, force=True)
        if scores is None:
            return False
        order = sorted(range(len(top)), key=lambda i: scores[i], reverse=True)
        base = top[0].fused if top else 0.0      # keeps the reranked head above the untouched tail
        for new_rank, i in enumerate(order, start=1):
            c = top[i]
            c.rerank_score = scores[i]
            c.fused = base + RERANK_WEIGHT / (RERANK_K + new_rank) + RERANK_FUSED_WEIGHT / (RERANK_K + i + 1)
        return True

    def _apply_priors(self, cands: list[Candidate], settings) -> None:
        """Small multiplicative boosts for popularity and maintenance; never enough to reorder strong matches."""
        if not cands:
            return
        ids = [c.repo_id for c in cands]
        meta: dict[int, Any] = {}
        for start in range(0, len(ids), 500):
            part = ids[start : start + 500]
            for r in self.db.all(
                f"SELECT id, stars, pushed_at, is_archived, is_fork FROM repos WHERE id IN ({placeholders(len(part))})", part
            ):
                meta[r["id"]] = r
        fresh = {"active": 1.0, "maintained": 0.7, "slowing": 0.35, "stale": 0.1, "archived": 0.0, "unknown": 0.3}
        top = max(c.fused for c in cands) or 1.0
        for c in cands:
            r = meta.get(c.repo_id)
            pop = min(1.0, math.log10((r["stars"] if r else 0) + 1) / 5.0)
            fr = fresh[maintenance_status(r["pushed_at"], bool(r["is_archived"]))] if r else 0.3
            c.prior = 1.0 + settings.search.popularity_boost * pop + settings.search.freshness_boost * fr
            if r and r["is_fork"]:
                c.prior *= FORK_PRIOR          # a fork should not outrank its near-identical upstream
            c.score = c.fused * c.prior
            if c.name_match:
                c.score += top * 2      # exact-name hits lead, ordered among themselves by their own score

    def _sort(self, cands: list[Candidate], sort: str) -> list[Candidate]:
        if sort == "relevance":
            return sorted(cands, key=lambda c: (-c.score, c.repo_id))      # deterministic order for equal scores
        col = {"stars": "stars", "pushed_at": "pushed_at", "starred_at": "starred_at", "name": "lower(full_name)"}[sort]
        ids = [c.repo_id for c in cands]
        by_id = {c.repo_id: c for c in cands}
        order = "ASC" if sort == "name" else "DESC"
        rows: list[int] = []
        for start in range(0, len(ids), 500):
            part = ids[start : start + 500]
            rows += [(r["id"], r["v"]) for r in self.db.all(f"SELECT id, {col} AS v FROM repos WHERE id IN ({placeholders(len(part))})", part)]
        rows.sort(key=lambda t: (t[1] is None, t[1]), reverse=(order == "DESC"))
        return [by_id[i] for i, _ in rows]

    def _diversify(self, cands: list[Candidate], per_owner: int) -> list[Candidate]:
        owners = {
            r["id"]: r["owner"]
            for r in self.db.all(
                f"SELECT id, owner FROM repos WHERE id IN ({','.join(str(c.repo_id) for c in cands) or 'NULL'})"
            )
        }
        counts: dict[str, int] = {}
        kept, overflow = [], []
        for c in cands:
            owner = owners.get(c.repo_id, "")
            counts[owner] = counts.get(owner, 0) + 1
            (kept if counts[owner] <= per_owner else overflow).append(c)
        return kept + overflow

    # ------------------------------------------------------------------ result assembly

    def _chunk_rows(self, chunk_ids: list[int]) -> dict[int, Any]:
        rows: dict[int, Any] = {}
        for start in range(0, len(chunk_ids), 500):
            part = chunk_ids[start : start + 500]
            for r in self.db.all(
                "SELECT c.id, c.repo_id, c.document_id, c.path, c.kind, c.heading, c.start_line, c.end_line, c.content, "
                "d.commit_sha, d.indexed_at, r.html_url, r.default_branch FROM chunks c JOIN documents d ON d.id=c.document_id "
                f"JOIN repos r ON r.id=c.repo_id WHERE c.id IN ({placeholders(len(part))})",
                part,
            ):
                rows[r["id"]] = r
        return rows

    def match_from_row(self, row: Any, terms: list[str], *, score: float = 0.0, source: str = "lexical") -> Match:
        return Match(
            chunk_id=row["id"], document_id=row["document_id"], repo_id=row["repo_id"], path=row["path"], kind=row["kind"],
            heading=row["heading"], start_line=row["start_line"], end_line=row["end_line"],
            snippet=lexical.make_snippet(row["content"], terms), commit_sha=row["commit_sha"], indexed_at=row["indexed_at"],
            html_url=permalink(row["html_url"], row["commit_sha"] or row["default_branch"], row["path"], row["start_line"], row["end_line"]),
            score=round(score, 4), source=source,  # type: ignore[arg-type]
        )

    def _build_results(self, page: list[Candidate], q: Query, *, explain: bool) -> list[SearchResult]:
        summaries = repo_store.get_summaries(self.db, [c.repo_id for c in page])
        terms = q.terms + [w for p in q.phrases for w in words(p)] + list(q.corrections.values())
        wanted: list[int] = []
        tops: dict[int, list[tuple[int, float, str]]] = {}
        for c in page:
            best = sorted(c.chunk_scores.items(), key=lambda kv: kv[1][0], reverse=True)[:MATCHES_PER_REPO]
            tops[c.repo_id] = [(cid, sc, src) for cid, (sc, src) in best]
            wanted += [cid for cid, _ in best]
        rows = self._chunk_rows(wanted)
        results: list[SearchResult] = []
        for c in page:
            repo = summaries.get(c.repo_id)
            if repo is None:
                continue
            matches = [self.match_from_row(rows[cid], terms, score=sc, source=src) for cid, sc, src in tops[c.repo_id] if cid in rows]
            highlights = {}
            if repo.description:
                marked = lexical.highlight(repo.description, terms)
                if marked != repo.description:
                    highlights["description"] = marked
            results.append(
                SearchResult(
                    repo=repo, score=round(c.score, 6), matches=matches, field_highlights=highlights,
                    signals=ResultSignals(
                        lexical_rank=c.lexical_rank, semantic_rank=c.semantic_rank, fused_score=round(c.fused, 6),
                        rerank_score=None if c.rerank_score is None else round(c.rerank_score, 4),
                        prior=round(c.prior, 4), name_match=c.name_match,
                    ) if explain or c.name_match else ResultSignals(name_match=c.name_match),
                )
            )
        return results

    def _passage_results(self, req, q: Query, mode: str, lex_chunk, sem_chunk, notices, started) -> SearchResponse:
        """docs/code scope: rank passages (RRF at chunk level) with a per-repository cap for diversity."""
        k = self.ctx.settings.search.rrf_k
        scores: dict[int, float] = {}
        source: dict[int, str] = {}
        repo_of: dict[int, int] = {}
        for rank, hit in enumerate(lex_chunk, start=1):
            scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank)
            source[hit.id] = "lexical"
            repo_of[hit.id] = hit.repo_id
        for rank, hit in enumerate(sem_chunk, start=1):
            cid = int(hit.payload.get("chunk_id", 0))
            if not cid:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            source[cid] = "both" if cid in source else "semantic"
            repo_of[cid] = int(hit.payload.get("repo_id", 0))
        ordered = sorted(scores, key=lambda cid: scores[cid], reverse=True)
        per_repo: dict[int, int] = {}
        kept: list[int] = []
        for cid in ordered:
            n = per_repo.get(repo_of[cid], 0)
            if n >= 3:
                continue
            per_repo[repo_of[cid]] = n + 1
            kept.append(cid)
        page = kept[req.offset : req.offset + req.limit]
        rows = self._chunk_rows(page)
        summaries = repo_store.get_summaries(self.db, [repo_of[c] for c in page])
        terms = q.terms + [w for p in q.phrases for w in words(p)]
        results = [
            SearchResult(
                repo=summaries[repo_of[cid]], score=round(scores[cid], 6),
                matches=[self.match_from_row(rows[cid], terms, score=scores[cid], source=source[cid])],
            )
            for cid in page if cid in rows and repo_of[cid] in summaries
        ]
        if req.record and self.ctx.settings.search.save_history:
            self._record(req, len(kept))
        return SearchResponse(
            query=req.query, mode=mode, requested_mode=req.mode, scope=req.scope, parsed=q.to_schema(), total=len(kept),
            took_ms=int((time.monotonic() - started) * 1000), results=results, notices=notices,
            embedding_model=self.ctx.embeddings.model_key if sem_chunk else None,
        )

    def _browse(self, req: SearchRequest, q: Query, cf: CompiledFilter, started: float) -> SearchResponse:
        """No free text: filters and operators only, ordered by the requested sort."""
        sort = {"relevance": "r.starred_at", "stars": "r.stars", "pushed_at": "r.pushed_at", "starred_at": "r.starred_at", "name": "lower(r.full_name)"}[req.sort]
        order = "ASC" if req.sort == "name" else "DESC"
        total = self.db.scalar(f"SELECT count(*) FROM repos r WHERE {cf.where}", cf.params, 0)
        ids = [
            r["id"]
            for r in self.db.all(
                f"SELECT r.id FROM repos r WHERE {cf.where} ORDER BY {sort} IS NULL, {sort} {order}, r.id DESC LIMIT ? OFFSET ?",
                (*cf.params, req.limit, req.offset),
            )
        ]
        results = [SearchResult(repo=s, score=0.0) for s in repo_store.ordered_summaries(self.db, ids)]
        _, filters = parse_query(req.query, req.filters)
        facets = repo_store.compute_facets(self.db, filters, smart_resolver=self._smart_resolver)
        return SearchResponse(
            query=req.query, mode="keyword", requested_mode=req.mode, scope=req.scope, parsed=q.to_schema(), total=total,
            took_ms=int((time.monotonic() - started) * 1000), results=results, facets=facets,
        )

    def _expand(self, q: Query) -> None:
        """Query expansion for the lexical side (setting ``search.query_expansion = auto``; off by default).

        Always: well-known abbreviations. Additionally, when a *local* model is routed for the
        ``expand_query`` task, up to four alternative keywords from that model (cached per query,
        short timeout, any failure ignored). Cloud models are never used for this: a search must
        not send text off the machine. On the evaluation set the abbreviation map did not improve
        ranking, which is why expansion is opt-in.
        """
        text = " ".join(q.all_terms)
        for short, long in EXPANSIONS.items():
            if re.search(rf"\b{re.escape(short)}\b", text) and long not in q.expansions:
                q.expansions.append(long)
        key = text.strip()
        if not key or len(q.terms) < 2:
            return
        cached = self._expansion_cache.get(key)
        if cached is None:
            cached = self._llm_expansions(q.text or key)
            if len(self._expansion_cache) > 500:
                self._expansion_cache.clear()
            self._expansion_cache[key] = cached
        for phrase in cached:
            if phrase not in q.expansions:
                q.expansions.append(phrase)

    def _llm_expansions(self, text: str) -> list[str]:
        from ..providers.base import ChatMessage, GenerationRequest, ProviderError

        try:
            resolved = self.ctx.router.resolve("expand_query")       # no cloud confirmation is ever given here
        except ProviderError:
            return []
        if resolved.is_cloud:
            return []
        schema = {"type": "object", "properties": {"keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 4}}, "required": ["keywords"]}
        try:
            parsed, result = resolved.provider.generate_json(
                GenerationRequest(
                    model=resolved.model,
                    system="You help search a library of software repositories. Reply with JSON only.",
                    messages=[ChatMessage(role="user", content=f"Give up to four alternative search keywords or short phrases (synonyms, the technical term) for: {text[:300]}")],
                    temperature=0.0, max_output_tokens=80, json_schema=schema, json_schema_name="expansion", timeout_s=8.0,
                )
            )
            self.ctx.router.record_usage(resolved, "expand_query", result.usage)
        except Exception:
            return []
        out = []
        for item in parsed.get("keywords", []) if isinstance(parsed, dict) else []:
            if isinstance(item, str) and 2 < len(item) <= 60:
                out.append(item.strip().lower())
        return out[:4]

    def _record(self, req: SearchRequest, count: int) -> None:
        if not req.query.strip():
            return
        try:
            with self.db.tx() as c:
                last = c.execute("SELECT id, query, mode FROM search_history ORDER BY id DESC LIMIT 1").fetchone()
                if last and last["query"] == req.query and last["mode"] == req.mode:
                    c.execute("UPDATE search_history SET created_at=?, result_count=? WHERE id=?", (utcnow(), count, last["id"]))
                else:
                    c.execute(
                        "INSERT INTO search_history(query, mode, scope, filters, result_count, created_at) VALUES (?,?,?,?,?,?)",
                        (req.query, req.mode, req.scope, dumps(req.filters.model_dump(exclude_defaults=True)), count, utcnow()),
                    )
                c.execute("DELETE FROM search_history WHERE id NOT IN (SELECT id FROM search_history ORDER BY id DESC LIMIT 500)")
        except Exception:
            log.debug("could not record search history", exc_info=True)

    # ------------------------------------------------------------------ suggestions and similarity

    def suggest(self, text: str, limit: int = 10) -> list[Suggestion]:
        out: list[Suggestion] = []
        t = text.strip()
        if not t:
            for r in self.db.all("SELECT DISTINCT query FROM search_history ORDER BY id DESC LIMIT ?", (limit,)):
                out.append(Suggestion(kind="history", text=r["query"]))
            return out
        last = t.split()[-1].lower()
        if ":" in last:
            op, _, val = last.partition(":")
            source = {"lang": "language", "language": "language", "license": "license_spdx", "owner": "owner"}.get(op)
            if source:
                for r in self.db.all(
                    f"SELECT {source} AS v, count(*) n FROM repos WHERE star_state='starred' AND lower({source}) LIKE ? GROUP BY v ORDER BY n DESC LIMIT 6",
                    (val + "%",),
                ):
                    out.append(Suggestion(kind="operator", text=f"{op}:{r['v']}", detail=f"{r['n']} repositories"))
            elif op == "topic":
                for r in self.db.all(
                    "SELECT lower(j.value) v, count(*) n FROM repos r, json_each(r.topics) j WHERE r.star_state='starred' AND lower(j.value) LIKE ? GROUP BY v ORDER BY n DESC LIMIT 6",
                    (val + "%",),
                ):
                    out.append(Suggestion(kind="operator", text=f"topic:{r['v']}", detail=f"{r['n']} repositories"))
            return out
        cf = compile_filters(RepoFilters())
        like = t.lower().replace("%", "").replace("_", r"\_") + "%"
        seen: set[int] = set()
        for r in self.db.all(
            "SELECT id, full_name, description FROM repos r WHERE star_state='starred' AND (lower(name) LIKE ? ESCAPE '\\' OR lower(full_name) LIKE ? ESCAPE '\\') ORDER BY stars DESC LIMIT 6",
            (like, like),
        ):
            seen.add(r["id"])
            out.append(Suggestion(kind="repo", text=r["full_name"], detail=r["description"], repo_id=r["id"]))
        if len(out) < 6:
            ids = [i for i in lexical.name_matches(self.db, t, cf, limit=8) if i not in seen][: 6 - len(out)]
            for s in repo_store.ordered_summaries(self.db, ids):
                out.append(Suggestion(kind="repo", text=s.full_name, detail=s.description, repo_id=s.id))
        for r in self.db.all(
            "SELECT lower(j.value) v, count(*) n FROM repos r, json_each(r.topics) j WHERE r.star_state='starred' AND lower(j.value) LIKE ? GROUP BY v ORDER BY n DESC LIMIT 4",
            (t.lower() + "%",),
        ):
            out.append(Suggestion(kind="topic", text=f"topic:{r['v']}", detail=f"{r['n']} repositories"))
        for r in self.db.all(
            "SELECT DISTINCT query FROM search_history WHERE lower(query) LIKE ? ORDER BY id DESC LIMIT 3", (t.lower() + "%",)
        ):
            if r["query"].lower() != t.lower():
                out.append(Suggestion(kind="history", text=r["query"]))
        return out[:limit]

    def similar_repos(self, repo_id: int, *, limit: int = 20) -> list[tuple[int, float]]:
        """Nearest repositories by repository-level embedding. Empty when vectors are unavailable."""
        emb = self.ctx.embeddings
        try:
            index = self._vectors()
            name = index.repos_collection(emb.model_key)
            vec = index.get_vector(name, repo_id)
            if vec is None:
                return []
            starred = {r["id"] for r in self.db.all("SELECT id FROM repos WHERE star_state='starred'")}
            hits = index.search(name, vec, limit=limit + 25)
        except (VectorUnavailable, EmbeddingUnavailable):
            return []
        return [(int(h.id), h.score) for h in hits if int(h.id) != repo_id and int(h.id) in starred][:limit]


def permalink(html_url: str | None, ref: str | None, path: str, start: int | None, end: int | None) -> str | None:
    """GitHub URL for a file at a commit with a line anchor."""
    if not html_url or not ref:
        return html_url
    from urllib.parse import quote

    url = f"{html_url}/blob/{ref}/{quote(path, safe='/')}"
    if path.lower().endswith((".md", ".mdx", ".markdown", ".rst")):
        url += "?plain=1"
    if start:
        url += f"#L{start}" + (f"-L{end}" if end and end != start else "")
    return url
