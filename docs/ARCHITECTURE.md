# Architecture

StarIndex is one Python process (FastAPI + a background worker) with a React single-page
application, a SQLite database and an embedded Qdrant vector index. Everything is local.

```
 Browser (React SPA)  ──HTTP/SSE──►  FastAPI  ─┬─►  library / search / agent services
                                               │
                                               ├─►  JobQueue (SQLite) ◄── Worker threads + scheduler
                                               │         │
                              GitHub REST API ◄┴── github.sync, indexing.pipeline
                                               │
                 SQLite (WAL): repos, documents, chunks, FTS5, embeddings, jobs, annotations, reports
                 Qdrant (embedded or server): chunk vectors, repository vectors  (derived, rebuildable)
                 Model providers (optional): Ollama, OpenAI-compatible, Anthropic, Gemini, Azure, ChatGPT plan
```

## Modules

| Package | Responsibility | Depends on |
|---|---|---|
| `starindex.config`, `context`, `settings_store`, `secrets`, `logsetup` | process configuration, service wiring (`AppContext`), typed user settings, secret storage, structured logs | — |
| `starindex.db` | thread-local SQLite connections (WAL), forward-only numbered migrations | — |
| `starindex.github` | `client` (auth, ETags, rate limits, retries, host allow-list), `sync` (star discovery), `auth` (status) | db, settings |
| `starindex.indexing` | `selection` (what to index, safe archive reading), `chunking`, `manifests`, `classify`, `pipeline` (per-repository indexing, embedding of pending work) | github, search |
| `starindex.search` | `query` (operators → filters + FTS expression), `lexical` (FTS5, typo correction, snippets), `embeddings` (backends + cache), `vector` (Qdrant), `exact` (filtered exact search), `rerank`, `service` (hybrid fusion) | db, library.repos |
| `starindex.providers` | `base` (contracts), adapters, `registry`, `router` (task routing, privacy enforcement, usage), `chatgpt_plan` (OAuth + Responses), `hardware`, `pricing` | settings, secrets |
| `starindex.agent` | `evidence` (citable items, citation checks), `safety`, `tools` (typed read-only tools), `ask`, `research`, `compare` | search, providers, library |
| `starindex.library` | `repos` (GitHub upserts, filter → SQL, read models), `annotations` (notes, tags, collections, categories), `insights` (related, duplicates), `reports` | db |
| `starindex.jobs` | `queue` (leases, dedupe, retries), `worker` (threads, scheduler), `handlers`, `handle` | everything above |
| `starindex.api` | `schemas` (the HTTP contract), `routes/*`, `app` (security middleware, static UI) | everything above |
| `starindex.storage` | backup, restore, export, sizes | db |
| `starindex.testing` | fake GitHub API, scripted fake LLM, corpus loader, test harness | — |
| `frontend/` | React + TypeScript + Vite application; types generated from `schemas.py` | HTTP API |

Each concern has one implementation: GitHub data enters only through `library.repos.upsert_from_github`;
filters become SQL only in `library.repos.compile_filters`; every search (UI, Ask, research tools, smart
collections) goes through `SearchService.search`; every model call goes through `ModelRouter.resolve`.

## Data model (SQLite)

- `repos` — keyed by GitHub's immutable numeric repository id. Name, owner and everything else are
  attributes; renames add a row to `repo_name_history`. Sync state (`star_state`, `access_state`,
  `last_seen_run`) and index state (`index_level`, `indexed_level`, `index_status`, `indexed_commit_sha`) live here.
- `documents` → `chunks` — indexed files and their passages with path, 1-based line range, commit SHA and
  index time. `chunk_fts` (external-content FTS5) mirrors chunks through triggers.
- `repo_fts`, `repo_name_trgm`, `search_terms` — repository-level lexical document, trigram name index,
  vocabulary for typo correction.
- `embeddings` — vectors keyed by `(content_hash, model_key)`. **Source of truth for vectors.**
- `repo_annotations`, `tags`, `repo_tags`, `collections`, `collection_repos`, `repo_categories` — personal
  knowledge. No synchronisation code writes these tables.
- `jobs`, `schedules`, `sync_runs`, `http_cache`, `worker_heartbeats` — background work.
- `reports`, `repo_summaries`, `search_history`, `saved_searches`, `usage_log`, `providers`, `chatgpt_accounts`, `settings`.

Schema changes are new files in `backend/starindex/db/migrations/` applied in order at start-up and
recorded in `schema_migrations`.

## Synchronisation

Discovery of stars is separate from content indexing.

*Full sync* walks `GET /user/starred` (100 per page, `application/vnd.github.star+json` for star
timestamps) following `Link` headers until there is no next page. Each page and the resume checkpoint
(the next page URL) are written in one transaction, so an interrupted run continues from the
following page without duplicates or gaps. Only after a completed walk are repositories that were not
returned examined, one by one: `GET /repositories/{id}` distinguishes deleted/blocked (kept, marked
`not_found`/`blocked`) from still-existing ones, and `GET /user/starred/{owner}/{repo}` confirms whether
it is really unstarred or was merely missed because the list shifted during paging. A guard refuses to
mark more than half of the library as unstarred in one run.

*Incremental sync* requests the newest page with `If-None-Match`; a 304 costs nothing. Otherwise it
reads pages until one contains only known repositories. Because unstarring an old repository does not
change the newest page, the total count (from the pagination of a one-item page) is compared with the
local count and a full reconciliation is queued on mismatch.

Primary rate limits (`x-ratelimit-remaining: 0`) defer the job until the reset time without consuming a
retry; secondary limits honour `Retry-After`; network and 5xx errors back off exponentially.

Unstarring applies the retention setting: `keep` (default; the repository is hidden from the default
view but everything is retained), `keep_annotated`, or `delete`.

## Indexing

| Level | What is fetched | How |
|---|---|---|
| metadata | nothing beyond the starred list | — |
| docs (default) | README, Markdown docs, example READMEs, manifests, selected configuration and API description files | git tree listing + individual files by commit SHA |
| deep | the above plus source code | one archive download, read member-by-member in memory |

Per repository: conditional request for the branch head (304 → nothing to do), tree listing, selection by
rules with per-level file limits, skip of files whose git blob SHA is unchanged, decode (binary files and
Git LFS pointers are dropped), chunk, parse manifests, store in one transaction. Vendored and generated
directories, lock files, minified assets, translations, files over the size limit, symlinks and unsafe
paths are excluded. Archives are never extracted to disk and nothing is executed.

Chunkers: Markdown by heading hierarchy (tiny sections are merged with their neighbour), code by
top-level definitions with a size cap, structured files by top-level keys, plain text by paragraphs.
A chunk's id is a UUIDv5 of (repository id, path, content hash, duplicate ordinal): unchanged text keeps
its identity and its embedding when lines move.

Embedding runs as a separate, single job in bounded batches: repository-level vectors first (semantic
search becomes usable quickly), then passages ordered README → docs → code. Vectors are cached in
SQLite, then upserted into Qdrant.

## Search

1. Parse operators into filters; remaining words become terms (stop words removed), phrases and exclusions.
2. Typo correction: a term absent from the vocabulary *and* from the full-text indexes is replaced
   (OR-ed) with the closest vocabulary term (Damerau–Levenshtein ≤ 1–2).
3. Lexical retrieval (BM25): repository documents (weighted columns: name, topics, description, …) and passages.
   Short queries are conjunctive, longer natural-language queries disjunctive.
4. Dense retrieval: repository vectors and passage vectors.
5. Weighted reciprocal rank fusion (k = 60) of the four lists at repository level.
6. Optional cross-encoder reranking of the top 20 on each repository's own summary, blended 1:1 with the fused rank.
7. Bounded priors: at most +3 % for popularity and +3 % for maintenance, ×0.9 for forks. Exact
   repository-name matches lead.
8. One result per repository, with up to three matching passages (path, lines, commit, permalink).

Filters are applied inside retrieval: in SQL for the lexical side; on the dense side by exact search over
the SQLite-stored vectors of the allowed repositories (embedded Qdrant) or by indexed payload filters
(Qdrant server). When the embedding model or the vector index is unavailable, the request runs as keyword
search and says so. Weights and the reranker configuration were chosen on the evaluation set
([EVALUATION.md](EVALUATION.md)).

## Models and agent workflows

`providers.base` defines `ChatProvider` (model discovery, streaming, structured generation) and
`ProviderCapabilities`; adapters declare what they support instead of assuming OpenAI semantics.
`ModelRouter.resolve(task, override, confirm_cloud)` picks the provider and model for a task type
(classify, summarize, expand_query, answer, reasoning, compare), enforces the privacy settings and
never substitutes another provider after an error — failures are returned with the list of providers
the user may explicitly choose.

*Ask*: deterministic retrieval → evidence items `[E1..En]` → streamed answer → citation check.

*Research*: deterministic seed retrieval → model proposes tool calls as JSON (validated against Pydantic
argument models) → code executes them, records a trace step and adds evidence → model decides whether
more is needed → streamed synthesis → citation check, license observations and aging-repository list
computed by code. Limits on tool calls, wall-clock time, tokens, evidence size and planning rounds are
enforced by code. A single agent is used deliberately: retrieval is local and fast, so parallel
sub-agents would add cost without adding evidence.

*Evidence classes*: `observed` (GitHub metadata, dependency manifests, source code), `documented` (what a
project says about itself in README/docs), `inferred` (the model's conclusions, labelled as such),
`unverified` (citations or repositories that do not resolve; reported in `report.unsupported`).

## Background jobs

`jobs` rows are claimed with a 120-second lease renewed by the worker. A crashed worker's lease expires
and the job is claimed again; handlers keep checkpoints in `jobs.state` and are idempotent. A worker that
lost its lease abandons the job without touching it; on shutdown running jobs return to the queue without
losing an attempt. `dedupe_key` guarantees one live job per unit of work (one sync, one index job per
repository, one embedding job). The scheduler is a table of intervals evaluated by the worker.

The worker runs inside the API process by default because the embedded vector index can be opened by one
process only. With `STARINDEX_QDRANT_URL` set, `starindex serve --no-worker` plus `starindex worker`
run them separately.

## Design decisions

| Decision | Reason |
|---|---|
| `fastembed` (ONNX Runtime) as the default embedding and reranking runtime instead of Sentence Transformers | no multi-gigabyte PyTorch install on Windows, CPU works out of the box, CUDA optional. Sentence Transformers, Ollama and OpenAI-compatible embedding servers remain selectable. |
| Embedded Qdrant by default | zero setup on Windows. Its payload filtering is a Python scan, so filtered dense search uses exact search over SQLite-stored vectors instead; Qdrant server mode uses native filters. |
| Vectors stored in SQLite as well | the vector index is derived data: rebuild, model switch, backup and restore never re-embed. |
| Synchronous Python (`httpx.Client`, generators for streams) | simple, testable; FastAPI runs handlers in its thread pool; ONNX releases the GIL. |
| One provider-neutral structured-output path (`generate_json`) instead of native tool calling | works with small local models and every provider; tool execution stays in deterministic code. |
| No automatic provider fallback | cost and privacy must never change without the user choosing it. |
| SQL migrations as plain files | no ORM; FTS5 virtual tables and triggers are first-class. |
