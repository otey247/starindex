/**
 * Dev-only mock of the StarIndex API, mounted as Vite middleware by `npm run dev:mock`.
 * It mirrors docs/API.md closely enough to exercise every screen, including streaming,
 * provider failures and the first-run flow. It never ships: vite.config.ts imports this
 * file only when STARINDEX_MOCK=1 and the command is `serve`.
 *
 * Scenario control for manual testing and screenshots:
 *   POST /api/__mock  {"first_run": true, "llm": "none|local|cloud|chatgpt", "fail": "usage_limit|unavailable|auth|null", "reset": true}
 */
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";
import type { Evidence, Facets, FacetValue, Job, Match, ProviderOption, RepoDetail, RepoFilters, Report, SearchResponse, SearchResult, TraceStep } from "../src/api/types.ts";
import { ago, buildState, evidenceFromChunk, iso, metadataEvidence, type MockChunk, type MockState } from "./db.ts";
import { CONCEPTS } from "./seed.ts";

let state: MockState = buildState();
if (process.env.MOCK_SCENARIO === "first_run") applyScenario({ first_run: true });

type Json = Record<string, any>;
interface Ctx {
  req: IncomingMessage;
  res: ServerResponse;
  params: string[];
  query: URLSearchParams;
  body: Json;
}
type Handler = (ctx: Ctx) => unknown | Promise<unknown>;

const routes: { method: string; pattern: RegExp; handler: Handler }[] = [];
const on = (method: string, path: string, handler: Handler) => {
  const pattern = new RegExp(`^${path.replace(/:[a-z_]+/g, "([^/]+)")}$`);
  routes.push({ method, pattern, handler });
};

class HttpError extends Error {
  constructor(
    public status: number,
    public kind: string,
    message: string,
  ) {
    super(message);
  }
}
const notFound = (what: string) => new HttpError(404, "not_found", `${what} was not found.`);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const nowIso = () => iso(Date.now());
const STREAMED = Symbol("streamed");

// ---------------------------------------------------------------------------------- helpers

function applyScenario(body: Json) {
  if (body.reset) state = buildState();
  if (body.first_run) {
    const fresh = buildState();
    state = { ...fresh, firstRun: true, repos: [], docs: [], chunks: [], deps: new Map(), collections: [], reports: [], jobs: [], runs: [], history: [], saved: [], _seedRepos: fresh.repos, _seedDocs: fresh.docs, _seedChunks: fresh.chunks, _seedDeps: fresh.deps } as MockState & Json;
    state.settings.onboarding_completed = false;
    state.github = { method: "none", token_stored: false, username: null };
    state.categories.forEach((c) => (c.repo_count = 0));
  }
  if (body.llm) state.llm = body.llm;
  if ("fail" in body) state.fail = body.fail ?? null;
  if (body.chatgpt === "signed_in") {
    state.chatgpt.accounts = [
      { id: "acc-1", label: "Personal", email: "user@example.com", client_id_suffix: "a9F2", plan_usage_enabled: true, is_active: true, signed_in: true, scopes: ["chatgpt.tokens.use.direct", "email", "offline_access", "openid", "profile", "resource.invoke"], saved_at: ago(2), expires_at: iso(Date.now() + 40 * 60_000), last_error: null, welcome_pending: Boolean(body.welcome) },
    ];
  }
  if (body.chatgpt === "no_consent") {
    state.chatgpt.accounts = [
      { id: "acc-1", label: "Personal", email: "user@example.com", client_id_suffix: "a9F2", plan_usage_enabled: false, is_active: false, signed_in: true, scopes: ["email", "openid", "profile"], saved_at: ago(0.01), expires_at: iso(Date.now() + 55 * 60_000), last_error: null, welcome_pending: false },
    ];
  }
  if (body.cloud_allowed !== undefined) state.settings.privacy.allow_cloud_models = Boolean(body.cloud_allowed);
  if (body.running_sync) startSync("full");
}

function summary(repo: RepoDetail) {
  return repo; // RepoDetail is a superset of RepoSummary
}

function matchFilters(repo: RepoDetail, f: Partial<RepoFilters>): boolean {
  const has = (list: unknown): list is string[] => Array.isArray(list) && list.length > 0;
  const starState = f.star_state ?? "starred";
  if (starState !== "all" && repo.star_state !== starState) return false;
  if (has(f.languages) && !f.languages.some((l) => l.toLowerCase() === (repo.language ?? "").toLowerCase())) return false;
  if (has(f.topics) && !f.topics.every((t) => repo.topics.includes(t))) return false;
  if (has(f.categories) && !f.categories.some((c) => repo.categories.some((rc) => rc.slug === c))) return false;
  if (has(f.tags) && !f.tags.some((t) => repo.tags.includes(t))) return false;
  if (has(f.licenses) && !f.licenses.some((l) => (l === "none" ? !repo.license_spdx : (repo.license_spdx ?? "").toLowerCase() === l.toLowerCase()))) return false;
  if (has(f.owners) && !f.owners.some((o) => o.toLowerCase() === repo.owner.toLowerCase())) return false;
  if (Array.isArray(f.collections) && f.collections.length && !f.collections.some((id) => repo.collections.some((c) => c.id === id))) return false;
  if (Array.isArray(f.repo_ids) && f.repo_ids.length && !f.repo_ids.includes(repo.id)) return false;
  if (has(f.maintenance) && !f.maintenance.includes(repo.maintenance)) return false;
  if (has(f.index_status) && !f.index_status.includes(repo.index_status)) return false;
  if (has(f.indexed_level) && !f.indexed_level.includes(repo.indexed_level ?? "")) return false;
  if (f.min_stars != null && repo.stars < f.min_stars) return false;
  if (f.max_stars != null && repo.stars > f.max_stars) return false;
  if (f.min_size_kb != null && repo.size_kb < f.min_size_kb) return false;
  if (f.max_size_kb != null && repo.size_kb > f.max_size_kb) return false;
  if (f.archived != null && repo.is_archived !== f.archived) return false;
  if (f.fork != null && repo.is_fork !== f.fork) return false;
  if (f.favorite != null && repo.favorite !== f.favorite) return false;
  if (f.has_note != null && repo.has_note !== f.has_note) return false;
  const day = (s: string | null | undefined) => (s ?? "").slice(0, 10);
  if (f.pushed_after && day(repo.pushed_at) < f.pushed_after) return false;
  if (f.pushed_before && day(repo.pushed_at) > f.pushed_before) return false;
  if (f.starred_after && day(repo.starred_at) < f.starred_after) return false;
  if (f.starred_before && day(repo.starred_at) > f.starred_before) return false;
  return true;
}

function facetsFor(repos: RepoDetail[]): Facets {
  const count = (values: (string | null | undefined)[][], labels?: Map<string, string>): FacetValue[] => {
    const m = new Map<string, number>();
    for (const list of values) for (const v of list) if (v) m.set(v, (m.get(v) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([value, n]) => ({ value, label: labels?.get(value) ?? null, count: n }));
  };
  const catLabels = new Map(state.categories.map((c) => [c.slug, c.name]));
  const colLabels = new Map(state.collections.map((c) => [String(c.id), c.name]));
  return {
    languages: count(repos.map((r) => [r.language])),
    topics: count(repos.map((r) => r.topics)).slice(0, 60),
    licenses: count(repos.map((r) => [r.license_spdx ?? "none"])),
    categories: count(repos.map((r) => r.categories.map((c) => c.slug)), catLabels),
    tags: count(repos.map((r) => r.tags)),
    collections: count(repos.map((r) => r.collections.map((c) => String(c.id))), colLabels),
    maintenance: count(repos.map((r) => [r.maintenance])),
    index_status: count(repos.map((r) => [r.index_status])),
    indexed_level: count(repos.map((r) => [r.indexed_level])),
    owners: count(repos.map((r) => [r.owner])).slice(0, 30),
  };
}

const STOP = new Set("a an and the of to for with without in on my i that which what how could can is are be from all show me find have starred repositories repos repo projects project related best open source using only support built sensible defaults library libraries local locally running run runs code python react open-source alternatives".split(" "));

function levenshtein(a: string, b: string): number {
  if (Math.abs(a.length - b.length) > 2) return 3;
  const dp = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array<number>(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) dp[0]![j] = j;
  for (let i = 1; i <= a.length; i++)
    for (let j = 1; j <= b.length; j++) dp[i]![j] = Math.min(dp[i - 1]![j]! + 1, dp[i]![j - 1]! + 1, dp[i - 1]![j - 1]! + (a[i - 1] === b[j - 1] ? 0 : 1));
  return dp[a.length]![b.length]!;
}

let vocabCache: { key: number; words: Map<string, number> } | null = null;
function vocab(): Map<string, number> {
  if (vocabCache && vocabCache.key === state.repos.length) return vocabCache.words;
  const words = new Map<string, number>();
  for (const r of state.repos) {
    const text = `${r.name} ${r.owner} ${r.description ?? ""} ${r.topics.join(" ")}`.toLowerCase();
    for (const w of new Set(text.split(/[^a-z0-9+#.]+/).filter((x) => x.length > 2))) words.set(w, (words.get(w) ?? 0) + 1);
  }
  vocabCache = { key: state.repos.length, words };
  return words;
}

function mark(text: string, terms: string[]): string {
  if (!terms.length) return text;
  const re = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.replace(re, "«$1»");
}

function snippetFor(content: string, terms: string[]): string {
  const flat = content.replace(/\s+/g, " ");
  const lower = flat.toLowerCase();
  let at = -1;
  for (const t of terms) {
    at = lower.indexOf(t);
    if (at !== -1) break;
  }
  const start = Math.max(0, (at === -1 ? 0 : at) - 70);
  const piece = flat.slice(start, start + 230);
  return `${start > 0 ? "…" : ""}${mark(piece, terms)}${start + 230 < flat.length ? "…" : ""}`;
}

function runSearch(body: Json): SearchResponse {
  const t0 = Date.now();
  const rawQuery: string = String(body.query ?? "");
  const requested = (body.mode ?? "hybrid") as SearchResponse["mode"];
  const scope = (body.scope ?? "repos") as SearchResponse["scope"];
  const filters: Partial<RepoFilters> = { ...(body.filters ?? {}) };
  const operators: Record<string, string[]> = {};
  const phrases: string[] = [];
  const excluded: string[] = [];
  const terms: string[] = [];
  const notices: string[] = [];
  const addOp = (k: string, v: string) => (operators[k] = [...(operators[k] ?? []), v]);

  for (const m of rawQuery.matchAll(/(-?[a-zA-Z_]+:)?"[^"]*"?|\S+/g)) {
    const tok = m[0];
    const op = /^(-?)([a-z_]+):(.*)$/i.exec(tok);
    if (op && ["lang", "language", "topic", "license", "owner", "stars", "is", "tag", "category", "in", "pushed", "starred", "collection"].includes(op[2]!.toLowerCase())) {
      const key = op[2]!.toLowerCase().replace("language", "lang");
      const value = op[3]!.replace(/^"|"$/g, "");
      addOp(key, value);
      if (key === "lang") filters.languages = [...(filters.languages ?? []), value];
      else if (key === "topic") filters.topics = [...(filters.topics ?? []), value.toLowerCase()];
      else if (key === "license") filters.licenses = [...(filters.licenses ?? []), value];
      else if (key === "owner") filters.owners = [...(filters.owners ?? []), value];
      else if (key === "tag") filters.tags = [...(filters.tags ?? []), value];
      else if (key === "category") filters.categories = [...(filters.categories ?? []), value];
      else if (key === "is") {
        if (value === "archived") filters.archived = true;
        if (value === "fork") filters.fork = true;
        if (value === "favorite") filters.favorite = true;
        if (value === "unstarred") filters.star_state = "unstarred";
      } else if (key === "stars") {
        const range = /^(\d+)\.\.(\d+)$/.exec(value);
        if (range) {
          filters.min_stars = Number(range[1]);
          filters.max_stars = Number(range[2]);
        } else if (value.startsWith(">")) filters.min_stars = Number(value.replace(/[>=]/g, "")) + (value.startsWith(">=") ? 0 : 1);
        else if (value.startsWith("<")) filters.max_stars = Number(value.replace(/[<=]/g, "")) - (value.startsWith("<=") ? 0 : 1);
      } else if (key === "pushed" && value.startsWith(">")) filters.pushed_after = value.replace(/[>=]/g, "");
      continue;
    }
    if (tok === "OR") continue;
    if (tok.startsWith('"')) {
      phrases.push(tok.replace(/"/g, "").toLowerCase());
      continue;
    }
    if (tok.startsWith("-") && tok.length > 1) {
      excluded.push(tok.slice(1).toLowerCase());
      continue;
    }
    const w = tok.toLowerCase().replace(/[^a-z0-9+#.\-]/g, "");
    if (w && !STOP.has(w)) terms.push(w);
  }

  // typo tolerance against the library's own vocabulary
  const corrections: { original: string; corrected: string }[] = [];
  const words = vocab();
  const effective = terms.map((t) => {
    if (!state.settings.search.typo_tolerance || t.length < 5) return t;
    if ([...words.keys()].some((w) => w === t || w.startsWith(t))) return t;
    let best: string | null = null;
    let bestScore = 99;
    for (const [w, df] of words) {
      const d = levenshtein(t, w);
      if (d <= 2) {
        const score = d * 10 - Math.min(df, 9);
        if (score < bestScore) {
          best = w;
          bestScore = score;
        }
      }
    }
    if (best) corrections.push({ original: t, corrected: best });
    return best ?? t;
  });

  let mode = requested;
  if (state.settings.embedding.backend === "none" && mode !== "keyword") {
    mode = "keyword";
    notices.push("The semantic index is not available (no embedding backend). Results are from keyword search.");
  }
  const conceptTopics: string[] = [];
  const conceptCats: string[] = [];
  if (mode !== "keyword") {
    for (const [pattern, cats, topics] of CONCEPTS) {
      if (pattern.test(rawQuery)) {
        conceptCats.push(...cats);
        conceptTopics.push(...topics);
      }
    }
  }

  const pool = state.repos.filter((r) => matchFilters(r, filters));
  const hasText = effective.length > 0 || phrases.length > 0;
  const chunksByRepo = new Map<number, MockChunk[]>();
  for (const c of state.chunks) chunksByRepo.set(c.repo_id, [...(chunksByRepo.get(c.repo_id) ?? []), c]);

  const scored: { repo: RepoDetail; lex: number; sem: number; matches: Match[] }[] = [];
  for (const repo of pool) {
    const name = repo.full_name.toLowerCase();
    const desc = (repo.description ?? "").toLowerCase();
    const chunks = chunksByRepo.get(repo.id) ?? [];
    const allText = `${name} ${desc} ${repo.topics.join(" ")} ${chunks.map((c) => c.content).join(" ").toLowerCase()}`;
    if (excluded.some((e) => allText.includes(e))) continue;
    if (phrases.some((p) => !allText.includes(p))) continue;
    let lex = 0;
    for (const t of [...effective, ...phrases]) {
      if (name.includes(t)) lex += repo.name.toLowerCase() === t ? 12 : 6;
      if (repo.topics.some((x) => x === t || x.includes(t))) lex += 4;
      if (desc.includes(t)) lex += 3;
      lex += Math.min(3, chunks.filter((c) => c.content.toLowerCase().includes(t)).length) * 0.8;
    }
    let sem = 0;
    if (mode !== "keyword") {
      sem += repo.categories.filter((c) => conceptCats.includes(c.slug)).length * 3;
      sem += repo.topics.filter((t) => conceptTopics.includes(t)).length * 2.2;
      sem += lex * 0.35;
    }
    if (hasText && lex === 0 && sem === 0) continue;
    if (mode === "keyword" && hasText && lex === 0) continue;
    if (mode === "semantic" && hasText && sem === 0) continue;
    const hl = [...effective, ...phrases];
    const wanted = scope === "code" ? chunks.filter((c) => c.kind === "code") : scope === "docs" ? chunks.filter((c) => c.kind !== "code") : chunks;
    const ranked = wanted
      .map((c) => ({ c, s: hl.reduce((n, t) => n + (c.content.toLowerCase().includes(t) ? 1 : 0), 0) + (conceptTopics.some((t) => c.content.toLowerCase().includes(t.replace(/-/g, " "))) ? 0.5 : 0) }))
      .filter((x) => x.s > 0 || (scope !== "repos" && !hasText))
      .sort((a, b) => b.s - a.s)
      .slice(0, scope === "repos" ? 2 : 3);
    if (scope !== "repos" && ranked.length === 0) continue;
    const doc = (id: number) => state.docs.find((d) => d.id === id)!;
    const matches: Match[] = ranked.map(({ c, s }) => ({
      chunk_id: c.id,
      document_id: c.document_id,
      repo_id: repo.id,
      path: c.path,
      kind: c.kind,
      heading: c.heading,
      start_line: c.start_line,
      end_line: c.end_line,
      snippet: snippetFor(c.content, hl.length ? hl : conceptTopics.map((t) => t.replace(/-/g, " "))),
      commit_sha: doc(c.document_id).commit_sha,
      indexed_at: doc(c.document_id).indexed_at,
      html_url: `${doc(c.document_id).html_url}#L${c.start_line}-L${c.end_line}`,
      score: s,
      source: mode === "keyword" ? "lexical" : s >= 1 ? "both" : "semantic",
    }));
    scored.push({ repo, lex, sem, matches });
  }

  const lexRank = new Map([...scored].filter((x) => x.lex > 0).sort((a, b) => b.lex - a.lex).map((x, i) => [x.repo.id, i + 1]));
  const semRank = new Map([...scored].filter((x) => x.sem > 0).sort((a, b) => b.sem - a.sem).map((x, i) => [x.repo.id, i + 1]));
  const rerank = body.rerank ?? state.settings.rerank.enabled;
  const results: SearchResult[] = scored.map((x) => {
    const lr = lexRank.get(x.repo.id) ?? null;
    const sr = semRank.get(x.repo.id) ?? null;
    const fused = (lr && mode !== "semantic" ? 1 / (60 + lr) : 0) + (sr && mode !== "keyword" ? 1 / (60 + sr) : 0);
    const prior = 1 + Math.min(0.03, Math.log10(Math.max(10, x.repo.stars)) / 200) + (x.repo.maintenance === "active" ? 0.02 : 0);
    const rr = mode === "hybrid" && rerank ? Math.min(0.99, (x.lex + x.sem) / 22) : null;
    const score = hasText ? (rr !== null ? rr * prior : fused * prior) : x.repo.stars / 1e6;
    const hl = [...effective, ...phrases];
    return {
      repo: summary(x.repo),
      score,
      signals: { lexical_rank: lr, semantic_rank: sr, fused_score: fused, rerank_score: rr, prior, name_match: hl.some((t) => x.repo.name.toLowerCase().includes(t)) },
      matches: x.matches,
      field_highlights: hl.length ? { name: mark(x.repo.name, hl), description: mark(x.repo.description ?? "", hl) } : {},
    } as SearchResult;
  });
  const sort = body.sort ?? "relevance";
  results.sort((a, b) => {
    if (sort === "stars") return b.repo.stars - a.repo.stars;
    if (sort === "pushed_at") return (b.repo.pushed_at ?? "").localeCompare(a.repo.pushed_at ?? "");
    if (sort === "starred_at") return (b.repo.starred_at ?? "").localeCompare(a.repo.starred_at ?? "");
    if (sort === "name") return a.repo.full_name.localeCompare(b.repo.full_name);
    return b.score - a.score;
  });
  const limit = Number(body.limit ?? 25);
  const offset = Number(body.offset ?? 0);
  const response: SearchResponse = {
    query: rawQuery,
    mode,
    requested_mode: requested,
    scope,
    parsed: { text: terms.join(" "), phrases, excluded, operators, corrections, expansions: [] },
    total: results.length,
    took_ms: Math.max(3, Date.now() - t0 + (mode === "keyword" ? 6 : 31)),
    results: results.slice(offset, offset + limit),
    facets: facetsFor(results.map((r) => r.repo as RepoDetail)),
    notices,
    reranked: mode === "hybrid" && Boolean(rerank) && hasText,
    embedding_model: mode === "keyword" ? null : state.settings.embedding.model,
  };
  if (body.record !== false && state.settings.search.save_history && rawQuery.trim() && offset === 0) {
    state.history = [{ id: state.nextId++, query: rawQuery, mode: requested, scope, filters: (body.filters ?? {}) as RepoFilters, result_count: results.length, created_at: nowIso() }, ...state.history.filter((h) => h.query !== rawQuery)].slice(0, 50);
  }
  return response;
}

// ---------------------------------------------------------------------------------- model + streaming

interface Active {
  configured: boolean;
  provider_id: string | null;
  provider_kind: string | null;
  provider_name: string | null;
  model: string | null;
  is_cloud: boolean;
}

function activeModel(): Active {
  if (state.llm === "none") return { configured: false, provider_id: null, provider_kind: null, provider_name: null, model: null, is_cloud: false };
  if (state.llm === "cloud") return { configured: true, provider_id: "anthropic", provider_kind: "anthropic", provider_name: "Anthropic", model: "claude-sonnet-5-5", is_cloud: true };
  if (state.llm === "chatgpt") return { configured: true, provider_id: "chatgpt-plan", provider_kind: "chatgpt_plan", provider_name: "ChatGPT plan", model: "gpt-6.1-sol", is_cloud: true };
  const d = state.routing.default ?? { provider_id: "ollama", model: "qwen3:14b" };
  return { configured: true, provider_id: d.provider_id, provider_kind: "ollama", provider_name: "Ollama", model: d.model, is_cloud: false };
}

const DEFAULT_MODEL: Record<string, string> = { ollama: "qwen3:14b", lmstudio: "qwen2.5-coder-14b-instruct", anthropic: "claude-sonnet-5-5", "chatgpt-plan": "gpt-6.1-sol" };

function providerOptions(): ProviderOption[] {
  return state.providers
    .filter((p) => p.enabled && (p.capabilities.is_local || state.settings.privacy.allow_cloud_models))
    .filter((p) => p.kind !== "chatgpt_plan" || state.chatgpt.accounts.some((a) => a.plan_usage_enabled && a.signed_in))
    .map((p) => ({ provider_id: p.id, provider_kind: p.kind, name: p.name, is_local: p.capabilities.is_local, is_paid: p.capabilities.is_paid, default_model: DEFAULT_MODEL[p.id] ?? null }));
}

function resolveRoute(body: Json): Active {
  if (body.route?.provider_id) {
    const p = state.providers.find((x) => x.id === body.route.provider_id);
    if (p) return { configured: true, provider_id: p.id, provider_kind: p.kind, provider_name: p.name, model: body.route.model || DEFAULT_MODEL[p.id] || "default", is_cloud: !p.capabilities.is_local };
  }
  return activeModel();
}

function sse(res: ServerResponse) {
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive", "X-Accel-Buffering": "no" });
  let closed = false;
  res.on("close", () => (closed = true));
  return {
    send(event: string, data: unknown) {
      if (!closed) res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    },
    get closed() {
      return closed;
    },
    end() {
      if (!closed) res.end();
    },
  };
}

function errorInfo(kind: string, message: string, a: Active, extra: Json = {}) {
  return { kind, message, provider_id: a.provider_id, provider_kind: a.provider_kind, status: extra.status ?? null, code: extra.code ?? null, retryable: Boolean(extra.retryable), retry_after_s: null, action_url: extra.action_url ?? null, action_label: extra.action_label ?? null };
}

/** Shared pre-flight for every generation stream. Returns false when an error was sent. */
function preflight(stream: ReturnType<typeof sse>, a: Active, body: Json, reportId: number | null = null): boolean {
  const alternatives = providerOptions().filter((o) => o.provider_id !== a.provider_id);
  if (!a.configured) {
    stream.send("error", { error: errorInfo("not_configured", "No model is configured for this task. Add a local or cloud provider in Settings → Model providers.", a), report_id: reportId, alternatives: [] });
    return false;
  }
  if (a.is_cloud && !state.settings.privacy.allow_cloud_models) {
    stream.send("error", { error: errorInfo("cloud_disabled", `${a.provider_name} is a cloud provider and outbound model traffic is turned off in Privacy settings.`, a), report_id: reportId, alternatives: alternatives.filter((o) => o.is_local) });
    return false;
  }
  if (a.is_cloud && state.settings.privacy.confirm_before_cloud && !body.confirm_cloud) {
    stream.send("error", { error: errorInfo("confirmation_required", `Sending repository context to ${a.provider_name} needs your confirmation.`, a), report_id: reportId, alternatives: [] });
    return false;
  }
  if (state.fail === "usage_limit") {
    stream.send("error", {
      error: errorInfo("usage_limit", a.provider_kind === "chatgpt_plan" ? "Your ChatGPT plan usage limit was reached for this app." : `${a.provider_name} reports that the usage limit was reached.`, a, { status: 429, code: a.provider_kind === "chatgpt_plan" ? "subscription_sharing_usage_limit_exceeded" : "rate_limit_exceeded", action_url: a.provider_kind === "chatgpt_plan" ? "https://chatgpt.com/settings/usage" : null, action_label: "Manage usage" }),
      report_id: reportId,
      alternatives,
    });
    return false;
  }
  if (state.fail === "unavailable") {
    stream.send("error", { error: errorInfo("unavailable", `${a.provider_name} did not respond at ${a.is_cloud ? "its API" : "http://127.0.0.1:11434"}. Is the server running and the model loaded?`, a, { retryable: true, status: 503 }), report_id: reportId, alternatives });
    return false;
  }
  if (state.fail === "auth") {
    stream.send("error", { error: errorInfo("auth", `${a.provider_name} rejected the stored credentials.`, a, { status: 401 }), report_id: reportId, alternatives });
    return false;
  }
  return true;
}

async function streamText(stream: ReturnType<typeof sse>, text: string, perChunk = 14) {
  const parts = text.match(/\S+\s*|\s+/g) ?? [];
  for (let i = 0; i < parts.length; i += 3) {
    if (stream.closed) return;
    stream.send("delta", { text: parts.slice(i, i + 3).join("") });
    await sleep(perChunk);
  }
}

function gatherEvidence(question: string, repoIds: number[] = [], limit = 8): Evidence[] {
  const res = runSearch({ query: question, mode: "hybrid", scope: "repos", limit: 8, record: false, filters: repoIds.length ? { repo_ids: repoIds, star_state: "all" } : {} });
  const out: Evidence[] = [];
  for (const r of res.results) {
    if (out.length >= limit) break;
    const chunk = state.chunks.find((c) => c.id === r.matches[0]?.chunk_id) ?? state.chunks.find((c) => c.repo_id === r.repo.id);
    if (chunk) out.push(evidenceFromChunk(state, chunk, `E${out.length + 1}`));
    else out.push(metadataEvidence(r.repo as RepoDetail, `E${out.length + 1}`));
  }
  if (out.length === 0 && repoIds.length) {
    for (const c of state.chunks.filter((x) => repoIds.includes(x.repo_id)).slice(0, 4)) out.push(evidenceFromChunk(state, c, `E${out.length + 1}`));
  }
  // one metadata item so "observed" evidence is represented
  const first = out[0];
  if (first) out.push(metadataEvidence(state.repos.find((r) => r.id === first.repo_id)!, `E${out.length + 1}`));
  return out;
}

function answerFor(question: string, evidence: Evidence[]): string {
  if (evidence.length === 0) {
    return `I could not find indexed evidence for this in your starred repositories, so I cannot answer it reliably.\n\n**What is unknown:** whether any starred repository covers “${question}”. Try indexing more repositories at the documentation level, or rephrase the question.`;
  }
  const byRepo = new Map<string, Evidence[]>();
  for (const e of evidence) byRepo.set(e.repo_full_name, [...(byRepo.get(e.repo_full_name) ?? []), e]);
  const lines: string[] = [`Based on the indexed content of your starred repositories, ${byRepo.size} project${byRepo.size === 1 ? " is" : "s are"} relevant here.\n`];
  for (const [name, evs] of [...byRepo.entries()].slice(0, 6)) {
    const doc = evs.find((e) => e.evidence_class === "documented") ?? evs[0]!;
    const meta = evs.find((e) => e.kind === "metadata");
    const repo = state.repos.find((r) => r.full_name === name)!;
    lines.push(`- **${name}** — its documentation describes it as: ${(repo.description ?? "").replace(/\.$/, "").toLowerCase()} [${doc.ref}]${meta ? `. It is ${repo.maintenance === "active" ? "actively maintained" : repo.maintenance} and licensed ${repo.license_spdx ?? "without a declared license"} [${meta.ref}]` : ""}.`);
  }
  lines.push(
    "\n### What the evidence does and does not show\n",
    `- The feature descriptions above are **documented by the projects themselves** in their READMEs; they are claims, not verified behaviour.`,
    `- Star counts, languages, licenses and last-push dates are **observed** repository metadata.`,
    `- I did not find indexed source code confirming how these features are implemented. Deep-index the repositories you care about to check.`,
  );
  return lines.join("\n");
}

function saveReport(partial: Partial<Report> & Pick<Report, "kind" | "title" | "question">, a: Active): Report {
  const report: Report = {
    id: state.nextId++,
    status: "completed",
    saved: false,
    provider_id: a.provider_id,
    provider_kind: a.provider_kind,
    model: a.model,
    repo_ids: [],
    created_at: nowIso(),
    finished_at: nowIso(),
    answer_md: "",
    evidence: [],
    trace: [],
    usage: { input_tokens: 0, output_tokens: 0, estimated: !a.is_cloud, cost_usd: null, calls: 1 },
    data: {},
    warnings: [],
    unsupported: [],
    error: null,
    collections: [],
    repos: [],
    ...partial,
  };
  report.repo_ids = [...new Set(report.evidence.map((e) => e.repo_id).concat(report.repo_ids))];
  report.repos = report.repo_ids.map((id) => state.repos.find((r) => r.id === id)!).filter(Boolean);
  const inTok = Math.round(report.evidence.reduce((n, e) => n + e.text.length, 0) / 4) + 420;
  const outTok = Math.round(report.answer_md.length / 4);
  report.usage = { input_tokens: inTok * report.usage.calls, output_tokens: outTok, estimated: !a.is_cloud, cost_usd: a.provider_kind === "anthropic" ? Number(((inTok * report.usage.calls * 3 + outTok * 15) / 1e6).toFixed(4)) : null, calls: report.usage.calls };
  state.reports.unshift(report);
  return report;
}

// ---------------------------------------------------------------------------------- sync simulation

let syncTimer: ReturnType<typeof setInterval> | null = null;

function addJob(type: string, extra: Partial<Job> = {}): Job {
  const job: Job = { id: state.nextId++, type, status: "queued", priority: 100, attempts: 0, max_attempts: 4, progress_current: 0, progress_total: 0, progress_message: "", error: null, payload: {}, result: null, repo_full_name: null, created_at: nowIso(), started_at: null, finished_at: null, run_after: null, ...extra };
  state.jobs.unshift(job);
  return job;
}

function startSync(kind: "full" | "incremental"): Job {
  const job = addJob(kind === "full" ? "sync_full" : "sync_incremental", { status: "running", started_at: nowIso(), attempts: 1 });
  const seed = (state as MockState & Json)._seedRepos as RepoDetail[] | undefined;
  const fresh = state.firstRun && seed;
  const total = fresh ? seed.length : state.repos.length;
  state.currentRun = { id: state.nextId++, kind, status: "running", started_at: nowIso(), finished_at: null, pages_fetched: 0, repos_seen: 0, added: 0, updated: 0, renamed: 0, unstarred: 0, restarred: 0, not_modified: false, total_expected: fresh ? total : kind === "full" ? total : null, error: null };
  if (syncTimer) clearInterval(syncTimer);
  syncTimer = setInterval(() => {
    const run = state.currentRun;
    if (!run) return void (syncTimer && clearInterval(syncTimer));
    if (!fresh && kind === "incremental") {
      run.not_modified = true;
      run.pages_fetched = 1;
    } else {
      const step = Math.max(6, Math.ceil(total / 9));
      const next = Math.min(total, run.repos_seen + step);
      if (fresh) {
        for (const r of seed.slice(run.repos_seen, next)) {
          state.repos.push({ ...r, index_status: "queued", indexed_level: null, indexed_at: null, doc_count: 0, chunk_count: 0, embedded_chunk_count: 0 });
          for (const c of r.categories) state.categories.find((x) => x.slug === c.slug)!.repo_count += 1;
        }
        run.added = next;
      } else run.updated = Math.round(next * 0.4);
      run.repos_seen = next;
      run.pages_fetched = Math.ceil(next / 100);
      job.progress_current = next;
      job.progress_total = total;
      job.progress_message = `page ${run.pages_fetched}`;
    }
    if (run.repos_seen >= total || run.not_modified) {
      run.status = "succeeded";
      run.finished_at = nowIso();
      state.runs.unshift(run);
      state.currentRun = null;
      job.status = "succeeded";
      job.finished_at = nowIso();
      job.progress_message = run.not_modified ? "No changes on GitHub (304)" : `${total} repositories`;
      if (syncTimer) clearInterval(syncTimer);
      if (fresh) startIndexing();
    }
  }, 700);
  return job;
}

let indexTimer: ReturnType<typeof setInterval> | null = null;
function startIndexing() {
  const s = state as MockState & Json;
  if (indexTimer) clearInterval(indexTimer);
  indexTimer = setInterval(() => {
    const queued = state.repos.filter((r) => r.index_status === "queued" || r.index_status === "indexing");
    if (queued.length === 0) return void (indexTimer && clearInterval(indexTimer));
    for (const r of queued.slice(0, 4)) {
      const seed = (s._seedRepos as RepoDetail[] | undefined)?.find((x) => x.id === r.id);
      if (r.index_status === "queued") {
        r.index_status = "indexing";
        continue;
      }
      Object.assign(r, { index_status: "indexed", indexed_level: r.index_level, indexed_at: nowIso(), doc_count: seed?.doc_count ?? 3, chunk_count: seed?.chunk_count ?? 14, embedded_chunk_count: seed?.chunk_count ?? 14, indexed_commit_sha: seed?.indexed_commit_sha ?? null });
      if (s._seedDocs) {
        state.docs.push(...(s._seedDocs as MockState["docs"]).filter((d) => d.repo_id === r.id));
        state.chunks.push(...(s._seedChunks as MockChunk[]).filter((c) => c.repo_id === r.id));
        const deps = (s._seedDeps as MockState["deps"]).get(r.id);
        if (deps) state.deps.set(r.id, deps);
      }
    }
  }, 600);
}

function githubStatus() {
  const g = state.github;
  const configured = g.method !== "none";
  return {
    method: g.method,
    configured,
    login: g.method === "gh_cli" || g.method === "token" ? "otey247" : null,
    username: g.method === "public" ? g.username : null,
    gh_cli_available: true,
    gh_cli_authenticated: true,
    token_stored: g.token_stored,
    scopes: g.method === "token" ? ["read:user"] : [],
    ok: configured,
    detail: configured ? "" : "Choose how StarIndex should read your starred repositories.",
    rate_limit: configured ? { limit: g.method === "public" ? 60 : 5000, remaining: g.method === "public" ? 52 : 4871, reset_at: iso(Date.now() + 38 * 60_000), resource: "core" } : null,
  };
}

function indexProgress() {
  const starred = state.repos.filter((r) => r.star_state === "starred");
  const n = (s: string) => starred.filter((r) => r.index_status === s).length;
  const by_level: Record<string, number> = {};
  for (const r of starred) if (r.indexed_level) by_level[r.indexed_level] = (by_level[r.indexed_level] ?? 0) + 1;
  return {
    total: starred.length,
    indexed: n("indexed"),
    pending: n("pending"),
    queued: n("queued"),
    indexing: n("indexing"),
    failed: n("failed"),
    skipped: n("skipped"),
    by_level,
    documents: state.docs.length,
    chunks: state.chunks.length,
    chunks_embedded: state.repos.reduce((sum, r) => sum + r.embedded_chunk_count, 0),
    repos_embedded: n("indexed"),
  };
}

function syncStatus() {
  const idx = indexProgress();
  const running = state.jobs.filter((j) => j.status === "running").length + idx.indexing;
  const queued = state.jobs.filter((j) => j.status === "queued").length + idx.queued;
  const by_type: Record<string, Record<string, number>> = {};
  for (const j of state.jobs) {
    by_type[j.type] ??= { queued: 0, running: 0, failed: 0 };
    if (j.status in by_type[j.type]!) by_type[j.type]![j.status]! += 1;
  }
  if (idx.queued + idx.indexing > 0) by_type.index_repo = { queued: idx.queued, running: idx.indexing, failed: by_type.index_repo?.failed ?? 0 };
  const worker = { alive: true, worker_id: "embedded-1", last_seen: nowIso(), mode: "embedded" as const };
  return {
    github: githubStatus(),
    current_run: state.currentRun,
    last_full: state.runs.find((r) => r.kind === "full" && r.status === "succeeded") ?? null,
    last_incremental: state.runs.find((r) => r.kind === "incremental") ?? null,
    index: idx,
    queue: { queued, running, failed: state.jobs.filter((j) => j.status === "failed").length, succeeded_last_hour: 37, throughput_per_min: running > 0 ? 18.5 : 0, by_type, eta_seconds: queued + running > 0 ? Math.round(((queued + running) / 18.5) * 60) : null },
    worker,
    next_scheduled_sync: state.schedules.find((s) => s.name === "sync_incremental" && s.enabled)?.next_run_at ?? null,
    rate_limit: githubStatus().rate_limit,
  };
}

function collectionOut(c: MockState["collections"][number]) {
  let repoIds = c.repo_ids;
  if (c.kind === "smart" && c.smart_query) repoIds = runSearch({ ...c.smart_query, limit: 200, record: false }).results.map((r) => r.repo.id);
  const repos = repoIds.map((id) => state.repos.find((r) => r.id === id)!).filter(Boolean);
  const { repo_ids: _a, report_ids: _b, ...rest } = c;
  return { ...rest, repo_count: repos.length, report_count: c.report_ids.length, preview: repos.slice(0, 5), repos, reports: c.report_ids.map((id) => state.reports.find((r) => r.id === id)!).filter(Boolean) };
}

const getRepo = (id: string | number) => {
  const repo = state.repos.find((r) => r.id === Number(id));
  if (!repo) throw notFound("Repository");
  return repo;
};

// ---------------------------------------------------------------------------------- routes: system

on("POST", "/__mock", ({ body }) => {
  applyScenario(body);
  return { ok: true };
});
on("GET", "/health", () => ({ status: "ok", version: "0.1.0-mock" }));
on("GET", "/system/status", () => {
  const a = activeModel();
  const idx = indexProgress();
  return {
    version: "0.1.0",
    data_dir: "C:\\Users\\otey\\AppData\\Local\\StarIndex",
    first_run: state.firstRun && state.repos.length === 0 && !state.settings.onboarding_completed,
    onboarding_completed: state.settings.onboarding_completed,
    github: githubStatus(),
    counts: { repos: state.repos.length, starred: idx.total, unstarred: state.repos.length - idx.total, documents: state.docs.length, chunks: state.chunks.length, chunks_embedded: idx.chunks_embedded, collections: state.collections.length, reports: state.reports.length },
    embedding: { backend: state.settings.embedding.backend, model: state.settings.embedding.model, model_key: "fastembed:bge-small-en-v1.5:384", dim: 384, ready: state.settings.embedding.backend !== "none", detail: "" },
    vector_backend: "qdrant-embedded",
    vector_ok: true,
    llm: a,
    worker: { alive: true, worker_id: "embedded-1", last_seen: nowIso(), mode: "embedded" },
    secret_backend: "keyring:WinVaultKeyring",
    cloud_allowed: state.settings.privacy.allow_cloud_models,
    offline_mode: state.settings.privacy.offline_mode,
  };
});
on("GET", "/system/diagnostics", () => ({
  generated_at: nowIso(),
  checks: [
    { name: "Database", status: "ok", detail: "SQLite 3.45 in WAL mode, schema 0001_init.sql, integrity check passed.", hint: "" },
    { name: "Full-text index", status: "ok", detail: `FTS5 available. ${state.chunks.length.toLocaleString()} passages indexed.`, hint: "" },
    { name: "Vector index", status: "ok", detail: "Qdrant embedded mode, collection chunks__bge-small-en-v1.5 in sync with the database.", hint: "" },
    { name: "Embedding model", status: "ok", detail: "BAAI/bge-small-en-v1.5 loaded with CUDAExecutionProvider (384 dimensions).", hint: "" },
    { name: "Reranker", status: "ok", detail: "Xenova/ms-marco-MiniLM-L-6-v2 loaded.", hint: "" },
    { name: "GitHub", status: state.github.method === "none" ? "warn" : "ok", detail: state.github.method === "none" ? "No GitHub access configured." : "Authenticated through GitHub CLI as otey247. 4,871 of 5,000 requests left.", hint: "Connect GitHub in Settings → GitHub access." },
    { name: "Background worker", status: "ok", detail: "Embedded worker, 3 threads, heartbeat 2 seconds ago.", hint: "" },
    { name: "Secret storage", status: "ok", detail: "Windows Credential Manager.", hint: "" },
    { name: "Ollama", status: state.fail === "unavailable" ? "fail" : "ok", detail: state.fail === "unavailable" ? "No response from http://127.0.0.1:11434." : "Reachable at http://127.0.0.1:11434, 5 models.", hint: "Start Ollama, or disable this provider." },
    { name: "LM Studio", status: "info", detail: "Provider is disabled.", hint: "" },
    { name: "Network binding", status: "ok", detail: "Listening on 127.0.0.1:8765 only.", hint: "" },
    { name: "Disk space", status: "warn", detail: "41 GB free on C:. Deep indexing of large collections may need more.", hint: "Free up disk space or move the data directory with STARINDEX_DATA_DIR." },
  ],
}));
on("GET", "/system/hardware", () => ({
  os: "Windows 11 (10.0.26100)",
  cpu_count: 24,
  memory_total_mb: 65_536,
  gpus: [{ name: "NVIDIA GeForce RTX 4080 SUPER", memory_total_mb: 16_376, memory_free_mb: 13_920, driver: "566.36" }],
  cuda_available: true,
  onnx_providers: ["CUDAExecutionProvider", "CPUExecutionProvider"],
  recommendations: [
    "16 GB of VRAM comfortably runs a 14B-class model at 4-bit quantisation for answers and research.",
    "Use a small 3–4B model for classification and summaries; it is several times faster and good enough for those tasks.",
    "Embeddings and reranking run on CUDA through ONNX Runtime; CPU fallback is automatic if the GPU is busy.",
  ],
}));
on("GET", "/settings", () => state.settings);
on("PATCH", "/settings", ({ body }) => {
  const merge = (base: Json, patch: Json): Json => {
    const out: Json = { ...base };
    for (const [k, v] of Object.entries(patch)) out[k] = v && typeof v === "object" && !Array.isArray(v) && typeof out[k] === "object" && out[k] !== null ? merge(out[k], v as Json) : v;
    return out;
  };
  state.settings = merge(state.settings as Json, body.patch ?? {}) as MockState["settings"];
  if (state.settings.onboarding_completed) state.firstRun = false;
  for (const p of state.providers) p.blocked_by_privacy = !p.capabilities.is_local && !state.settings.privacy.allow_cloud_models;
  return state.settings;
});
on("GET", "/storage", () => ({ data_dir: "C:\\Users\\otey\\AppData\\Local\\StarIndex", database_bytes: 412_876_800, vector_bytes: 96_468_992, models_bytes: 228_589_568, backups_bytes: 96_423_936, free_bytes: 44_023_414_784, embeddings_rows: state.chunks.length }));
const backups = [
  { name: "starindex-20261109-031500.db", created_at: ago(2), size_bytes: 48_211_968, path: "backups/starindex-20261109-031500.db" },
  { name: "starindex-20261102-031500.db", created_at: ago(9), size_bytes: 46_989_312, path: "backups/starindex-20261102-031500.db" },
];
on("GET", "/storage/backups", () => backups);
on("POST", "/storage/backups", () => {
  const job = addJob("backup", { status: "running", started_at: nowIso(), attempts: 1, progress_message: "Copying database" });
  setTimeout(() => {
    job.status = "succeeded";
    job.finished_at = nowIso();
    backups.unshift({ name: `starindex-${nowIso().replace(/[-:TZ]/g, "").slice(0, 14)}.db`, created_at: nowIso(), size_bytes: 48_300_032, path: "backups/new.db" });
  }, 1200);
  return job;
});
on("POST", "/storage/restore", () => ({ ok: true, message: "Backup restored. Restart StarIndex to load it." }));
on("GET", "/export", ({ res, query }) => {
  const format = query.get("format") ?? "json";
  res.writeHead(200, { "Content-Type": format === "csv" ? "text/csv" : "application/json", "Content-Disposition": `attachment; filename="starindex-export.${format}"` });
  res.end(format === "csv" ? `full_name,stars,language,tags\n${state.repos.map((r) => `${r.full_name},${r.stars},${r.language},"${r.tags.join(";")}"`).join("\n")}` : JSON.stringify({ repos: state.repos }, null, 2));
  return STREAMED;
});

// ---------------------------------------------------------------------------------- routes: github

on("GET", "/github/auth", () => githubStatus());
on("PUT", "/github/auth", ({ body }) => {
  if (body.method === "token" && !body.token && !state.github.token_stored) throw new HttpError(422, "validation", "A token is required.");
  state.github = { method: body.method, token_stored: body.method === "token" ? true : state.github.token_stored, username: body.username ?? null };
  return githubStatus();
});
on("DELETE", "/github/auth/token", () => {
  state.github = { method: "none", token_stored: false, username: null };
  return githubStatus();
});

// ---------------------------------------------------------------------------------- routes: library

function listRepos(body: Json) {
  const text = String(body.text ?? "").toLowerCase();
  let items = state.repos.filter((r) => matchFilters(r, body.filters ?? {}));
  if (text) items = items.filter((r) => `${r.full_name} ${r.description ?? ""} ${r.topics.join(" ")}`.toLowerCase().includes(text));
  return items;
}
on("POST", "/repos/list", ({ body }) => {
  const items = listRepos(body);
  const sort = body.sort ?? "starred_at";
  const dir = (body.order ?? "desc") === "asc" ? 1 : -1;
  const key = (r: RepoDetail): string | number =>
    sort === "stars" ? r.stars : sort === "name" ? r.full_name.toLowerCase() : sort === "size" ? r.size_kb : sort === "priority" ? r.priority : sort === "last_opened" ? (r.last_opened_at ?? "") : ((r as Json)[sort] ?? "");
  items.sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0) * dir);
  const limit = Number(body.limit ?? 50);
  const offset = Number(body.offset ?? 0);
  return { items: items.slice(offset, offset + limit).map(summary), total: items.length, limit, offset };
});
on("POST", "/repos/facets", ({ body }) => facetsFor(listRepos(body)));
on("GET", "/repos/by-name/:owner/:name", ({ params }) => {
  const repo = state.repos.find((r) => r.full_name.toLowerCase() === `${params[0]}/${params[1]}`.toLowerCase());
  if (!repo) throw notFound("Repository");
  return repo;
});
on("POST", "/repos/index-batch", ({ body }) => {
  let ids: number[] = body.repo_ids ?? [];
  if (body.collection_id) ids = state.collections.find((c) => c.id === body.collection_id)?.repo_ids ?? [];
  let queued = 0;
  for (const id of ids) {
    const r = state.repos.find((x) => x.id === id);
    if (!r) continue;
    if (r.indexed_level === body.level && !body.force) continue;
    r.index_level = body.level;
    r.index_status = "queued";
    queued += 1;
  }
  if (queued) startIndexing();
  return { queued, skipped: ids.length - queued, job_ids: [] };
});
on("GET", "/repos/:id", ({ params }) => getRepo(params[0]!));
on("GET", "/repos/:id/readme", ({ params }) => {
  const doc = state.docs.find((d) => d.repo_id === Number(params[0]) && d.kind === "readme");
  if (!doc) throw notFound("README");
  return doc;
});
on("GET", "/repos/:id/documents", ({ params }) => state.docs.filter((d) => d.repo_id === Number(params[0])).map(({ content: _c, chunks: _k, ...info }) => info));
on("GET", "/repos/:id/documents/:doc", ({ params }) => {
  const doc = state.docs.find((d) => d.id === Number(params[1]) && d.repo_id === Number(params[0]));
  if (!doc) throw notFound("Document");
  return doc;
});
on("GET", "/repos/:id/dependencies", ({ params }) => state.deps.get(Number(params[0])) ?? []);
on("GET", "/repos/:id/related", ({ params, query }) => {
  const repo = getRepo(params[0]!);
  const mine = state.deps.get(repo.id) ?? [];
  const out = state.repos
    .filter((r) => r.id !== repo.id && r.star_state === "starred")
    .map((r) => {
      const reasons: Json[] = [];
      const sharedTopics = r.topics.filter((t) => repo.topics.includes(t));
      const sharedCats = r.categories.filter((c) => repo.categories.some((x) => x.slug === c.slug));
      const sharedDeps = (state.deps.get(r.id) ?? []).filter((d) => mine.some((m) => m.name === d.name && m.ecosystem === d.ecosystem));
      const sim = Math.min(0.93, 0.34 + sharedTopics.length * 0.13 + sharedCats.length * 0.12);
      if (sharedTopics.length + sharedCats.length > 0) reasons.push({ kind: "semantic", detail: `README and description embeddings are close (cosine ${sim.toFixed(2)})`, weight: sim });
      if (sharedTopics.length) reasons.push({ kind: "topics", detail: sharedTopics.join(", "), weight: 0.1 * sharedTopics.length });
      if (sharedDeps.length) reasons.push({ kind: "dependencies", detail: `both declare ${sharedDeps.slice(0, 4).map((d) => d.name).join(", ")}`, weight: 0.08 * sharedDeps.length });
      if (sharedCats.length) reasons.push({ kind: "category", detail: sharedCats.map((c) => c.name).join(", "), weight: 0.1 });
      if (r.owner === repo.owner) reasons.push({ kind: "owner", detail: `both from ${r.owner}`, weight: 0.15 });
      const score = reasons.reduce((n, x) => n + x.weight, 0);
      return { repo: summary(r), score: Math.min(0.99, score * 0.62), reasons };
    })
    .filter((x) => x.reasons.length >= 2)
    .sort((a, b) => b.score - a.score);
  return out.slice(0, Number(query.get("limit") ?? 12));
});
on("PATCH", "/repos/:id/annotation", ({ params, body }) => {
  const repo = getRepo(params[0]!);
  if (typeof body.note === "string") {
    repo.note = body.note;
    repo.has_note = body.note.trim().length > 0;
  }
  if (typeof body.favorite === "boolean") repo.favorite = body.favorite;
  if (typeof body.priority === "number") repo.priority = body.priority;
  return repo;
});
on("PUT", "/repos/:id/tags", ({ params, body }) => {
  const repo = getRepo(params[0]!);
  repo.tags = [...new Set((body.tags as string[]).map((t) => t.trim()).filter(Boolean))];
  return repo;
});
on("PUT", "/repos/:id/categories", ({ params, body }) => {
  const repo = getRepo(params[0]!);
  for (const c of repo.categories) state.categories.find((x) => x.id === c.id)!.repo_count -= 1;
  repo.categories = (body.category_ids as number[]).map((id) => {
    const c = state.categories.find((x) => x.id === id)!;
    c.repo_count += 1;
    const prev = repo.categories.find((x) => x.id === id);
    return prev ?? { id: c.id, slug: c.slug, name: c.name, source: "user", confidence: 1 };
  });
  return repo;
});
on("POST", "/repos/:id/opened", ({ params }) => {
  const repo = getRepo(params[0]!);
  repo.open_count += 1;
  repo.last_opened_at = nowIso();
  return { ok: true, message: "" };
});
on("POST", "/repos/:id/index", ({ params, body }) => {
  const repo = getRepo(params[0]!);
  repo.index_level = body.level ?? "docs";
  repo.index_status = "queued";
  repo.index_error = null;
  startIndexing();
  return addJob("index_repo", { repo_full_name: repo.full_name, payload: { repo_id: repo.id, level: repo.index_level } });
});
on("GET", "/repos/:id/summary", ({ params, query }) => {
  const repo = getRepo(params[0]!);
  const kind = query.get("kind") ?? "summary";
  const cached = state.summaries.get(`${repo.id}:${kind}`);
  return { repo_id: repo.id, kind, content: cached?.content ?? null, evidence: cached ? gatherEvidence(repo.name, [repo.id], 4) : [], provider: cached?.provider ?? null, model: cached?.model ?? null, created_at: cached?.created_at ?? null, stale: false };
});
on("POST", "/repos/:id/summary", async ({ params, query, body, res }) => {
  const repo = getRepo(params[0]!);
  const kind = query.get("kind") ?? "summary";
  const a = resolveRoute(body);
  const stream = sse(res);
  if (!preflight(stream, a, body)) return (stream.end(), STREAMED);
  stream.send("status", { message: "Reading indexed documentation", provider_id: a.provider_id, provider_kind: a.provider_kind, model: a.model, is_cloud: a.is_cloud });
  await sleep(350);
  const evidence = gatherEvidence(repo.name, [repo.id], 4);
  stream.send("evidence", { evidence });
  const ref = (i: number) => evidence[Math.min(i, evidence.length - 1)]?.ref ?? "E1";
  const text =
    kind === "summary"
      ? `**${repo.name}** is ${(repo.description ?? "a project").replace(/\.$/, "").replace(/^[A-Z]/, (c) => c.toLowerCase())} [${ref(0)}]. Its README lists ${repo.topics.slice(0, 3).join(", ")} among its focus areas and describes a small core with pluggable adapters [${ref(1)}].\n\nIt is written mainly in ${repo.language}, licensed ${repo.license_spdx ?? "without a declared license"}, and was last pushed ${repo.pushed_at?.slice(0, 10)} [${ref(evidence.length - 1)}].`
      : `Ideas grounded in what ${repo.name}'s documentation describes:\n\n1. **Use it as the ${repo.categories[0]?.name.toLowerCase() ?? "core"} layer of a local tool.** The quickstart shows a single-process setup with no hosted dependency [${ref(0)}].\n2. **Combine it with other things you starred.** Its ${repo.topics[0] ?? "main"} support lines up with repositories in the same category in your library [${ref(1)}].\n3. **Prototype before committing.** The getting-started guide documents default configuration that works locally [${ref(2)}].\n\n**Not verified:** performance and production readiness. The documentation makes claims; no source code for this repository has been deep-indexed.`;
  await streamText(stream, text);
  state.summaries.set(`${repo.id}:${kind}`, { content: text, created_at: nowIso(), provider: a.provider_id ?? "", model: a.model ?? "" });
  stream.send("done", { summary: { repo_id: repo.id, kind, content: text, evidence, provider: a.provider_id, model: a.model, created_at: nowIso(), stale: false } });
  stream.end();
  return STREAMED;
});
on("GET", "/library/overview", () => {
  const starred = state.repos.filter((r) => r.star_state === "starred");
  const byStarred = [...starred].sort((a, b) => (b.starred_at ?? "").localeCompare(a.starred_at ?? ""));
  return {
    total_starred: starred.length,
    total_unstarred_retained: state.repos.length - starred.length,
    recently_starred: byStarred.slice(0, 8).map(summary),
    recently_rediscovered: starred.filter((r) => r.last_opened_at).sort((a, b) => (a.starred_at ?? "").localeCompare(b.starred_at ?? "")).slice(0, 8).map(summary),
    favorites: starred.filter((r) => r.favorite).slice(0, 8).map(summary),
    needs_attention: state.repos.filter((r) => r.is_archived || r.access_state !== "ok" || r.maintenance === "stale").slice(0, 8).map(summary),
    category_counts: state.categories.map((c) => ({ value: c.slug, label: c.name, count: c.repo_count })),
  };
});
on("GET", "/library/duplicates", () => {
  const by = (n: string) => state.repos.find((r) => r.full_name === n);
  const groups = [
    { reason: "near_duplicate", detail: "Both are Python animation engines for mathematical videos; the community edition is a maintained fork of the original design.", repos: [by("3b1b/manim"), by("ManimCommunity/manim")] },
    { reason: "near_duplicate", detail: "Overlapping purpose: typo-tolerant search engines with hybrid/vector search (similarity 0.88).", repos: [by("meilisearch/meilisearch"), by("typesense/typesense")] },
    { reason: "near_duplicate", detail: "Overlapping purpose: cloud-native API gateways built on NGINX/OpenResty (similarity 0.86).", repos: [by("Kong/kong"), by("apache/apisix")] },
  ];
  return groups.map((g) => ({ ...g, repos: g.repos.filter(Boolean) })).filter((g) => g.repos.length > 1);
});

// ---------------------------------------------------------------------------------- routes: search

on("POST", "/search", ({ body }) => runSearch(body));
on("GET", "/search/suggest", ({ query }) => {
  const q = (query.get("q") ?? "").trim().toLowerCase();
  const last = q.split(/\s+/).pop() ?? "";
  if (!q) return state.history.slice(0, 5).map((h) => ({ kind: "history", text: h.query, detail: `${h.result_count} results`, repo_id: null }));
  const out: Json[] = [];
  if (last.length >= 2) {
    for (const r of state.repos.filter((x) => x.name.toLowerCase().startsWith(last) || x.full_name.toLowerCase().includes(last)).slice(0, 4)) out.push({ kind: "repo", text: r.full_name, detail: r.description, repo_id: r.id });
    const topics = new Set(state.repos.flatMap((r) => r.topics));
    for (const t of [...topics].filter((x) => x.startsWith(last)).slice(0, 3)) out.push({ kind: "topic", text: `topic:${t}`, detail: "GitHub topic", repo_id: null });
    const langs = new Set(state.repos.map((r) => r.language ?? ""));
    for (const l of [...langs].filter((x) => x.toLowerCase().startsWith(last)).slice(0, 2)) out.push({ kind: "language", text: `lang:${l.toLowerCase()}`, detail: "Language", repo_id: null });
  }
  for (const s of state.saved.filter((x) => x.name.toLowerCase().includes(q)).slice(0, 2)) out.push({ kind: "saved", text: s.request.query, detail: `Saved: ${s.name}`, repo_id: null });
  for (const h of state.history.filter((x) => x.query.toLowerCase().includes(q) && x.query.toLowerCase() !== q).slice(0, 2)) out.push({ kind: "history", text: h.query, detail: "Recent", repo_id: null });
  return out;
});
on("GET", "/search/history", ({ query }) => state.history.slice(0, Number(query.get("limit") ?? 50)));
on("DELETE", "/search/history", () => {
  state.history = [];
  return { ok: true, message: "" };
});
on("GET", "/saved-searches", () => state.saved);
on("POST", "/saved-searches", ({ body }) => {
  const s = { id: state.nextId++, name: body.name, request: body.request, created_at: nowIso(), last_run_at: null };
  state.saved.unshift(s);
  return s;
});
on("DELETE", "/saved-searches/:id", ({ params }) => {
  state.saved = state.saved.filter((s) => s.id !== Number(params[0]));
  return { ok: true, message: "" };
});

// ---------------------------------------------------------------------------------- routes: ask / research / compare

on("GET", "/model-options", () => ({ active: activeModel(), options: providerOptions(), cloud_allowed: state.settings.privacy.allow_cloud_models, confirm_before_cloud: state.settings.privacy.confirm_before_cloud }));

on("POST", "/ask", async ({ body, res }) => {
  const a = resolveRoute(body);
  const stream = sse(res);
  const evidence = gatherEvidence(body.question, body.repo_ids ?? []);
  if (!a.configured) {
    stream.send("status", { message: "Retrieving evidence", provider_id: null, provider_kind: null, model: null, is_cloud: false });
    await sleep(250);
    stream.send("evidence", { evidence });
  }
  if (!preflight(stream, a, body)) return (stream.end(), STREAMED);
  stream.send("status", { message: "Retrieving evidence", provider_id: a.provider_id, provider_kind: a.provider_kind, model: a.model, is_cloud: a.is_cloud });
  await sleep(380);
  stream.send("evidence", { evidence });
  stream.send("status", { message: "Writing the answer" });
  await sleep(300);
  const text = answerFor(body.question, evidence);
  await streamText(stream, text);
  const report = saveReport({ kind: "ask", title: String(body.question).slice(0, 90), question: body.question, answer_md: text, evidence }, a);
  stream.send("done", { report });
  stream.end();
  return STREAMED;
});

on("POST", "/research", async ({ body, res }) => {
  const a = resolveRoute(body);
  const stream = sse(res);
  if (!preflight(stream, a, body)) return (stream.end(), STREAMED);
  const question = String(body.question);
  stream.send("status", { message: "Planning", provider_id: a.provider_id, provider_kind: a.provider_kind, model: a.model, is_cloud: a.is_cloud });
  await sleep(500);
  const plan = ["Find repositories relevant to each part of the question", "Read the README and documentation passages that describe the relevant capabilities", "Check licenses, maintenance activity and shared dependencies", "Compare the strongest candidates and note gaps", "Write the report with citations"];
  stream.send("plan", { steps: plan });
  const steps: TraceStep[] = [];
  const evidence: Evidence[] = [];
  const step = async (s: Omit<TraceStep, "index">, delay = 520) => {
    const full: TraceStep = { ...s, index: steps.length + 1 };
    steps.push(full);
    stream.send("step", full);
    await sleep(delay);
  };
  await step({ kind: "plan", title: "Planned 5 steps", tool: null, arguments: {}, summary: plan.join(" → "), repo_ids: [], evidence_refs: [], duration_ms: 1840, ok: true }, 300);

  const queries = [question, "local-first vector search embedded", "agent framework typed tools mcp", "document parsing pdf local"];
  for (const [i, q] of queries.entries()) {
    if (stream.closed) return STREAMED;
    const found = runSearch({ query: q, mode: "hybrid", limit: 6, record: false, filters: body.collection_id ? { collections: [body.collection_id] } : {} });
    const refs: string[] = [];
    for (const r of found.results.slice(0, 3)) {
      if (evidence.some((e) => e.repo_id === r.repo.id)) continue;
      const chunk = state.chunks.find((c) => c.id === r.matches[0]?.chunk_id) ?? state.chunks.find((c) => c.repo_id === r.repo.id);
      if (!chunk) continue;
      const ev = evidenceFromChunk(state, chunk, `E${evidence.length + 1}`);
      evidence.push(ev);
      refs.push(ev.ref);
    }
    await step({ kind: "tool", title: i === 0 ? "Searched repositories for the overall objective" : `Searched for ${["", "the retrieval layer", "the agent layer", "document ingestion"][i]}`, tool: i % 2 === 0 ? "search_repositories" : "search_documents", arguments: { query: q.slice(0, 80), mode: "hybrid", limit: 6 }, summary: `${found.total} matches; kept ${refs.length} new source${refs.length === 1 ? "" : "s"}.`, repo_ids: found.results.slice(0, 5).map((r) => r.repo.id), evidence_refs: refs, duration_ms: 40 + i * 13, ok: true });
    if (refs.length) stream.send("evidence", { evidence });
  }
  const top = [...new Set(evidence.map((e) => e.repo_id))].slice(0, 4);
  for (const id of top.slice(0, 2)) {
    const repo = state.repos.find((r) => r.id === id)!;
    const ev = metadataEvidence(repo, `E${evidence.length + 1}`);
    evidence.push(ev);
    await step({ kind: "tool", title: `Checked activity and license of ${repo.full_name}`, tool: "get_repository_activity", arguments: { repo: repo.full_name }, summary: `${repo.maintenance}, last push ${repo.pushed_at?.slice(0, 10)}, license ${repo.license_spdx ?? "none"}.`, repo_ids: [id], evidence_refs: [ev.ref], duration_ms: 4, ok: true }, 380);
  }
  stream.send("evidence", { evidence });
  await step({ kind: "tool", title: "Looked for license conflicts among the candidates", tool: "filter_by_license", arguments: { repos: top.map((id) => state.repos.find((r) => r.id === id)!.full_name), compatible_with: "MIT" }, summary: "Permissive licenses throughout except where noted in the report.", repo_ids: top, evidence_refs: [], duration_ms: 3, ok: true }, 380);
  await step({ kind: "tool", title: "Compared the leading candidates", tool: "compare_repositories", arguments: { repo_ids: top }, summary: "Compared language, deployment model, dependencies and maintenance.", repo_ids: top, evidence_refs: [], duration_ms: 9, ok: true }, 420);
  await step({ kind: "synthesis", title: "Selected evidence and wrote the report", tool: null, arguments: {}, summary: `${evidence.length} evidence items from ${new Set(evidence.map((e) => e.repo_id)).size} repositories.`, repo_ids: [], evidence_refs: evidence.map((e) => e.ref), duration_ms: null, ok: true }, 200);
  stream.send("status", { message: "Writing the report" });

  const name = (i: number) => evidence[i]?.repo_full_name ?? "—";
  const ref = (i: number) => evidence[Math.min(i, evidence.length - 1)]?.ref ?? "E1";
  const metaRef = evidence.find((e) => e.kind === "metadata")?.ref ?? ref(0);
  const text = `## Recommendation

For this objective, the strongest fit among your starred repositories is a stack built around **${name(0)}**, **${name(1)}** and **${name(2)}**. Each contributes a distinct layer, and all three run without a hosted service according to their own documentation [${ref(0)}][${ref(1)}][${ref(2)}].

## Recommended repositories

| Repository | Role in the solution | Evidence |
|---|---|---|
| ${name(0)} | Primary component for the core capability | documented [${ref(0)}] |
| ${name(1)} | Supporting layer; integrates through its SDK | documented [${ref(1)}] |
| ${name(2)} | Ingestion / tooling around the core | documented [${ref(2)}] |
| ${name(3)} | Optional: evaluation and operations | documented [${ref(3)}] |

## Component architecture

1. **Ingestion** reads source material and normalises it into text passages [${ref(2)}].
2. **Retrieval** stores passages and vectors locally and answers hybrid queries [${ref(0)}].
3. **Orchestration** exposes retrieval as typed tools to a bounded agent loop [${ref(1)}].
4. **Evaluation** measures retrieval quality and traces agent runs [${ref(3)}].

Integration boundaries are process-local: each layer is a library or a local server, so the pieces can be swapped independently.

## Licensing and maintenance

- The leading candidates are actively maintained and permissively licensed; this is observed repository metadata [${metaRef}].
- Check any project with a non-standard or copyleft license before embedding it in something you distribute. **Inferred** from declared SPDX identifiers only.

## Technical uncertainties

- Whether the SDK versions are mutually compatible is **not established**: no indexed evidence states it.
- Performance at your library's size is **unknown**; READMEs make throughput claims that were not verified.
- None of these repositories has been deep-indexed, so nothing above was confirmed against source code.

## Implementation plan

1. Stand up retrieval with ${name(0)} in embedded mode and index a small sample.
2. Wrap search and file access as typed tools in ${name(1)}.
3. Add ingestion with ${name(2)} for the document types you need.
4. Build a 30-query evaluation set before tuning anything.
`;
  await streamText(stream, text, 9);
  const report = saveReport(
    {
      kind: "research",
      title: question.length > 80 ? `${question.slice(0, 77)}…` : question,
      question,
      answer_md: text,
      evidence,
      trace: steps,
      warnings: ["No repository in this report has source code indexed; capability statements rest on project documentation."],
      unsupported: [{ text: "Each layer can be swapped independently.", reason: "Stated as a design inference; no indexed passage says so explicitly." }],
      data: { capability: body.capability ?? "auto", limits: { tool_calls: steps.filter((s) => s.kind === "tool").length, max_tool_calls: state.settings.research.max_tool_calls } },
      usage: { input_tokens: 0, output_tokens: 0, estimated: true, cost_usd: null, calls: 6 },
    },
    a,
  );
  stream.send("done", { report });
  stream.end();
  return STREAMED;
});
on("POST", "/research/:id/cancel", () => ({ ok: true, message: "" }));

function buildComparison(ids: number[]) {
  const repos = ids.map((id) => getRepo(id));
  if (repos.length < 2) throw new HttpError(422, "validation", "Select at least two repositories.");
  const evidence: Evidence[] = [];
  const refFor = (repo: RepoDetail, kind: string): string[] => {
    const chunk = state.chunks.find((c) => c.repo_id === repo.id && (kind === "manifest" ? c.kind === "manifest" : kind === "arch" ? c.heading === "Architecture" : c.heading === "Features"));
    if (!chunk) return [];
    const existing = evidence.find((e) => e.chunk_id === chunk.id);
    if (existing) return [existing.ref];
    const ev = evidenceFromChunk(state, chunk, `E${evidence.length + 1}`);
    evidence.push(ev);
    return [ev.ref];
  };
  const row = (key: string, label: string, group: string, values: string[], evidence_class = "observed", evidence_refs: string[][] = []) => ({ key, label, group, values, evidence_class, evidence_refs });
  const deps = repos.map((r) => state.deps.get(r.id) ?? []);
  const rows = [
    row("language", "Primary language", "identity", repos.map((r) => r.language ?? "—")),
    row("stars", "Stars", "identity", repos.map((r) => r.stars.toLocaleString("en-US"))),
    row("homepage", "Homepage", "identity", repos.map((r) => r.homepage?.replace(/^https?:\/\//, "") ?? "—")),
    row("features", "Stated focus", "capabilities", repos.map((r) => r.topics.slice(0, 4).join(", ") || "—"), "documented", repos.map((r) => refFor(r, "features"))),
    row("mcp", "MCP support", "capabilities", repos.map((r) => (r.topics.includes("mcp") ? "Documented" : "Not mentioned in indexed docs")), "documented"),
    row("local", "Runs without a hosted service", "capabilities", repos.map(() => "Documented in README"), "documented"),
    row("arch", "Architecture", "architecture", repos.map(() => "Small core with pluggable adapters"), "documented", repos.map((r) => refFor(r, "arch"))),
    row("deploy", "Deployment model", "architecture", repos.map((r) => (["Rust", "Go", "C++"].includes(r.language ?? "") ? "Single binary / server" : "Library, in-process")), "inferred"),
    row("maintenance", "Maintenance", "maturity", repos.map((r) => r.maintenance[0]!.toUpperCase() + r.maintenance.slice(1))),
    row("pushed", "Last push", "maturity", repos.map((r) => r.pushed_at?.slice(0, 10) ?? "—")),
    row("created", "Created", "maturity", repos.map((r) => r.created_at?.slice(0, 10) ?? "—")),
    row("issues", "Open issues", "maturity", repos.map((r) => String(r.open_issues))),
    row("deps", "Declared dependencies", "dependencies", deps.map((d) => (d.length ? `${d.length} in ${d[0]!.manifest_path}` : "No manifest indexed")), "observed", repos.map((r) => refFor(r, "manifest"))),
    row("depnames", "Notable packages", "dependencies", deps.map((d) => d.slice(0, 4).map((x) => x.name).join(", ") || "—")),
    row("license", "License", "licensing", repos.map((r) => r.license_spdx ?? "None declared")),
    row("copyleft", "Copyleft obligations", "licensing", repos.map((r) => (/GPL|AGPL|MPL/.test(r.license_spdx ?? "") ? "Yes — review before distributing" : r.license_spdx === "NOASSERTION" ? "Custom license — read it" : "None")), "inferred"),
  ];
  const shared = (lists: string[][]) => {
    const m = new Map<string, number[]>();
    lists.forEach((list, i) => list.forEach((v) => m.set(v, [...(m.get(v) ?? []), repos[i]!.id])));
    return [...m.entries()].filter(([, v]) => v.length > 1).map(([name, repo_ids]) => ({ name, repo_ids })).sort((a, b) => b.repo_ids.length - a.repo_ids.length);
  };
  const licenses = [...new Set(repos.map((r) => r.license_spdx ?? "none"))];
  const license_notes: string[] = [];
  if (licenses.some((l) => /AGPL|GPL/.test(l)) && licenses.some((l) => /MIT|Apache|BSD/.test(l))) license_notes.push("Mixing a GPL/AGPL project with permissively licensed ones is fine for local use, but distributing a combined work would have to follow the copyleft terms.");
  if (licenses.includes("NOASSERTION")) license_notes.push("At least one repository uses a custom license that GitHub could not classify. Read its LICENSE file before commercial use.");
  if (licenses.length === 1) license_notes.push(`All repositories declare the same license (${licenses[0]}).`);
  return { repos: repos.map(summary), rows, shared_topics: shared(repos.map((r) => r.topics)), shared_dependencies: shared(deps.map((d) => d.map((x) => x.name))), license_notes, evidence, report_id: null };
}
on("POST", "/compare", ({ body }) => buildComparison(body.repo_ids ?? []));
on("POST", "/compare/save", ({ body }) => {
  const comparison = buildComparison(body.repo_ids ?? []);
  const names = comparison.repos.map((r) => r.name).join(" vs ");
  const report = saveReport({ kind: "compare", title: `Comparison: ${names}`, question: `Compare ${names}`, evidence: comparison.evidence, data: { comparison }, saved: true, repo_ids: body.repo_ids, provider_id: null, provider_kind: null, model: null }, { configured: false, provider_id: null, provider_kind: null, provider_name: null, model: null, is_cloud: false });
  return report;
});
on("POST", "/compare/narrative", async ({ body, res }) => {
  const a = resolveRoute(body);
  const stream = sse(res);
  if (!preflight(stream, a, body)) return (stream.end(), STREAMED);
  const comparison = buildComparison(body.repo_ids ?? []);
  stream.send("status", { message: "Reading evidence for each repository", provider_id: a.provider_id, provider_kind: a.provider_kind, model: a.model, is_cloud: a.is_cloud });
  await sleep(450);
  stream.send("evidence", { evidence: comparison.evidence });
  const [r1, r2] = comparison.repos as RepoDetail[];
  const ref = (i: number) => comparison.evidence[Math.min(i, comparison.evidence.length - 1)]?.ref ?? "E1";
  const text = `**${r1!.name}** and **${r2!.name}** overlap in purpose but differ in how you would adopt them${body.focus ? ` ${body.focus}` : ""}.\n\n- **Scope.** ${r1!.name} documents ${r1!.topics.slice(0, 3).join(", ")} [${ref(0)}], while ${r2!.name} focuses on ${r2!.topics.slice(0, 3).join(", ")} [${ref(1)}].\n- **Adoption cost.** ${r1!.language === r2!.language ? `Both are ${r1!.language} projects, so they fit the same toolchain` : `${r1!.name} is ${r1!.language} and ${r2!.name} is ${r2!.language}; expect a process boundary between them`}. This is inferred from repository metadata.\n- **Maturity.** ${r1!.name} is ${r1!.maintenance}; ${r2!.name} is ${r2!.maintenance}.\n- **Licensing.** ${r1!.license_spdx ?? "No license"} versus ${r2!.license_spdx ?? "no license"}.\n\n**Unknown:** runtime performance and API stability; neither is evidenced in the indexed documentation.`;
  await streamText(stream, text);
  const names = comparison.repos.map((r) => r.name).join(" vs ");
  const report = saveReport({ kind: "compare", title: `Comparison: ${names}`, question: body.focus ? `Compare ${names} ${body.focus}` : `Compare ${names}`, answer_md: text, evidence: comparison.evidence, data: { comparison }, repo_ids: body.repo_ids }, a);
  stream.send("done", { report });
  stream.end();
  return STREAMED;
});

on("GET", "/reports", ({ query }) => {
  let list = state.reports;
  const kind = query.get("kind");
  if (kind) list = list.filter((r) => r.kind === kind);
  if (query.get("saved") === "true") list = list.filter((r) => r.saved);
  return list.slice(0, Number(query.get("limit") ?? 50)).map(({ answer_md: _a, evidence: _e, trace: _t, data: _d, ...rest }) => rest);
});
const getReport = (id: string) => {
  const r = state.reports.find((x) => x.id === Number(id));
  if (!r) throw notFound("Report");
  return r;
};
on("GET", "/reports/:id", ({ params }) => getReport(params[0]!));
on("PATCH", "/reports/:id", ({ params, body }) => {
  const r = getReport(params[0]!);
  if (typeof body.title === "string") r.title = body.title;
  if (typeof body.saved === "boolean") r.saved = body.saved;
  if (Array.isArray(body.collection_ids)) {
    for (const c of state.collections) c.report_ids = c.report_ids.filter((id) => id !== r.id);
    for (const id of body.collection_ids as number[]) state.collections.find((c) => c.id === id)?.report_ids.push(r.id);
    r.collections = state.collections.filter((c) => c.report_ids.includes(r.id)).map((c) => ({ id: c.id, name: c.name }));
  }
  return r;
});
on("DELETE", "/reports/:id", ({ params }) => {
  state.reports = state.reports.filter((r) => r.id !== Number(params[0]));
  return { ok: true, message: "" };
});
on("GET", "/reports/:id/export", ({ params, res }) => {
  const r = getReport(params[0]!);
  res.writeHead(200, { "Content-Type": "text/markdown; charset=utf-8", "Content-Disposition": `attachment; filename="report-${r.id}.md"` });
  res.end(`# ${r.title}\n\n${r.answer_md}\n\n## Evidence\n\n${r.evidence.map((e) => `- [${e.ref}] ${e.repo_full_name} ${e.path ?? ""} ${e.html_url ?? ""}`).join("\n")}\n`);
  return STREAMED;
});
on("GET", "/evidence/chunk/:id", ({ params }) => {
  const chunk = state.chunks.find((c) => c.id === Number(params[0]));
  if (!chunk) throw notFound("Passage");
  const doc = state.docs.find((d) => d.id === chunk.document_id)!;
  const lines = doc.content.split("\n");
  const before = lines.slice(Math.max(0, chunk.start_line - 4), chunk.start_line - 1);
  const after = lines.slice(chunk.end_line, chunk.end_line + 3);
  const ev = evidenceFromChunk(state, chunk, "E");
  return { ...ev, text: lines.slice(chunk.start_line - 1, chunk.end_line).join("\n"), context_before: before.join("\n"), context_after: after.join("\n"), context_start_line: chunk.start_line - before.length, repo: state.repos.find((r) => r.id === chunk.repo_id) };
});

// ---------------------------------------------------------------------------------- routes: collections / tags / categories

on("GET", "/collections", () => state.collections.map((c) => {
  const { repos: _r, reports: _p, ...rest } = collectionOut(c);
  return rest;
}));
on("POST", "/collections", ({ body }) => {
  if (state.collections.some((c) => c.name.toLowerCase() === String(body.name).toLowerCase())) throw new HttpError(409, "conflict", "A collection with this name already exists.");
  const id = state.nextId++;
  const c = { id, name: body.name, description: body.description ?? "", kind: body.kind ?? "manual", smart_query: body.smart_query ?? null, repo_count: 0, report_count: 0, created_at: nowIso(), updated_at: nowIso(), preview: [], repo_ids: (body.repo_ids ?? []) as number[], report_ids: [] as number[] };
  state.collections.unshift(c);
  for (const rid of c.repo_ids) state.repos.find((r) => r.id === rid)?.collections.push({ id, name: c.name });
  const { repos: _r, reports: _p, ...rest } = collectionOut(c);
  return rest;
});
const getCollection = (id: string) => {
  const c = state.collections.find((x) => x.id === Number(id));
  if (!c) throw notFound("Collection");
  return c;
};
on("GET", "/collections/:id", ({ params }) => collectionOut(getCollection(params[0]!)));
on("PATCH", "/collections/:id", ({ params, body }) => {
  const c = getCollection(params[0]!);
  if (typeof body.name === "string") {
    c.name = body.name;
    for (const r of state.repos) for (const m of r.collections) if (m.id === c.id) m.name = c.name;
  }
  if (typeof body.description === "string") c.description = body.description;
  if (body.smart_query) c.smart_query = body.smart_query;
  c.updated_at = nowIso();
  return collectionOut(c);
});
on("DELETE", "/collections/:id", ({ params }) => {
  const id = Number(params[0]);
  state.collections = state.collections.filter((c) => c.id !== id);
  for (const r of state.repos) r.collections = r.collections.filter((c) => c.id !== id);
  return { ok: true, message: "" };
});
on("POST", "/collections/:id/repos", ({ params, body }) => {
  const c = getCollection(params[0]!);
  for (const rid of body.repo_ids as number[]) {
    if (c.repo_ids.includes(rid)) continue;
    c.repo_ids.push(rid);
    state.repos.find((r) => r.id === rid)?.collections.push({ id: c.id, name: c.name });
  }
  c.updated_at = nowIso();
  return collectionOut(c);
});
on("POST", "/collections/:id/repos/remove", ({ params, body }) => {
  const c = getCollection(params[0]!);
  const ids = new Set(body.repo_ids as number[]);
  c.repo_ids = c.repo_ids.filter((x) => !ids.has(x));
  for (const r of state.repos) if (ids.has(r.id)) r.collections = r.collections.filter((m) => m.id !== c.id);
  return collectionOut(c);
});
on("GET", "/tags", () => {
  const m = new Map<string, number>();
  for (const r of state.repos) for (const t of r.tags) m.set(t, (m.get(t) ?? 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count }));
});
on("GET", "/categories", () => state.categories);
on("POST", "/categories", ({ body }) => {
  const c = { id: state.nextId++, slug: String(body.name).toLowerCase().replace(/[^a-z0-9]+/g, "-"), name: body.name, description: body.description ?? "", keywords: body.keywords ?? [], is_builtin: false, repo_count: 0 };
  state.categories.push(c);
  return c;
});
on("PATCH", "/categories/:id", ({ params, body }) => {
  const c = state.categories.find((x) => x.id === Number(params[0]));
  if (!c) throw notFound("Category");
  Object.assign(c, Object.fromEntries(Object.entries(body).filter(([k]) => ["name", "description", "keywords"].includes(k))));
  for (const r of state.repos) for (const rc of r.categories) if (rc.id === c.id) rc.name = c.name;
  return c;
});
on("DELETE", "/categories/:id", ({ params }) => {
  const id = Number(params[0]);
  state.categories = state.categories.filter((c) => c.id !== id);
  for (const r of state.repos) r.categories = r.categories.filter((c) => c.id !== id);
  return { ok: true, message: "" };
});

// ---------------------------------------------------------------------------------- routes: sync / jobs

on("GET", "/sync/status", () => syncStatus());
on("POST", "/sync/start", ({ body }) => {
  if (state.github.method === "none") throw new HttpError(409, "github_not_configured", "Connect GitHub before syncing.");
  return startSync(body.kind ?? "incremental");
});
on("POST", "/sync/cancel", () => {
  if (state.currentRun) {
    state.currentRun.status = "cancelled";
    state.currentRun.finished_at = nowIso();
    state.runs.unshift(state.currentRun);
    state.currentRun = null;
    const job = state.jobs.find((j) => j.status === "running" && j.type.startsWith("sync"));
    if (job) Object.assign(job, { status: "cancelled", finished_at: nowIso() });
  }
  return { ok: true, message: "" };
});
on("GET", "/sync/runs", ({ query }) => state.runs.slice(0, Number(query.get("limit") ?? 20)));
on("GET", "/jobs", ({ query }) => {
  let items = state.jobs;
  const status = query.get("status");
  const type = query.get("type");
  if (status) items = items.filter((j) => j.status === status);
  if (type) items = items.filter((j) => j.type === type);
  const offset = Number(query.get("offset") ?? 0);
  const limit = Number(query.get("limit") ?? 25);
  return { items: items.slice(offset, offset + limit), total: items.length };
});
on("POST", "/jobs", ({ body }) => {
  const job = addJob(body.type, { status: "running", started_at: nowIso(), attempts: 1, progress_message: "Working" });
  setTimeout(() => Object.assign(job, { status: "succeeded", finished_at: nowIso(), progress_message: "Done" }), 2500);
  return job;
});
on("POST", "/jobs/retry-failed", () => {
  for (const j of state.jobs.filter((x) => x.status === "failed")) Object.assign(j, { status: "queued", error: null, attempts: 0, finished_at: null });
  for (const r of state.repos.filter((x) => x.index_status === "failed")) Object.assign(r, { index_status: "queued", index_error: null });
  startIndexing();
  setTimeout(() => state.jobs.filter((x) => x.status === "queued" && x.type === "index_repo").forEach((j) => Object.assign(j, { status: "succeeded", finished_at: nowIso(), attempts: 1, progress_message: "3 files, 14 passages" })), 2200);
  return { ok: true, message: "" };
});
on("POST", "/jobs/clear-finished", () => {
  state.jobs = state.jobs.filter((j) => j.status !== "succeeded" && j.status !== "cancelled");
  return { ok: true, message: "" };
});
on("POST", "/jobs/:id/retry", ({ params }) => {
  const j = state.jobs.find((x) => x.id === Number(params[0]));
  if (!j) throw notFound("Job");
  Object.assign(j, { status: "queued", error: null, finished_at: null });
  setTimeout(() => Object.assign(j, { status: "succeeded", finished_at: nowIso(), attempts: 1 }), 1800);
  return j;
});
on("POST", "/jobs/:id/cancel", ({ params }) => {
  const j = state.jobs.find((x) => x.id === Number(params[0]));
  if (!j) throw notFound("Job");
  Object.assign(j, { status: "cancelled", finished_at: nowIso() });
  return j;
});
on("GET", "/schedules", () => state.schedules);
on("PATCH", "/schedules/:name", ({ params, body }) => {
  const s = state.schedules.find((x) => x.name === decodeURIComponent(params[0]!));
  if (!s) throw notFound("Schedule");
  if (typeof body.enabled === "boolean") s.enabled = body.enabled;
  if (typeof body.interval_minutes === "number") {
    s.interval_minutes = body.interval_minutes;
    s.next_run_at = iso(Date.now() + body.interval_minutes * 60_000);
  }
  return s;
});

// ---------------------------------------------------------------------------------- routes: providers

const TEMPLATES = [
  { kind: "ollama", label: "Ollama", category: "local", description: "Local models through the Ollama server. Models are discovered automatically.", default_base_url: "http://127.0.0.1:11434", needs_api_key: false, api_key_optional: false, base_url_editable: true, option_fields: [], docs_url: "https://ollama.com" },
  { kind: "lmstudio", label: "LM Studio", category: "local", description: "LM Studio's local server (OpenAI-compatible).", default_base_url: "http://127.0.0.1:1234/v1", needs_api_key: false, api_key_optional: false, base_url_editable: true, option_fields: [], docs_url: null },
  { kind: "vllm", label: "vLLM", category: "local", description: "High-throughput GPU serving with an OpenAI-compatible API.", default_base_url: "http://127.0.0.1:8000/v1", needs_api_key: true, api_key_optional: true, base_url_editable: true, option_fields: [], docs_url: null },
  { kind: "openai_compat", label: "OpenAI-compatible server", category: "local", description: "llama.cpp server, text-generation-webui, LocalAI or any endpoint that speaks the Chat Completions API.", default_base_url: "http://127.0.0.1:8080/v1", needs_api_key: true, api_key_optional: true, base_url_editable: true, option_fields: [], docs_url: null },
  { kind: "openai", label: "OpenAI API", category: "cloud", description: "OpenAI's paid API with your own API key.", default_base_url: "https://api.openai.com/v1", needs_api_key: true, api_key_optional: false, base_url_editable: false, option_fields: [], docs_url: null },
  { kind: "anthropic", label: "Anthropic", category: "cloud", description: "Claude models through the Anthropic API.", default_base_url: "https://api.anthropic.com", needs_api_key: true, api_key_optional: false, base_url_editable: false, option_fields: [], docs_url: null },
  { kind: "gemini", label: "Google Gemini", category: "cloud", description: "Gemini models through the Gemini API.", default_base_url: "https://generativelanguage.googleapis.com", needs_api_key: true, api_key_optional: false, base_url_editable: false, option_fields: [], docs_url: null },
  { kind: "azure_openai", label: "Azure OpenAI", category: "cloud", description: "Models deployed in your Azure OpenAI resource.", default_base_url: null, needs_api_key: true, api_key_optional: false, base_url_editable: true, option_fields: [{ key: "api_version", label: "API version", placeholder: "preview", required: false }, { key: "deployment", label: "Deployment name", placeholder: "my-deployment", required: true }], docs_url: null },
];
const MODELS: Record<string, { id: string; display_name?: string; kind: string; context_window?: number }[]> = {
  ollama: [
    { id: "qwen3:14b", kind: "chat", context_window: 40_960 },
    { id: "qwen3:4b", kind: "chat", context_window: 40_960 },
    { id: "llama3.3:70b-instruct-q4_K_M", kind: "chat", context_window: 131_072 },
    { id: "gemma3:12b", kind: "chat", context_window: 131_072 },
    { id: "nomic-embed-text", kind: "embedding", context_window: 8192 },
  ],
  lmstudio: [{ id: "qwen2.5-coder-14b-instruct", kind: "chat", context_window: 32_768 }],
  anthropic: [
    { id: "claude-fable-5-2", display_name: "Claude Fable 5.2", kind: "chat", context_window: 1_000_000 },
    { id: "claude-opus-5-5", display_name: "Claude Opus 5.5", kind: "chat", context_window: 500_000 },
    { id: "claude-sonnet-5-5", display_name: "Claude Sonnet 5.5", kind: "chat", context_window: 500_000 },
    { id: "claude-haiku-5-5", display_name: "Claude Haiku 5.5", kind: "chat", context_window: 200_000 },
  ],
  "chatgpt-plan": [
    { id: "gpt-6.1-sol", display_name: "GPT-6.1 Sol", kind: "chat" },
    { id: "gpt-6.1-sol-mini", display_name: "GPT-6.1 Sol mini", kind: "chat" },
  ],
};
const getProvider = (id: string) => {
  const p = state.providers.find((x) => x.id === decodeURIComponent(id));
  if (!p) throw notFound("Provider");
  return p;
};
on("GET", "/providers/templates", () => TEMPLATES);
on("GET", "/providers", () => state.providers.map((p) => ({ ...p, blocked_by_privacy: !p.capabilities.is_local && !state.settings.privacy.allow_cloud_models })));
on("POST", "/providers", ({ body }) => {
  const t = TEMPLATES.find((x) => x.kind === body.kind);
  if (!t) throw new HttpError(422, "validation", "Unknown provider kind.");
  let id = String(body.name).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || body.kind;
  while (state.providers.some((p) => p.id === id)) id = `${id}-2`;
  const local = t.category === "local";
  const p = { id, kind: body.kind, name: body.name, category: t.category as "local" | "cloud", base_url: body.base_url ?? t.default_base_url, enabled: body.enabled ?? true, options: body.options ?? {}, has_secret: Boolean(body.api_key), capabilities: { is_local: local, is_paid: !local, streaming: true, model_discovery: true, embeddings: local, structured_output: (local ? "json_schema" : body.kind === "anthropic" ? "tool" : "json_schema") as "json_schema" | "tool", accepts_temperature: true, accepts_max_output_tokens: true, notes: [] }, blocked_by_privacy: !local && !state.settings.privacy.allow_cloud_models, created_at: nowIso() };
  state.providers.push(p);
  return p;
});
on("PATCH", "/providers/:id", ({ params, body }) => Object.assign(getProvider(params[0]!), Object.fromEntries(Object.entries(body).filter(([k]) => ["name", "base_url", "options", "enabled"].includes(k)))));
on("DELETE", "/providers/:id", ({ params }) => {
  const p = getProvider(params[0]!);
  state.providers = state.providers.filter((x) => x.id !== p.id);
  return { ok: true, message: "" };
});
on("PUT", "/providers/:id/secret", ({ params }) => Object.assign(getProvider(params[0]!), { has_secret: true }));
on("DELETE", "/providers/:id/secret", ({ params }) => Object.assign(getProvider(params[0]!), { has_secret: false }));
on("POST", "/providers/:id/test", async ({ params, body }) => {
  const p = getProvider(params[0]!);
  await sleep(600);
  if (!p.capabilities.is_local && !p.has_secret) return { ok: false, latency_ms: null, detail: "", model_count: null, error: errorInfo("auth", "No API key is stored for this provider.", { ...activeModel(), provider_id: p.id, provider_kind: p.kind }), sample: null };
  if (p.id === "lmstudio" || state.fail === "unavailable") return { ok: false, latency_ms: null, detail: "", model_count: null, error: errorInfo("unavailable", `Could not connect to ${p.base_url}. Is the server running?`, { ...activeModel(), provider_id: p.id, provider_kind: p.kind }, { retryable: true }), sample: null };
  const models = MODELS[p.id] ?? [{ id: "default", kind: "chat" }];
  return { ok: true, latency_ms: p.capabilities.is_local ? 38 : 412, detail: `${models.length} models available`, model_count: models.length, error: null, sample: body.model ? "StarIndex connection test: OK." : null };
});
on("GET", "/providers/:id/models", ({ params }) => {
  const p = getProvider(params[0]!);
  if (p.id === "lmstudio") return { provider_id: p.id, models: [], error: errorInfo("unavailable", "LM Studio is not reachable at http://127.0.0.1:1234/v1.", { ...activeModel(), provider_id: p.id, provider_kind: p.kind }) };
  return { provider_id: p.id, models: (MODELS[p.id] ?? []).map((m) => ({ display_name: null, context_window: null, size_bytes: null, details: {}, ...m })), error: null };
});
const TASKS = ["classify", "summarize", "expand_query", "answer", "reasoning", "compare"];
const routingOut = () => ({
  default: state.routing.default,
  tasks: state.routing.tasks,
  effective: Object.fromEntries(TASKS.map((t) => [t, state.routing.tasks[t] ?? state.routing.default])),
  task_labels: { classify: "Metadata classification", summarize: "Repository summaries", expand_query: "Query expansion", answer: "Answer synthesis", reasoning: "Multi-repository reasoning", compare: "Comparison reports" },
});
on("GET", "/routing", () => routingOut());
on("PUT", "/routing", ({ body }) => {
  if ("default" in body) {
    state.routing.default = body.default;
    const p = state.providers.find((x) => x.id === body.default?.provider_id);
    state.llm = !body.default ? "none" : p?.kind === "chatgpt_plan" ? "chatgpt" : p && !p.capabilities.is_local ? "cloud" : "local";
  }
  for (const [task, route] of Object.entries(body.tasks ?? {})) {
    if (route) state.routing.tasks[task] = route as { provider_id: string; model: string };
    else delete state.routing.tasks[task];
  }
  return routingOut();
});
on("GET", "/usage", ({ query }) => ({
  since: ago(Number(query.get("days") ?? 30)),
  rows: [
    { provider_id: "ollama", provider_kind: "ollama", model: "qwen3:14b", task: "answer", calls: 41, input_tokens: 186_400, output_tokens: 22_150, cost_usd: null, estimated: true },
    { provider_id: "ollama", provider_kind: "ollama", model: "qwen3:14b", task: "reasoning", calls: 58, input_tokens: 412_900, output_tokens: 61_300, cost_usd: null, estimated: true },
    { provider_id: "ollama", provider_kind: "ollama", model: "qwen3:4b", task: "classify", calls: 89, input_tokens: 71_200, output_tokens: 4_450, cost_usd: null, estimated: true },
    { provider_id: "ollama", provider_kind: "ollama", model: "qwen3:4b", task: "summarize", calls: 23, input_tokens: 64_800, output_tokens: 6_900, cost_usd: null, estimated: true },
    { provider_id: "anthropic", provider_kind: "anthropic", model: "claude-sonnet-5-5", task: "reasoning", calls: 12, input_tokens: 96_300, output_tokens: 18_700, cost_usd: 0.5694, estimated: false },
  ],
  total_cost_usd: 0.5694,
  pricing_note: "Cost is estimated only for models with a configured price. Local models have no per-token cost; ChatGPT plan usage counts against your plan, not a price per token.",
}));

// ---------------------------------------------------------------------------------- routes: chatgpt plan

const chatgptStatus = () => ({
  available: state.settings.privacy.allow_cloud_models,
  reason: state.settings.privacy.allow_cloud_models ? "" : "Cloud models are turned off in Privacy settings.",
  host_id_set: true,
  accounts: state.chatgpt.accounts,
  active_account_id: state.chatgpt.accounts.find((a) => a.is_active)?.id ?? null,
  usage_url: "https://chatgpt.com/settings/usage",
  blocked_by_privacy: !state.settings.privacy.allow_cloud_models,
});
on("GET", "/chatgpt/status", () => chatgptStatus());
on("POST", "/chatgpt/login", ({ body }) => {
  const attempt_id = `att-${state.nextId++}`;
  state.chatgpt.attempts.set(attempt_id, { created: Date.now(), account_id: body.account_id ?? null, enable: Boolean(body.enable_plan_usage) });
  return { attempt_id, auth_url: "https://auth.openai.com/api/accounts/authorize?client_id=dynamic_agent_client&mock=1", status: "pending", message: "", account_id: null, plan_usage_enabled: null };
});
on("GET", "/chatgpt/login/:id", ({ params }) => {
  const attempt = state.chatgpt.attempts.get(params[0]!);
  if (!attempt) throw notFound("Sign-in attempt");
  if (Date.now() - attempt.created < 3200) return { attempt_id: params[0], auth_url: "https://auth.openai.com/api/accounts/authorize?client_id=dynamic_agent_client&mock=1", status: "pending", message: "", account_id: null, plan_usage_enabled: null };
  let account = state.chatgpt.accounts.find((a) => a.id === attempt.account_id);
  if (!account) {
    account = { id: `acc-${state.nextId++}`, label: state.chatgpt.accounts.length ? "Work" : "Personal", email: "user@example.com", client_id_suffix: "a9F2", plan_usage_enabled: true, is_active: state.chatgpt.accounts.length === 0, signed_in: true, scopes: ["chatgpt.tokens.use.direct", "email", "offline_access", "openid", "profile", "resource.invoke"], saved_at: nowIso(), expires_at: iso(Date.now() + 3600_000), last_error: null, welcome_pending: true };
    state.chatgpt.accounts.push(account);
  } else {
    Object.assign(account, { signed_in: true, plan_usage_enabled: true, welcome_pending: !account.plan_usage_enabled, is_active: account.is_active || !state.chatgpt.accounts.some((a) => a.is_active), expires_at: iso(Date.now() + 3600_000) });
  }
  return { attempt_id: params[0], auth_url: null, status: "completed", message: "", account_id: account.id, plan_usage_enabled: true };
});
on("POST", "/chatgpt/accounts/:id/activate", ({ params }) => {
  for (const a of state.chatgpt.accounts) a.is_active = a.id === params[0];
  return chatgptStatus();
});
on("POST", "/chatgpt/accounts/:id/sign-out", ({ params }) => {
  const a = state.chatgpt.accounts.find((x) => x.id === params[0]);
  if (a) Object.assign(a, { signed_in: false, is_active: false, expires_at: null });
  return chatgptStatus();
});
on("DELETE", "/chatgpt/accounts/:id", ({ params }) => {
  state.chatgpt.accounts = state.chatgpt.accounts.filter((a) => a.id !== params[0]);
  return chatgptStatus();
});
on("POST", "/chatgpt/accounts/:id/welcome-shown", ({ params }) => {
  const a = state.chatgpt.accounts.find((x) => x.id === params[0]);
  if (a) a.welcome_pending = false;
  return { ok: true, message: "" };
});

// ---------------------------------------------------------------------------------- plugin

async function readBody(req: IncomingMessage): Promise<Json> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return {};
  }
}

export function mockApiPlugin(): Plugin {
  return {
    name: "starindex-mock-api",
    apply: "serve",
    configureServer(server) {
      server.config.logger.info("  StarIndex mock API active (fixture data, no backend)\n");
      server.middlewares.use(async (req, res, next) => {
        if (!req.url?.startsWith("/api/")) return next();
        const url = new URL(req.url, "http://localhost");
        const path = url.pathname.slice(4);
        const method = req.method ?? "GET";
        const route = routes.find((r) => r.method === method && r.pattern.test(path));
        const fail = (status: number, kind: string, message: string) => {
          res.writeHead(status, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: { kind, message, detail: {} } }));
        };
        if (!route) return fail(404, "not_found", `No mock for ${method} ${path}`);
        try {
          const body = method === "GET" || method === "DELETE" ? {} : await readBody(req);
          const params = (route.pattern.exec(path) ?? []).slice(1);
          await sleep(method === "GET" ? 40 : 70); // enough latency to see loading states
          const result = await route.handler({ req, res, params, query: url.searchParams, body });
          if (result === STREAMED) return;
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify(result));
        } catch (err) {
          if (err instanceof HttpError) return fail(err.status, err.kind, err.message);
          server.config.logger.error(String((err as Error).stack ?? err));
          if (!res.headersSent) fail(500, "internal", (err as Error).message);
        }
      });
    },
  };
}
