"""Exact (brute-force) vector search over the embedding cache in SQLite.

Used for semantic search restricted to a subset of repositories when the vector engine cannot
filter efficiently (embedded Qdrant evaluates payload filters point by point in Python).
Vectors are L2-normalised, so the dot product is the cosine similarity.
"""

from __future__ import annotations

import numpy as np

from ..db import Database, placeholders
from .vector import VectorHit

MAX_EXACT_CHUNKS = 60_000


def search_repos(db: Database, model_key: str, vector: np.ndarray, repo_ids: list[int], limit: int) -> list[VectorHit]:
    ids: list[int] = []
    blobs: list[bytes] = []
    for start in range(0, len(repo_ids), 900):
        part = repo_ids[start : start + 900]
        for row in db.all(
            f"SELECT r.id, e.vector FROM repos r JOIN embeddings e ON e.content_hash=r.embed_text_hash AND e.model_key=? "
            f"WHERE r.embedded_model_key=? AND r.id IN ({placeholders(len(part))})",
            (model_key, model_key, *part),
        ):
            ids.append(row["id"])
            blobs.append(row["vector"])
    return _top(ids, blobs, vector, limit, lambda i: {"repo_id": i})


def count_chunks(db: Database, model_key: str, repo_ids: list[int], kinds: list[str] | None) -> int:
    total = 0
    kind_sql = f" AND kind IN ({placeholders(len(kinds))})" if kinds else ""
    for start in range(0, len(repo_ids), 900):
        part = repo_ids[start : start + 900]
        total += db.scalar(
            f"SELECT count(*) FROM chunks WHERE embedded_model_key=? AND repo_id IN ({placeholders(len(part))}){kind_sql}",
            (model_key, *part, *(kinds or [])), 0,
        )
    return total


def search_chunks(db: Database, model_key: str, vector: np.ndarray, repo_ids: list[int], kinds: list[str] | None, limit: int) -> list[VectorHit]:
    meta: list[tuple[int, int, str, str]] = []
    blobs: list[bytes] = []
    kind_sql = f" AND c.kind IN ({placeholders(len(kinds))})" if kinds else ""
    for start in range(0, len(repo_ids), 900):
        part = repo_ids[start : start + 900]
        for row in db.all(
            f"SELECT c.id, c.repo_id, c.kind, c.path, e.vector FROM chunks c JOIN embeddings e ON e.content_hash=c.embed_hash AND e.model_key=? "
            f"WHERE c.embedded_model_key=? AND c.repo_id IN ({placeholders(len(part))}){kind_sql}",
            (model_key, model_key, *part, *(kinds or [])),
        ):
            meta.append((row["id"], row["repo_id"], row["kind"], row["path"]))
            blobs.append(row["vector"])
    return _top(meta, blobs, vector, limit, lambda m: {"chunk_id": m[0], "repo_id": m[1], "kind": m[2], "path": m[3]})


def _top(keys: list, blobs: list[bytes], vector: np.ndarray, limit: int, payload) -> list[VectorHit]:
    if not blobs:
        return []
    matrix = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(blobs), -1)
    scores = matrix @ np.asarray(vector, dtype=np.float32)
    k = min(limit, len(scores))
    order = np.argpartition(-scores, k - 1)[:k] if k < len(scores) else np.arange(len(scores))
    order = order[np.argsort(-scores[order])]
    return [VectorHit(keys[i] if not isinstance(keys[i], tuple) else keys[i][0], float(scores[i]), payload(keys[i])) for i in order]
