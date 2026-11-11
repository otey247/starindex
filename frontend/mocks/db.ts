/**
 * In-memory state for the dev mock API. Built deterministically from the seed so
 * screenshots and manual testing are stable across restarts.
 */
import type {
  AppSettings,
  Category,
  ChatGPTAccount,
  Collection,
  Dependency,
  DocumentContent,
  Evidence,
  Job,
  ProviderInfo,
  RepoDetail,
  Report,
  SavedSearch,
  Schedule,
  SearchHistoryEntry,
  SearchRequestInput,
  SyncRun,
} from "../src/api/types.ts";
import { CATEGORY_SEED, SEED } from "./seed.ts";

const DAY = 86_400_000;
export const NOW = Date.now();
export const iso = (ms: number) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
export const ago = (days: number) => iso(NOW - days * DAY);

/** Small deterministic PRNG so "random" fixture details never change between runs. */
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0xffffffff;
  };
}

export interface MockDoc extends DocumentContent {
  repo_id: number;
}

export interface MockChunk {
  id: number;
  repo_id: number;
  document_id: number;
  path: string;
  kind: string;
  heading: string;
  start_line: number;
  end_line: number;
  content: string;
}

const LANG_SECONDARY: Record<string, [string, string]> = {
  Python: ["Cython", "Shell"],
  TypeScript: ["JavaScript", "CSS"],
  JavaScript: ["HTML", "GLSL"],
  Rust: ["Python", "Shell"],
  Go: ["Shell", "Makefile"],
  "C++": ["C", "CMake"],
  "C#": ["PowerShell", "Shell"],
  Lua: ["Perl", "Shell"],
  C: ["Makefile", "SQL"],
};

const DEPS: Record<string, [eco: string, manifest: string, names: string[]]> = {
  Python: ["pypi", "pyproject.toml", ["pydantic", "httpx", "numpy", "typer", "openai", "tiktoken", "fastapi", "tenacity", "jinja2", "rich"]],
  TypeScript: ["npm", "package.json", ["react", "zod", "typescript", "vite", "tailwindcss", "@radix-ui/react-slot", "clsx", "three", "@modelcontextprotocol/sdk", "esbuild"]],
  JavaScript: ["npm", "package.json", ["three", "rollup", "eslint", "vite", "gl-matrix", "react", "typescript"]],
  Rust: ["cargo", "Cargo.toml", ["serde", "tokio", "anyhow", "clap", "rayon", "tonic", "tracing"]],
  Go: ["go", "go.mod", ["github.com/spf13/cobra", "golang.org/x/sync", "google.golang.org/grpc", "github.com/stretchr/testify"]],
};

function featureLines(topics: string[], description: string): string[] {
  const base = topics.slice(0, 5).map((t) => `- **${t.replace(/-/g, " ")}** support built in, with sensible defaults`);
  return [`- ${description.replace(/\.$/, "")}`, ...base, "- Runs locally; no hosted service required for the core workflow"];
}

function buildReadme(fullName: string, description: string, language: string, topics: string[], license: string | null): string {
  const name = fullName.split("/")[1]!;
  const install =
    language === "Python"
      ? `pip install ${name.toLowerCase()}`
      : language === "TypeScript" || language === "JavaScript"
        ? `npm install ${name.toLowerCase()}`
        : language === "Rust"
          ? `cargo add ${name.toLowerCase()}`
          : language === "Go"
            ? `go install github.com/${fullName}@latest`
            : `git clone https://github.com/${fullName}.git`;
  const mcp = topics.includes("mcp")
    ? `\n## MCP support\n\n${name} speaks the Model Context Protocol. Tools exposed by any MCP server can be attached to an agent, and ${name} can itself be exposed as an MCP server over stdio or streamable HTTP.\n`
    : "";
  return `# ${name}

![build](https://img.shields.io/badge/build-passing-brightgreen) ![license](https://img.shields.io/badge/license-${license ?? "none"}-blue)

${description}

## Features

${featureLines(topics, description).join("\n")}

## Installation

\`\`\`bash
${install}
\`\`\`

## Quickstart

\`\`\`${language === "Python" ? "python" : language === "Rust" ? "rust" : language === "Go" ? "go" : "ts"}
${language === "Python" ? `from ${name.toLowerCase().replace(/[^a-z0-9]/g, "_")} import Client\n\nclient = Client()\nresult = client.run("hello")\nprint(result)` : `import { create } from "${name.toLowerCase()}";\n\nconst app = create();\nawait app.run();`}
\`\`\`
${mcp}
## Architecture

${name} is organised as a small core with pluggable components. The core owns scheduling and state; integrations live in separate packages so that you only install what you use.

| Component | Responsibility |
|---|---|
| core | lifecycle, configuration, state |
| adapters | ${topics.slice(0, 3).join(", ") || "integrations"} |
| cli | local development and inspection |

## Documentation

See the [docs](docs/getting-started.md) for guides and the full API reference.

## License

${license && license !== "NOASSERTION" ? `Released under the ${license} license.` : "See LICENSE for terms; this project uses a custom license."}
`;
}

function chunkMarkdown(text: string): { heading: string; start: number; end: number; content: string }[] {
  const lines = text.split("\n");
  const chunks: { heading: string; start: number; end: number; content: string }[] = [];
  let start = 0;
  let heading = "";
  const flush = (end: number) => {
    const content = lines.slice(start, end).join("\n").trim();
    if (content) chunks.push({ heading, start: start + 1, end, content });
  };
  lines.forEach((line, i) => {
    if (/^#{1,3} /.test(line) && i > start) {
      flush(i);
      start = i;
    }
    if (/^#{1,3} /.test(line)) heading = line.replace(/^#+\s*/, "");
  });
  flush(lines.length);
  return chunks;
}

function maintenance(pushedDays: number, flag?: string): RepoDetail["maintenance"] {
  if (flag === "archived") return "archived";
  if (flag === "stale") return "stale";
  if (flag === "slowing") return "slowing";
  if (pushedDays <= 30) return "active";
  if (pushedDays <= 180) return "maintained";
  if (pushedDays <= 400) return "slowing";
  return "stale";
}

export interface MockState {
  firstRun: boolean;
  llm: "none" | "local" | "cloud" | "chatgpt";
  fail: null | "usage_limit" | "unavailable" | "auth";
  repos: RepoDetail[];
  docs: MockDoc[];
  chunks: MockChunk[];
  deps: Map<number, Dependency[]>;
  categories: Category[];
  collections: (Collection & { repo_ids: number[]; report_ids: number[] })[];
  reports: Report[];
  saved: SavedSearch[];
  history: SearchHistoryEntry[];
  jobs: Job[];
  runs: SyncRun[];
  currentRun: SyncRun | null;
  schedules: Schedule[];
  providers: ProviderInfo[];
  settings: AppSettings;
  routing: { default: { provider_id: string; model: string } | null; tasks: Record<string, { provider_id: string; model: string }> };
  github: { method: "gh_cli" | "token" | "public" | "none"; token_stored: boolean; username: string | null };
  chatgpt: { accounts: ChatGPTAccount[]; attempts: Map<string, { created: number; account_id: string | null; enable: boolean }> };
  summaries: Map<string, { content: string; created_at: string; provider: string; model: string }>;
  nextId: number;
}

export function buildState(): MockState {
  const rand = rng(247);
  const repos: RepoDetail[] = [];
  const docs: MockDoc[] = [];
  const chunks: MockChunk[] = [];
  const deps = new Map<number, Dependency[]>();
  const categories: Category[] = CATEGORY_SEED.map(([slug, name, description, keywords], i) => ({
    id: i + 1,
    slug,
    name,
    description,
    keywords,
    is_builtin: true,
    repo_count: 0,
  }));
  const catBySlug = new Map(categories.map((c) => [c.slug, c]));
  let docId = 1;
  let chunkId = 1;

  SEED.forEach(([fullName, description, language, stars, license, topics, cats, pushedDays, flag], i) => {
    const [owner, name] = fullName.split("/") as [string, string];
    const id = 100_000_000 + i * 7919;
    const starredDays = Math.round(4 + i * 6.3 + rand() * 9);
    const deep = i % 9 === 0;
    const pendingIdx = i % 17 === 13;
    const failed = i === 41;
    const sha = Array.from({ length: 40 }, () => "0123456789abcdef"[Math.floor(rand() * 16)]).join("");
    const secondary = LANG_SECONDARY[language] ?? ["Shell", "Makefile"];
    const main = Math.round(400_000 + rand() * 4_000_000);
    const languages: Record<string, number> = { [language]: main, [secondary[0]]: Math.round(main * (0.05 + rand() * 0.2)), [secondary[1]]: Math.round(main * 0.02) };
    const indexed = !pendingIdx && !failed;
    const repo: RepoDetail = {
      id,
      node_id: `R_${id.toString(36)}`,
      full_name: fullName,
      owner,
      name,
      owner_avatar_url: null,
      description,
      html_url: `https://github.com/${fullName}`,
      homepage: i % 3 === 0 ? `https://${name.toLowerCase().replace(/[^a-z0-9]/g, "")}.dev` : null,
      clone_url: `https://github.com/${fullName}.git`,
      topics,
      language,
      languages,
      stars,
      forks: Math.round(stars * (0.06 + rand() * 0.1)),
      watchers: Math.round(stars * 0.012),
      open_issues: Math.round(20 + rand() * 900),
      license_spdx: license,
      license_name: license && license !== "NOASSERTION" ? `${license} License` : license ? "Other" : null,
      default_branch: i % 5 === 0 ? "master" : "main",
      is_fork: false,
      is_archived: flag === "archived",
      is_private: false,
      is_template: false,
      is_disabled: false,
      parent_full_name: null,
      pushed_at: ago(pushedDays),
      updated_at: ago(Math.max(0, pushedDays - 0.5)),
      created_at: ago(400 + Math.round(rand() * 2600)),
      starred_at: ago(starredDays),
      star_state: i === 58 ? "unstarred" : "starred",
      unstarred_at: i === 58 ? ago(12) : null,
      access_state: "ok",
      size_kb: Math.round(2_000 + rand() * 480_000),
      index_level: deep ? "deep" : "docs",
      indexed_level: indexed ? (deep ? "deep" : "docs") : null,
      index_status: failed ? "failed" : pendingIdx ? "pending" : "indexed",
      index_error: failed ? "GitHub returned 403 while listing the file tree (secondary rate limit). Will retry with backoff." : null,
      indexed_commit_sha: indexed ? sha : null,
      indexed_at: indexed ? ago(rand() * 3) : null,
      tree_truncated: false,
      maintenance: maintenance(pushedDays, flag),
      categories: cats.map((slug, ci) => {
        const c = catBySlug.get(slug)!;
        return { id: c.id, slug: c.slug, name: c.name, source: ci === 0 ? "rule" : "embedding", confidence: ci === 0 ? 0.93 : 0.71 };
      }),
      tags: [],
      collections: [],
      favorite: [0, 9, 16, 27, 43, 51, 59].includes(i),
      priority: i === 16 ? 2 : i === 51 ? 1 : 0,
      has_note: false,
      note: "",
      last_opened_at: i % 6 === 0 ? ago(rand() * 20) : null,
      open_count: i % 6 === 0 ? 1 + Math.floor(rand() * 9) : 0,
      doc_count: 0,
      chunk_count: 0,
      embedded_chunk_count: 0,
      name_history: [],
      first_seen_at: ago(starredDays),
      last_seen_at: ago(0.1),
    };
    for (const c of repo.categories) catBySlug.get(c.slug)!.repo_count += 1;

    if (indexed) {
      const addDoc = (path: string, kind: string, content: string, lang: string | null) => {
        const parts = kind === "code" ? chunkCode(content) : chunkMarkdown(content);
        const doc: MockDoc = {
          id: docId++,
          repo_id: id,
          path,
          kind,
          language: lang,
          size_bytes: content.length,
          level: kind === "code" ? "deep" : "docs",
          commit_sha: sha,
          indexed_at: repo.indexed_at!,
          truncated: false,
          chunk_count: parts.length,
          html_url: `https://github.com/${fullName}/blob/${sha}/${path}`,
          content,
          chunks: [],
        };
        for (const p of parts) {
          const cid = chunkId++;
          doc.chunks.push({ chunk_id: cid, start_line: p.start, end_line: p.end, heading: p.heading });
          chunks.push({ id: cid, repo_id: id, document_id: doc.id, path, kind, heading: p.heading, start_line: p.start, end_line: p.end, content: p.content });
        }
        docs.push(doc);
        repo.doc_count += 1;
        repo.chunk_count += parts.length;
      };
      addDoc("README.md", "readme", buildReadme(fullName, description, language, topics, license), "Markdown");
      addDoc(
        "docs/getting-started.md",
        "doc",
        `# Getting started\n\nThis guide walks through installing ${name} and running it for the first time.\n\n## Requirements\n\n- A recent ${language} toolchain\n- Roughly 2 GB of free disk space\n\n## Configuration\n\n${name} reads configuration from environment variables and an optional config file. Every option has a default that works locally.\n\n## Deployment\n\nRun it as a single process for development. For production, the documentation recommends a container image and a persistent volume for state.\n`,
        "Markdown",
      );
      const dep = DEPS[language];
      if (dep) {
        const [eco, manifest, names] = dep;
        const picked = names.filter((_, k) => (i + k) % 3 !== 0).slice(0, 6);
        addDoc(manifest, "manifest", `# ${manifest}\n\n${picked.map((n) => `${n} = "^${1 + ((i + n.length) % 5)}.${n.length % 10}.0"`).join("\n")}\n`, null);
        deps.set(
          id,
          picked.map((n, k) => ({ ecosystem: eco, name: n, version_spec: `^${1 + ((i + n.length) % 5)}.${n.length % 10}.0`, scope: k > 3 ? "dev" : "runtime", manifest_path: manifest, shared_with: 0 })),
        );
      }
      if (deep) {
        const ext = language === "Python" ? "py" : language === "Rust" ? "rs" : language === "Go" ? "go" : "ts";
        addDoc(`src/core/engine.${ext}`, "code", buildCode(name, language), language);
        addDoc(`src/adapters/registry.${ext}`, "code", buildCode(`${name}Registry`, language), language);
      }
      repo.embedded_chunk_count = i % 11 === 5 ? Math.floor(repo.chunk_count / 2) : repo.chunk_count;
    }
    repos.push(repo);
  });

  // shared dependency counts
  const depUse = new Map<string, number>();
  for (const list of deps.values()) for (const d of list) depUse.set(`${d.ecosystem}:${d.name}`, (depUse.get(`${d.ecosystem}:${d.name}`) ?? 0) + 1);
  for (const list of deps.values()) for (const d of list) d.shared_with = (depUse.get(`${d.ecosystem}:${d.name}`) ?? 1) - 1;

  // annotations
  const byName = (n: string) => repos.find((r) => r.full_name === n)!;
  const note = (n: string, text: string, tags: string[]) => {
    const r = byName(n);
    r.note = text;
    r.has_note = true;
    r.tags = tags;
  };
  note("qdrant/qdrant", "Candidate vector store for the local search platform. Embedded mode via the Python client avoids running a server.", ["evaluate", "search-platform"]);
  note("pmndrs/react-three-fiber", "Base for the house-builder prototype. Check drei helpers for controls + snapping.", ["sims-builder"]);
  note("remotion-dev/remotion", "Check the license before using commercially — company licence needed above a team size.", ["video", "license-check"]);
  byName("pmndrs/drei").tags = ["sims-builder"];
  byName("dimforge/rapier").tags = ["sims-builder"];
  byName("langfuse/langfuse").tags = ["evaluate"];
  byName("theatre-js/theatre").name_history = [{ old_full_name: "AriaMinaei/theatre", new_full_name: "theatre-js/theatre", changed_at: ago(900) }];
  byName("donmccurdy/three-pathfinding").access_state = "ok";

  const mkCollection = (id: number, name: string, description: string, names: string[], kind: "manual" | "smart" = "manual", smart_query: SearchRequestInput | null = null) => {
    const repo_ids = names.map((n) => byName(n).id);
    for (const rid of repo_ids) repos.find((r) => r.id === rid)!.collections.push({ id, name });
    return {
      id,
      name,
      description,
      kind,
      smart_query: smart_query as Collection["smart_query"],
      repo_count: repo_ids.length,
      report_count: 0,
      created_at: ago(40 - id * 4),
      updated_at: ago(id),
      preview: [],
      repo_ids,
      report_ids: [] as number[],
    };
  };
  const collections = [
    mkCollection(1, "Local search platform", "Pieces for a local-first hybrid search stack with agentic research.", ["qdrant/qdrant", "neuml/txtai", "lancedb/lancedb", "ollama/ollama", "pydantic/pydantic-ai", "docling-project/docling", "meilisearch/meilisearch"]),
    mkCollection(2, "Sims-style house builder", "3D editor experiment: rendering, physics, navigation, UI.", ["mrdoob/three.js", "pmndrs/react-three-fiber", "pmndrs/drei", "pmndrs/react-three-rapier", "dimforge/rapier", "donmccurdy/three-pathfinding", "shadcn-ui/ui"]),
    mkCollection(3, "Agent frameworks to evaluate", "", ["langchain-ai/langgraph", "openai/openai-agents-python", "mastra-ai/mastra", "google/adk-python"]),
    mkCollection(4, "MCP servers in TypeScript", "Smart collection: stays current as new stars arrive.", [], "smart", { query: "topic:mcp lang:typescript", mode: "keyword" }),
  ];

  const settings: AppSettings = {
    onboarding_completed: true,
    github: { auth_method: "gh_cli", username: null },
    indexing: {
      default_level: "docs",
      auto_index_new: true,
      max_file_bytes: 400_000,
      max_doc_chars: 200_000,
      max_docs_files: 40,
      max_deep_files: 1500,
      max_tarball_mb: 150,
      max_repo_size_mb_for_deep: 2000,
      exclude_dirs: ["node_modules", "vendor", "third_party", "dist", "build", "target", ".git", "__pycache__", ".venv", "coverage"],
      exclude_globs: ["*.min.js", "*.min.css", "*.map", "*.lock", "package-lock.json", "*.pb.go", "*.snap", "*.svg"],
      code_extensions: [".py", ".ts", ".tsx", ".js", ".go", ".rs", ".java", ".cs", ".cpp", ".c", ".h"],
      fetch_concurrency: 4,
      worker_concurrency: 3,
      embed_batch_size: 32,
    },
    embedding: { backend: "fastembed", model: "BAAI/bge-small-en-v1.5", provider_id: null, device: "auto", query_prefix: "", document_prefix: "" },
    rerank: { enabled: true, backend: "fastembed", model: "Xenova/ms-marco-MiniLM-L-6-v2", top_n: 30 },
    search: { default_mode: "hybrid", rrf_k: 60, lexical_weight: 1, semantic_weight: 1, popularity_boost: 0.03, freshness_boost: 0.03, max_results_per_owner: 0, query_expansion: "off", typo_tolerance: true, save_history: true },
    privacy: { allow_cloud_models: true, confirm_before_cloud: true, offline_mode: false },
    sync: { incremental_interval_minutes: 360, full_interval_hours: 168, scheduled_sync_enabled: true, unstarred_retention: "keep" },
    research: { max_tool_calls: 12, max_seconds: 240, max_context_chars: 36_000, max_total_tokens: 150_000, max_output_tokens: 3000 },
    storage: { backup_enabled: true, backup_interval_hours: 168, backup_keep: 5 },
    routing: { default: null, tasks: {} },
    pricing: [],
  } as AppSettings;

  const caps = (local: boolean, structured: "json_schema" | "json_object" | "tool" | "prompt", embeddings = false, notes: string[] = []) => ({
    is_local: local,
    is_paid: !local,
    streaming: true,
    model_discovery: true,
    embeddings,
    structured_output: structured,
    accepts_temperature: true,
    accepts_max_output_tokens: true,
    notes,
  });
  const providers: ProviderInfo[] = [
    { id: "ollama", kind: "ollama", name: "Ollama", category: "local", base_url: "http://127.0.0.1:11434", enabled: true, options: {}, has_secret: false, capabilities: caps(true, "json_schema", true), blocked_by_privacy: false, created_at: ago(30) },
    { id: "lmstudio", kind: "lmstudio", name: "LM Studio", category: "local", base_url: "http://127.0.0.1:1234/v1", enabled: false, options: {}, has_secret: false, capabilities: caps(true, "json_schema", true), blocked_by_privacy: false, created_at: ago(22) },
    { id: "anthropic", kind: "anthropic", name: "Anthropic", category: "cloud", base_url: null, enabled: true, options: {}, has_secret: true, capabilities: caps(false, "tool"), blocked_by_privacy: false, created_at: ago(12) },
    { id: "chatgpt-plan", kind: "chatgpt_plan", name: "ChatGPT plan", category: "chatgpt_plan", base_url: null, enabled: true, options: {}, has_secret: false, capabilities: { ...caps(false, "prompt", false, ["No temperature or output-token limit on this route."]), accepts_temperature: false, accepts_max_output_tokens: false }, blocked_by_privacy: false, created_at: ago(3) },
  ];

  const jobs: Job[] = [];
  let jobId = 9000;
  const job = (type: string, status: Job["status"], minutesAgo: number, extra: Partial<Job> = {}): Job => ({
    id: jobId++,
    type,
    status,
    priority: 100,
    attempts: status === "queued" ? 0 : 1,
    max_attempts: 4,
    progress_current: 0,
    progress_total: 0,
    progress_message: "",
    error: null,
    payload: {},
    result: null,
    repo_full_name: null,
    created_at: iso(NOW - (minutesAgo + 2) * 60_000),
    started_at: status === "queued" ? null : iso(NOW - (minutesAgo + 1) * 60_000),
    finished_at: status === "succeeded" || status === "failed" ? iso(NOW - minutesAgo * 60_000) : null,
    run_after: null,
    ...extra,
  });
  jobs.push(
    job("index_repo", "failed", 14, { repo_full_name: repos[41]!.full_name, attempts: 4, error: "GitHubError 403: secondary rate limit while listing the git tree. Retried 4 times with backoff.", payload: { repo_id: repos[41]!.id, level: "docs" } }),
    job("sync_incremental", "succeeded", 126, { progress_message: "No changes on GitHub (304)", result: { not_modified: true } }),
    job("embed_pending", "succeeded", 131, { progress_current: 412, progress_total: 412, progress_message: "412 passages embedded", result: { embedded: 412 } }),
    job("classify_repos", "succeeded", 133, { progress_current: SEED.length, progress_total: SEED.length, progress_message: "Classified with rules + embeddings" }),
    job("backup", "succeeded", 2880, { progress_message: "starindex-backup.db written", result: { bytes: 48_211_968 } }),
    job("sync_full", "succeeded", 10_100, { progress_current: SEED.length, progress_total: SEED.length, progress_message: `${SEED.length} repositories` }),
  );
  for (const r of repos.filter((x) => x.index_status === "indexed").slice(0, 14)) {
    jobs.push(job("index_repo", "succeeded", 140 + jobs.length, { repo_full_name: r.full_name, payload: { repo_id: r.id, level: r.index_level }, progress_message: `${r.doc_count} files, ${r.chunk_count} passages` }));
  }

  const runs: SyncRun[] = [
    { id: 12, kind: "incremental", status: "succeeded", started_at: iso(NOW - 127 * 60_000), finished_at: iso(NOW - 126 * 60_000), pages_fetched: 1, repos_seen: 0, added: 0, updated: 0, renamed: 0, unstarred: 0, restarred: 0, not_modified: true, total_expected: SEED.length, error: null },
    { id: 11, kind: "incremental", status: "succeeded", started_at: ago(0.45), finished_at: ago(0.449), pages_fetched: 1, repos_seen: 100, added: 2, updated: 6, renamed: 0, unstarred: 0, restarred: 0, not_modified: false, total_expected: SEED.length, error: null },
    { id: 10, kind: "full", status: "succeeded", started_at: ago(7.01), finished_at: ago(7), pages_fetched: 1, repos_seen: SEED.length, added: 3, updated: 41, renamed: 1, unstarred: 1, restarred: 0, not_modified: false, total_expected: SEED.length, error: null },
    { id: 9, kind: "full", status: "interrupted", started_at: ago(7.2), finished_at: ago(7.19), pages_fetched: 0, repos_seen: 0, added: 0, updated: 0, renamed: 0, unstarred: 0, restarred: 0, not_modified: false, total_expected: null, error: "Network unreachable; resumed by the next run." },
  ];

  const schedules: Schedule[] = [
    { name: "sync_incremental", job_type: "sync_incremental", interval_minutes: 360, enabled: true, next_run_at: iso(NOW + 234 * 60_000), last_run_at: iso(NOW - 126 * 60_000) },
    { name: "sync_full", job_type: "sync_full", interval_minutes: 10_080, enabled: true, next_run_at: iso(NOW + 4 * 60_000 * 60), last_run_at: ago(7) },
    { name: "backup", job_type: "backup", interval_minutes: 10_080, enabled: true, next_run_at: iso(NOW + 5 * DAY), last_run_at: ago(2) },
    { name: "cleanup", job_type: "cleanup", interval_minutes: 1440, enabled: true, next_run_at: iso(NOW + 9 * 60_000 * 60), last_run_at: ago(0.6) },
  ];

  const state: MockState = {
    firstRun: false,
    llm: "local",
    fail: null,
    repos,
    docs,
    chunks,
    deps,
    categories,
    collections,
    reports: [],
    saved: [
      { id: 1, name: "Agent frameworks with MCP", request: { query: "agentic orchestration with MCP support", mode: "hybrid" } as SavedSearch["request"], created_at: ago(9), last_run_at: ago(1) },
      { id: 2, name: "Local PDF processing in Python", request: { query: "process PDFs locally lang:python", mode: "hybrid" } as SavedSearch["request"], created_at: ago(20), last_run_at: ago(6) },
    ],
    history: [],
    jobs,
    runs,
    currentRun: null,
    schedules,
    providers,
    settings,
    routing: { default: { provider_id: "ollama", model: "qwen3:14b" }, tasks: { classify: { provider_id: "ollama", model: "qwen3:4b" } } },
    github: { method: "gh_cli", token_stored: false, username: null },
    chatgpt: { accounts: [], attempts: new Map() },
    summaries: new Map(),
    nextId: 5000,
  };
  const queries = ["agentic orchestration with MCP support", "vector database embedded", "react three fiber physics", "pdf table extraction", "api gatway kubernetes", "animation from code"];
  state.history = queries.map((q, i) => ({ id: 400 - i, query: q, mode: "hybrid", scope: "repos", filters: state.settings && ({} as SearchHistoryEntry["filters"]), result_count: 9 + i * 3, created_at: iso(NOW - (i * 47 + 12) * 60_000) }));
  return state;
}

function chunkCode(text: string) {
  const lines = text.split("\n");
  const out: { heading: string; start: number; end: number; content: string }[] = [];
  const size = 24;
  for (let i = 0; i < lines.length; i += size) {
    const slice = lines.slice(i, i + size);
    const sym = slice.find((l) => /^(export |pub |def |class |func |fn |async )/.test(l.trim())) ?? "";
    out.push({ heading: sym.trim().slice(0, 60), start: i + 1, end: Math.min(lines.length, i + size), content: slice.join("\n") });
  }
  return out;
}

function buildCode(name: string, language: string): string {
  const cls = name.replace(/[^A-Za-z0-9]/g, "");
  if (language === "Python") {
    return `"""Core engine for ${name}."""\n\nfrom __future__ import annotations\n\nimport asyncio\nfrom dataclasses import dataclass, field\nfrom typing import Any, Callable\n\n\n@dataclass\nclass Step:\n    name: str\n    run: Callable[..., Any]\n    retries: int = 2\n\n\n@dataclass\nclass ${cls}Engine:\n    """Schedules steps and keeps their state between runs."""\n\n    steps: list[Step] = field(default_factory=list)\n    state: dict[str, Any] = field(default_factory=dict)\n\n    def register(self, step: Step) -> None:\n        if any(s.name == step.name for s in self.steps):\n            raise ValueError(f"duplicate step: {step.name}")\n        self.steps.append(step)\n\n    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:\n        for step in self.steps:\n            for attempt in range(step.retries + 1):\n                try:\n                    result = step.run(payload, self.state)\n                    if asyncio.iscoroutine(result):\n                        result = await result\n                    self.state[step.name] = result\n                    break\n                except Exception:\n                    if attempt == step.retries:\n                        raise\n                    await asyncio.sleep(0.2 * 2**attempt)\n        return self.state\n`;
  }
  return `// Core engine for ${name}.\n\nexport interface Step<T = unknown> {\n  name: string;\n  run: (payload: Record<string, unknown>, state: Map<string, unknown>) => Promise<T> | T;\n  retries?: number;\n}\n\nexport class ${cls}Engine {\n  private steps: Step[] = [];\n  private state = new Map<string, unknown>();\n\n  register(step: Step): void {\n    if (this.steps.some((s) => s.name === step.name)) {\n      throw new Error(\`duplicate step: \${step.name}\`);\n    }\n    this.steps.push(step);\n  }\n\n  async run(payload: Record<string, unknown>): Promise<Map<string, unknown>> {\n    for (const step of this.steps) {\n      const retries = step.retries ?? 2;\n      for (let attempt = 0; attempt <= retries; attempt++) {\n        try {\n          this.state.set(step.name, await step.run(payload, this.state));\n          break;\n        } catch (err) {\n          if (attempt === retries) throw err;\n          await new Promise((r) => setTimeout(r, 200 * 2 ** attempt));\n        }\n      }\n    }\n    return this.state;\n  }\n}\n`;
}

export function evidenceFromChunk(state: MockState, chunk: MockChunk, ref: string): Evidence {
  const repo = state.repos.find((r) => r.id === chunk.repo_id)!;
  const doc = state.docs.find((d) => d.id === chunk.document_id)!;
  return {
    ref,
    evidence_class: chunk.kind === "code" || chunk.kind === "manifest" ? "observed" : "documented",
    kind: chunk.kind as Evidence["kind"],
    repo_id: repo.id,
    repo_full_name: repo.full_name,
    title: chunk.heading || chunk.path,
    text: chunk.content.slice(0, 700),
    path: chunk.path,
    start_line: chunk.start_line,
    end_line: chunk.end_line,
    commit_sha: doc.commit_sha,
    indexed_at: doc.indexed_at,
    html_url: `${doc.html_url}#L${chunk.start_line}-L${chunk.end_line}`,
    chunk_id: chunk.id,
    document_id: doc.id,
    injection_flag: false,
  };
}

export function metadataEvidence(repo: RepoDetail, ref: string): Evidence {
  return {
    ref,
    evidence_class: "observed",
    kind: "metadata",
    repo_id: repo.id,
    repo_full_name: repo.full_name,
    title: "Repository metadata",
    text: `${repo.full_name} — ${repo.stars.toLocaleString("en-US")} stars, primary language ${repo.language}, license ${repo.license_spdx ?? "none declared"}, last push ${repo.pushed_at?.slice(0, 10)}, ${repo.is_archived ? "archived" : "not archived"}.`,
    path: null,
    start_line: null,
    end_line: null,
    commit_sha: null,
    indexed_at: repo.last_seen_at,
    html_url: repo.html_url,
    chunk_id: null,
    document_id: null,
    injection_flag: false,
  };
}
