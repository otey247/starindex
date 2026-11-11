"""Lexical retrieval on SQLite FTS5 (BM25), typo correction and snippets."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein

from ..db import Database, loads, placeholders
from ..library.repos import CompiledFilter
from .query import STOPWORDS, Query, fts_expression, in_columns, words

# bm25() column weights for repo_fts(name, owner, description, topics, categories, readme, notes)
REPO_WEIGHTS = (10.0, 2.0, 5.0, 6.0, 3.0, 1.5, 4.0)
# chunk_fts(content, heading, path)
CHUNK_WEIGHTS = (1.0, 2.0, 0.6)
README_FTS_CHARS = 6000
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SEP = re.compile(r"[-_./]+")


@dataclass
class LexHit:
    id: int          # repo id or chunk id
    repo_id: int
    score: float     # higher is better (negated bm25)


def name_variants(full_name: str) -> str:
    """'vercel/next.js' -> 'vercel/next.js next.js next js nextjs' so partial names match."""
    owner, _, name = full_name.partition("/")
    parts = [p for p in _SEP.split(_CAMEL.sub(" ", name)) if p]
    split = " ".join(" ".join(parts).split())
    joined = "".join(split.split())
    return " ".join(dict.fromkeys([full_name, name, split, joined]))


def refresh_repo_docs(c: sqlite3.Connection, repo_ids: list[int]) -> None:
    """Rebuild the repository-level lexical documents for the given repositories."""
    for repo_id in dict.fromkeys(repo_ids):
        row = c.execute("SELECT id, full_name, owner, description, topics FROM repos WHERE id=?", (repo_id,)).fetchone()
        c.execute("DELETE FROM repo_fts WHERE rowid=?", (repo_id,))
        c.execute("DELETE FROM repo_name_trgm WHERE rowid=?", (repo_id,))
        if row is None:
            continue
        topics = loads(row["topics"], [])
        topic_text = " ".join(topics) + " " + " ".join(t.replace("-", " ") for t in topics if "-" in t)
        cats = " ".join(
            r["name"]
            for r in c.execute(
                "SELECT cat.name FROM repo_categories rc JOIN categories cat ON cat.id=rc.category_id "
                "WHERE rc.repo_id=? AND rc.excluded=0",
                (repo_id,),
            )
        )
        readme_parts: list[str] = []
        size = 0
        for ch in c.execute(
            "SELECT content FROM chunks WHERE repo_id=? AND kind='readme' ORDER BY document_id, ordinal", (repo_id,)
        ):
            readme_parts.append(ch["content"])
            size += len(ch["content"])
            if size >= README_FTS_CHARS:
                break
        ann = c.execute("SELECT note FROM repo_annotations WHERE repo_id=?", (repo_id,)).fetchone()
        tags = " ".join(
            r["name"] for r in c.execute("SELECT t.name FROM repo_tags rt JOIN tags t ON t.id=rt.tag_id WHERE rt.repo_id=?", (repo_id,))
        )
        notes = ((ann["note"] if ann else "") + " " + tags).strip()
        c.execute(
            "INSERT INTO repo_fts(rowid, name, owner, description, topics, categories, readme, notes) VALUES (?,?,?,?,?,?,?,?)",
            (repo_id, name_variants(row["full_name"]), row["owner"], row["description"] or "", topic_text, cats,
             "\n".join(readme_parts)[:README_FTS_CHARS], notes),
        )
        c.execute("INSERT INTO repo_name_trgm(rowid, full_name) VALUES (?,?)", (repo_id, row["full_name"].lower()))


def rebuild_terms(db: Database) -> int:
    """Rebuild the typo-correction vocabulary from repository names, descriptions and topics."""
    df: dict[str, int] = {}
    for row in db.all("SELECT full_name, description, topics FROM repos WHERE star_state='starred'"):
        text = f"{name_variants(row['full_name'])} {row['description'] or ''} {' '.join(loads(row['topics'], []))}"
        for term in {w for w in words(text.replace("-", " ")) if len(w) >= 3 and not w.isdigit()}:
            df[term] = df.get(term, 0) + 1
    with db.tx() as c:
        c.execute("DELETE FROM search_terms")
        c.executemany("INSERT INTO search_terms(term, df) VALUES (?,?)", df.items())
    return len(df)


class TypoCorrector:
    """Suggests corrections for query terms that do not occur in the library vocabulary."""

    def __init__(self, db: Database):
        self.db = db
        self._vocab: dict[str, int] | None = None
        self._terms: list[str] = []
        self._stamp: Any = None

    def _load(self) -> None:
        stamp = self.db.scalar("SELECT count(*) || ':' || COALESCE(sum(df),0) FROM search_terms")
        if self._vocab is not None and stamp == self._stamp:
            return
        self._vocab = {r["term"]: r["df"] for r in self.db.all("SELECT term, df FROM search_terms")}
        self._terms = list(self._vocab)
        self._stamp = stamp

    def correct(self, term: str) -> str | None:
        self._load()
        assert self._vocab is not None
        if len(term) < 4 or term in self._vocab or term in STOPWORDS or term.isdigit() or not self._terms:
            return None
        # Plural/singular of a known word is handled by stemming, not a typo.
        if term.endswith("s") and term[:-1] in self._vocab or f"{term}s" in self._vocab:
            return None
        # The vocabulary covers names, descriptions and topics. A word that occurs anywhere in the
        # indexed text (after stemming) is spelled correctly as far as this library is concerned.
        try:
            for table in ("repo_fts", "chunk_fts"):
                if self.db.one(f"SELECT 1 FROM {table} WHERE {table} MATCH ? LIMIT 1", ('"' + term.replace('"', '""') + '"',)):
                    return None
        except Exception:
            return None
        max_dist = 1 if len(term) <= 5 else 2
        candidates = process.extract(
            term, self._terms, scorer=DamerauLevenshtein.distance, score_cutoff=max_dist, limit=8
        )
        if not candidates:
            return None
        # Closest first; among equally close candidates prefer the more common word and same first letter.
        best = min(candidates, key=lambda c: (c[1], c[0][0] != term[0], -self._vocab[c[0]]))  # type: ignore[index]
        return best[0]

    def apply(self, q: Query) -> None:
        for term in q.terms:
            fixed = self.correct(term)
            if fixed:
                q.corrections[term] = fixed


def _run(db: Database, sql: str, params: list[Any]) -> list[sqlite3.Row]:
    try:
        return db.all(sql, params)
    except sqlite3.OperationalError:
        return []       # malformed FTS expression from odd input: treat as no lexical hits


def search_repos(db: Database, q: Query, cf: CompiledFilter, *, limit: int = 200, strict: bool | None = None) -> list[LexHit]:
    expr = fts_expression(q, columns=in_columns(q), strict=strict)
    if not expr:
        return []
    weights = ", ".join(str(w) for w in REPO_WEIGHTS)
    rows = _run(
        db,
        f"SELECT repo_fts.rowid AS id, bm25(repo_fts, {weights}) AS score FROM repo_fts "
        f"JOIN repos r ON r.id=repo_fts.rowid WHERE repo_fts MATCH ? AND {cf.where} ORDER BY score LIMIT ?",
        [expr, *cf.params, limit],
    )
    return [LexHit(r["id"], r["id"], -float(r["score"])) for r in rows]


def count_repos(db: Database, q: Query, cf: CompiledFilter, *, strict: bool | None = None) -> int:
    expr = fts_expression(q, columns=in_columns(q), strict=strict)
    if not expr:
        return 0
    rows = _run(
        db,
        f"SELECT count(*) AS n FROM repo_fts JOIN repos r ON r.id=repo_fts.rowid WHERE repo_fts MATCH ? AND {cf.where}",
        [expr, *cf.params],
    )
    return rows[0]["n"] if rows else 0


def search_chunks(
    db: Database, q: Query, cf: CompiledFilter, *, kinds: list[str] | None = None, limit: int = 400, strict: bool | None = None
) -> list[LexHit]:
    expr = fts_expression(q, strict=strict)
    if not expr:
        return []
    weights = ", ".join(str(w) for w in CHUNK_WEIGHTS)
    kind_sql = f" AND c.kind IN ({placeholders(len(kinds))})" if kinds else ""
    rows = _run(
        db,
        f"SELECT c.id AS id, c.repo_id AS repo_id, bm25(chunk_fts, {weights}) AS score FROM chunk_fts "
        f"JOIN chunks c ON c.id=chunk_fts.rowid JOIN repos r ON r.id=c.repo_id "
        f"WHERE chunk_fts MATCH ? AND {cf.where}{kind_sql} ORDER BY score LIMIT ?",
        [expr, *cf.params, *(kinds or []), limit],
    )
    return [LexHit(r["id"], r["repo_id"], -float(r["score"])) for r in rows]


def name_matches(db: Database, text: str, cf: CompiledFilter, *, limit: int = 20) -> list[int]:
    """Substring / fuzzy repository-name matches via the trigram index (needs >= 3 characters)."""
    needle = re.sub(r"[^a-z0-9./_-]+", "", text.lower())
    if len(needle) < 3:
        return []
    rows = _run(
        db,
        "SELECT repo_name_trgm.rowid AS id FROM repo_name_trgm JOIN repos r ON r.id=repo_name_trgm.rowid "
        f"WHERE repo_name_trgm MATCH ? AND {cf.where} ORDER BY r.stars DESC LIMIT ?",
        ['"' + needle.replace('"', '""') + '"', *cf.params, limit],
    )
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------------- snippets

MARK_OPEN, MARK_CLOSE = "«", "»"


def _stem(word: str) -> str:
    w = word.lower()
    for suffix in ("ization", "isation", "ations", "ation", "ings", "ing", "ies", "ers", "er", "ed", "es", "s"):
        if len(w) - len(suffix) >= 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def highlight(text: str, terms: list[str]) -> str:
    """Wrap words that match any query term (by simple stem) in « » markers."""
    stems = {_stem(t) for t in terms if len(t) > 1}
    if not stems:
        return text

    def repl(m: re.Match[str]) -> str:
        w = m.group(0)
        s = _stem(w)
        if s in stems or any(len(st) >= 4 and s.startswith(st) for st in stems):
            return f"{MARK_OPEN}{w}{MARK_CLOSE}"
        return w

    return re.sub(r"[^\W_]+", repl, text)


def make_snippet(content: str, terms: list[str], *, width: int = 280) -> str:
    """Best window of ``content`` for the query terms, with matches marked."""
    flat = re.sub(r"\s+", " ", content).strip()
    if len(flat) <= width:
        return highlight(flat, terms)
    stems = {_stem(t) for t in terms if len(t) > 1}
    positions = [m.start() for m in re.finditer(r"[^\W_]+", flat) if _stem(m.group(0)) in stems]
    if not positions:
        return highlight(flat[:width].rsplit(" ", 1)[0] + "…", terms)
    best_start, best_count = positions[0], 0
    j = 0
    for i, start in enumerate(positions):
        while positions[j] < start:
            j += 1
        k = i
        while k + 1 < len(positions) and positions[k + 1] - start <= width - 40:
            k += 1
        if k - i + 1 > best_count:
            best_count, best_start = k - i + 1, start
    begin = max(0, best_start - 40)
    if begin > 0:
        space = flat.find(" ", begin)
        begin = space + 1 if 0 <= space < best_start else begin
    end = min(len(flat), begin + width)
    snippet = flat[begin:end]
    if end < len(flat):
        snippet = snippet.rsplit(" ", 1)[0] + "…"
    if begin > 0:
        snippet = "…" + snippet
    return highlight(snippet, terms)
