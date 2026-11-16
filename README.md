# StarIndex

Local-first search and research over the GitHub repositories you have starred.

StarIndex imports your complete starred list, indexes each repository's README, documentation,
manifests and (optionally) source code, and gives you keyword, semantic and hybrid search, cited
answers, bounded agentic research and side-by-side comparison — all running on your own machine,
with your data in a local SQLite database and a local vector index.

- **Works without any language model.** Search, filters, comparison tables, related repositories and
  collections need no LLM. Ask and Research use a model you configure.
- **Local by default.** Embeddings and reranking run locally (ONNX). Model traffic to a cloud provider
  is blocked until you enable it and is confirmed per request.
- **Traceable.** Every generated statement cites indexed evidence: repository, file path, line range,
  commit SHA and index time. Citations that do not resolve are flagged, never hidden.

## Quick start (Windows)

```powershell
# from the folder that contains this README
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

`start.ps1` opens <http://127.0.0.1:8765>. On first run StarIndex asks how to reach GitHub
(GitHub CLI, a read-only access token, or a public username), then imports and indexes your stars in
the background. Search is usable as soon as the metadata has arrived.

Full instructions, GPU options, running at sign-in and troubleshooting: [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md).
Linux/macOS: `scripts/start.sh`.

## What it does

| Area | Summary |
|---|---|
| **Search** | Keyword (SQLite FTS5/BM25 with typo correction, phrases, operators), semantic (local embeddings in Qdrant), hybrid (reciprocal rank fusion + optional local cross-encoder reranking). Scopes: repositories, documentation passages, code. |
| **Filters** | Language, topics, stars, license, maintenance, size, archived, pushed/starred dates, tags, collections, categories, index status. Also as operators: `lang:rust stars:>1000 topic:mcp is:archived pushed:>2025-01-01 "exact phrase" -excluded`. |
| **Ask AI** | Retrieves evidence, streams an answer with `[E#]` citations you can open down to the file lines. |
| **Research** | A bounded plan → tool calls → synthesis loop over typed, read-only tools, with limits on tool calls, time, tokens and context. Produces a structured report and a Research Trace. |
| **Compare** | Deterministic table (identity, capabilities, architecture, maturity, dependencies, licensing) with license observations; optional model-written narrative. |
| **Library** | Categories (automatic, editable), collections (manual and smart), tags, notes, favorites, duplicates and forks, related repositories, recently starred/rediscovered. |
| **Sync** | Complete paginated import with star timestamps, ETags, rate-limit handling, resume after interruption, renames/transfers/deletions/unstars handled by immutable repository id. |
| **Indexing levels** | Metadata · Documentation (default) · Deep (source code), per repository or in batches. Nothing from a repository is ever executed. |
| **Your own evaluation** | Mark results relevant / not relevant or name a repository that should have matched; StarIndex scores keyword, semantic and hybrid configurations on your judgments and suggests a change only when there is enough data — applied only when you confirm. |
| **MCP server** | The read-only research tools are available to Claude Code, Claude Desktop and other agents over stdio or HTTP ([docs/MCP.md](docs/MCP.md)). |
| **Models** | Ollama, LM Studio, vLLM and any OpenAI-compatible server locally; OpenAI, Anthropic, Gemini, Azure OpenAI as explicit opt-in; ChatGPT plan via OpenAI's Sign in with ChatGPT for open-source apps. Routing per task type. |

## Commands

```text
starindex serve [--port 8765] [--open]     run the application (API + worker + web UI)
starindex sync [--full]                    synchronise and index without the server
starindex search "query" [--mode hybrid]   search from the terminal
starindex doctor                           check dependencies and configuration
starindex verify [--sync] [--research]      check a running installation end to end, write a report
starindex mcp                              MCP server over stdio for other agents
starindex backup create|list|restore       database backups
starindex export [--format json|csv]       export the library with your annotations
starindex paths                            show where data is stored
```

Data lives outside the source tree: `%LOCALAPPDATA%\StarIndex` on Windows
(`~/.local/share/StarIndex` on Linux, `~/Library/Application Support/StarIndex` on macOS), or wherever
`STARINDEX_DATA_DIR` points.

## Development

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1     # backend :8765 + Vite dev server :5173
```

```bash
python -m pytest backend/tests                 # backend tests (no network needed)
cd frontend && npm run test && npm run build   # unit tests, typecheck, production build
cd e2e && npm test                             # browser tests against an isolated instance
python eval/build_corpus.py && python eval/run_eval.py   # search quality evaluation
```

## Documentation

- [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md) — installation, launchers, GPU, scheduling, troubleshooting
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — modules, data flow and design decisions
- [docs/EVALUATION.md](docs/EVALUATION.md) — search evaluation method and measured results
- [docs/PROVIDERS.md](docs/PROVIDERS.md) — model providers, capability matrix, adding a provider
- [docs/CHATGPT_PLAN.md](docs/CHATGPT_PLAN.md) — Sign in with ChatGPT (plan usage) integration and its limits
- [docs/MCP.md](docs/MCP.md) — using the library from Claude Code and other agents
- [docs/SECURITY.md](docs/SECURITY.md) — threat model, untrusted content, secrets, network exposure
- [docs/EXTENDING.md](docs/EXTENDING.md) — adding tools, categories, indexers, embedding backends
- [docs/API.md](docs/API.md) — HTTP API reference
- [docs/TRACKER.md](docs/TRACKER.md) — implementation status and known limitations

## License

MIT. StarIndex is an open-source, locally hosted application; the ChatGPT plan integration follows
OpenAI's documented path for such applications (see [docs/CHATGPT_PLAN.md](docs/CHATGPT_PLAN.md)).
