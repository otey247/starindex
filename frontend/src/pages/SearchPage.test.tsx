import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EMPTY_FILTERS, type SearchResponse } from "@/api/types";
import { makeRepo, renderApp } from "@/test/utils";
import SearchPage from "./SearchPage";

const api = vi.hoisted(() => ({
  search: vi.fn(),
  suggest: vi.fn(),
  settings: vi.fn(),
  systemStatus: vi.fn(),
  repoFacets: vi.fn(),
  collections: vi.fn(),
  categories: vi.fn(),
  savedSearches: vi.fn(),
  searchHistory: vi.fn(),
  createSavedSearch: vi.fn(),
}));
vi.mock("@/api/client", () => ({ api }));

const facets = { languages: [{ value: "Rust", label: null, count: 2 }, { value: "Python", label: null, count: 1 }], topics: [], licenses: [], categories: [], tags: [], collections: [], maintenance: [], index_status: [], indexed_level: [], owners: [] };

function response(overrides: Partial<SearchResponse> = {}): SearchResponse {
  return {
    query: "vector databse",
    mode: "hybrid",
    requested_mode: "hybrid",
    scope: "repos",
    parsed: { text: "vector databse", phrases: [], excluded: [], operators: {}, corrections: [{ original: "databse", corrected: "database" }], expansions: [] },
    total: 2,
    took_ms: 37,
    results: [
      {
        repo: makeRepo(),
        score: 0.91,
        signals: { lexical_rank: 1, semantic_rank: 2, fused_score: 0.03, rerank_score: 0.91, prior: 1.02, name_match: false },
        matches: [{ chunk_id: 77, document_id: 5, repo_id: 1, path: "README.md", kind: "readme", heading: "Features", start_line: 7, end_line: 16, snippet: "A «vector» «database» for AI", commit_sha: "abcdef123", indexed_at: null, html_url: "https://github.com/qdrant/qdrant/blob/abc/README.md#L7-L16", score: 1, source: "both" }],
        field_highlights: { description: "«Vector» «database» and vector search engine." },
      },
      { repo: makeRepo({ id: 2, full_name: "lancedb/lancedb", owner: "lancedb", name: "lancedb", description: "Embedded retrieval engine." }), score: 0.7, signals: { lexical_rank: 2, semantic_rank: 1, fused_score: 0.03, rerank_score: 0.7, prior: 1, name_match: false }, matches: [], field_highlights: {} },
    ],
    facets,
    notices: [],
    reranked: true,
    embedding_model: "BAAI/bge-small-en-v1.5",
    ...overrides,
  };
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.settings.mockResolvedValue({ search: { default_mode: "hybrid" } });
  api.systemStatus.mockResolvedValue({ counts: { starred: 89, documents: 261, chunks: 1049 } });
  api.suggest.mockResolvedValue([]);
  api.repoFacets.mockResolvedValue(facets);
  api.collections.mockResolvedValue([]);
  api.categories.mockResolvedValue([]);
  api.savedSearches.mockResolvedValue([]);
  api.searchHistory.mockResolvedValue([]);
  api.search.mockResolvedValue(response());
});

describe("SearchPage", () => {
  it("shows the start panel, and searching needs no model", async () => {
    renderApp(<SearchPage />, { route: "/search", path: "/search" });
    expect(await screen.findByText("Try")).toBeInTheDocument();
    expect(screen.getByText("Operators")).toBeInTheDocument();
    expect(api.search).not.toHaveBeenCalled();
    await userEvent.type(screen.getByRole("combobox", { name: "Search query" }), "vector databse{Enter}");
    await waitFor(() => expect(api.search).toHaveBeenCalled());
    expect(api.search.mock.calls[0]![0]).toMatchObject({ query: "vector databse", mode: "hybrid", scope: "repos", sort: "relevance", limit: 25, offset: 0 });
  });

  it("renders results from the URL with summary, highlights and typo correction", async () => {
    renderApp(<SearchPage />, { route: "/search?q=vector+databse", path: "/search" });
    const summary = await screen.findByTestId("result-summary");
    expect(summary).toHaveTextContent("2 repositories");
    expect(summary).toHaveTextContent("37 ms");
    expect(summary).toHaveTextContent("hybrid");
    expect(summary).toHaveTextContent("reranked");
    expect(summary).toHaveTextContent("bge-small-en-v1.5");
    expect(summary).toHaveAttribute("title", "BAAI/bge-small-en-v1.5");
    const rows = screen.getAllByRole("article");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("README.md")).toBeInTheDocument();
    expect([...rows[0]!.querySelectorAll("mark.hit")].map((m) => m.textContent)).toEqual(["Vector", "database", "vector", "database"]);

    const notices = screen.getByTestId("search-notices");
    expect(notices).toHaveTextContent("Showing results for vector database.");
    api.search.mockClear();
    await userEvent.click(within(notices).getByRole("button", { name: "Search instead for databse" }));
    await waitFor(() => expect(api.search).toHaveBeenCalled());
    expect(api.search.mock.calls[0]![0].query).toBe('vector "databse"');
  });

  it("tells the user when the server degraded to keyword search", async () => {
    api.search.mockResolvedValue(response({ mode: "keyword", requested_mode: "hybrid", reranked: false, embedding_model: null, notices: ["Semantic index not ready; used keyword search."], parsed: { text: "x", phrases: [], excluded: [], operators: {}, corrections: [], expansions: [] } }));
    renderApp(<SearchPage />, { route: "/search?q=x", path: "/search" });
    expect(await screen.findByText("Semantic index not ready; used keyword search.")).toBeInTheDocument();
    expect(screen.getByTestId("result-summary")).toHaveTextContent("keyword");
  });

  it("sends filters from the URL and from the sidebar", async () => {
    renderApp(<SearchPage />, { route: "/search?q=vector&lang=Rust&stars=1000..", path: "/search" });
    await waitFor(() => expect(api.search).toHaveBeenCalled());
    expect(api.search.mock.calls[0]![0].filters).toEqual({ ...EMPTY_FILTERS, languages: ["Rust"], min_stars: 1000 });
    expect(await screen.findByText("lang: Rust")).toBeInTheDocument();
    api.search.mockClear();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Python" }));
    await waitFor(() => expect(api.search).toHaveBeenCalled());
    expect(api.search.mock.calls.at(-1)![0].filters.languages).toEqual(["Rust", "Python"]);
  });

  it("hands Ask and Research modes off to their workspaces", async () => {
    renderApp(
      <>
        <SearchPage />
      </>,
      { route: "/search", path: "/search" },
    );
    await userEvent.click(await screen.findByRole("radio", { name: "Ask" }));
    expect(screen.getByRole("button", { name: /^Ask/ })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Ask a question about your starred repositories")).toBeInTheDocument();
    expect(api.search).not.toHaveBeenCalled();
  });

  it("explains an empty result and offers ways out", async () => {
    api.search.mockResolvedValue(response({ total: 0, results: [], query: "zzz", mode: "keyword", requested_mode: "keyword", parsed: { text: "zzz", phrases: [], excluded: [], operators: {}, corrections: [], expansions: [] } }));
    renderApp(<SearchPage />, { route: "/search?q=zzz&mode=keyword&lang=Rust", path: "/search" });
    expect(await screen.findByText("No repositories match")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear 1 filter" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try hybrid search" })).toBeInTheDocument();
  });

  it("reports a failed search with a retry", async () => {
    api.search.mockRejectedValue(new Error("StarIndex is not reachable. Is the local server running?"));
    renderApp(<SearchPage />, { route: "/search?q=x", path: "/search" });
    expect(await screen.findByText("Search failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
