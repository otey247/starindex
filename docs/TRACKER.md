# Implementation tracker

Status of every area in the build specification. "Verified" names what was actually executed in the
build environment (a Linux cloud VM with 4 CPU cores, no GPU, no Docker daemon, no Windows machine and
no access to the owner's GitHub account). Additional live verification on 2026-11-17 imported all
8,613 stars from the owner's account and executed the Windows installer, launcher, stop script and
verification wrapper in both PowerShell editions on Windows hosted runners. Full-library content
indexing, interactive ChatGPT sign-in and GPU execution remain incomplete.
The larger-model smoke run completed with citations but did not pass strict factual-support review;
see [the live verification record](VERIFICATION_2026-11-17.md).

## Status

| Area | State | Verified by |
|---|---|---|
| Star synchronisation: pagination, star timestamps, ETags, rate limits, retry/backoff, resume, incremental | done | `backend/tests/test_github_sync.py` against the fake GitHub API; live first import on 2026-11-17: 8,613/8,613 stars, 87 pages |
| Renamed, transferred, deleted, blocked, unstarred, re-starred repositories; retention policies; annotations untouched by sync | done | same test module |
| Indexing levels (metadata, documentation, deep), batch upgrades, exclusions, size limits, LFS/binary/symlink/traversal handling | done | `backend/tests/test_indexing.py` |
| Citation metadata (path, line range, commit SHA, index time), stable chunk identity | done | `test_indexing.py`, `test_search.py` |
| Lexical search (FTS5/BM25), phrases, operators, typo correction | done | `test_search.py`, evaluation |
| Embeddings (fastembed default; Ollama, OpenAI-compatible, Sentence Transformers selectable), cache, model-versioned index | done | fastembed run for real on the evaluation corpus; other backends through provider mocks |
| Vector index (Qdrant embedded / server), filtered retrieval | done (embedded) | real runs; **Qdrant server mode not run here** (no Docker, release download blocked) |
| Hybrid fusion, reranking, bounded priors, deduplication | done | `test_search.py`, evaluation (`docs/EVALUATION.md`) |
| Search without any LLM; degradation to keyword when embeddings are unavailable | done | `test_search.py` |
| Evaluation set (52 queries, graded judgments, 242 real repositories) and harness | done | `eval/run_eval.py`, results in `eval/results/` |
| Provider abstraction and adapters: Ollama, LM Studio, vLLM, OpenAI-compatible, OpenAI, Anthropic, Gemini, Azure OpenAI | done | `backend/tests/providers/` with mock transports. **Live**: llama.cpp (Qwen2.5 1.5B) and Ollama 0.40.16 (Qwen2.5 7.6B Q4_K_M), including native streaming and structured output. LM Studio, vLLM and cloud APIs remain unverified |
| Task routing, privacy enforcement, explicit switching, no silent fallback, usage and cost | done | `test_router.py`, `test_agent.py` |
| ChatGPT plan (Sign in with ChatGPT, open-source token sharing) | implemented from the official docs | mock-based tests only; **never run against OpenAI** — see `docs/CHATGPT_PLAN.md` |
| Ask (grounded, cited, streamed) | done; quality gate incomplete | `test_agent.py`, `test_api.py`; initial small-model answer was correctly cited. Later live Ollama 7.6B output had resolving citations but incorrect counts/comparisons |
| Research (plan, typed tools, limits, trace, synthesis, citation checks) | done; quality gate incomplete | `test_agent.py`; live 1.5B report was uncited. Live Ollama 7.6B run completed in 399s with four tool calls and eight resolving citations, but factual-support review did not pass |
| Prompt-injection containment | done | `test_agent.py` (poisoned README fixture, model that obeys the injection) |
| Compare (deterministic table, license observations, optional narrative), saved comparisons | done | `test_agent.py`, `test_api.py` |
| Categories (rules + embeddings, optional model pass), collections, smart collections, tags, notes, favorites, duplicates, related | done | `test_indexing.py`, `test_search.py`, `test_api.py` |
| Background jobs: persistence, leases, resume after restart, cancellation, scheduling, backup/restore, cleanup | done | `backend/tests/test_jobs.py`; restart of the running server mid-embedding |
| HTTP API, security middleware (Host/Origin checks, access token for non-loopback), secret hygiene | done | `backend/tests/test_api.py` |
| Web interface: all eight sections, repository workspace, onboarding, keyboard navigation, responsive layout | done | Vitest unit/component tests; Playwright suite in `e2e/` against a real backend |
| Relevance feedback and personal evaluation (judgments from your own searches, configuration grid, explicit apply) | done | `backend/tests/test_extensions.py` |
| MCP server (stdio and HTTP) over the read-only research tools | done | `test_extensions.py`, including the reference Python SDK client over stdio |
| `starindex verify` end-to-end check with report | done | live Linux installation; Windows wrapper executed in PowerShell 5.1 and 7. Incomplete checks return exit 2; `--sync` waits for discovery, indexing and embeddings |
| Windows installer, launcher, stop script; CMD shortcut; Task Scheduler registration | partially verified | `install.ps1`, `start.ps1`, `stop.ps1`, `verify.ps1` executed in both PowerShell editions ([CI](https://github.com/otey247/starindex/actions/runs/44021627972)); CMD shortcut and Task Scheduler not executed |
| Docker Compose for optional Qdrant/Ollama | written | **not executed** (no Docker daemon here) |
| Documentation | done | — |

## Not verified in the build environment (do these on the target machine)

`scripts\verify.ps1 -Sync` runs items 1–5 below in one go and writes a report.

1. **Full-library indexing.** The live first import of the owner's 8,613 stars completed on 2026-11-17.
   Documentation and embedding work remains queued; this does not establish full indexing coverage.
2. **Target Windows machine.** Installer and launcher smoke tests now pass on Windows hosted runners.
   The CMD shortcut, interactive browser behavior and Task Scheduler still need target-machine checks.
3. **ChatGPT plan sign-in and inference.** Follow the manual steps in `docs/CHATGPT_PLAN.md`.
4. **Cloud providers and LM Studio/vLLM.** Ollama native chat was verified with the 7.6B model; other
   servers/providers remain unverified. Use *Test* in Settings → Models, or the opt-in live
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
- Generation quality remains a release gate. Initial live Qwen2.5 1.5B and 3B llama.cpp runs wrote
  Research reports without valid citations. A later live Ollama 7.6B test produced cited Ask and Research
  outputs, but Ask reversed a numeric comparison and misstated a fork count; Research generalized beyond
  a selected excerpt. Resolving references does not establish factual support.
- Maintenance status is inferred from the last push date only (no issue or release activity).
- Dependencies are read from manifests in the repository root and up to two directories below it.
- The reranker and the default embedding model are English-centric.
- Relevance judgments in the evaluation set were written by the build agent from knowledge of the
  projects, not by independent annotators.
- GitHub device-flow OAuth is not implemented; authentication is GitHub CLI, a personal access token or
  public mode.
