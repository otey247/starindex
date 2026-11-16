/**
 * Friendly aliases over the generated OpenAPI component types (`npm run gen:api`).
 * Response shapes come straight from the backend contract; request shapes are partial
 * because the server fills defaults.
 */
import type { components } from "./types.gen";

/**
 * Fields declared with a default on the server are optional in the generated schema but
 * are always present in responses, so response aliases treat every field as required.
 */
type DeepRequired<T> = T extends (infer U)[]
  ? DeepRequired<U>[]
  : T extends object
    ? { [K in keyof T]-?: DeepRequired<Exclude<T[K], undefined>> }
    : T;

type S = { [K in keyof components["schemas"]]: DeepRequired<components["schemas"][K]> };

export type Ok = S["Ok"];
export type RepoSummary = S["RepoSummary"];
export type RepoDetail = S["RepoDetail"];
export type RepoPage = S["RepoPage"];
export type RepoFilters = S["RepoFilters"];
export type Facets = S["Facets"];
export type FacetValue = S["FacetValue"];
export type CategoryRef = S["CategoryRef"];
export type CollectionRef = S["CollectionRef"];
export type DocumentInfo = S["DocumentInfo"];
export type DocumentContent = S["DocumentContent"];
export type ChunkSpan = S["ChunkSpan"];
export type Dependency = S["Dependency"];
export type RelatedRepo = S["RelatedRepo"];
export type RelationReason = S["RelationReason"];
export type DuplicateGroup = S["DuplicateGroup"];
export type LibraryOverview = S["LibraryOverview"];
export type BatchIndexResponse = S["BatchIndexResponse"];
export type RepoSummaryText = S["RepoSummaryText"];
export type IndexLevel = "metadata" | "docs" | "deep";

export type SearchMode = "keyword" | "semantic" | "hybrid";
export type SearchScope = "repos" | "docs" | "code";
export type SearchSort = "relevance" | "stars" | "pushed_at" | "starred_at" | "name";
export type SearchResponse = S["SearchResponse"];
export type SearchResult = S["SearchResult"];
export type Match = S["Match"];
export type ResultSignals = S["ResultSignals"];
export type ParsedQuery = S["ParsedQuery"];
export type Suggestion = S["Suggestion"];
export type SearchHistoryEntry = S["SearchHistoryEntry"];
export type SavedSearch = S["SavedSearch"];

export type SearchFeedback = S["SearchFeedback"];
export type FeedbackQuery = S["FeedbackQuery"];
export type FeedbackVerdict = "relevant" | "not_relevant" | "missing";
export type EvalStatus = S["EvalStatus"];
export type EvalRun = S["EvalRun"];
export type EvalConfigResult = S["EvalConfigResult"];
export type McpInfo = S["McpInfo"];
export type ToolInfo = S["ToolInfo"];
export type ToolCallResult = S["ToolCallResult"];

export type Evidence = S["Evidence"];
export type EvidenceDetail = S["EvidenceDetail"];
export type EvidenceClass = "observed" | "documented" | "inferred" | "unverified";
export type TraceStep = S["TraceStep"];
export type Report = S["Report"];
export type ReportSummary = S["ReportSummary"];
export type UsageInfo = S["UsageInfo"];
export type UnsupportedCitation = S["UnsupportedCitation"];
export type ProviderErrorInfo = S["ProviderErrorInfo"];
export type ProviderOption = S["ProviderOption"];
export type ResearchCapability =
  | "auto"
  | "discovery"
  | "comparison"
  | "composition"
  | "gap_analysis"
  | "scouting"
  | "relationships";

export type Comparison = S["Comparison"];
export type CompareRow = S["CompareRow"];
export type SharedItem = S["SharedItem"];

export type Collection = S["Collection"];
export type CollectionDetail = S["CollectionDetail"];
export type Tag = S["Tag"];
export type Category = S["Category"];

export type GitHubAuthStatus = S["GitHubAuthStatus"];
export type RateLimitInfo = S["RateLimitInfo"];
export type SyncStatus = S["SyncStatus"];
export type SyncRun = S["SyncRun"];
export type IndexProgress = S["IndexProgress"];
export type QueueSummary = S["QueueSummary"];
export type WorkerInfo = S["WorkerInfo"];
export type Job = S["Job"];
export type JobPage = S["JobPage"];
export type Schedule = S["Schedule"];

export type ProviderTemplate = S["ProviderTemplate"];
export type ProviderInfo = S["ProviderInfo"];
export type ProviderCapabilities = S["ProviderCapabilities"];
export type ProviderTestResult = S["ProviderTestResult"];
export type ProviderModels = S["ProviderModels"];
export type ModelInfo = S["ModelInfo"];
export type RoutingInfo = S["RoutingInfo"];
export type Route = S["Route"];
export type UsageSummary = S["UsageSummary"];
export type UsageRow = S["UsageRow"];
export type HardwareInfo = S["HardwareInfo"];
export type TaskType = "classify" | "summarize" | "expand_query" | "answer" | "reasoning" | "compare";

export type ChatGPTStatus = S["ChatGPTStatus"];
export type ChatGPTAccount = S["ChatGPTAccount"];
export type ChatGPTLoginAttempt = S["ChatGPTLoginAttempt"];

export type SystemStatus = S["SystemStatus"];
export type LlmStatus = S["LlmStatus"];
export type Diagnostics = S["Diagnostics"];
export type DiagnosticCheck = S["DiagnosticCheck"];
export type AppSettings = S["AppSettings"];
export type StorageInfo = S["StorageInfo"];
export type BackupInfo = S["BackupInfo"];

// ---- Requests (server applies defaults, so everything optional is optional here) ----

export type RepoFiltersInput = Partial<RepoFilters>;

export interface SearchRequestInput {
  query: string;
  mode?: SearchMode;
  scope?: SearchScope;
  filters?: RepoFiltersInput;
  sort?: SearchSort;
  limit?: number;
  offset?: number;
  rerank?: boolean | null;
  explain?: boolean;
  record?: boolean;
}

export type LibrarySort =
  | "starred_at"
  | "stars"
  | "pushed_at"
  | "name"
  | "updated_at"
  | "last_opened"
  | "size"
  | "priority";

export interface RepoListRequestInput {
  filters?: RepoFiltersInput;
  text?: string;
  sort?: LibrarySort;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

export interface RouteOverride {
  provider_id: string;
  model: string;
}

export interface AskRequestInput {
  question: string;
  repo_ids?: number[];
  filters?: RepoFiltersInput | null;
  route?: RouteOverride | null;
  confirm_cloud?: boolean;
  history?: { role: string; content: string }[];
}

export interface ResearchRequestInput {
  question: string;
  capability?: ResearchCapability;
  repo_ids?: number[];
  collection_id?: number | null;
  route?: RouteOverride | null;
  confirm_cloud?: boolean;
  limits?: { max_tool_calls?: number; max_seconds?: number; max_context_chars?: number } | null;
}

export interface CompareNarrativeInput {
  repo_ids: number[];
  focus?: string;
  route?: RouteOverride | null;
  confirm_cloud?: boolean;
}

export interface SummaryStreamInput {
  route?: RouteOverride | null;
  confirm_cloud?: boolean;
}

export interface BatchIndexInput {
  level: IndexLevel;
  repo_ids?: number[];
  collection_id?: number | null;
  category_slug?: string | null;
  filters?: RepoFiltersInput | null;
  force?: boolean;
}

export interface CollectionCreateInput {
  name: string;
  description?: string;
  kind?: "manual" | "smart";
  smart_query?: SearchRequestInput | null;
  repo_ids?: number[];
}

export interface ProviderCreateInput {
  kind: string;
  name: string;
  base_url?: string | null;
  options?: Record<string, unknown>;
  api_key?: string | null;
  enabled?: boolean;
}

export interface GitHubAuthUpdateInput {
  method: "gh_cli" | "token" | "public" | "none";
  token?: string | null;
  username?: string | null;
}

export type JobType =
  | "sync_full"
  | "sync_incremental"
  | "embed_pending"
  | "classify_repos"
  | "rebuild_search_index"
  | "rebuild_vector_index"
  | "cleanup"
  | "backup"
  | "refresh_metadata";

export interface ModelOptions {
  active: LlmStatus;
  options: ProviderOption[];
  cloud_allowed: boolean;
  confirm_before_cloud: boolean;
}

// ---- Server-sent event payloads (documented beside ProviderErrorInfo in schemas.py) ----

export interface StatusEvent {
  message: string;
  provider_id?: string | null;
  provider_kind?: string | null;
  provider_name?: string | null;
  model?: string | null;
  is_cloud?: boolean;
  report_id?: number | null;
  limits?: { max_tool_calls?: number; max_seconds?: number; max_context_chars?: number } | null;
}

export interface StreamErrorEvent {
  error: ProviderErrorInfo;
  report_id?: number | null;
  alternatives?: ProviderOption[];
}

export type StreamEvent =
  | { type: "status"; data: StatusEvent }
  | { type: "plan"; data: { steps: string[] } }
  | { type: "step"; data: TraceStep }
  // Research sends evidence incrementally: each event carries only new items.
  | { type: "evidence"; data: { evidence: Evidence[]; notices?: string[] } }
  | { type: "delta"; data: { text: string } }
  | { type: "done"; data: { report?: Report; summary?: RepoSummaryText; usage?: UsageInfo | null } }
  | { type: "error"; data: StreamErrorEvent };

/** Kind-specific payload of a research report (`Report.data`). */
export interface ResearchReportData {
  capability?: string;
  limits?: { max_tool_calls?: number; max_seconds?: number; max_context_chars?: number; tool_calls_used?: number; seconds_used?: number };
  limits_hit?: string[] | string | boolean | null;
  license_notes?: string[];
  aging_repositories?: unknown[];
}

export const EMPTY_FILTERS: RepoFilters = {
  languages: [],
  topics: [],
  categories: [],
  collections: [],
  tags: [],
  licenses: [],
  owners: [],
  min_stars: null,
  max_stars: null,
  min_size_kb: null,
  max_size_kb: null,
  archived: null,
  fork: null,
  favorite: null,
  has_note: null,
  star_state: "starred",
  maintenance: [],
  index_status: [],
  indexed_level: [],
  pushed_after: null,
  pushed_before: null,
  starred_after: null,
  starred_before: null,
  repo_ids: [],
};
