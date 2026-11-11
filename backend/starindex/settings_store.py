"""Typed, user-editable application settings persisted in SQLite.

Secrets are deliberately absent from these models (see ``starindex.secrets``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .db import Database, dumps, loads, utcnow

IndexLevel = Literal["metadata", "docs", "deep"]
TaskType = Literal["classify", "summarize", "expand_query", "answer", "reasoning", "compare"]
TASK_TYPES: tuple[TaskType, ...] = ("classify", "summarize", "expand_query", "answer", "reasoning", "compare")

DEFAULT_EXCLUDE_DIRS = [
    "node_modules", "vendor", "vendors", "third_party", "third-party", "thirdparty", "external", "extern",
    "deps", "bower_components", "dist", "build", "out", "target", "bin", "obj", ".git", ".github",
    ".idea", ".vscode", "__pycache__", ".venv", "venv", "env", ".tox", ".next", ".nuxt", "coverage",
    "site-packages", "Pods", ".gradle", "fixtures", "testdata", "snapshots", "__snapshots__",
]
DEFAULT_EXCLUDE_GLOBS = [
    "*.min.js", "*.min.css", "*.map", "*.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "*.pb.go", "*_pb2.py", "*.generated.*", "*.g.dart", "*.snap", "*.svg", "*.ipynb",
]
DEFAULT_CODE_EXTENSIONS = [
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".kts", ".cs",
    ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".swift", ".rb", ".php", ".scala", ".lua", ".zig",
    ".dart", ".ex", ".exs", ".clj", ".hs", ".ml", ".r", ".jl", ".sh", ".ps1", ".sql", ".proto",
    ".graphql", ".gql", ".vue", ".svelte", ".glsl", ".wgsl", ".gd", ".tf", ".nix",
]


class GitHubSettings(BaseModel):
    auth_method: Literal["gh_cli", "token", "public", "none"] = "none"
    username: str | None = None  # required for "public" mode; informational otherwise


class IndexingSettings(BaseModel):
    default_level: IndexLevel = "docs"
    auto_index_new: bool = True
    max_file_bytes: int = Field(400_000, ge=1_000, le=20_000_000)
    max_doc_chars: int = Field(200_000, ge=5_000, le=5_000_000)
    max_docs_files: int = Field(40, ge=1, le=500)          # documentation level, per repository
    max_deep_files: int = Field(1500, ge=10, le=50_000)    # deep level, per repository
    max_tarball_mb: int = Field(150, ge=1, le=5_000)
    max_repo_size_mb_for_deep: int = Field(2_000, ge=1, le=100_000)
    exclude_dirs: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE_DIRS))
    exclude_globs: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE_GLOBS))
    code_extensions: list[str] = Field(default_factory=lambda: list(DEFAULT_CODE_EXTENSIONS))
    fetch_concurrency: int = Field(4, ge=1, le=16)
    worker_concurrency: int = Field(3, ge=1, le=16)
    embed_batch_size: int = Field(32, ge=1, le=512)


class EmbeddingSettings(BaseModel):
    backend: Literal["fastembed", "ollama", "openai_compat", "sentence_transformers", "none"] = "fastembed"
    model: str = "BAAI/bge-small-en-v1.5"
    provider_id: str | None = None      # for ollama / openai_compat backends: which configured provider
    device: Literal["auto", "cpu", "cuda"] = "auto"
    query_prefix: str = ""              # some models want "query: " / "search_query: "
    document_prefix: str = ""


class RerankSettings(BaseModel):
    enabled: bool = True
    backend: Literal["fastembed", "none"] = "fastembed"
    model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    top_n: int = Field(30, ge=5, le=200)


class SearchSettings(BaseModel):
    default_mode: Literal["keyword", "semantic", "hybrid"] = "hybrid"
    rrf_k: int = Field(60, ge=1, le=500)
    lexical_weight: float = Field(1.0, ge=0, le=5)
    semantic_weight: float = Field(1.0, ge=0, le=5)
    # Bounded priors: relevance stays primary. Each is the maximum relative boost.
    popularity_boost: float = Field(0.03, ge=0, le=0.25)
    freshness_boost: float = Field(0.03, ge=0, le=0.25)
    max_results_per_owner: int = Field(0, ge=0, le=50)   # 0 = unlimited
    query_expansion: Literal["off", "auto"] = "off"
    typo_tolerance: bool = True
    save_history: bool = True


class PrivacySettings(BaseModel):
    allow_cloud_models: bool = False     # master switch for outbound model traffic
    confirm_before_cloud: bool = True    # require explicit confirmation per request
    offline_mode: bool = False           # also blocks GitHub traffic


class SyncSettings(BaseModel):
    incremental_interval_minutes: int = Field(360, ge=15, le=10_080)
    full_interval_hours: int = Field(168, ge=1, le=2_160)
    scheduled_sync_enabled: bool = True
    unstarred_retention: Literal["keep", "keep_annotated", "delete"] = "keep"


class ResearchLimits(BaseModel):
    max_tool_calls: int = Field(12, ge=1, le=60)
    max_seconds: int = Field(240, ge=10, le=3_600)
    max_context_chars: int = Field(36_000, ge=2_000, le=400_000)
    max_total_tokens: int = Field(150_000, ge=2_000, le=5_000_000)
    max_output_tokens: int = Field(3_000, ge=128, le=32_000)


class StorageSettings(BaseModel):
    backup_enabled: bool = True
    backup_interval_hours: int = Field(168, ge=1, le=8_760)
    backup_keep: int = Field(5, ge=1, le=100)


class Route(BaseModel):
    provider_id: str
    model: str


class RoutingSettings(BaseModel):
    default: Route | None = None
    tasks: dict[TaskType, Route] = Field(default_factory=dict)


class ModelPrice(BaseModel):
    """USD per million tokens. Only shown as an estimate when present."""

    provider_kind: str
    model_prefix: str
    input_per_mtok: float
    output_per_mtok: float
    source: str = "user"


class AppSettings(BaseModel):
    onboarding_completed: bool = False
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    indexing: IndexingSettings = Field(default_factory=IndexingSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    rerank: RerankSettings = Field(default_factory=RerankSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    sync: SyncSettings = Field(default_factory=SyncSettings)
    research: ResearchLimits = Field(default_factory=ResearchLimits)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    routing: RoutingSettings = Field(default_factory=RoutingSettings)
    pricing: list[ModelPrice] = Field(default_factory=list)


_KEY = "app"


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class SettingsStore:
    def __init__(self, db: Database):
        self.db = db

    def load(self) -> AppSettings:
        raw = loads(self.db.scalar("SELECT value FROM settings WHERE key=?", (_KEY,)), {})
        try:
            return AppSettings.model_validate(raw)
        except Exception:
            # A corrupted or outdated blob must not brick the app: keep what validates.
            merged = _deep_merge(AppSettings().model_dump(), raw if isinstance(raw, dict) else {})
            try:
                return AppSettings.model_validate(merged)
            except Exception:
                return AppSettings()

    def save(self, settings: AppSettings) -> AppSettings:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (_KEY, dumps(settings.model_dump()), utcnow()),
            )
        return settings

    def patch(self, patch: dict[str, Any]) -> AppSettings:
        """Deep-merge a partial update, validate, persist. Raises pydantic.ValidationError."""
        current = self.load().model_dump()
        updated = AppSettings.model_validate(_deep_merge(current, patch))
        return self.save(updated)

    # Small free-form values (host id, cursors) share the table under their own keys.
    def get_value(self, key: str, default: Any = None) -> Any:
        return loads(self.db.scalar("SELECT value FROM settings WHERE key=?", (key,)), default)

    def set_value(self, key: str, value: Any) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, dumps(value), utcnow()),
            )
