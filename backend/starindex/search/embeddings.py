"""Embedding backends and the embedding cache.

Embedding configuration is independent of generation configuration. Vectors are cached in
SQLite by (content hash, model key), so unchanged text is never embedded twice and a vector
index can be rebuilt without recomputing anything. ``model_key`` versions every vector: a
different model (or backend) gets its own cache entries and its own vector collections.
"""

from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import numpy as np

from ..db import placeholders, utcnow

if TYPE_CHECKING:
    from ..context import AppContext

log = logging.getLogger(__name__)

# Retrieval instructions some models expect on the query side (documents are embedded as-is).
QUERY_PREFIXES = {
    "BAAI/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "BAAI/bge-base-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "BAAI/bge-large-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "snowflake/snowflake-arctic-embed-xs": "Represent this sentence for searching relevant passages: ",
    "snowflake/snowflake-arctic-embed-s": "Represent this sentence for searching relevant passages: ",
    "snowflake/snowflake-arctic-embed-m": "Represent this sentence for searching relevant passages: ",
    "nomic-ai/nomic-embed-text-v1.5": "search_query: ",
    "nomic-ai/nomic-embed-text-v1.5-Q": "search_query: ",
    "nomic-embed-text": "search_query: ",
}
DOCUMENT_PREFIXES = {
    "nomic-ai/nomic-embed-text-v1.5": "search_document: ",
    "nomic-ai/nomic-embed-text-v1.5-Q": "search_document: ",
    "nomic-embed-text": "search_document: ",
}
MAX_EMBED_CHARS = 6000


class EmbeddingUnavailable(Exception):
    """The configured embedding backend cannot be used right now (search degrades to keyword)."""


class EmbeddingBackend(ABC):
    name: str
    model: str

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) float32 array."""


def _normalize(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class FastEmbedBackend(EmbeddingBackend):
    """ONNX Runtime models via fastembed. CPU by default; CUDA when onnxruntime-gpu is installed."""

    name = "fastembed"

    def __init__(self, model: str, cache_dir: str, device: str, offline: bool, batch_size: int):
        from fastembed import TextEmbedding

        self.model = model
        self.batch_size = batch_size
        providers = None
        if device in ("auto", "cuda"):
            try:
                import onnxruntime as ort

                if "CUDAExecutionProvider" in ort.get_available_providers():
                    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
                elif device == "cuda":
                    log.warning("CUDA requested for embeddings but onnxruntime has no CUDA provider; using CPU")
            except ImportError:
                pass
        self.device = "cuda" if providers else "cpu"
        kwargs: dict = {"cache_dir": cache_dir}
        if providers:
            kwargs["providers"] = providers
        try:
            # Prefer the local cache so a downloaded model never needs the network again.
            self._model = TextEmbedding(model, local_files_only=True, **kwargs)
        except Exception:
            if offline:
                raise EmbeddingUnavailable(
                    f"Embedding model {model} is not downloaded and offline mode is on."
                ) from None
            self._model = TextEmbedding(model, **kwargs)

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(list(self._model.embed(texts, batch_size=self.batch_size)), dtype=np.float32)


class SentenceTransformersBackend(EmbeddingBackend):
    name = "sentence_transformers"

    def __init__(self, model: str, cache_dir: str, device: str, batch_size: int):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingUnavailable(
                "Sentence Transformers is not installed. Run: pip install 'starindex[sentence-transformers]'"
            ) from exc
        self.model = model
        self.batch_size = batch_size
        self._model = SentenceTransformer(model, cache_folder=cache_dir, device=None if device == "auto" else device)

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self._model.encode(texts, batch_size=self.batch_size, show_progress_bar=False), dtype=np.float32)


class HashingBackend(EmbeddingBackend):
    """Deterministic bag-of-words vectors. Not semantic: for tests and diagnostics only."""

    name = "hashing"

    def __init__(self, model: str = "hashing-256"):
        self.model = model
        self.dim = 256

    def embed(self, texts: list[str]) -> np.ndarray:
        import hashlib
        import re

        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                h = int.from_bytes(hashlib.md5(word.encode()).digest()[:4], "little")
                out[i, h % self.dim] += 1.0
        return out


class ProviderBackend(EmbeddingBackend):
    """Embeddings served by a configured provider (Ollama or an OpenAI-compatible server)."""

    def __init__(self, ctx: AppContext, backend: str, provider_id: str, model: str, batch_size: int):
        self.name = backend
        self.model = model
        self.ctx = ctx
        self.provider_id = provider_id
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> np.ndarray:
        from ..providers.base import CLOUD_KINDS, ProviderError

        cfg = self.ctx.providers.get(self.provider_id)
        if cfg is None or not cfg.enabled:
            raise EmbeddingUnavailable(f"Embedding provider '{self.provider_id}' is not configured or is disabled.")
        if cfg.kind in CLOUD_KINDS and not self.ctx.settings.privacy.allow_cloud_models:
            raise EmbeddingUnavailable("Cloud embedding providers are blocked by the privacy settings.")
        provider = self.ctx.router.provider(self.provider_id)
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), self.batch_size):
                out.extend(provider.embed(self.model, texts[i : i + self.batch_size]))
        except ProviderError as exc:
            raise EmbeddingUnavailable(f"Embedding request failed: {exc.message}") from exc
        return np.asarray(out, dtype=np.float32)


class EmbeddingService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.db = ctx.db
        self._lock = threading.RLock()
        self._backend: EmbeddingBackend | None = None
        self._signature: tuple | None = None
        self._dim: int | None = None
        self._error: str | None = None

    # -- configuration ----------------------------------------------------------------------

    def _settings_signature(self) -> tuple:
        e = self.ctx.settings.embedding
        return (e.backend, e.model, e.provider_id, e.device)

    @property
    def enabled(self) -> bool:
        return self.ctx.settings.embedding.backend != "none"

    @property
    def model_key(self) -> str:
        """Identifies the vector space. Changing backend or model changes the key."""
        e = self.ctx.settings.embedding
        return f"{e.backend}:{e.model}"

    @property
    def dim(self) -> int | None:
        if self._dim is None and self._signature == self._settings_signature():
            return None
        return self._dim

    def known_dim(self) -> int | None:
        """Dimension from the cache without loading the model."""
        if self._dim is not None and self._signature == self._settings_signature():
            return self._dim
        return self.db.scalar("SELECT dim FROM embeddings WHERE model_key=? LIMIT 1", (self.model_key,))

    def loaded(self) -> bool:
        return self._backend is not None and self._signature == self._settings_signature()

    def last_error(self) -> str | None:
        return self._error

    def backend(self) -> EmbeddingBackend:
        """Load (or reuse) the configured backend. Raises EmbeddingUnavailable."""
        sig = self._settings_signature()
        with self._lock:
            if self._backend is not None and self._signature == sig:
                return self._backend
            e = self.ctx.settings.embedding
            idx = self.ctx.settings.indexing
            cache_dir = str(self.ctx.config.paths.models_dir)
            os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
            try:
                if e.backend == "none":
                    raise EmbeddingUnavailable("Semantic search is turned off (embedding backend: none).")
                if e.backend == "fastembed":
                    backend: EmbeddingBackend = FastEmbedBackend(
                        e.model, cache_dir, e.device, self.ctx.settings.privacy.offline_mode, idx.embed_batch_size
                    )
                elif e.backend == "hashing":
                    backend = HashingBackend(e.model)
                elif e.backend == "sentence_transformers":
                    backend = SentenceTransformersBackend(e.model, cache_dir, e.device, idx.embed_batch_size)
                elif e.backend in ("ollama", "openai_compat"):
                    if not e.provider_id:
                        raise EmbeddingUnavailable("Choose which provider serves the embedding model in Settings.")
                    backend = ProviderBackend(self.ctx, e.backend, e.provider_id, e.model, idx.embed_batch_size)
                else:
                    raise EmbeddingUnavailable(f"Unknown embedding backend: {e.backend}")
                probe = backend.embed(["dimension probe"])
            except EmbeddingUnavailable as exc:
                self._error = str(exc)
                raise
            except Exception as exc:
                self._error = f"Could not load embedding model {e.model}: {type(exc).__name__}: {str(exc)[:200]}"
                raise EmbeddingUnavailable(self._error) from exc
            self._backend, self._signature, self._dim, self._error = backend, sig, int(probe.shape[1]), None
            log.info("embedding backend ready: %s (%d dims)", self.model_key, self._dim)
            return backend

    # -- embedding --------------------------------------------------------------------------

    def _prefixes(self) -> tuple[str, str]:
        e = self.ctx.settings.embedding
        return (
            e.query_prefix or QUERY_PREFIXES.get(e.model, ""),
            e.document_prefix or DOCUMENT_PREFIXES.get(e.model, ""),
        )

    def embed_query(self, text: str) -> np.ndarray:
        backend = self.backend()
        qp, _ = self._prefixes()
        return _normalize(backend.embed([qp + text[:MAX_EMBED_CHARS]]))[0]

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        backend = self.backend()
        _, dp = self._prefixes()
        return _normalize(backend.embed([dp + t[:MAX_EMBED_CHARS] for t in texts]))

    def embed_cached(self, items: list[tuple[str, str]]) -> dict[str, np.ndarray]:
        """Embed ``(content_hash, text)`` pairs, reusing cached vectors. Returns hash -> vector."""
        model_key = self.model_key
        out: dict[str, np.ndarray] = {}
        hashes = list(dict.fromkeys(h for h, _ in items))
        for start in range(0, len(hashes), 500):
            part = hashes[start : start + 500]
            for row in self.db.all(
                f"SELECT content_hash, vector FROM embeddings WHERE model_key=? AND content_hash IN ({placeholders(len(part))})",
                (model_key, *part),
            ):
                out[row["content_hash"]] = np.frombuffer(row["vector"], dtype=np.float32)
        missing: dict[str, str] = {}
        for h, text in items:
            if h not in out and h not in missing:
                missing[h] = text
        if missing:
            keys = list(missing)
            vectors = self.embed_documents([missing[k] for k in keys])
            now = utcnow()
            with self.db.tx() as c:
                c.executemany(
                    "INSERT OR REPLACE INTO embeddings(content_hash, model_key, dim, vector, created_at) VALUES (?,?,?,?,?)",
                    [(k, model_key, int(vectors.shape[1]), vectors[i].astype(np.float32).tobytes(), now) for i, k in enumerate(keys)],
                )
            for i, k in enumerate(keys):
                out[k] = vectors[i]
        return out

    def cached_vector(self, content_hash: str) -> np.ndarray | None:
        row = self.db.one(
            "SELECT vector FROM embeddings WHERE model_key=? AND content_hash=?", (self.model_key, content_hash)
        )
        return np.frombuffer(row["vector"], dtype=np.float32) if row else None
