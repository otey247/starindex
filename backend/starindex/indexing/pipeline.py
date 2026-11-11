"""Content indexing for one repository, and embedding of whatever is pending.

Indexing is separate from star discovery: it reads the repository tree (no clone), fetches
only selected files (documentation level) or streams the archive (deep level), chunks them,
and records exact paths, line ranges and the commit SHA for citations. Unchanged files are
skipped by git blob SHA, unchanged chunks keep their identity and embedding.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from ..db import dumps, loads, placeholders, utcnow
from ..github.client import GitHubError, RateLimited
from ..jobs.handle import JobHandle, inline_job
from ..jobs.queue import RetryLater
from ..search.embeddings import EmbeddingUnavailable
from ..search.lexical import refresh_repo_docs
from ..search.vector import VectorUnavailable
from . import selection
from .chunking import Chunk, assign_uids, chunk_document, content_hash
from .manifests import Dep, parse_manifest

if TYPE_CHECKING:
    from ..context import AppContext

log = logging.getLogger(__name__)
LEVEL_RANK = {"metadata": 0, "docs": 1, "deep": 2}
EMBED_BATCH = 96
README_EMBED_CHARS = 1800


@dataclass
class IndexResult:
    repo_id: int
    status: str                 # indexed | unchanged | skipped
    level: str
    documents: int = 0
    chunks: int = 0
    fetched: int = 0
    reused: int = 0
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class _Prepared:
    cand: selection.Candidate
    text: str
    truncated: bool
    chunks: list[Chunk]
    deps: list[Dep] = field(default_factory=list)


def enqueue_index(ctx: AppContext, repo_id: int, level: str | None = None, *, force: bool = False, priority: int | None = None) -> int:
    """Queue (or find the already queued) index job for a repository at the given level."""
    with ctx.db.tx() as c:
        if level:
            c.execute("UPDATE repos SET index_level=? WHERE id=?", (level, repo_id))
        c.execute("UPDATE repos SET index_status='queued' WHERE id=? AND index_status IN ('pending','failed','indexed','skipped')", (repo_id,))
    return ctx.jobs.enqueue(
        "index_repo", {"repo_id": repo_id, "force": force}, dedupe_key=f"index:{repo_id}", priority=priority
    )


def purge_repo(ctx: AppContext, repo_id: int) -> None:
    """Delete a repository with all derived data (documents, chunks, vectors, annotations)."""
    try:
        ctx.vectors.delete_repo(repo_id)
    except Exception:
        log.debug("vector purge skipped for %s", repo_id, exc_info=True)
    from ..library.repos import delete_repo

    delete_repo(ctx.db, repo_id)


def _mark(ctx: AppContext, repo_id: int, status: str, error: str | None = None, **extra: Any) -> None:
    sets = {"index_status": status, "index_error": error, **extra}
    with ctx.db.tx() as c:
        c.execute(f"UPDATE repos SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), repo_id))


def index_repo(ctx: AppContext, repo_id: int, *, force: bool = False, job: JobHandle | None = None) -> IndexResult:
    job = job or inline_job()
    db = ctx.db
    settings = ctx.settings.indexing
    repo = db.one("SELECT * FROM repos WHERE id=?", (repo_id,))
    if repo is None:
        return IndexResult(repo_id, "skipped", "metadata", note="repository no longer exists")
    level = repo["index_level"]
    name = repo["full_name"]
    now = utcnow()

    if level == "metadata":
        # Metadata level: nothing to fetch. Drop any content indexed earlier at a higher level.
        removed = _remove_documents(ctx, repo_id, keep_paths=set())
        _mark(ctx, repo_id, "indexed", indexed_level="metadata", indexed_at=now, doc_count=0, chunk_count=0, embedded_model_key=None)
        with db.tx() as c:
            refresh_repo_docs(c, [repo_id])
        _drop_vectors(ctx, removed)
        return IndexResult(repo_id, "indexed", "metadata")

    if repo["access_state"] != "ok":
        _mark(ctx, repo_id, "skipped", f"Repository is not accessible ({repo['access_state']}).")
        return IndexResult(repo_id, "skipped", level, note="not accessible")
    if not repo["default_branch"]:
        _mark(ctx, repo_id, "skipped", "Repository has no default branch (empty repository).")
        return IndexResult(repo_id, "skipped", level, note="empty repository")

    _mark(ctx, repo_id, "indexing")
    client = ctx.github
    note: str | None = None
    try:
        job.progress(0, 0, f"{name}: checking latest commit")
        sha = client.head_commit(name, repo["default_branch"])
        already = repo["indexed_commit_sha"]
        satisfied = repo["indexed_level"] is not None and LEVEL_RANK[repo["indexed_level"]] == LEVEL_RANK[level]
        if sha is None:                       # 304: branch head unchanged since we last looked
            sha = already
            if sha is None:
                client.forget_etag(f"{client.api_url}/repos/{name}/commits/{repo['default_branch']}")
                sha = client.head_commit(name, repo["default_branch"])
        if sha is None:
            raise GitHubError("server", "Could not determine the latest commit.")
        if sha == already and satisfied and not force:
            _mark(ctx, repo_id, "indexed", indexed_at=now)
            return IndexResult(repo_id, "unchanged", level, documents=repo["doc_count"], chunks=repo["chunk_count"])

        if level == "deep" and repo["size_kb"] / 1024 > settings.max_repo_size_mb_for_deep:
            note = (
                f"Deep indexing skipped: repository is {repo['size_kb'] // 1024} MB "
                f"(limit {settings.max_repo_size_mb_for_deep} MB). Indexed documentation only."
            )
            level = "docs"

        languages = client.languages(name)
        tree, truncated = client.tree(name, sha)
        candidates = selection.select_from_tree(tree, settings, level=level)
        existing = {
            r["path"]: r
            for r in db.all("SELECT id, path, blob_sha, content_hash, commit_sha FROM documents WHERE repo_id=?", (repo_id,))
        }
        reuse = {c.path for c in candidates if c.blob_sha and c.path in existing and existing[c.path]["blob_sha"] == c.blob_sha and not force}
        to_fetch = [c for c in candidates if c.path not in reuse]
        job.progress(0, len(to_fetch), f"{name}: fetching {len(to_fetch)} files")

        fetched: dict[str, bytes] = {}
        if level == "deep" and (len([c for c in to_fetch if c.kind == "code"]) > 12 or truncated):
            archive = client.download_tarball(name, sha, max_bytes=settings.max_tarball_mb * 1024 * 1024)
            try:
                wanted = None if truncated else {c.path for c in to_fetch}
                for item in selection.iter_archive(archive, wanted=wanted, settings=settings):
                    fetched[item.path] = item.data
                    if len(fetched) % 200 == 0:
                        job.progress(len(fetched), len(to_fetch))
            finally:
                try:
                    archive.unlink(missing_ok=True)
                except OSError:        # Windows keeps a file locked while a handle is open; cleanup retries later
                    log.debug("could not remove %s yet", archive)
            if truncated:      # the tree listing was incomplete: classify what the archive actually held
                known = {c.path for c in candidates}
                for path, data in fetched.items():
                    if path not in known:
                        verdict = selection.classify_path(path, len(data), settings, deep=True)
                        if verdict:
                            candidates.append(selection.Candidate(path, verdict[0], len(data), None, verdict[1]))
        else:
            private = bool(repo["is_private"])

            def fetch(c: selection.Candidate) -> tuple[str, bytes | None]:
                return c.path, client.file(name, sha, c.path, max_bytes=settings.max_file_bytes, private=private)

            with ThreadPoolExecutor(max_workers=settings.fetch_concurrency) as pool:
                for done, (path, data) in enumerate(pool.map(fetch, to_fetch), start=1):
                    if data is not None:
                        fetched[path] = data
                    if done % 10 == 0:
                        job.progress(done, len(to_fetch))

        prepared: list[_Prepared] = []
        for cand in candidates:
            if cand.path in reuse:
                continue
            data = fetched.get(cand.path)
            if data is None:
                continue
            text = selection.decode_text(data)      # None for binaries and Git LFS pointers
            if text is None:
                continue
            cut = len(text) > settings.max_doc_chars
            if cut:
                text = text[: settings.max_doc_chars]
            chunks = chunk_document(cand.path, cand.kind, text)
            assign_uids(repo_id, cand.path, chunks)
            deps = parse_manifest(cand.path, text) if cand.kind in ("manifest", "config") else []
            prepared.append(_Prepared(cand, text, cut, chunks, deps))

        keep_paths = reuse | {p.cand.path for p in prepared}
        removed_uids = _store(ctx, repo_id, sha, level, prepared, keep_paths, reuse)
        doc_count = db.scalar("SELECT count(*) FROM documents WHERE repo_id=?", (repo_id,), 0)
        chunk_count = db.scalar("SELECT count(*) FROM chunks WHERE repo_id=?", (repo_id,), 0)
        with db.tx() as c:
            c.execute(
                "UPDATE repos SET languages=?, indexed_level=?, index_status='indexed', index_error=?, indexed_commit_sha=?, "
                "indexed_at=?, tree_truncated=?, doc_count=?, chunk_count=?, embedded_model_key=NULL WHERE id=?",
                (dumps(languages), level, note, sha, utcnow(), int(truncated), doc_count, chunk_count, repo_id),
            )
            refresh_repo_docs(c, [repo_id])
        _drop_vectors(ctx, removed_uids)
        from .classify import classify_repo

        classify_repo(ctx, repo_id)
        return IndexResult(repo_id, "indexed", level, documents=doc_count, chunks=chunk_count, fetched=len(fetched), reused=len(reuse), note=note)
    except RateLimited as exc:
        _mark(ctx, repo_id, "queued")
        raise RetryLater(exc.wait_s + 2, exc.message) from exc
    except GitHubError as exc:
        if exc.kind in ("not_found", "blocked", "forbidden"):
            state = {"blocked": "blocked", "forbidden": "error"}.get(exc.kind, "not_found")
            _mark(ctx, repo_id, "skipped", exc.message, access_state=state, access_checked_at=utcnow())
            return IndexResult(repo_id, "skipped", level, note=exc.message)
        if exc.kind == "too_large":
            _mark(ctx, repo_id, "skipped", exc.message)
            return IndexResult(repo_id, "skipped", level, note=exc.message)
        _mark(ctx, repo_id, "failed", exc.message)
        raise
    except Exception as exc:
        _mark(ctx, repo_id, "failed", f"{type(exc).__name__}: {str(exc)[:300]}")
        raise


def _store(ctx: AppContext, repo_id: int, sha: str, level: str, prepared: list[_Prepared], keep_paths: set[str], reuse: set[str]) -> list[str]:
    """Write documents, chunks and dependencies for one repository in a single transaction."""
    removed_uids: list[str] = []
    now = utcnow()
    with ctx.db.tx() as c:
        for row in c.execute("SELECT id, path FROM documents WHERE repo_id=?", (repo_id,)).fetchall():
            if row["path"] not in keep_paths:
                removed_uids += [r["uid"] for r in c.execute("SELECT uid FROM chunks WHERE document_id=?", (row["id"],))]
                c.execute("DELETE FROM documents WHERE id=?", (row["id"],))
        if reuse:
            # Unchanged files still belong to the new commit for citation purposes.
            c.execute(
                f"UPDATE documents SET commit_sha=?, level=?, indexed_at=? WHERE repo_id=? AND path IN ({placeholders(len(reuse))})",
                (sha, level, now, repo_id, *reuse),
            )
        for item in prepared:
            cand = item.cand
            doc_hash = content_hash(item.text)
            c.execute(
                "INSERT INTO documents(repo_id, path, kind, language, size_bytes, blob_sha, content_hash, commit_sha, level, content, truncated, indexed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(repo_id, path) DO UPDATE SET kind=excluded.kind, language=excluded.language, "
                "size_bytes=excluded.size_bytes, blob_sha=excluded.blob_sha, content_hash=excluded.content_hash, commit_sha=excluded.commit_sha, "
                "level=excluded.level, content=excluded.content, truncated=excluded.truncated, indexed_at=excluded.indexed_at",
                (repo_id, cand.path, cand.kind, cand.language, cand.size, cand.blob_sha, doc_hash, sha, level, item.text, int(item.truncated), now),
            )
            doc_id = c.execute("SELECT id FROM documents WHERE repo_id=? AND path=?", (repo_id, cand.path)).fetchone()["id"]
            old = {r["uid"]: r["id"] for r in c.execute("SELECT id, uid FROM chunks WHERE document_id=?", (doc_id,))}
            new_uids = {ch.uid for ch in item.chunks}
            for uid, chunk_id in old.items():
                if uid not in new_uids:
                    removed_uids.append(uid)
                    c.execute("DELETE FROM chunks WHERE id=?", (chunk_id,))
            for ch in item.chunks:
                if ch.uid in old:      # same text as before: keep identity and embedding, refresh position
                    c.execute(
                        "UPDATE chunks SET ordinal=?, heading=?, start_line=?, end_line=?, kind=? WHERE id=?",
                        (ch.ordinal, ch.heading, ch.start_line, ch.end_line, cand.kind, old[ch.uid]),
                    )
                else:
                    c.execute(
                        "INSERT INTO chunks(uid, repo_id, document_id, ordinal, kind, path, heading, start_line, end_line, content, content_hash, injection_flag) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ch.uid, repo_id, doc_id, ch.ordinal, cand.kind, cand.path, ch.heading, ch.start_line, ch.end_line, ch.content, ch.content_hash, int(ch.injection_flag)),
                    )
        # Dependencies: replace those from re-read manifests, drop those from removed manifests.
        touched = [p.cand.path for p in prepared if p.cand.kind in ("manifest", "config")]
        if touched:
            c.execute(f"DELETE FROM repo_dependencies WHERE repo_id=? AND manifest_path IN ({placeholders(len(touched))})", (repo_id, *touched))
        if keep_paths:
            c.execute(
                f"DELETE FROM repo_dependencies WHERE repo_id=? AND manifest_path NOT IN ({placeholders(len(keep_paths))})",
                (repo_id, *keep_paths),
            )
        else:
            c.execute("DELETE FROM repo_dependencies WHERE repo_id=?", (repo_id,))
        for item in prepared:
            for dep in item.deps:
                c.execute(
                    "INSERT OR IGNORE INTO repo_dependencies(repo_id, ecosystem, name, version_spec, scope, manifest_path) VALUES (?,?,?,?,?,?)",
                    (repo_id, dep.ecosystem, dep.name, dep.version_spec, dep.scope, item.cand.path),
                )
    return removed_uids


def _remove_documents(ctx: AppContext, repo_id: int, keep_paths: set[str]) -> list[str]:
    removed: list[str] = []
    with ctx.db.tx() as c:
        for row in c.execute("SELECT id, path FROM documents WHERE repo_id=?", (repo_id,)).fetchall():
            if row["path"] not in keep_paths:
                removed += [r["uid"] for r in c.execute("SELECT uid FROM chunks WHERE document_id=?", (row["id"],))]
                c.execute("DELETE FROM documents WHERE id=?", (row["id"],))
        if not keep_paths:
            c.execute("DELETE FROM repo_dependencies WHERE repo_id=?", (repo_id,))
    return removed


def _drop_vectors(ctx: AppContext, uids: list[str]) -> None:
    if not uids:
        return
    try:
        index = ctx.vectors
        for name in index.collections():
            if name.startswith("chunks__"):
                index.delete_points(name, uids)
    except Exception:
        log.debug("could not delete %d stale vectors (they are ignored at query time)", len(uids), exc_info=True)


# ---------------------------------------------------------------------------------- embedding


def repo_embed_text(db, repo_id: int) -> str | None:
    """Repository-level document: identity, description, topics and the start of the README."""
    row = db.one("SELECT full_name, description, topics, language FROM repos WHERE id=?", (repo_id,))
    if row is None:
        return None
    topics = ", ".join(loads(row["topics"], [])[:20])
    parts = [row["full_name"].replace("/", " / "), row["description"] or ""]
    if topics:
        parts.append(f"Topics: {topics}")
    if row["language"]:
        parts.append(f"Language: {row['language']}")
    intro: list[str] = []
    size = 0
    for ch in db.all("SELECT content FROM chunks WHERE repo_id=? AND kind='readme' ORDER BY document_id, ordinal LIMIT 6", (repo_id,)):
        intro.append(ch["content"])
        size += len(ch["content"])
        if size >= README_EMBED_CHARS:
            break
    text = "\n".join(p for p in parts if p)
    if intro:
        text += "\n" + "\n".join(intro)[:README_EMBED_CHARS]
    return text


def chunk_embed_text(full_name: str, path: str, heading: str, content: str) -> str:
    header = f"{full_name} — {path}" + (f" — {heading}" if heading else "")
    return f"{header}\n{content}"


def embed_pending(ctx: AppContext, job: JobHandle | None = None, *, max_batches: int | None = None) -> dict[str, int]:
    """Embed chunks and repositories that are not yet in the vector index for the active model.

    Bounded batches keep memory flat; the loop re-checks the queue so it also picks up work
    produced while it runs. Safe to interrupt: progress is committed per batch.
    """
    job = job or inline_job()
    emb = ctx.embeddings
    if not emb.enabled:
        return {"chunks": 0, "repos": 0}
    model_key = emb.model_key
    db = ctx.db
    try:
        emb.backend()
        index = ctx.vectors
    except EmbeddingUnavailable as exc:
        raise RuntimeError(str(exc)) from exc
    except VectorUnavailable as exc:
        raise RuntimeError(str(exc)) from exc
    dim = emb.dim or emb.known_dim()
    assert dim is not None
    chunks_name, repos_name = index.chunks_collection(model_key), index.repos_collection(model_key)
    done_chunks = done_repos = batches = 0

    def embed_repos() -> None:
        """Repository-level vectors first: a few hundred short texts make semantic search usable at once."""
        nonlocal done_repos
        from .classify import classify_repo

        while True:
            rows = db.all(
                "SELECT id FROM repos WHERE (embedded_model_key IS NOT ?) AND index_status NOT IN ('indexing') ORDER BY id LIMIT ?",
                (model_key, EMBED_BATCH),
            )
            if not rows:
                return
            items: list[tuple[int, str, str]] = []
            for r in rows:
                text = repo_embed_text(db, r["id"])
                if text:
                    items.append((r["id"], content_hash(text), text))
            vectors = emb.embed_cached([(h, t) for _, h, t in items])
            index.upsert(repos_name, dim, [(rid, vectors[h], {"repo_id": rid}) for rid, h, _ in items])
            with db.tx() as c:
                c.executemany(
                    "UPDATE repos SET embedded_model_key=?, embed_text_hash=? WHERE id=?", [(model_key, h, rid) for rid, h, _ in items]
                )
            done_repos += len(items)
            job.progress(message=f"Embedding repository summaries ({done_repos})")
            for rid, h, _ in items:
                classify_repo(ctx, rid, vector=np.asarray(vectors[h]))
            if len(rows) < EMBED_BATCH:
                return

    embed_repos()
    total = db.scalar("SELECT count(*) FROM chunks WHERE embedded_model_key IS NOT ?", (model_key,), 0)
    while True:
        # READMEs and docs before manifests and code, so the most useful passages are searchable first.
        rows = db.all(
            "SELECT c.id, c.uid, c.repo_id, c.kind, c.path, c.heading, c.content, r.full_name FROM chunks c "
            "JOIN repos r ON r.id=c.repo_id WHERE c.embedded_model_key IS NOT ? "
            "ORDER BY CASE c.kind WHEN 'readme' THEN 0 WHEN 'doc' THEN 1 WHEN 'example' THEN 2 WHEN 'api' THEN 3 WHEN 'code' THEN 4 ELSE 5 END, c.id LIMIT ?",
            (model_key, EMBED_BATCH),
        )
        if not rows:
            break
        rows = sorted(rows, key=lambda r: len(r["content"]))      # similar lengths per batch: less padding
        texts = [chunk_embed_text(r["full_name"], r["path"], r["heading"], r["content"]) for r in rows]
        hashes = [content_hash(t) for t in texts]
        vectors = emb.embed_cached(list(zip(hashes, texts, strict=True)))
        index.upsert(
            chunks_name, dim,
            [(r["uid"], vectors[h], {"repo_id": r["repo_id"], "chunk_id": r["id"], "kind": r["kind"], "path": r["path"]})
             for r, h in zip(rows, hashes, strict=True)],
        )
        with db.tx() as c:
            c.executemany(
                "UPDATE chunks SET embedded_model_key=?, embed_hash=? WHERE id=?",
                [(model_key, h, r["id"]) for r, h in zip(rows, hashes, strict=True)],
            )
        done_chunks += len(rows)
        batches += 1
        job.progress(done_chunks, max(total, done_chunks), f"Embedding passages ({done_chunks}/{max(total, done_chunks)})")
        if batches % 5 == 0:
            embed_repos()          # repositories indexed meanwhile
        if max_batches is not None and batches >= max_batches:
            break
    embed_repos()
    return {"chunks": done_chunks, "repos": done_repos}


def rebuild_vector_index(ctx: AppContext, job: JobHandle | None = None) -> dict[str, int]:
    """Drop the active model's collections and rebuild them from the embedding cache."""
    job = job or inline_job()
    emb = ctx.embeddings
    index = ctx.vectors
    key = emb.model_key
    index.drop(index.chunks_collection(key))
    index.drop(index.repos_collection(key))
    with ctx.db.tx() as c:
        c.execute("UPDATE chunks SET embedded_model_key=NULL")
        c.execute("UPDATE repos SET embedded_model_key=NULL")
    return embed_pending(ctx, job)
