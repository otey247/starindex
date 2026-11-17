# Live verification — 2026-11-17

The release verification is **incomplete**. No additional product extensions were started.

## Executed checks

| Gate | Evidence | Result |
|---|---|---|
| Windows installer and launcher | [Windows CI](https://github.com/otey247/starindex/actions/runs/44021627972), both Windows PowerShell 5.1 and PowerShell 7, Python 3.12.10, Node.js 22 | Passed: install, build, launch with `-NoBrowser`, HTTP health, UI, live worker, WinVaultKeyring backend, stop, verification wrapper/report/exit code |
| Owner's real starred list | Authenticated as `otey247` through GitHub CLI; first live full sync | Passed: 8,613 expected and imported, 87 pages, 2026-11-17 04:40:08–04:44:22 UTC |
| Full-library indexing | Snapshot at 05:18:17 UTC | Incomplete: 1,016 indexed repositories, 7,597 unfinished; 16,391 documents, 123,085 passages; 5,163 repository vectors and no passage vectors yet; no failed jobs |
| Live Ollama | Ollama 0.40.16, Qwen2.5 7.6B Instruct Q4_K_M, native `/api/chat` | Passed streaming, usage reporting and JSON-schema generation tests against the actual server |
| Ask citation integrity | Live answer with the 7.6B model, 302 seconds | Five distinct citation IDs resolve to eleven evidence items; no unknown references |
| Ask factual support | Direct review of the answer against its retrieved metadata | **Failed**: reversed star comparison and an incorrect fork count; see below |
| Research with the larger model | Same Ollama provider and real indexed library; 399 seconds, four tool calls | Completed with eight resolving citation IDs across thirteen evidence items; strict factual support review did not pass |
| ChatGPT sign-in and inference | Real authorization request reached `auth.openai.com/log-in` with HTTP 200 | Incomplete: no user sign-in, issued tokens or live inference; login-page access is not a sign-in |
| GPU embeddings | Linux host reports no GPU and only Azure/CPU ONNX providers | Not executed; CUDA availability and GPU kernel execution/throughput remain unverified |

The connected Office-PC was offline. Windows hosted runners verify the scripts, but do not verify
that desktop's browser, GPU, CMD shortcut or Task Scheduler configuration.

## Model test conditions and quality review

The Linux VM has a four-core CPU quota, no GPU and approximately 16 GiB of usable RAM. Background
indexing was paused for the repeated model checks and is resumed afterwards. The provider used an
8,192-token context, a 900-second read timeout, a 6,000-character evidence budget, at most four
research tool calls and at most 900 output tokens. These are bounded smoke checks, not a library-wide
quality evaluation or a GPU performance benchmark.

The GGUF was imported from `bartowski/Qwen2.5-7B-Instruct-GGUF` after checking its published SHA-256:
`65b8fcd92af6b4fefa935c625d1ac27ea29dcb6ee14589c55a8f115ceaaa1423`.

The Ask answer stated that Qdrant's **35,598** stars exceeded Milvus's **46,676**, and changed
Qdrant's **2,820** forks into **28,200**. All necessary values were present in its cited E1/E3
metadata. Resolving citation identifiers did not establish factual correctness. The verifier now
labels this automatic check *citation integrity* and requires a separate factual-support review.

Research's opening sentence attributed ANN search to both repositories, but its selected Qdrant
E3 excerpt described a vector database/search engine without establishing ANN search. The bounded
evidence also omitted Qdrant's known license and maintenance fields, so the report marked them
unknown. This run verifies live transport, research execution and citation references; it does not
establish reliable claim grounding or complete comparison coverage. The four-call budget was reached.

## Repairs made during verification

- Prefer the configured Python on `PATH` in the Windows installer and retain dependency-resolution
  output. Both PowerShell editions now execute successfully with Python 3.12.
- Follow the requested sync job instead of accepting an earlier successful run. `--sync` also waits
  for indexing and embedding jobs within one deadline; incomplete verification returns exit 2.
- Schedule embeddings ahead of the bulk documentation backlog, including already queued work through
  a migration. Interactive index requests promote their existing background jobs without duplicates.
- Inherit the configured provider timeout unless a request explicitly overrides it. The previous
  180-second request default masked the provider setting and timed out the first large-model Ask.
- Wait for native embedding batches to reach a checkpoint before closing worker resources.
- Distinguish available CUDA providers and stored ChatGPT sessions from actual GPU execution and
  live ChatGPT inference.

Local library databases, model binaries, account material and raw execution artifacts are outside
the repository. Verification reports and model evidence are retained in the isolated data directory.

## Automated validation

- Full backend suite: 375 passed, 3 skipped; the final verifier-specific run: 11 passed.
- Native Ollama live tests: 1 passed; the separate OpenAI-compatible live test was skipped.
- Frontend: 130 tests passed; production build and regenerated API typecheck passed.
- Windows workflow passed in both shells, including verification of the incomplete exit code.
- Changed Python files passed Ruff; workflow passed actionlint; patch passed `git diff --check`.

These automated checks do not close the factual-support failures or the unexecuted hardware and
interactive authentication gates above.
