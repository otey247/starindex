# Installing and running StarIndex on Windows

## Requirements

| | |
|---|---|
| Windows 10 or 11 (64-bit) | |
| Python 3.11, 3.12 or 3.13 | `winget install Python.Python.3.12` |
| Node.js 20+ | only if `frontend\dist` is not present (source checkout without a built UI): `winget install OpenJS.NodeJS.LTS` |
| Disk | about 1 GB for the environment and models, plus the index: measured 67 MB for 242 repositories at the documentation level (45 MB database + 22 MB vector index); repositories with large docs folders or deep indexing need more |
| Optional | GitHub CLI (`winget install GitHub.cli`), Ollama or LM Studio for local language models, an NVIDIA GPU |

No Docker, database server or cloud account is required.

## Install

Open PowerShell in the StarIndex folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

The script creates `.venv`, installs the Python dependencies, builds the web interface when needed
and finishes with `starindex doctor`, which lists what is working and what is not.

Options:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Gpu                    # ONNX Runtime with CUDA for embeddings/reranking
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -SentenceTransformers   # optional PyTorch-based embedding backend
```

## Start (normal use)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

or double-click `Start-StarIndex.cmd`. One process serves the API, the background worker and the web
interface at <http://127.0.0.1:8765>. Stop it with Ctrl+C or by closing the window; unfinished
synchronisation, indexing and embedding continue from their checkpoints at the next start.

```powershell
scripts\start.ps1 -Port 9000 -NoBrowser -DataDir D:\StarIndexData
```

### First run

1. **Connect GitHub.** Pick one:
   - *GitHub CLI* — if `gh auth login` has been done, StarIndex asks `gh` for its token each time it needs one.
   - *Access token* — a fine-grained personal access token with read-only access is enough
     (for public stars no repository permission is needed; add read access to *Starring* and to
     *Contents/Metadata* of private repositories if you want those). The token is stored in Windows
     Credential Manager, never in a file or in the browser.
   - *Public username* — no token; reads the public starred list of a username (60 requests per hour,
     so the first indexing of a large list takes hours and is paced automatically).
2. **Start the first sync.** The starred list is imported page by page, then each repository's
   documentation is fetched and indexed. The Sync page shows pages, repositories, queue, throughput
   and errors with retry controls.
3. **Search.** Keyword search works as soon as metadata exists; semantic and hybrid search switch on
   as embeddings are built (repository-level vectors first, so this takes seconds to minutes).
   The first semantic query downloads the embedding model (about 130 MB) and the reranker (about 90 MB)
   into `%LOCALAPPDATA%\StarIndex\models`. After that no internet connection is needed for search.

### Language models (optional)

Search does not need one. For Ask, Research, summaries and comparison narratives open
**Settings → Models**:

- **Ollama** (`winget install Ollama.Ollama`, then `ollama pull <model>`): add the provider, press
  *Test*, pick a model from the discovered list, set it as default.
- **LM Studio / vLLM / other OpenAI-compatible servers**: add with the server's base URL.
- **Cloud APIs** (OpenAI, Anthropic, Gemini, Azure OpenAI): require *Privacy → Allow cloud models*,
  an API key (stored in Credential Manager) and a confirmation each time repository content would be sent.
- **ChatGPT plan**: *Continue with ChatGPT* in Settings; see [CHATGPT_PLAN.md](CHATGPT_PLAN.md).

**Settings → Hardware** shows the detected GPU and suggests model sizes. With an NVIDIA GPU, Ollama and
LM Studio use it automatically; for GPU embeddings install with `-Gpu`, which replaces the CPU build of
ONNX Runtime with `onnxruntime-gpu` (it needs the CUDA and cuDNN runtime libraries listed in the ONNX
Runtime documentation). Embeddings fall back to the CPU when no CUDA provider is available.

## Verify the installation

After connecting GitHub and (optionally) adding a model in the app:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify.ps1 -Sync
```

This starts StarIndex if needed and checks, against your real account and hardware: launch and web
interface, Credential Manager, the full import of your starred list, indexing and embeddings, search in
all three modes, every configured model provider, a cited answer from the default local model
(`-Research` adds a research run), ChatGPT plan status, GPU detection and the MCP endpoints. The result is
printed and saved as `verification-report.md` in the data directory.

## Run in the background at sign-in

Scheduled synchronisation, indexing and backups run inside StarIndex, so it only has to be running:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register-startup.ps1           # start hidden at sign-in
powershell -ExecutionPolicy Bypass -File scripts\register-startup.ps1 -Remove   # undo
```

This registers a per-user Windows Task Scheduler task (no administrator rights). Intervals are edited
on the Sync page (incremental sync every 6 hours, full reconciliation weekly, backup weekly and daily
cleanup by default).

## Development mode

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

Starts the backend on port 8765 with a separate `.dev-data` directory and the Vite dev server with hot
reload on <http://127.0.0.1:5173>.

## Where things are

```powershell
.venv\Scripts\python -m starindex.cli paths
```

| | Default location |
|---|---|
| Database (repositories, notes, collections, reports, embeddings) | `%LOCALAPPDATA%\StarIndex\starindex.db` |
| Vector index (rebuildable) | `%LOCALAPPDATA%\StarIndex\qdrant` |
| Embedding and reranker models | `%LOCALAPPDATA%\StarIndex\models` |
| Backups | `%LOCALAPPDATA%\StarIndex\backups` |
| Logs (JSON lines, rotating) | `%LOCALAPPDATA%\StarIndex\logs\starindex.log` |
| Secrets | Windows Credential Manager, entries named `StarIndex` |

Set `STARINDEX_DATA_DIR` (or `start.ps1 -DataDir`) to move everything except secrets.

### Backup, restore, export

Backups are consistent copies of the database made while the application runs (Settings → Storage, or
`starindex backup create`). A restore replaces the library with the backup's content and rebuilds the
vector index from the embeddings stored in the database; no re-embedding is needed. To move to another
machine copy the newest file from `backups\` (or the whole data directory with StarIndex stopped) and
sign in to GitHub/model providers again, because secrets stay in the old machine's Credential Manager.
`starindex export --format csv|json` writes a portable list of repositories with your notes, tags and collections.

## Optional: Qdrant as a server

The embedded index needs no setup and is fine for documentation-level indexing of thousands of
repositories. If you deep-index a great deal of source code, run Qdrant as a server:

```powershell
docker compose up -d qdrant
scripts\start.ps1 -QdrantUrl http://127.0.0.1:6333
```

then press *Rebuild vector index* on the Sync page. This needs Docker Desktop with the WSL 2 backend.
(Qdrant also publishes a native Windows binary; any server reachable at the URL works.)

## Environment variables

| Variable | Purpose |
|---|---|
| `STARINDEX_DATA_DIR` | data directory |
| `STARINDEX_PORT`, `STARINDEX_HOST` | listen address (default `127.0.0.1:8765`) |
| `STARINDEX_ACCESS_TOKEN` | required when `STARINDEX_HOST` is not loopback; clients send `Authorization: Bearer …` or open `/?token=…` once |
| `STARINDEX_QDRANT_URL` | use a Qdrant server instead of the embedded index |
| `STARINDEX_GITHUB_API_URL`, `STARINDEX_GITHUB_RAW_URL` | GitHub Enterprise Server or a test server |
| `STARINDEX_MODELS_DIR` | where embedding/reranker models are cached |
| `STARINDEX_LOG_LEVEL` | `INFO` (default) or `DEBUG` |
| `STARINDEX_DISABLE_KEYRING` | `1` stores secrets in a user-only file instead of the OS keyring |

## Troubleshooting

| Symptom | What to do |
|---|---|
| `running scripts is disabled on this system` | use the `powershell -ExecutionPolicy Bypass -File …` form shown above |
| Port 8765 in use | `scripts\start.ps1 -Port 8770`, or `scripts\stop.ps1` to stop an old instance |
| "The vector index is in use by another StarIndex process" | only one process can open the embedded index: stop the other instance |
| Semantic search says the index is being built | watch Sync → queue; keyword search works meanwhile |
| Embedding model cannot be downloaded | check the connection to huggingface.co once; afterwards it is cached. Offline mode never downloads |
| GitHub rate limit reached | jobs pause until the limit resets and continue by themselves; a token gives 5,000 requests per hour |
| A repository shows "not accessible" | it was deleted, made private or blocked on GitHub; your notes and collections for it are kept |
| Something else | `starindex doctor`, Settings → Diagnostics, and `logs\starindex.log` |

## Verification status of this document

The PowerShell scripts were written for Windows PowerShell 5.1 and PowerShell 7 but were authored on
Linux and have not yet been executed on a Windows machine. The application itself (the Python package,
`starindex serve`, the web interface) was run and tested on Linux; its Windows-specific paths
(Credential Manager through `keyring`, `%LOCALAPPDATA%` through `platformdirs`) use the standard
libraries for that purpose. If a script misbehaves, the manual equivalent is:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e .
cd frontend; npm ci; npm run build; cd ..
.venv\Scripts\python -m starindex.cli serve --open
```
