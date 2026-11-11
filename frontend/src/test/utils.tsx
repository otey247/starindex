import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { Evidence, RepoSummary } from "@/api/types";

export function renderApp(ui: ReactElement, { route = "/", path = "*" }: { route?: string; path?: string } = {}): RenderResult & { client: QueryClient } {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 }, mutations: { retry: false } } });
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path={path} element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return Object.assign(result, { client });
}

export function makeRepo(overrides: Partial<RepoSummary> = {}): RepoSummary {
  return {
    id: 1,
    full_name: "qdrant/qdrant",
    owner: "qdrant",
    name: "qdrant",
    owner_avatar_url: null,
    description: "Vector database and vector search engine.",
    html_url: "https://github.com/qdrant/qdrant",
    homepage: null,
    topics: ["vector-database", "search-engine"],
    language: "Rust",
    stars: 26800,
    forks: 1800,
    license_spdx: "Apache-2.0",
    is_fork: false,
    is_archived: false,
    is_private: false,
    pushed_at: new Date(Date.now() - 86_400_000).toISOString(),
    updated_at: null,
    created_at: null,
    starred_at: new Date(Date.now() - 30 * 86_400_000).toISOString(),
    star_state: "starred",
    access_state: "ok",
    size_kb: 1000,
    index_level: "docs",
    indexed_level: "docs",
    index_status: "indexed",
    indexed_at: null,
    maintenance: "active",
    categories: [{ id: 3, slug: "rag", name: "RAG and enterprise search", source: "rule", confidence: 0.9 }],
    tags: [],
    collections: [],
    favorite: false,
    priority: 0,
    has_note: false,
    ...overrides,
  };
}

export function makeEvidence(overrides: Partial<Evidence> = {}): Evidence {
  return {
    ref: "E1",
    evidence_class: "documented",
    kind: "readme",
    repo_id: 1,
    repo_full_name: "qdrant/qdrant",
    title: "Features",
    text: "Qdrant runs in embedded mode\nwithout a server.",
    path: "README.md",
    start_line: 10,
    end_line: 11,
    commit_sha: "abcdef1234567890",
    indexed_at: new Date().toISOString(),
    html_url: "https://github.com/qdrant/qdrant/blob/abcdef1/README.md#L10-L11",
    chunk_id: 77,
    document_id: 5,
    injection_flag: false,
    ...overrides,
  };
}

/** Build a streaming Response whose body yields the given string chunks. */
export function streamResponse(chunks: string[], init: ResponseInit = { status: 200 }): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, { headers: { "Content-Type": "text/event-stream" }, ...init });
}
