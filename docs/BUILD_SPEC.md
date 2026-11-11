# StarIndex build specification

Local-first search and research over a user's starred GitHub repositories. Runs on a Windows
desktop (also Linux/macOS), stores everything under the user's data directory, works with no LLM.

## Stack (and deviations from the suggested stack)

| Layer | Choice | Note |
|---|---|---|
| Frontend | React + TypeScript + Vite | served by the API in normal use; Vite dev server in development |
| Backend | Python ≥ 3.11, FastAPI, Pydantic v2 | synchronous services, `httpx` |
| Metadata | SQLite (WAL) with numbered SQL migrations | `backend/starindex/db/migrations` |
| Lexical | SQLite FTS5 (BM25, porter stemming, prefix + trigram indexes) | plus a vocabulary table for typo correction |
| Vectors | Qdrant through `qdrant-client` | **embedded mode by default** (no server/Docker); server mode via `STARINDEX_QDRANT_URL` |
| Embeddings | `fastembed` (ONNX) by default; Ollama, OpenAI-compatible and Sentence Transformers selectable | **deviation**: fastembed avoids a multi-GB torch install on Windows and still supports CUDA |
| Reranking | local cross-encoder through `fastembed` | optional |
| Jobs | SQLite-backed persistent queue with leases; worker runs embedded in the API process or standalone | embedded Qdrant is single-process, so the default is one process |
| Tests | pytest, Vitest, Playwright | fake GitHub API server for sync/indexing tests |

Embeddings are stored in SQLite as the source of truth; the Qdrant collections are derived and can be
rebuilt without re-embedding. Collections are named per embedding model key, so a model change builds a
new index in the background while the old one keeps serving.

## Module ownership

| Package | Responsibility |
|---|---|
| `starindex.config`, `context`, `settings_store`, `secrets` | runtime config, service wiring, typed settings, secret storage |
| `starindex.db` | connections, migrations |
| `starindex.github` | auth, REST client (ETags, rate limits, retries), star synchronisation |
| `starindex.indexing` | file selection, safe archive reading, chunking, manifests, classification, pipeline |
| `starindex.search` | query parsing, lexical, embeddings, vector index, fusion, reranking, service |
| `starindex.providers` | provider contracts, adapters, routing, ChatGPT plan OAuth + inference, hardware, pricing |
| `starindex.agent` | evidence model, tools, ask, research workflow, compare, safety |
| `starindex.library` | repositories, annotations, tags, collections, categories, reports |
| `starindex.jobs` | queue, worker, scheduler, handlers |
| `starindex.api` | FastAPI app, schemas (contract), routes, security middleware |
| `frontend/` | React application |
| `eval/` | search evaluation corpus builder, queries with relevance judgments, metrics |
| `e2e/` | Playwright browser tests |

## Non-negotiable behaviours

- Repository identity is the GitHub numeric id. Renames/transfers update the name and record history.
- Sync never writes annotation tables. Unstarring applies the retention setting; default keeps everything.
- Search (keyword, semantic, hybrid) never requires an LLM; an unavailable embedding model degrades to keyword with a notice.
- No automatic fallback to another provider after a model error. The user picks explicitly.
- Cloud model traffic is off until enabled, and each request needs confirmation unless the user turns that off.
- Repository content is untrusted data: never executed, always delimited in prompts, tools are read-only
  except `save_research_report` (local database only).
- Services bind to loopback. A non-loopback host requires `STARINDEX_ACCESS_TOKEN`.
