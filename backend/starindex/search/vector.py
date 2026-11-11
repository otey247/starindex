"""Vector index on Qdrant (embedded by default, server when STARINDEX_QDRANT_URL is set).

The index is derived data: every vector also lives in the SQLite embedding cache, so any
collection can be dropped and rebuilt. Collections are named per embedding model key, which
lets a new model be indexed in the background while the old collection is still served.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from ..context import AppContext

log = logging.getLogger(__name__)


class VectorUnavailable(Exception):
    pass


@dataclass
class VectorHit:
    id: Any                 # chunk uid (str) or repo id (int)
    score: float
    payload: dict[str, Any]


def collection_suffix(model_key: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", model_key.lower()).strip("_")[:40]
    return f"{slug}_{hashlib.sha1(model_key.encode()).hexdigest()[:8]}"


class VectorIndex:
    def __init__(self, *, path: str | None = None, url: str | None = None):
        from qdrant_client import QdrantClient

        self.mode = "server" if url else "embedded"
        self.location = url or path
        try:
            self._client = QdrantClient(url=url, timeout=30) if url else QdrantClient(path=path)
        except Exception as exc:
            raise VectorUnavailable(
                "The vector index is in use by another StarIndex process."
                if "already accessed" in str(exc)
                else f"Could not open the vector index: {type(exc).__name__}: {str(exc)[:200]}"
            ) from exc
        # The embedded engine is not safe for concurrent calls; the server is.
        self._lock = threading.RLock()
        self._known: dict[str, int] = {}

    def close(self) -> None:
        with self._lock:
            try:
                self._client.close()
            except Exception:
                pass

    # -- collections ------------------------------------------------------------------------

    @staticmethod
    def chunks_collection(model_key: str) -> str:
        return f"chunks__{collection_suffix(model_key)}"

    @staticmethod
    def repos_collection(model_key: str) -> str:
        return f"repos__{collection_suffix(model_key)}"

    def _ensure(self, name: str, dim: int) -> None:
        from qdrant_client import models as qm

        if self._known.get(name) == dim:
            return
        with self._lock:
            if not self._client.collection_exists(name):
                self._client.create_collection(name, vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE))
                if self.mode == "server":      # the embedded engine has no payload indexes
                    for field, schema in (("repo_id", qm.PayloadSchemaType.INTEGER), ("kind", qm.PayloadSchemaType.KEYWORD)):
                        try:
                            self._client.create_payload_index(name, field_name=field, field_schema=schema)
                        except Exception:
                            log.debug("payload index creation failed", exc_info=True)
            self._known[name] = dim

    def exists(self, name: str) -> bool:
        with self._lock:
            return bool(self._client.collection_exists(name))

    def count(self, name: str) -> int:
        with self._lock:
            if not self._client.collection_exists(name):
                return 0
            return int(self._client.count(name, exact=True).count)

    def collections(self) -> list[str]:
        with self._lock:
            return [c.name for c in self._client.get_collections().collections]

    def drop(self, name: str) -> None:
        with self._lock:
            if self._client.collection_exists(name):
                self._client.delete_collection(name)
            self._known.pop(name, None)

    # -- writes -----------------------------------------------------------------------------

    def upsert(self, name: str, dim: int, points: list[tuple[Any, np.ndarray, dict[str, Any]]]) -> None:
        from qdrant_client import models as qm

        if not points:
            return
        self._ensure(name, dim)
        structs = [qm.PointStruct(id=pid, vector=np.asarray(vec, dtype=np.float32).tolist(), payload=payload) for pid, vec, payload in points]
        with self._lock:
            for start in range(0, len(structs), 256):
                self._client.upsert(name, points=structs[start : start + 256], wait=True)

    def delete_points(self, name: str, ids: list[Any]) -> None:
        from qdrant_client import models as qm

        if not ids:
            return
        with self._lock:
            if self._client.collection_exists(name):
                for start in range(0, len(ids), 500):
                    self._client.delete(name, points_selector=qm.PointIdsList(points=ids[start : start + 500]), wait=True)

    def delete_repo(self, repo_id: int) -> None:
        """Remove a repository from every collection (all embedding models)."""
        from qdrant_client import models as qm

        with self._lock:
            for name in self.collections():
                try:
                    if name.startswith("chunks__"):
                        self._client.delete(
                            name,
                            points_selector=qm.FilterSelector(
                                filter=qm.Filter(must=[qm.FieldCondition(key="repo_id", match=qm.MatchValue(value=repo_id))])
                            ),
                            wait=True,
                        )
                    elif name.startswith("repos__"):
                        self._client.delete(name, points_selector=qm.PointIdsList(points=[repo_id]), wait=True)
                except Exception:
                    log.debug("vector delete failed for %s", name, exc_info=True)

    # -- queries ----------------------------------------------------------------------------

    def search(
        self,
        name: str,
        vector: np.ndarray,
        *,
        limit: int,
        repo_ids: list[int] | None = None,
        kinds: list[str] | None = None,
        exclude_repo_ids: list[int] | None = None,
    ) -> list[VectorHit]:
        """Nearest neighbours with filters applied inside the vector engine (pre-filtering)."""
        from qdrant_client import models as qm

        must: list[Any] = []
        must_not: list[Any] = []
        if repo_ids is not None:
            if not repo_ids:
                return []
            must.append(qm.FieldCondition(key="repo_id", match=qm.MatchAny(any=[int(i) for i in repo_ids])))
        if kinds:
            must.append(qm.FieldCondition(key="kind", match=qm.MatchAny(any=kinds)))
        if exclude_repo_ids:
            must_not.append(qm.FieldCondition(key="repo_id", match=qm.MatchAny(any=[int(i) for i in exclude_repo_ids])))
        flt = qm.Filter(must=must or None, must_not=must_not or None) if (must or must_not) else None
        with self._lock:
            if not self._client.collection_exists(name):
                return []
            res = self._client.query_points(
                name, query=np.asarray(vector, dtype=np.float32).tolist(), query_filter=flt, limit=limit, with_payload=True
            )
        return [VectorHit(p.id, float(p.score), dict(p.payload or {})) for p in res.points]

    def get_vector(self, name: str, point_id: Any) -> np.ndarray | None:
        with self._lock:
            if not self._client.collection_exists(name):
                return None
            got = self._client.retrieve(name, ids=[point_id], with_vectors=True, with_payload=False)
        if not got or got[0].vector is None:
            return None
        return np.asarray(got[0].vector, dtype=np.float32)


def open_vector_index(ctx: AppContext) -> VectorIndex:
    if ctx.config.qdrant_url:
        return VectorIndex(url=ctx.config.qdrant_url)
    return VectorIndex(path=str(ctx.config.paths.vector_dir))
