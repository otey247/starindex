/** TanStack Query hooks. Keys are centralised so mutations can invalidate precisely. */
import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type * as T from "./types";

export const keys = {
  status: ["system", "status"] as const,
  modelOptions: ["model-options"] as const,
  settings: ["settings"] as const,
  sync: ["sync", "status"] as const,
  repos: (req: unknown) => ["repos", "list", req] as const,
  facets: (req: unknown) => ["repos", "facets", req] as const,
  repo: (id: number) => ["repo", id] as const,
  repoPart: (id: number, part: string, extra?: unknown) => ["repo", id, part, extra ?? null] as const,
  search: (req: unknown) => ["search", req] as const,
  collections: ["collections"] as const,
  collection: (id: number) => ["collections", id] as const,
  tags: ["tags"] as const,
  categories: ["categories"] as const,
  reports: (params: unknown) => ["reports", params] as const,
  report: (id: number) => ["report", id] as const,
  providers: ["providers"] as const,
  routing: ["routing"] as const,
  chatgpt: ["chatgpt"] as const,
  jobs: (params: unknown) => ["jobs", params] as const,
  schedules: ["schedules"] as const,
  saved: ["saved-searches"] as const,
  history: ["search-history"] as const,
};

export function useSystemStatus() {
  return useQuery({ queryKey: keys.status, queryFn: api.systemStatus, staleTime: 10_000 });
}

export function useModelOptions() {
  return useQuery({ queryKey: keys.modelOptions, queryFn: api.modelOptions, staleTime: 15_000 });
}

export function useSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: api.settings, staleTime: 30_000 });
}

export function usePatchSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Record<string, unknown>) => api.patchSettings(patch),
    onSuccess: (data) => {
      qc.setQueryData(keys.settings, data);
      qc.invalidateQueries({ queryKey: keys.status });
      qc.invalidateQueries({ queryKey: keys.modelOptions });
      qc.invalidateQueries({ queryKey: keys.providers });
      qc.invalidateQueries({ queryKey: keys.chatgpt });
    },
  });
}

/** Sync status polls quickly while work is in flight and slowly otherwise. */
export function useSyncStatus(enabled = true) {
  return useQuery({
    queryKey: keys.sync,
    queryFn: api.syncStatus,
    enabled,
    refetchInterval: (query) => (isSyncActive(query.state.data) ? 2000 : 15_000),
  });
}

export function isSyncActive(s: T.SyncStatus | undefined): boolean {
  if (!s) return false;
  return Boolean(s.current_run) || s.queue.running > 0 || s.queue.queued > 0;
}

export function useRepoList(req: T.RepoListRequestInput, enabled = true) {
  return useQuery({
    queryKey: keys.repos(req),
    queryFn: ({ signal }) => api.listRepos(req, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useFacets(req: T.RepoListRequestInput, enabled = true) {
  return useQuery({
    queryKey: keys.facets(req),
    queryFn: () => api.repoFacets(req),
    placeholderData: keepPreviousData,
    staleTime: 20_000,
    enabled,
  });
}

export function useRepo(id: number) {
  return useQuery({ queryKey: keys.repo(id), queryFn: () => api.repo(id), enabled: Number.isFinite(id) });
}

export function useSearch(req: T.SearchRequestInput | null) {
  return useQuery({
    queryKey: keys.search(req),
    queryFn: ({ signal }) => api.search(req as T.SearchRequestInput, signal),
    enabled: req !== null,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function useCollections() {
  return useQuery({ queryKey: keys.collections, queryFn: api.collections, staleTime: 15_000 });
}

export function useTags() {
  return useQuery({ queryKey: keys.tags, queryFn: api.tags, staleTime: 15_000 });
}

export function useCategories() {
  return useQuery({ queryKey: keys.categories, queryFn: api.categories, staleTime: 30_000 });
}

export function useProviders() {
  return useQuery({ queryKey: keys.providers, queryFn: api.providers });
}

export function useChatGPTStatus(refetchMs?: number) {
  return useQuery({ queryKey: keys.chatgpt, queryFn: api.chatgptStatus, refetchInterval: refetchMs });
}

/** After anything that changes what a repository card shows. */
export function invalidateRepoData(qc: QueryClient, id?: number) {
  qc.invalidateQueries({ queryKey: ["repos"] });
  qc.invalidateQueries({ queryKey: ["search"] });
  qc.invalidateQueries({ queryKey: keys.collections });
  qc.invalidateQueries({ queryKey: keys.tags });
  qc.invalidateQueries({ queryKey: ["library"] });
  if (id !== undefined) qc.invalidateQueries({ queryKey: keys.repo(id) });
}

export function invalidateModels(qc: QueryClient) {
  qc.invalidateQueries({ queryKey: keys.providers });
  qc.invalidateQueries({ queryKey: keys.routing });
  qc.invalidateQueries({ queryKey: keys.modelOptions });
  qc.invalidateQueries({ queryKey: keys.status });
  qc.invalidateQueries({ queryKey: keys.chatgpt });
}
