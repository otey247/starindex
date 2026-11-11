/**
 * Search/Library state <-> URL query string. Compact, stable and human-editable:
 *   ?q=agent+orchestration&mode=hybrid&lang=python,typescript&stars=1000..&archived=0
 */
import { EMPTY_FILTERS, type RepoFilters, type SearchMode, type SearchScope, type SearchSort } from "@/api/types";

export type ResultView = "list" | "table" | "grid";

export interface SearchUrlState {
  q: string;
  mode: SearchMode;
  scope: SearchScope;
  sort: SearchSort;
  view: ResultView;
  explain: boolean;
  page: number;
  filters: RepoFilters;
}

export const DEFAULT_SEARCH_STATE: SearchUrlState = {
  q: "",
  mode: "hybrid",
  scope: "repos",
  sort: "relevance",
  view: "list",
  explain: false,
  page: 1,
  filters: EMPTY_FILTERS,
};

const LIST_PARAMS: [keyof RepoFilters, string][] = [
  ["languages", "lang"],
  ["topics", "topic"],
  ["categories", "category"],
  ["tags", "tag"],
  ["licenses", "license"],
  ["owners", "owner"],
  ["maintenance", "maint"],
  ["index_status", "index"],
  ["indexed_level", "level"],
];
const TRI_PARAMS: [keyof RepoFilters, string][] = [
  ["archived", "archived"],
  ["fork", "fork"],
  ["favorite", "fav"],
  ["has_note", "note"],
];
const MODES = new Set(["keyword", "semantic", "hybrid"]);
const SCOPES = new Set(["repos", "docs", "code"]);
const SORTS = new Set(["relevance", "stars", "pushed_at", "starred_at", "name"]);
const VIEWS = new Set(["list", "table", "grid"]);

const enc = (v: string) => v.replaceAll(",", "%2C");
const dec = (v: string) => v.replaceAll("%2C", ",");

function encodeRange(min: number | null, max: number | null): string | null {
  if (min === null && max === null) return null;
  return `${min ?? ""}..${max ?? ""}`;
}

function decodeRange(value: string | null): [number | null, number | null] {
  if (!value) return [null, null];
  const [a, b] = value.split("..");
  const num = (s: string | undefined) => {
    if (s === undefined || s === "") return null;
    const n = Number(s);
    return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
  };
  return [num(a), num(b)];
}

function isDate(value: string | null): value is string {
  return !!value && /^\d{4}-\d{2}-\d{2}$/.test(value);
}

export function filtersToParams(f: RepoFilters, sp: URLSearchParams = new URLSearchParams()): URLSearchParams {
  for (const [key, param] of LIST_PARAMS) {
    const list = f[key] as string[];
    if (list.length) sp.set(param, list.map(enc).join(","));
  }
  if (f.collections.length) sp.set("collection", f.collections.join(","));
  for (const [key, param] of TRI_PARAMS) {
    const v = f[key] as boolean | null;
    if (v !== null) sp.set(param, v ? "1" : "0");
  }
  const stars = encodeRange(f.min_stars, f.max_stars);
  if (stars) sp.set("stars", stars);
  const size = encodeRange(f.min_size_kb, f.max_size_kb);
  if (size) sp.set("size", size);
  if (f.pushed_after) sp.set("pushed_after", f.pushed_after);
  if (f.pushed_before) sp.set("pushed_before", f.pushed_before);
  if (f.starred_after) sp.set("starred_after", f.starred_after);
  if (f.starred_before) sp.set("starred_before", f.starred_before);
  if (f.star_state !== "starred") sp.set("state", f.star_state);
  if (f.repo_ids.length) sp.set("repos", f.repo_ids.join(","));
  return sp;
}

export function filtersFromParams(sp: URLSearchParams): RepoFilters {
  const f: RepoFilters = { ...EMPTY_FILTERS };
  for (const [key, param] of LIST_PARAMS) {
    const raw = sp.get(param);
    (f[key] as string[]) = raw ? raw.split(",").map(dec).filter(Boolean) : [];
  }
  const ids = (param: string) =>
    (sp.get(param) ?? "")
      .split(",")
      .map((s) => Number(s))
      .filter((n) => Number.isInteger(n) && n > 0);
  f.collections = ids("collection");
  f.repo_ids = ids("repos");
  for (const [key, param] of TRI_PARAMS) {
    const raw = sp.get(param);
    (f[key] as boolean | null) = raw === "1" ? true : raw === "0" ? false : null;
  }
  [f.min_stars, f.max_stars] = decodeRange(sp.get("stars"));
  [f.min_size_kb, f.max_size_kb] = decodeRange(sp.get("size"));
  f.pushed_after = isDate(sp.get("pushed_after")) ? sp.get("pushed_after") : null;
  f.pushed_before = isDate(sp.get("pushed_before")) ? sp.get("pushed_before") : null;
  f.starred_after = isDate(sp.get("starred_after")) ? sp.get("starred_after") : null;
  f.starred_before = isDate(sp.get("starred_before")) ? sp.get("starred_before") : null;
  const state = sp.get("state");
  f.star_state = state === "all" || state === "unstarred" ? state : "starred";
  return f;
}

export function searchStateToParams(s: SearchUrlState): URLSearchParams {
  const sp = new URLSearchParams();
  if (s.q) sp.set("q", s.q);
  if (s.mode !== DEFAULT_SEARCH_STATE.mode) sp.set("mode", s.mode);
  if (s.scope !== DEFAULT_SEARCH_STATE.scope) sp.set("scope", s.scope);
  if (s.sort !== DEFAULT_SEARCH_STATE.sort) sp.set("sort", s.sort);
  if (s.view !== DEFAULT_SEARCH_STATE.view) sp.set("view", s.view);
  if (s.explain) sp.set("explain", "1");
  if (s.page > 1) sp.set("page", String(s.page));
  return filtersToParams(s.filters, sp);
}

export function searchStateFromParams(sp: URLSearchParams, defaults: Partial<SearchUrlState> = {}): SearchUrlState {
  const base = { ...DEFAULT_SEARCH_STATE, ...defaults };
  const pick = <V extends string>(param: string, allowed: Set<string>, fallback: V): V => {
    const raw = sp.get(param);
    return raw && allowed.has(raw) ? (raw as V) : fallback;
  };
  const page = Number(sp.get("page"));
  return {
    q: sp.get("q") ?? "",
    mode: pick("mode", MODES, base.mode),
    scope: pick("scope", SCOPES, base.scope),
    sort: pick("sort", SORTS, base.sort),
    view: pick("view", VIEWS, base.view),
    explain: sp.get("explain") === "1",
    page: Number.isInteger(page) && page > 1 ? page : 1,
    filters: filtersFromParams(sp),
  };
}
