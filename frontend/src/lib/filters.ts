/** Pure helpers for the RepoFilters state shared by Library and Search. */
import { EMPTY_FILTERS, type RepoFilters } from "@/api/types";

export type ListKey =
  | "languages"
  | "topics"
  | "categories"
  | "tags"
  | "licenses"
  | "owners"
  | "maintenance"
  | "index_status"
  | "indexed_level";

export type TriKey = "archived" | "fork" | "favorite" | "has_note";
export type NumKey = "min_stars" | "max_stars" | "min_size_kb" | "max_size_kb";
export type DateKey = "pushed_after" | "pushed_before" | "starred_after" | "starred_before";

export type FilterAction =
  | { type: "toggle"; key: ListKey; value: string }
  | { type: "toggleCollection"; id: number }
  | { type: "setList"; key: ListKey; values: string[] }
  | { type: "setTri"; key: TriKey; value: boolean | null }
  | { type: "setNum"; key: NumKey; value: number | null }
  | { type: "setDate"; key: DateKey; value: string | null }
  | { type: "setStarState"; value: RepoFilters["star_state"] }
  | { type: "clearKey"; key: keyof RepoFilters }
  | { type: "replace"; filters: RepoFilters }
  | { type: "clear" };

function toggleIn<V>(list: V[], value: V): V[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export function filtersReducer(state: RepoFilters, action: FilterAction): RepoFilters {
  switch (action.type) {
    case "toggle":
      return { ...state, [action.key]: toggleIn(state[action.key], action.value) };
    case "toggleCollection":
      return { ...state, collections: toggleIn(state.collections, action.id) };
    case "setList":
      return { ...state, [action.key]: [...action.values] };
    case "setTri":
      return { ...state, [action.key]: action.value };
    case "setNum": {
      const v = action.value;
      return { ...state, [action.key]: v === null || Number.isNaN(v) || v < 0 ? null : Math.floor(v) };
    }
    case "setDate":
      return { ...state, [action.key]: action.value || null };
    case "setStarState":
      return { ...state, star_state: action.value };
    case "clearKey":
      return { ...state, [action.key]: EMPTY_FILTERS[action.key] };
    case "replace":
      return { ...EMPTY_FILTERS, ...action.filters };
    case "clear":
      return { ...EMPTY_FILTERS };
  }
}

/** Number of filter facets that differ from the defaults. */
export function countActiveFilters(f: RepoFilters): number {
  let n = 0;
  for (const key of Object.keys(EMPTY_FILTERS) as (keyof RepoFilters)[]) {
    const value = f[key];
    const base = EMPTY_FILTERS[key];
    if (Array.isArray(value)) {
      if (value.length > 0) n += 1;
    } else if (value !== base) {
      n += 1;
    }
  }
  return n;
}

export function isEmptyFilters(f: RepoFilters): boolean {
  return countActiveFilters(f) === 0;
}

export interface ActiveChip {
  id: string;
  label: string;
  action: FilterAction;
}

const TRI_LABEL: Record<TriKey, [string, string]> = {
  archived: ["Archived", "Not archived"],
  fork: ["Forks", "Not forks"],
  favorite: ["Favorites", "Not favorites"],
  has_note: ["Has note", "No note"],
};

const LIST_PREFIX: Record<ListKey, string> = {
  languages: "lang",
  topics: "topic",
  categories: "category",
  tags: "tag",
  licenses: "license",
  owners: "owner",
  maintenance: "status",
  index_status: "index",
  indexed_level: "level",
};

/** Flat list of removable chips describing the active filters. */
export function activeChips(
  f: RepoFilters,
  names: { collections?: Record<number, string>; categories?: Record<string, string> } = {},
): ActiveChip[] {
  const chips: ActiveChip[] = [];
  for (const key of Object.keys(LIST_PREFIX) as ListKey[]) {
    for (const value of f[key]) {
      const shown = key === "categories" ? (names.categories?.[value] ?? value) : value;
      chips.push({ id: `${key}:${value}`, label: `${LIST_PREFIX[key]}: ${shown}`, action: { type: "toggle", key, value } });
    }
  }
  for (const id of f.collections) {
    chips.push({
      id: `collection:${id}`,
      label: `collection: ${names.collections?.[id] ?? `#${id}`}`,
      action: { type: "toggleCollection", id },
    });
  }
  for (const key of Object.keys(TRI_LABEL) as TriKey[]) {
    const v = f[key];
    if (v !== null) chips.push({ id: key, label: TRI_LABEL[key][v ? 0 : 1], action: { type: "setTri", key, value: null } });
  }
  if (f.min_stars !== null || f.max_stars !== null) {
    chips.push({ id: "stars", label: `stars: ${rangeLabel(f.min_stars, f.max_stars)}`, action: { type: "clearKey", key: "min_stars" } });
  }
  if (f.min_size_kb !== null || f.max_size_kb !== null) {
    chips.push({ id: "size", label: `size: ${rangeLabel(f.min_size_kb, f.max_size_kb)} KB`, action: { type: "clearKey", key: "min_size_kb" } });
  }
  if (f.pushed_after) chips.push({ id: "pushed_after", label: `pushed ≥ ${f.pushed_after}`, action: { type: "setDate", key: "pushed_after", value: null } });
  if (f.pushed_before) chips.push({ id: "pushed_before", label: `pushed ≤ ${f.pushed_before}`, action: { type: "setDate", key: "pushed_before", value: null } });
  if (f.starred_after) chips.push({ id: "starred_after", label: `starred ≥ ${f.starred_after}`, action: { type: "setDate", key: "starred_after", value: null } });
  if (f.starred_before) chips.push({ id: "starred_before", label: `starred ≤ ${f.starred_before}`, action: { type: "setDate", key: "starred_before", value: null } });
  if (f.star_state !== "starred") {
    chips.push({ id: "star_state", label: f.star_state === "all" ? "incl. unstarred" : "unstarred only", action: { type: "setStarState", value: "starred" } });
  }
  return chips;
}

/** Removing a range chip must clear both bounds. */
export function applyChipRemoval(state: RepoFilters, chip: ActiveChip): RepoFilters {
  if (chip.id === "stars") return { ...state, min_stars: null, max_stars: null };
  if (chip.id === "size") return { ...state, min_size_kb: null, max_size_kb: null };
  return filtersReducer(state, chip.action);
}

function rangeLabel(min: number | null, max: number | null): string {
  if (min !== null && max !== null) return `${min}–${max}`;
  if (min !== null) return `≥ ${min}`;
  return `≤ ${max}`;
}
