"""Optional local cross-encoder reranking (fastembed / ONNX Runtime)."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import AppContext

log = logging.getLogger(__name__)
MAX_PASSAGE_CHARS = 700


class Reranker:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self._lock = threading.Lock()
        self._model = None
        self._model_name: str | None = None
        self._failed: str | None = None

    @property
    def enabled(self) -> bool:
        r = self.ctx.settings.rerank
        return r.enabled and r.backend != "none"

    def status(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "Reranking is turned off."
        if self._failed:
            return False, self._failed
        if self._model is not None:
            return True, f"{self._model_name} loaded"
        return True, "Loads on first use."

    def _load(self):
        name = self.ctx.settings.rerank.model
        with self._lock:
            if self._model is not None and self._model_name == name:
                return self._model
            if self._failed and self._model_name == name:
                return None
            self._model_name = name
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                cache_dir = str(self.ctx.config.paths.models_dir)
                try:
                    self._model = TextCrossEncoder(name, cache_dir=cache_dir, local_files_only=True)
                except Exception:
                    if self.ctx.settings.privacy.offline_mode:
                        raise RuntimeError("model is not downloaded and offline mode is on") from None
                    self._model = TextCrossEncoder(name, cache_dir=cache_dir)
                self._failed = None
            except Exception as exc:
                self._model = None
                self._failed = f"Reranker {name} unavailable: {type(exc).__name__}: {str(exc)[:160]}"
                log.warning(self._failed)
            return self._model

    def score(self, query: str, passages: list[str], *, force: bool = False) -> list[float] | None:
        """Relevance score per passage (higher is better), or None when reranking is unavailable.

        ``force`` skips the global on/off setting (the caller already decided, e.g. a per-request override).
        """
        if not passages or self.ctx.settings.rerank.backend == "none" or not (force or self.enabled):
            return None
        model = self._load()
        if model is None:
            return None
        try:
            return [float(s) for s in model.rerank(query, [p[:MAX_PASSAGE_CHARS] for p in passages], batch_size=32)]
        except Exception as exc:
            log.warning("rerank failed: %s", exc)
            return None
