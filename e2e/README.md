# StarIndex end-to-end tests

Playwright tests that drive the real application in Chromium: the built frontend served by the
real StarIndex API, backed by a local fake GitHub and a scripted, OpenAI-compatible fake model
server. Nothing leaves the machine and no GitHub account or model is needed.

## Run

```bash
npm install
```

```bash
npm test
```

`npm test` runs `playwright test --trace off`. Global setup:

1. builds `frontend/` if `frontend/dist` is missing (set `STARINDEX_E2E_REBUILD=1` to force a rebuild);
2. starts two isolated instances with `scripts/demo_instance.py`, each with its own temporary data
   directory, hashing embeddings and a trimmed corpus so they are ready in seconds:
   - **main** (ports 8785–8787): connected, fully synced and indexed, fake model configured;
   - **first-run** (ports 8795–8797): untouched, for the onboarding test;
3. waits until the main instance has finished syncing, indexing and embedding.

Global teardown stops both instances and deletes their data directories.

Requirements: the Python environment at `../.venv` (or `STARINDEX_E2E_PYTHON`), Node 20+, and the
evaluation corpus used by the fake GitHub (`python eval/build_corpus.py`, once, needs internet).
`@playwright/test` is pinned to 1.63.0; if its bundled browser is not installed, set
`STARINDEX_E2E_CHROMIUM` to a Chromium executable (the config also falls back to
`/opt/pw-browsers/chromium` when that exists).

| Variable | Purpose |
|---|---|
| `STARINDEX_E2E_PORT`, `…_GITHUB_PORT`, `…_LLM_PORT` | ports of the main instance (default 8785, 8786, 8787) |
| `STARINDEX_E2E_FRESH_PORT`, `…_FRESH_GITHUB_PORT`, `…_FRESH_LLM_PORT` | ports of the first-run instance (default 8795–8797) |
| `STARINDEX_E2E_MAIN_URL`, `STARINDEX_E2E_LLM_URL` | reuse an instance you started yourself instead of spawning one |
| `STARINDEX_E2E_FRESH_URL` | same, for the first-run instance |
| `STARINDEX_E2E_KEEP_DATA=1` | keep the temporary data directories for inspection |

## What is covered

| Spec | Flows |
|---|---|
| `onboarding.spec.ts` | first run: invalid token rejected, connect, full sync with progress, land in Search, onboarding does not return |
| `search.spec.ts` | keyword and hybrid search, no model calls, filters and chips, operators, typo correction, documentation scope, `j`/`k`/Enter, saved searches and history, empty state |
| `repository.spec.ts` | detail view, README, indexed files with line anchors, dependencies, related, notes/tags/favorite persistence, index-level change → job in Sync, cached usage ideas |
| `compare.spec.ts` | pick → table → evidence → save → report; compare from search results; streamed narrative |
| `models.spec.ts` | active model indicator, add/test provider, model discovery, per-task routing, per-request model switch, provider failure with explicit alternatives and no auto-switch, cut-off stream, privacy switch, ChatGPT sign-in start (authorization URL only), every settings section |
| `collections.spec.ts` | manual and smart collections, add from a repository page, tag rename/delete, categories |
| `research.spec.ts` | research run, trace, citations → evidence viewer, save → reports library, unsupported claims flagged, stop a run, Ask AI citations |
| `sync.spec.ts` | sync status, incremental sync, schedules, library sorting, quick filter and bulk actions |
| `shortcuts.spec.ts` | `/`, `Ctrl+K`, `j`/`k`/`x`/`Esc`, `g l`, `?`, Enter on a focused button |
| `mobile.spec.ts` | phone viewport: bottom navigation, filter drawer, no horizontal overflow on any section |

Tests in the `desktop` project share one backend and run serially (`workers: 1`). Names created by
tests carry a per-run suffix, so the suite can also be pointed at an instance that already has data.

## Known constraint

`PUT /api/github/auth` rejects pasted tokens shorter than 20 characters, and the fake GitHub's token
is `demo-token` (10 characters). The onboarding test therefore checks that the short token is
rejected with a clear message, then connects through the credential-free "public stars by username"
path (`octo`), which exercises the same full-sync and indexing pipeline.
