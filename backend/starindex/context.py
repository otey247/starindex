"""Application context: the one place where long-lived services are constructed and wired.

Both the API process and the worker build an ``AppContext``. Services are created lazily so
that lightweight commands (migrations, diagnostics) do not load embedding models.
"""

from __future__ import annotations

import threading
from functools import cached_property
from typing import TYPE_CHECKING

from .config import RuntimeConfig, get_config
from .db import Database
from .secrets import SecretStore, open_secret_store
from .settings_store import AppSettings, SettingsStore

if TYPE_CHECKING:
    from .github.client import GitHubClient
    from .jobs.queue import JobQueue
    from .providers.chatgpt_plan.service import ChatGPTPlanService
    from .providers.registry import ProviderStore
    from .providers.router import ModelRouter
    from .search.embeddings import EmbeddingService
    from .search.rerank import Reranker
    from .search.service import SearchService
    from .search.vector import VectorIndex


class AppContext:
    def __init__(self, config: RuntimeConfig | None = None):
        self.config = config or get_config()
        self.config.paths.ensure()
        self.db = Database(self.config.paths.db_path)
        self.db.migrate()
        from .indexing.classify import ensure_builtin_categories

        ensure_builtin_categories(self.db)
        self.settings_store = SettingsStore(self.db)
        self._lock = threading.RLock()
        self.worker_mode: str = "none"   # embedded | external | none (set by whoever starts a worker)
        self.keyword_only: bool = False  # True in helper processes that must not open the vector index

    # -- configuration --------------------------------------------------------------------

    @property
    def settings(self) -> AppSettings:
        return self.settings_store.load()

    @cached_property
    def secrets(self) -> SecretStore:
        return open_secret_store(self.config.paths.secrets_dir, disable_keyring=self.config.disable_keyring)

    # -- services (lazy) ------------------------------------------------------------------

    @cached_property
    def jobs(self) -> JobQueue:
        from .jobs.queue import JobQueue

        return JobQueue(self.db)

    @cached_property
    def github(self) -> GitHubClient:
        from .github.client import build_client

        return build_client(self)

    @cached_property
    def embeddings(self) -> EmbeddingService:
        from .search.embeddings import EmbeddingService

        return EmbeddingService(self)

    @cached_property
    def vectors(self) -> VectorIndex:
        from .search.vector import open_vector_index

        return open_vector_index(self)

    @cached_property
    def reranker(self) -> Reranker:
        from .search.rerank import Reranker

        return Reranker(self)

    @cached_property
    def search(self) -> SearchService:
        from .search.service import SearchService

        return SearchService(self)

    @cached_property
    def providers(self) -> ProviderStore:
        from .providers.registry import ProviderStore

        return ProviderStore(self.db, self.secrets)

    @cached_property
    def chatgpt(self) -> ChatGPTPlanService:
        from .providers.chatgpt_plan.service import ChatGPTPlanService

        return ChatGPTPlanService(self.db, self.secrets, self.settings_store, self.config.paths.data_dir)

    @cached_property
    def router(self) -> ModelRouter:
        from .providers.router import ModelRouter

        return ModelRouter(self.db, self.settings_store, self.providers, self.chatgpt)

    def reset_service(self, name: str) -> None:
        """Drop a cached service so it is rebuilt with new settings (e.g. embedding model change)."""
        with self._lock:
            self.__dict__.pop(name, None)

    def close(self) -> None:
        vectors = self.__dict__.get("vectors")
        if vectors is not None:
            try:
                vectors.close()
            except Exception:
                pass
        self.db.close()
