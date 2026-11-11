"""HTTP contract: every request and response body of the StarIndex API.

These Pydantic models are the single source of truth for the frontend (TypeScript types
are generated from the OpenAPI document FastAPI derives from them).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..providers.base import ModelInfo, ProviderCapabilities
from ..settings_store import AppSettings, IndexLevel, Route, TaskType

SearchMode = Literal["keyword", "semantic", "hybrid"]
SearchScope = Literal["repos", "docs", "code"]
EvidenceClass = Literal["observed", "documented", "inferred", "unverified"]


class Ok(BaseModel):
    ok: bool = True
    message: str = ""


class ErrorBody(BaseModel):
    """Body of every non-2xx response: ``{"error": {...}}``."""

    kind: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


# ----------------------------------------------------------------------------- repositories


class CategoryRef(BaseModel):
    id: int
    slug: str
    name: str
    source: str = "rule"          # rule | embedding | llm | user
    confidence: float = 1.0


class CollectionRef(BaseModel):
    id: int
    name: str


class RepoSummary(BaseModel):
    """Compact repository card used in lists and search results."""

    id: int
    full_name: str
    owner: str
    name: str
    owner_avatar_url: str | None = None
    description: str | None = None
    html_url: str
    homepage: str | None = None
    topics: list[str] = []
    language: str | None = None
    stars: int = 0
    forks: int = 0
    license_spdx: str | None = None
    is_fork: bool = False
    is_archived: bool = False
    is_private: bool = False
    pushed_at: str | None = None
    updated_at: str | None = None
    created_at: str | None = None
    starred_at: str | None = None
    star_state: Literal["starred", "unstarred"] = "starred"
    access_state: Literal["ok", "not_found", "blocked", "error"] = "ok"
    size_kb: int = 0
    index_level: IndexLevel = "docs"
    indexed_level: IndexLevel | None = None
    index_status: Literal["pending", "queued", "indexing", "indexed", "failed", "skipped"] = "pending"
    indexed_at: str | None = None
    maintenance: Literal["active", "maintained", "slowing", "stale", "archived", "unknown"] = "unknown"
    categories: list[CategoryRef] = []
    tags: list[str] = []
    collections: list[CollectionRef] = []
    favorite: bool = False
    priority: int = 0
    has_note: bool = False


class NameChange(BaseModel):
    old_full_name: str
    new_full_name: str
    changed_at: str


class RepoDetail(RepoSummary):
    node_id: str | None = None
    clone_url: str | None = None
    watchers: int = 0
    open_issues: int = 0
    license_name: str | None = None
    default_branch: str | None = None
    is_template: bool = False
    is_disabled: bool = False
    parent_full_name: str | None = None
    languages: dict[str, int] = {}
    unstarred_at: str | None = None
    index_error: str | None = None
    indexed_commit_sha: str | None = None
    tree_truncated: bool = False
    doc_count: int = 0
    chunk_count: int = 0
    embedded_chunk_count: int = 0
    note: str = ""
    last_opened_at: str | None = None
    open_count: int = 0
    name_history: list[NameChange] = []
    first_seen_at: str | None = None
    last_seen_at: str | None = None


class RepoPage(BaseModel):
    items: list[RepoSummary]
    total: int
    limit: int
    offset: int


class RepoFilters(BaseModel):
    """Structured filters shared by the library listing and every search mode."""

    languages: list[str] = []
    topics: list[str] = []
    categories: list[str] = []          # category slugs
    collections: list[int] = []
    tags: list[str] = []
    licenses: list[str] = []            # SPDX ids; "none" matches repositories without a license
    owners: list[str] = []
    min_stars: int | None = None
    max_stars: int | None = None
    min_size_kb: int | None = None
    max_size_kb: int | None = None
    archived: bool | None = None
    fork: bool | None = None
    favorite: bool | None = None
    has_note: bool | None = None
    star_state: Literal["starred", "unstarred", "all"] = "starred"
    maintenance: list[str] = []         # active | maintained | slowing | stale | archived
    index_status: list[str] = []
    indexed_level: list[str] = []
    pushed_after: str | None = None     # ISO date
    pushed_before: str | None = None
    starred_after: str | None = None
    starred_before: str | None = None
    repo_ids: list[int] = []            # restrict to an explicit set


class FacetValue(BaseModel):
    value: str
    label: str | None = None
    count: int


class Facets(BaseModel):
    languages: list[FacetValue] = []
    topics: list[FacetValue] = []
    licenses: list[FacetValue] = []
    categories: list[FacetValue] = []
    tags: list[FacetValue] = []
    collections: list[FacetValue] = []
    maintenance: list[FacetValue] = []
    index_status: list[FacetValue] = []
    indexed_level: list[FacetValue] = []
    owners: list[FacetValue] = []


class RepoListRequest(BaseModel):
    filters: RepoFilters = Field(default_factory=RepoFilters)
    text: str = ""                      # quick substring filter on name/description/topics
    sort: Literal["starred_at", "stars", "pushed_at", "name", "updated_at", "last_opened", "size", "priority"] = "starred_at"
    order: Literal["asc", "desc"] = "desc"
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)


class AnnotationPatch(BaseModel):
    note: str | None = None
    favorite: bool | None = None
    priority: int | None = Field(None, ge=0, le=3)


class TagsUpdate(BaseModel):
    tags: list[str]


class CategoriesUpdate(BaseModel):
    category_ids: list[int]


class DocumentInfo(BaseModel):
    id: int
    path: str
    kind: str
    language: str | None = None
    size_bytes: int = 0
    level: str
    commit_sha: str | None = None
    indexed_at: str
    truncated: bool = False
    chunk_count: int = 0
    html_url: str | None = None


class ChunkSpan(BaseModel):
    chunk_id: int
    start_line: int
    end_line: int
    heading: str = ""


class DocumentContent(DocumentInfo):
    content: str
    chunks: list[ChunkSpan] = []


class Dependency(BaseModel):
    ecosystem: str
    name: str
    version_spec: str = ""
    scope: str = "runtime"
    manifest_path: str
    shared_with: int = 0        # how many other starred repositories declare the same dependency


class RelationReason(BaseModel):
    kind: Literal["semantic", "topics", "dependencies", "category", "owner", "fork", "language"]
    detail: str
    weight: float = 0.0


class RelatedRepo(BaseModel):
    repo: RepoSummary
    score: float
    reasons: list[RelationReason]


class DuplicateGroup(BaseModel):
    reason: Literal["fork", "same_name", "near_duplicate"]
    detail: str
    repos: list[RepoSummary]


class LibraryOverview(BaseModel):
    total_starred: int
    total_unstarred_retained: int
    recently_starred: list[RepoSummary]
    recently_rediscovered: list[RepoSummary]
    favorites: list[RepoSummary]
    needs_attention: list[RepoSummary]     # archived or inaccessible repositories
    category_counts: list[FacetValue]


class IndexRequest(BaseModel):
    level: IndexLevel = "docs"
    force: bool = False


class BatchIndexRequest(BaseModel):
    level: IndexLevel
    repo_ids: list[int] = []
    collection_id: int | None = None
    category_slug: str | None = None
    filters: RepoFilters | None = None
    force: bool = False


class BatchIndexResponse(BaseModel):
    queued: int
    skipped: int
    job_ids: list[int] = []


class RepoSummaryText(BaseModel):
    """Cached AI-generated text about one repository (summary or usage ideas)."""

    repo_id: int
    kind: Literal["summary", "usage_ideas"]
    content: str | None = None
    evidence: list[Evidence] = []
    provider: str | None = None
    model: str | None = None
    created_at: str | None = None
    stale: bool = False          # source material changed since generation


# ----------------------------------------------------------------------------- search


class SearchRequest(BaseModel):
    query: str = ""
    mode: SearchMode = "hybrid"
    scope: SearchScope = "repos"
    filters: RepoFilters = Field(default_factory=RepoFilters)
    sort: Literal["relevance", "stars", "pushed_at", "starred_at", "name"] = "relevance"
    limit: int = Field(25, ge=1, le=200)
    offset: int = Field(0, ge=0)
    rerank: bool | None = None           # None = follow settings
    explain: bool = False
    record: bool = True                  # write to search history


class Match(BaseModel):
    """A passage that explains why a repository (or document) matched."""

    chunk_id: int
    document_id: int
    repo_id: int
    path: str
    kind: str
    heading: str = ""
    start_line: int
    end_line: int
    snippet: str                         # plain text with matches wrapped in «…» markers
    commit_sha: str | None = None
    indexed_at: str | None = None
    html_url: str | None = None          # GitHub permalink with line anchor
    score: float = 0.0
    source: Literal["lexical", "semantic", "both"] = "lexical"


class ResultSignals(BaseModel):
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    fused_score: float | None = None
    rerank_score: float | None = None
    prior: float | None = None           # bounded popularity/freshness multiplier
    name_match: bool = False


class SearchResult(BaseModel):
    repo: RepoSummary
    score: float
    signals: ResultSignals = Field(default_factory=ResultSignals)
    matches: list[Match] = []
    field_highlights: dict[str, str] = {}   # description/name with «…» markers


class TermCorrection(BaseModel):
    original: str
    corrected: str


class ParsedQuery(BaseModel):
    text: str                             # free text after operators were removed
    phrases: list[str] = []
    excluded: list[str] = []
    operators: dict[str, list[str]] = {}  # e.g. {"lang": ["python"], "stars": [">1000"]}
    corrections: list[TermCorrection] = []
    expansions: list[str] = []


class SearchResponse(BaseModel):
    query: str
    mode: SearchMode                      # the mode actually executed
    requested_mode: SearchMode
    scope: SearchScope
    parsed: ParsedQuery
    total: int
    took_ms: int
    results: list[SearchResult]
    facets: Facets | None = None
    notices: list[str] = []               # e.g. "semantic index not ready; used keyword search"
    reranked: bool = False
    embedding_model: str | None = None


class Suggestion(BaseModel):
    kind: Literal["repo", "topic", "language", "history", "saved", "operator"]
    text: str
    detail: str | None = None
    repo_id: int | None = None


class SearchHistoryEntry(BaseModel):
    id: int
    query: str
    mode: str
    scope: str
    filters: RepoFilters
    result_count: int
    created_at: str


class SavedSearch(BaseModel):
    id: int
    name: str
    request: SearchRequest
    created_at: str
    last_run_at: str | None = None


class SavedSearchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    request: SearchRequest


# ----------------------------------------------------------------------------- evidence, ask, research


class Evidence(BaseModel):
    """One citable piece of indexed material. ``ref`` is what answers cite, e.g. [E3]."""

    ref: str
    evidence_class: EvidenceClass         # observed (metadata/code) | documented (project's own docs)
    kind: Literal["metadata", "readme", "doc", "manifest", "config", "api", "example", "code", "dependency", "activity"]
    repo_id: int
    repo_full_name: str
    title: str
    text: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    commit_sha: str | None = None
    indexed_at: str | None = None
    html_url: str | None = None
    chunk_id: int | None = None
    document_id: int | None = None
    injection_flag: bool = False          # text looked like it tries to instruct the model


class EvidenceDetail(Evidence):
    """Evidence plus surrounding lines for the citation viewer."""

    context_before: str = ""
    context_after: str = ""
    context_start_line: int | None = None
    repo: RepoSummary | None = None


class TraceStep(BaseModel):
    """Research Trace entry: what was done, never the model's private reasoning."""

    index: int
    kind: Literal["plan", "tool", "note", "synthesis", "limit", "warning"]
    title: str
    tool: str | None = None
    arguments: dict[str, Any] = {}
    summary: str = ""
    repo_ids: list[int] = []
    evidence_refs: list[str] = []
    duration_ms: int | None = None
    ok: bool = True


class RouteOverride(BaseModel):
    provider_id: str
    model: str


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    repo_ids: list[int] = []              # restrict to these repositories (e.g. Ask about this repo)
    filters: RepoFilters | None = None
    route: RouteOverride | None = None    # explicit provider/model for this request
    confirm_cloud: bool = False           # user acknowledged that context leaves the machine
    history: list[dict[str, str]] = []    # prior turns [{role, content}] for follow-ups


ResearchCapability = Literal["auto", "discovery", "comparison", "composition", "gap_analysis", "scouting", "relationships"]


class ResearchLimitsOverride(BaseModel):
    max_tool_calls: int | None = Field(None, ge=1, le=60)
    max_seconds: int | None = Field(None, ge=10, le=3600)
    max_context_chars: int | None = Field(None, ge=2000, le=400_000)


class ResearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    capability: ResearchCapability = "auto"
    repo_ids: list[int] = []
    collection_id: int | None = None
    route: RouteOverride | None = None
    confirm_cloud: bool = False
    limits: ResearchLimitsOverride | None = None


class UsageInfo(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated: bool = False
    cost_usd: float | None = None         # only when a price is configured for the model
    calls: int = 0


class UnsupportedCitation(BaseModel):
    text: str
    reason: str


class ReportSummary(BaseModel):
    id: int
    kind: Literal["ask", "research", "compare", "usage_ideas"]
    title: str
    question: str
    status: Literal["running", "completed", "failed", "cancelled"]
    saved: bool
    provider_id: str | None = None
    provider_kind: str | None = None
    model: str | None = None
    repo_ids: list[int] = []
    created_at: str
    finished_at: str | None = None


class Report(ReportSummary):
    answer_md: str = ""
    evidence: list[Evidence] = []
    trace: list[TraceStep] = []
    usage: UsageInfo = Field(default_factory=UsageInfo)
    data: dict[str, Any] = {}             # kind-specific: comparison table, limits hit, warnings
    warnings: list[str] = []
    unsupported: list[UnsupportedCitation] = []
    error: str | None = None
    collections: list[CollectionRef] = []
    repos: list[RepoSummary] = []


class ReportPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    saved: bool | None = None
    collection_ids: list[int] | None = None


# Server-sent events emitted by POST /api/ask, /api/research, /api/compare/narrative,
# /api/repos/{id}/summary.  Each SSE message is `event: <type>` + `data: <json>`.
#   status     {message, provider_id, provider_kind, model, is_cloud}
#   plan       {steps: [str]}                      (research only)
#   step       TraceStep                           (research only)
#   evidence   {evidence: [Evidence]}
#   delta      {text}
#   done       {report: Report}
#   error      {error: ProviderErrorInfo, report_id?: int, alternatives: [ProviderOption]}


class ProviderErrorInfo(BaseModel):
    kind: str
    message: str
    provider_id: str | None = None
    provider_kind: str | None = None
    status: int | None = None
    code: str | None = None
    retryable: bool = False
    retry_after_s: float | None = None
    action_url: str | None = None
    action_label: str | None = None


class ProviderOption(BaseModel):
    """A provider the user may explicitly switch to. Never selected automatically."""

    provider_id: str
    provider_kind: str
    name: str
    is_local: bool
    is_paid: bool
    default_model: str | None = None


# ----------------------------------------------------------------------------- compare


class CompareRequest(BaseModel):
    repo_ids: list[int] = Field(min_length=2, max_length=6)


class CompareRow(BaseModel):
    key: str
    label: str
    group: Literal["identity", "capabilities", "architecture", "maturity", "dependencies", "licensing"]
    values: list[str]                     # one cell per repository, same order as `repos`
    evidence_class: EvidenceClass = "observed"
    evidence_refs: list[list[str]] = []   # per cell


class SharedItem(BaseModel):
    name: str
    repo_ids: list[int]


class Comparison(BaseModel):
    repos: list[RepoSummary]
    rows: list[CompareRow]
    shared_topics: list[SharedItem] = []
    shared_dependencies: list[SharedItem] = []
    license_notes: list[str] = []         # deterministic compatibility observations
    evidence: list[Evidence] = []
    report_id: int | None = None          # set once saved


class CompareNarrativeRequest(BaseModel):
    repo_ids: list[int] = Field(min_length=2, max_length=6)
    focus: str = ""                       # optional user angle, e.g. "for a local RAG stack"
    route: RouteOverride | None = None
    confirm_cloud: bool = False


# ----------------------------------------------------------------------------- collections, tags, categories


class Collection(BaseModel):
    id: int
    name: str
    description: str = ""
    kind: Literal["manual", "smart"] = "manual"
    smart_query: SearchRequest | None = None
    repo_count: int = 0
    report_count: int = 0
    created_at: str
    updated_at: str
    preview: list[RepoSummary] = []


class CollectionDetail(Collection):
    repos: list[RepoSummary] = []
    reports: list[ReportSummary] = []


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    kind: Literal["manual", "smart"] = "manual"
    smart_query: SearchRequest | None = None
    repo_ids: list[int] = []


class CollectionPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None
    smart_query: SearchRequest | None = None


class RepoIds(BaseModel):
    repo_ids: list[int]


class Tag(BaseModel):
    name: str
    count: int


class Category(BaseModel):
    id: int
    slug: str
    name: str
    description: str = ""
    keywords: list[str] = []
    is_builtin: bool = False
    repo_count: int = 0


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    keywords: list[str] = []


class CategoryPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    description: str | None = None
    keywords: list[str] | None = None


# ----------------------------------------------------------------------------- sync and jobs


class RateLimitInfo(BaseModel):
    limit: int | None = None
    remaining: int | None = None
    reset_at: str | None = None
    resource: str | None = None


class GitHubAuthStatus(BaseModel):
    method: Literal["gh_cli", "token", "public", "none"]
    configured: bool
    login: str | None = None
    username: str | None = None           # public-mode username
    gh_cli_available: bool = False
    gh_cli_authenticated: bool = False
    token_stored: bool = False
    scopes: list[str] = []
    ok: bool = False
    detail: str = ""
    rate_limit: RateLimitInfo | None = None


class GitHubAuthUpdate(BaseModel):
    method: Literal["gh_cli", "token", "public", "none"]
    token: str | None = None              # only for method "token"; stored in the OS keyring
    username: str | None = None           # only for method "public"


class SyncRun(BaseModel):
    id: int
    kind: Literal["full", "incremental"]
    status: Literal["running", "succeeded", "failed", "cancelled", "interrupted"]
    started_at: str
    finished_at: str | None = None
    pages_fetched: int = 0
    repos_seen: int = 0
    added: int = 0
    updated: int = 0
    renamed: int = 0
    unstarred: int = 0
    restarred: int = 0
    not_modified: bool = False
    total_expected: int | None = None
    error: str | None = None


class IndexProgress(BaseModel):
    total: int
    indexed: int
    pending: int
    queued: int
    indexing: int
    failed: int
    skipped: int
    by_level: dict[str, int] = {}
    documents: int = 0
    chunks: int = 0
    chunks_embedded: int = 0
    repos_embedded: int = 0


class QueueSummary(BaseModel):
    queued: int
    running: int
    failed: int
    succeeded_last_hour: int
    throughput_per_min: float             # completed jobs per minute over the last 10 minutes
    by_type: dict[str, dict[str, int]] = {}
    eta_seconds: int | None = None


class WorkerInfo(BaseModel):
    alive: bool
    worker_id: str | None = None
    last_seen: str | None = None
    mode: Literal["embedded", "external", "none"] = "none"


class SyncStatus(BaseModel):
    github: GitHubAuthStatus
    current_run: SyncRun | None = None
    last_full: SyncRun | None = None
    last_incremental: SyncRun | None = None
    index: IndexProgress
    queue: QueueSummary
    worker: WorkerInfo
    next_scheduled_sync: str | None = None
    rate_limit: RateLimitInfo | None = None


class SyncStartRequest(BaseModel):
    kind: Literal["full", "incremental"] = "incremental"


class Job(BaseModel):
    id: int
    type: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    priority: int
    attempts: int
    max_attempts: int
    progress_current: int = 0
    progress_total: int = 0
    progress_message: str = ""
    error: str | None = None
    payload: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    repo_full_name: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    run_after: str | None = None


class JobPage(BaseModel):
    items: list[Job]
    total: int


class JobEnqueue(BaseModel):
    type: Literal[
        "sync_full", "sync_incremental", "embed_pending", "classify_repos", "rebuild_search_index",
        "rebuild_vector_index", "cleanup", "backup", "refresh_metadata",
    ]
    payload: dict[str, Any] = {}


class Schedule(BaseModel):
    name: str
    job_type: str
    interval_minutes: int
    enabled: bool
    next_run_at: str | None = None
    last_run_at: str | None = None


class SchedulePatch(BaseModel):
    interval_minutes: int | None = Field(None, ge=5, le=525_600)
    enabled: bool | None = None


# ----------------------------------------------------------------------------- providers


class ProviderTemplate(BaseModel):
    kind: str
    label: str
    category: Literal["local", "cloud", "chatgpt_plan"]
    description: str
    default_base_url: str | None = None
    needs_api_key: bool = False
    api_key_optional: bool = False
    base_url_editable: bool = True
    option_fields: list[dict[str, Any]] = []   # [{key, label, placeholder, required}]
    docs_url: str | None = None


class ProviderInfo(BaseModel):
    id: str
    kind: str
    name: str
    category: Literal["local", "cloud", "chatgpt_plan"]
    base_url: str | None = None
    enabled: bool
    options: dict[str, Any] = {}
    has_secret: bool = False
    capabilities: ProviderCapabilities
    blocked_by_privacy: bool = False      # cloud provider while outbound model traffic is disabled
    created_at: str | None = None


class ProviderCreate(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=80)
    base_url: str | None = None
    options: dict[str, Any] = {}
    api_key: str | None = None            # stored in the OS keyring, never echoed back
    enabled: bool = True


class ProviderPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    base_url: str | None = None
    options: dict[str, Any] | None = None
    enabled: bool | None = None


class SecretUpdate(BaseModel):
    api_key: str = Field(min_length=1)


class ProviderTestResult(BaseModel):
    ok: bool
    latency_ms: int | None = None
    detail: str = ""
    model_count: int | None = None
    error: ProviderErrorInfo | None = None
    sample: str | None = None             # short generation sample when a model was given


class ProviderTestRequest(BaseModel):
    model: str | None = None              # when set, also run a one-line generation


class ProviderModels(BaseModel):
    provider_id: str
    models: list[ModelInfo]
    error: ProviderErrorInfo | None = None


class RoutingInfo(BaseModel):
    default: Route | None = None
    tasks: dict[TaskType, Route] = {}
    effective: dict[TaskType, Route | None] = {}   # after applying the default
    task_labels: dict[TaskType, str] = {}


class RoutingUpdate(BaseModel):
    default: Route | None = None
    tasks: dict[TaskType, Route | None] = {}


class UsageRow(BaseModel):
    provider_id: str
    provider_kind: str
    model: str
    task: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    estimated: bool = False


class UsageSummary(BaseModel):
    since: str
    rows: list[UsageRow]
    total_cost_usd: float | None = None
    pricing_note: str = ""


class GpuInfo(BaseModel):
    name: str
    memory_total_mb: int | None = None
    memory_free_mb: int | None = None
    driver: str | None = None


class HardwareInfo(BaseModel):
    os: str
    cpu_count: int
    memory_total_mb: int | None = None
    gpus: list[GpuInfo] = []
    cuda_available: bool = False          # NVIDIA GPU visible via nvidia-smi
    onnx_providers: list[str] = []
    recommendations: list[str] = []       # sensible model/config suggestions for this machine


# ChatGPT plan (Sign in with ChatGPT, open-source token sharing)


class ChatGPTAccount(BaseModel):
    id: str
    label: str
    email: str | None = None
    client_id_suffix: str                 # last characters only; the id itself is not a secret but is noise
    plan_usage_enabled: bool
    is_active: bool
    signed_in: bool
    scopes: list[str] = []
    saved_at: str
    expires_at: str | None = None
    last_error: str | None = None
    welcome_pending: bool = False


class ChatGPTStatus(BaseModel):
    available: bool                       # integration usable right now
    reason: str = ""                      # why not, in plain language
    host_id_set: bool
    accounts: list[ChatGPTAccount] = []
    active_account_id: str | None = None
    usage_url: str = "https://chatgpt.com/settings/usage"
    blocked_by_privacy: bool = False


class ChatGPTLoginStart(BaseModel):
    account_id: str | None = None         # reauthorise a saved account; None = register a new one
    enable_plan_usage: bool = False       # user explicitly asked to (re)grant plan usage consent


class ChatGPTLoginAttempt(BaseModel):
    attempt_id: str
    auth_url: str | None = None           # open in the system browser
    status: Literal["pending", "completed", "denied", "failed", "expired"]
    message: str = ""
    account_id: str | None = None
    plan_usage_enabled: bool | None = None


# ----------------------------------------------------------------------------- system


class Counts(BaseModel):
    repos: int = 0
    starred: int = 0
    unstarred: int = 0
    documents: int = 0
    chunks: int = 0
    chunks_embedded: int = 0
    collections: int = 0
    reports: int = 0


class EmbeddingStatus(BaseModel):
    backend: str
    model: str
    model_key: str | None = None
    dim: int | None = None
    ready: bool
    detail: str = ""


class LlmStatus(BaseModel):
    configured: bool
    provider_id: str | None = None
    provider_kind: str | None = None
    provider_name: str | None = None
    model: str | None = None
    is_cloud: bool = False


class SystemStatus(BaseModel):
    version: str
    data_dir: str
    first_run: bool                       # no repositories and onboarding not completed
    onboarding_completed: bool
    github: GitHubAuthStatus
    counts: Counts
    embedding: EmbeddingStatus
    vector_backend: str
    vector_ok: bool
    llm: LlmStatus
    worker: WorkerInfo
    secret_backend: str
    cloud_allowed: bool
    offline_mode: bool


class DiagnosticCheck(BaseModel):
    name: str
    status: Literal["ok", "warn", "fail", "info"]
    detail: str
    hint: str = ""


class Diagnostics(BaseModel):
    checks: list[DiagnosticCheck]
    generated_at: str


class SettingsPatch(BaseModel):
    """Partial, deep-merged update of AppSettings."""

    patch: dict[str, Any]


class StorageInfo(BaseModel):
    data_dir: str
    database_bytes: int
    vector_bytes: int
    models_bytes: int
    backups_bytes: int
    free_bytes: int | None = None
    embeddings_rows: int = 0


class BackupInfo(BaseModel):
    name: str
    created_at: str
    size_bytes: int
    path: str


class RestoreRequest(BaseModel):
    name: str
    confirm: bool = False


__all__ = [name for name in dir() if not name.startswith("_")] + ["AppSettings"]

RepoSummaryText.model_rebuild()
