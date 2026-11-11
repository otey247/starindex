import { describe, expect, it } from "vitest";
import { EMPTY_FILTERS } from "@/api/types";
import { activeChips, applyChipRemoval, countActiveFilters, filtersReducer, isEmptyFilters } from "./filters";

describe("filtersReducer", () => {
  it("toggles list values on and off without mutating the input", () => {
    const a = filtersReducer(EMPTY_FILTERS, { type: "toggle", key: "languages", value: "Rust" });
    expect(a.languages).toEqual(["Rust"]);
    expect(EMPTY_FILTERS.languages).toEqual([]);
    const b = filtersReducer(a, { type: "toggle", key: "languages", value: "Go" });
    expect(b.languages).toEqual(["Rust", "Go"]);
    expect(filtersReducer(b, { type: "toggle", key: "languages", value: "Rust" }).languages).toEqual(["Go"]);
  });

  it("toggles collections by id", () => {
    const a = filtersReducer(EMPTY_FILTERS, { type: "toggleCollection", id: 7 });
    expect(a.collections).toEqual([7]);
    expect(filtersReducer(a, { type: "toggleCollection", id: 7 }).collections).toEqual([]);
  });

  it("sanitises numeric bounds", () => {
    expect(filtersReducer(EMPTY_FILTERS, { type: "setNum", key: "min_stars", value: 1500.7 }).min_stars).toBe(1500);
    expect(filtersReducer(EMPTY_FILTERS, { type: "setNum", key: "min_stars", value: -5 }).min_stars).toBeNull();
    expect(filtersReducer(EMPTY_FILTERS, { type: "setNum", key: "max_stars", value: Number.NaN }).max_stars).toBeNull();
  });

  it("sets tri-state flags, dates and star state", () => {
    let f = filtersReducer(EMPTY_FILTERS, { type: "setTri", key: "archived", value: false });
    f = filtersReducer(f, { type: "setDate", key: "pushed_after", value: "2025-01-01" });
    f = filtersReducer(f, { type: "setStarState", value: "all" });
    expect(f).toMatchObject({ archived: false, pushed_after: "2025-01-01", star_state: "all" });
    expect(filtersReducer(f, { type: "setDate", key: "pushed_after", value: "" }).pushed_after).toBeNull();
  });

  it("clears everything back to the defaults", () => {
    const f = filtersReducer({ ...EMPTY_FILTERS, topics: ["mcp"], min_stars: 10, archived: true }, { type: "clear" });
    expect(f).toEqual(EMPTY_FILTERS);
    expect(isEmptyFilters(f)).toBe(true);
  });
});

describe("countActiveFilters / activeChips", () => {
  const filters = { ...EMPTY_FILTERS, languages: ["Rust", "Go"], categories: ["rag"], collections: [3], min_stars: 100, max_stars: 5000, archived: false, starred_after: "2026-01-01", star_state: "all" as const };

  it("counts facets, not individual values", () => {
    expect(countActiveFilters(EMPTY_FILTERS)).toBe(0);
    // languages, categories, collections, min_stars, max_stars, archived, starred_after, star_state
    expect(countActiveFilters(filters)).toBe(8);
  });

  it("describes each active filter with readable labels", () => {
    const labels = activeChips(filters, { collections: { 3: "Search stack" }, categories: { rag: "RAG and enterprise search" } }).map((c) => c.label);
    expect(labels).toEqual(["lang: Rust", "lang: Go", "category: RAG and enterprise search", "collection: Search stack", "Not archived", "stars: 100–5000", "starred ≥ 2026-01-01", "incl. unstarred"]);
  });

  it("removing a range chip clears both bounds", () => {
    const stars = activeChips(filters).find((c) => c.id === "stars")!;
    expect(applyChipRemoval(filters, stars)).toMatchObject({ min_stars: null, max_stars: null });
  });

  it("removing a value chip removes only that value", () => {
    const rust = activeChips(filters).find((c) => c.id === "languages:Rust")!;
    expect(applyChipRemoval(filters, rust).languages).toEqual(["Go"]);
  });
});
