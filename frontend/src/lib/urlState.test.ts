import { describe, expect, it } from "vitest";
import { EMPTY_FILTERS } from "@/api/types";
import { DEFAULT_SEARCH_STATE, filtersFromParams, filtersToParams, searchStateFromParams, searchStateToParams } from "./urlState";

describe("search URL state", () => {
  it("writes nothing for the default state", () => {
    expect(searchStateToParams(DEFAULT_SEARCH_STATE).toString()).toBe("");
  });

  it("round-trips a fully populated state", () => {
    const state = {
      q: 'agent "tool calling" lang:python',
      mode: "semantic" as const,
      scope: "code" as const,
      sort: "stars" as const,
      view: "table" as const,
      explain: true,
      page: 3,
      filters: {
        ...EMPTY_FILTERS,
        languages: ["Python", "C#"],
        topics: ["mcp"],
        categories: ["agents"],
        licenses: ["MIT", "none"],
        tags: ["a,b"],
        collections: [4, 9],
        min_stars: 1000,
        max_stars: null,
        min_size_kb: null,
        max_size_kb: 2048,
        archived: false,
        favorite: true,
        pushed_after: "2025-01-01",
        starred_before: "2026-06-30",
        star_state: "all" as const,
        maintenance: ["active"],
        index_status: ["failed"],
      },
    };
    const params = searchStateToParams(state);
    expect(searchStateFromParams(new URLSearchParams(params.toString()))).toEqual(state);
  });

  it("produces a compact, readable query string", () => {
    const params = filtersToParams({ ...EMPTY_FILTERS, languages: ["Python", "Rust"], min_stars: 500, archived: false });
    expect(params.toString()).toBe("lang=Python%2CRust&archived=0&stars=500..");
  });

  it("ignores invalid values instead of throwing", () => {
    const sp = new URLSearchParams("mode=bogus&scope=x&sort=y&view=z&page=-2&stars=abc..def&pushed_after=yesterday&state=weird&collection=1,x,-4&archived=maybe");
    const state = searchStateFromParams(sp);
    expect(state.mode).toBe("hybrid");
    expect(state.scope).toBe("repos");
    expect(state.sort).toBe("relevance");
    expect(state.view).toBe("list");
    expect(state.page).toBe(1);
    expect(state.filters).toEqual({ ...EMPTY_FILTERS, collections: [1] });
  });

  it("uses caller defaults when the URL has no mode", () => {
    expect(searchStateFromParams(new URLSearchParams("q=x"), { mode: "keyword" }).mode).toBe("keyword");
    expect(searchStateFromParams(new URLSearchParams("q=x&mode=semantic"), { mode: "keyword" }).mode).toBe("semantic");
  });

  it("parses open-ended ranges", () => {
    expect(filtersFromParams(new URLSearchParams("stars=..999")).max_stars).toBe(999);
    expect(filtersFromParams(new URLSearchParams("stars=10..")).min_stars).toBe(10);
  });
});
