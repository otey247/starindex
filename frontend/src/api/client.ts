/**
 * Typed HTTP client: one function per endpoint in docs/API.md.
 */
import { ApiError, toApiError } from "./errors";
import { postStream, type StreamOptions } from "./sse";
import type * as T from "./types";

async function request<R>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<R> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? { Accept: "application/json" } : { "Content-Type": "application/json", Accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "network", "StarIndex is not reachable. Is the local server running?");
  }
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as R;
  return (await response.json()) as R;
}

const get = <R>(path: string, signal?: AbortSignal) => request<R>("GET", path, undefined, signal);
const post = <R>(path: string, body: unknown = {}) => request<R>("POST", path, body);
const put = <R>(path: string, body: unknown) => request<R>("PUT", path, body);
const patch = <R>(path: string, body: unknown) => request<R>("PATCH", path, body);
const del = <R>(path: string) => request<R>("DELETE", path);

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const api = {
  // ---- system
  health: () => get<{ status: string; version: string }>("/health"),
  systemStatus: () => get<T.SystemStatus>("/system/status"),
  diagnostics: () => get<T.Diagnostics>("/system/diagnostics"),
  hardware: () => get<T.HardwareInfo>("/system/hardware"),
  settings: () => get<T.AppSettings>("/settings"),
  patchSettings: (patchBody: Record<string, unknown>) => patch<T.AppSettings>("/settings", { patch: patchBody }),
  storage: () => get<T.StorageInfo>("/storage"),
  backups: () => get<T.BackupInfo[]>("/storage/backups"),
  createBackup: () => post<T.Job>("/storage/backups"),
  restoreBackup: (name: string) => post<T.Ok>("/storage/restore", { name, confirm: true }),
  exportUrl: (format: "json" | "csv") => `/api/export?format=${format}`,

  // ---- github
  githubAuth: () => get<T.GitHubAuthStatus>("/github/auth"),
  setGithubAuth: (body: T.GitHubAuthUpdateInput) => put<T.GitHubAuthStatus>("/github/auth", body),
  deleteGithubToken: () => del<T.GitHubAuthStatus>("/github/auth/token"),

  // ---- library
  listRepos: (body: T.RepoListRequestInput, signal?: AbortSignal) =>
    request<T.RepoPage>("POST", "/repos/list", body, signal),
  repoFacets: (body: T.RepoListRequestInput) => post<T.Facets>("/repos/facets", body),
  repo: (id: number) => get<T.RepoDetail>(`/repos/${id}`),
  repoByName: (owner: string, name: string) =>
    get<T.RepoDetail>(`/repos/by-name/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`),
  repoReadme: (id: number) => get<T.DocumentContent>(`/repos/${id}/readme`),
  repoDocuments: (id: number) => get<T.DocumentInfo[]>(`/repos/${id}/documents`),
  repoDocument: (id: number, docId: number) => get<T.DocumentContent>(`/repos/${id}/documents/${docId}`),
  repoDependencies: (id: number) => get<T.Dependency[]>(`/repos/${id}/dependencies`),
  repoRelated: (id: number, limit = 12) => get<T.RelatedRepo[]>(`/repos/${id}/related${qs({ limit })}`),
  patchAnnotation: (id: number, body: { note?: string; favorite?: boolean; priority?: number }) =>
    patch<T.RepoDetail>(`/repos/${id}/annotation`, body),
  setRepoTags: (id: number, tags: string[]) => put<T.RepoDetail>(`/repos/${id}/tags`, { tags }),
  setRepoCategories: (id: number, categoryIds: number[]) =>
    put<T.RepoDetail>(`/repos/${id}/categories`, { category_ids: categoryIds }),
  repoOpened: (id: number) => post<T.Ok>(`/repos/${id}/opened`),
  indexRepo: (id: number, level: T.IndexLevel, force = false) => post<T.Job>(`/repos/${id}/index`, { level, force }),
  indexBatch: (body: T.BatchIndexInput) => post<T.BatchIndexResponse>("/repos/index-batch", body),
  repoSummary: (id: number, kind: "summary" | "usage_ideas") =>
    get<T.RepoSummaryText>(`/repos/${id}/summary${qs({ kind })}`),
  streamRepoSummary: (id: number, kind: "summary" | "usage_ideas", body: T.SummaryStreamInput, opts: StreamOptions) =>
    postStream(`/repos/${id}/summary${qs({ kind })}`, body, opts),
  libraryOverview: () => get<T.LibraryOverview>("/library/overview"),
  libraryDuplicates: () => get<T.DuplicateGroup[]>("/library/duplicates"),

  // ---- search
  search: (body: T.SearchRequestInput, signal?: AbortSignal) =>
    request<T.SearchResponse>("POST", "/search", body, signal),
  suggest: (q: string, signal?: AbortSignal) => get<T.Suggestion[]>(`/search/suggest${qs({ q })}`, signal),
  searchHistory: (limit = 50) => get<T.SearchHistoryEntry[]>(`/search/history${qs({ limit })}`),
  clearSearchHistory: () => del<T.Ok>("/search/history"),
  savedSearches: () => get<T.SavedSearch[]>("/saved-searches"),
  createSavedSearch: (name: string, req: T.SearchRequestInput) =>
    post<T.SavedSearch>("/saved-searches", { name, request: req }),
  deleteSavedSearch: (id: number) => del<T.Ok>(`/saved-searches/${id}`),

  // ---- ask / research / compare / reports
  streamAsk: (body: T.AskRequestInput, opts: StreamOptions) => postStream("/ask", body, opts),
  streamResearch: (body: T.ResearchRequestInput, opts: StreamOptions) => postStream("/research", body, opts),
  cancelResearch: (reportId: number) => post<T.Ok>(`/research/${reportId}/cancel`),
  compare: (repoIds: number[]) => post<T.Comparison>("/compare", { repo_ids: repoIds }),
  saveComparison: (repoIds: number[]) => post<T.Report>("/compare/save", { repo_ids: repoIds }),
  streamCompareNarrative: (body: T.CompareNarrativeInput, opts: StreamOptions) =>
    postStream("/compare/narrative", body, opts),
  reports: (params: { kind?: string; saved?: boolean; limit?: number } = {}) =>
    get<T.ReportSummary[]>(`/reports${qs(params)}`),
  report: (id: number) => get<T.Report>(`/reports/${id}`),
  patchReport: (id: number, body: { title?: string; saved?: boolean; collection_ids?: number[] }) =>
    patch<T.Report>(`/reports/${id}`, body),
  deleteReport: (id: number) => del<T.Ok>(`/reports/${id}`),
  reportExportUrl: (id: number) => `/api/reports/${id}/export`,
  evidenceChunk: (chunkId: number) => get<T.EvidenceDetail>(`/evidence/chunk/${chunkId}`),
  modelOptions: () => get<T.ModelOptions>("/model-options"),

  // ---- collections / tags / categories
  collections: () => get<T.Collection[]>("/collections"),
  createCollection: (body: T.CollectionCreateInput) => post<T.Collection>("/collections", body),
  collection: (id: number) => get<T.CollectionDetail>(`/collections/${id}`),
  patchCollection: (
    id: number,
    body: { name?: string; description?: string; smart_query?: T.SearchRequestInput | null },
  ) => patch<T.Collection>(`/collections/${id}`, body),
  deleteCollection: (id: number) => del<T.Ok>(`/collections/${id}`),
  addToCollection: (id: number, repoIds: number[]) =>
    post<T.Collection>(`/collections/${id}/repos`, { repo_ids: repoIds }),
  removeFromCollection: (id: number, repoIds: number[]) =>
    post<T.Collection>(`/collections/${id}/repos/remove`, { repo_ids: repoIds }),
  tags: () => get<T.Tag[]>("/tags"),
  categories: () => get<T.Category[]>("/categories"),
  createCategory: (body: { name: string; description?: string; keywords?: string[] }) =>
    post<T.Category>("/categories", body),
  patchCategory: (id: number, body: { name?: string; description?: string; keywords?: string[] }) =>
    patch<T.Category>(`/categories/${id}`, body),
  deleteCategory: (id: number) => del<T.Ok>(`/categories/${id}`),

  // ---- sync / jobs
  syncStatus: () => get<T.SyncStatus>("/sync/status"),
  startSync: (kind: "full" | "incremental") => post<T.Job>("/sync/start", { kind }),
  cancelSync: () => post<T.Ok>("/sync/cancel"),
  syncRuns: (limit = 20) => get<T.SyncRun[]>(`/sync/runs${qs({ limit })}`),
  jobs: (params: { status?: string; type?: string; limit?: number; offset?: number } = {}) =>
    get<T.JobPage>(`/jobs${qs(params)}`),
  enqueueJob: (type: T.JobType, payload: Record<string, unknown> = {}) => post<T.Job>("/jobs", { type, payload }),
  retryJob: (id: number) => post<T.Job>(`/jobs/${id}/retry`),
  cancelJob: (id: number) => post<T.Job>(`/jobs/${id}/cancel`),
  retryFailedJobs: () => post<T.Ok>("/jobs/retry-failed"),
  clearFinishedJobs: () => post<T.Ok>("/jobs/clear-finished"),
  schedules: () => get<T.Schedule[]>("/schedules"),
  patchSchedule: (name: string, body: { interval_minutes?: number; enabled?: boolean }) =>
    patch<T.Schedule>(`/schedules/${encodeURIComponent(name)}`, body),

  // ---- providers
  providerTemplates: () => get<T.ProviderTemplate[]>("/providers/templates"),
  providers: () => get<T.ProviderInfo[]>("/providers"),
  createProvider: (body: T.ProviderCreateInput) => post<T.ProviderInfo>("/providers", body),
  patchProvider: (
    id: string,
    body: { name?: string; base_url?: string | null; options?: Record<string, unknown>; enabled?: boolean },
  ) => patch<T.ProviderInfo>(`/providers/${encodeURIComponent(id)}`, body),
  deleteProvider: (id: string) => del<T.Ok>(`/providers/${encodeURIComponent(id)}`),
  setProviderSecret: (id: string, apiKey: string) =>
    put<T.ProviderInfo>(`/providers/${encodeURIComponent(id)}/secret`, { api_key: apiKey }),
  deleteProviderSecret: (id: string) => del<T.ProviderInfo>(`/providers/${encodeURIComponent(id)}/secret`),
  testProvider: (id: string, model?: string | null) =>
    post<T.ProviderTestResult>(`/providers/${encodeURIComponent(id)}/test`, { model: model ?? null }),
  providerModels: (id: string) => get<T.ProviderModels>(`/providers/${encodeURIComponent(id)}/models`),
  routing: () => get<T.RoutingInfo>("/routing"),
  setRouting: (body: { default?: T.Route | null; tasks?: Partial<Record<T.TaskType, T.Route | null>> }) =>
    put<T.RoutingInfo>("/routing", body),
  usage: (days = 30) => get<T.UsageSummary>(`/usage${qs({ days })}`),

  // ---- chatgpt plan
  chatgptStatus: () => get<T.ChatGPTStatus>("/chatgpt/status"),
  chatgptLogin: (body: { account_id?: string | null; enable_plan_usage?: boolean }) =>
    post<T.ChatGPTLoginAttempt>("/chatgpt/login", body),
  chatgptLoginStatus: (attemptId: string) =>
    get<T.ChatGPTLoginAttempt>(`/chatgpt/login/${encodeURIComponent(attemptId)}`),
  chatgptActivate: (id: string) => post<T.ChatGPTStatus>(`/chatgpt/accounts/${encodeURIComponent(id)}/activate`),
  chatgptSignOut: (id: string) => post<T.ChatGPTStatus>(`/chatgpt/accounts/${encodeURIComponent(id)}/sign-out`),
  chatgptDelete: (id: string) => del<T.ChatGPTStatus>(`/chatgpt/accounts/${encodeURIComponent(id)}`),
  chatgptWelcomeShown: (id: string) => post<T.Ok>(`/chatgpt/accounts/${encodeURIComponent(id)}/welcome-shown`),
};

export type Api = typeof api;
