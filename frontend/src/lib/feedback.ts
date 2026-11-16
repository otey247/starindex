/** Pure logic for relevance feedback and for applying evaluation suggestions. */
import type { FeedbackVerdict, SearchFeedback } from "@/api/types";

export type FeedbackAction = { type: "delete"; id: number } | { type: "set"; verdict: FeedbackVerdict };

/** Clicking the active verdict clears it; clicking another one replaces it. */
export function nextFeedbackAction(current: SearchFeedback | undefined, clicked: FeedbackVerdict): FeedbackAction {
  if (current && current.verdict === clicked) return { type: "delete", id: current.id };
  return { type: "set", verdict: clicked };
}

export function feedbackByRepo(list: SearchFeedback[] | undefined): Map<number, SearchFeedback> {
  const map = new Map<number, SearchFeedback>();
  for (const f of list ?? []) map.set(f.repo_id, f);
  return map;
}

/** Apply the outcome of a mutation to the cached list for one query. */
export function applyFeedback(list: SearchFeedback[], change: { removedId?: number; upserted?: SearchFeedback }): SearchFeedback[] {
  let next = list;
  if (change.removedId !== undefined) next = next.filter((f) => f.id !== change.removedId);
  if (change.upserted) next = [...next.filter((f) => f.repo_id !== change.upserted!.repo_id && f.id !== change.upserted!.id), change.upserted];
  return next;
}

export interface SettingChange {
  path: string;
  from: unknown;
  to: unknown;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Flatten a nested settings patch into the leaf values that would actually change. */
export function settingsDiff(current: unknown, patch: unknown, prefix = ""): SettingChange[] {
  if (!isPlainObject(patch)) return [];
  const out: SettingChange[] = [];
  for (const [key, to] of Object.entries(patch)) {
    const path = prefix ? `${prefix}.${key}` : key;
    const from = isPlainObject(current) ? current[key] : undefined;
    if (isPlainObject(to)) out.push(...settingsDiff(from, to, path));
    else if (JSON.stringify(from) !== JSON.stringify(to)) out.push({ path, from, to });
  }
  return out;
}

const SETTING_LABEL: Record<string, string> = {
  "search.default_mode": "Default search mode",
  "search.semantic_weight": "Semantic weight",
  "search.lexical_weight": "Lexical weight",
  "search.rrf_k": "Fusion constant (RRF k)",
  "rerank.enabled": "Reranking",
  "rerank.top_n": "Candidates to rerank",
};

export function settingLabel(path: string): string {
  return SETTING_LABEL[path] ?? path;
}

export function formatSettingValue(value: unknown): string {
  if (value === undefined || value === null) return "not set";
  if (typeof value === "boolean") return value ? "on" : "off";
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** Prefill tool arguments from a JSON schema: every required parameter with a typed placeholder. */
export function exampleArguments(schema: Record<string, unknown> | undefined, hints: Record<string, unknown> = {}): Record<string, unknown> {
  const props = (isPlainObject(schema?.properties) ? schema!.properties : {}) as Record<string, Record<string, unknown>>;
  const required = Array.isArray(schema?.required) ? (schema!.required as string[]) : [];
  const out: Record<string, unknown> = {};
  for (const name of required) {
    if (name in hints) {
      out[name] = hints[name];
      continue;
    }
    const spec = props[name] ?? {};
    const type = Array.isArray(spec.type) ? spec.type[0] : spec.type;
    if (Array.isArray(spec.enum) && spec.enum.length) out[name] = spec.enum[0];
    else if (spec.default !== undefined) out[name] = spec.default;
    else if (type === "integer" || type === "number") out[name] = 1;
    else if (type === "boolean") out[name] = false;
    else if (type === "array") out[name] = [];
    else if (type === "object") out[name] = {};
    else out[name] = "";
  }
  return out;
}
