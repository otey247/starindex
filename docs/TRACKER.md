# Implementation tracker

Status of every area in the build specification. "Verified" names what was actually executed in the
build environment (a Linux cloud VM with 4 CPU cores, no GPU, no Docker daemon, no Windows machine and
no access to the owner's GitHub account).

## Status

| Area | State | Verified by |
|---|---|---|
| Star synchronisation: pagination, star timestamps, ETags, rate limits, retry/backoff, resume, incremental | done | `backend/tests/test_github_sync.py` against the fake GitHub API (257-repository walk, interruption and resume, primary and secondary rate limits) |
| Renamed, transferred, deleted, blocked, unstarred, re-starred repositories; retention policies; annotations untouched by sync | done | same test module |
| Indexing levels (metadata, documentation, deep), batch upgrades, exclusions, size limits, LFS/binary/symlink/traversal handling | done | `backend/tests/test_indexing.py` |
| Citation metadata (path, line range, commit SHA, index time), stable chunk identity | done | `test_indexing.py`, `test_search.py` |
| Lexical search (FTS5/BM25), phrases, operators, typo correction | done | `test_search.py`, evaluation |
| Embeddings (fastembed default; Ollama, OpenAI-compatible, Sentence Transformers selectable), cache, model-versioned index | done | fastembed run for real on the evaluation corpus; other backends through provider mocks |
| Vector index (Qdrant embedded / server), filtered retrieval | done (embedded) | real runs; **Qdrant server mode not run here** (no Docker, release download blocked) |
| Hybrid fusion, reranking, bounded priors, deduplication | done | `test_search.py`, evaluation (`docs/EVALUATION.md`) |
| Search without any LLM; degradation to keyword when embeddings are unavailable | done | `test_search.py` |
| Evaluation set (52 queries, graded judgments, 242 real repositories) and harness | done | `eval/run_eval.py`, results in `eval/results/` |
| Provider abstraction and adapters: Ollama, LM Studio, vLLM, OpenAI-compatible, OpenAI, Anthropic, Gemini, Azure OpenAI | done | `backend/tests/providers/` with mock transports. **Live**: OpenAI-compatible adapter against a real llama.cpp server (Qwen2.5-1.5B). Not run against real Ollama, LM Studio, vLLM or any cloud API |
| Task routing, privacy enforcement, explicit switching, no silent fallback, usage and cost | done | `test_router.py`, `test_agent.py` |
| ChatGPT plan (Sign in with ChatGPT, open-source token sharing) | implemented from the official docs | mock-based tests only; **never run against OpenAI** — see `docs/CHATGPT_PLAN.md` |
| Ask (grounded, cited, streamed) | done | `test_agent.py`, `test_api.py`; live with the real local model (correct, fully cited answer) |
| Research (plan, typed tools, limits, trace, synthesis, citation checks) | done | `test_agent.py`; live with the real local model (completed; the 1.5B model wrote an uncited report, which was flagged) |
| Prompt-injection containment | done | `test_agent.py` (poisoned README fixture, model that obeys the injection) |
| Compare (deterministic table, license observations, optional narrative), saved comparisons | done | `test_agent.py`, `test_api.py` |
| Categories (rules + embeddings, optional model pass), collections, smart collections, tags, notes, favorites, duplicates, related | done | `test_indexing.py`, `test_search.py`, `test_api.py` |
| Background jobs: persistence, leases, resume after restart, cancellation, scheduling, backup/restore, cleanup | done | `backend/tests/test_jobs.py`; restart of the running server mid-embedding |
| HTTP API, security middleware (Host/Origin checks, access token for non-loopback), secret hygiene | done | `backend/tests/test_api.py` |
| Web interface: all eight sections, repository workspace, onboarding, keyboard navigation, responsive layout | done | Vitest unit/component tests; Playwright suite in `e2e/` against a real backend |
| Windows launchers (`scripts/*.ps1`, `Start-StarIndex.cmd`), Task Scheduler registration | written | **not executed** (no Windows machine, no PowerShell here) |
| Docker Compose for optional Qdrant/Ollama | written | **not executed** (no Docker daemon here) |
| Documentation | done | — |

## Not verified in the build environment (do these on the target machine)

1. **The owner's real GitHub account.** The build environment could not call `/user/starred` for any
   account, so the import of the real starred list has not run. Everything was exercised against a fake
   GitHub API that replays 242 real public repositories.
2. **Windows.** Run `scripts\install.ps1`, then `scripts\start.ps1`; `starindex doctor` reports problems.
3. **ChatGPT plan sign-in and inference.** Follow the manual steps in `docs/CHATGPT_PLAN.md`.
4. **Cloud providers and Ollama/LM Studio/vLLM.** Use *Test* in Settings → Models, or the opt-in live
   tests (`STARINDEX_TEST_OLLAMA_URL`, `STARINDEX_TEST_OPENAI_COMPAT_URL`). The Gemini adapter in
   particular was written without access to Google's current documentation.
5. **GPU.** CUDA detection and the `-Gpu` install path have not run on a machine with a GPU.
6. **Qdrant server mode** and the standalone `starindex worker`.

## Known limitations

- Embedding throughput on CPU is modest (about 6 passages per second on the 4-core build VM while other
  work was running). Repository-level vectors are built first, so semantic search is usable early; a GPU
  (`-Gpu`) or a smaller indexing level speeds up the rest.
- The embedded vector index scans in-process. It is appropriate for documentation-level indexing of
  thousands of repositories; for very large deep indexes use Qdrant as a server.
- Small local models (around 1–3 B parameters) follow the citation rules for short answers but not
  reliably for long research reports; such reports are flagged as uncited. Use a larger model for Research.
- Maintenance status is inferred from the last push date only (no issue or release activity).
- Dependencies are read from manifests in the repository root and up to two directories below it.
- The reranker and the default embedding model are English-centric.
- Relevance judgments in the evaluation set were written by the build agent from knowledge of the
  projects, not by independent annotators.
- GitHub device-flow OAuth is not implemented; authentication is GitHub CLI, a personal access token or
  public mode.
