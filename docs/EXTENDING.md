# Extending StarIndex

## Add a model provider

1. Implement `ChatProvider` from `backend/starindex/providers/base.py`: `capabilities()`, `list_models()`,
   `stream()` (yield `delta` events and exactly one `completed`; raise `ProviderError` with the right
   `kind`; raise `stream_incomplete` when the stream ends without the provider's terminal marker), and
   optionally `embed()`.
2. Register it in `providers/registry.py`: a `ProviderTemplate` (label, default URL, whether a key is
   needed, option fields) and a branch in `build_provider`.
3. Add tests with `httpx.MockTransport` next to the existing ones in `backend/tests/providers/`.

Nothing else changes: routing, privacy checks, usage logging, the settings UI and the agent workflows
work through the contract. See [PROVIDERS.md](PROVIDERS.md) for the capability matrix.

## Add a research tool

In `backend/starindex/agent/tools.py`: define a Pydantic argument model, write
`fn(state: ToolState, args) -> ToolResult` (add evidence through `state.evidence`, return a compact text
summary), and add a `ToolSpec` to `TOOLS`. The planner prompt, validation, trace and limits pick it up.
Keep tools read-only over local data; anything that could modify the host does not belong here.

## Add an embedding backend

Subclass `EmbeddingBackend` in `backend/starindex/search/embeddings.py`, add the name to
`EmbeddingSettings.backend` and a branch in `EmbeddingService.backend()`. The model key
(`backend:model`) versions the vectors; switching builds a new index in the background.

## Add or change categories

Categories are rows in the `categories` table (Collections → Categories in the UI). Built-in ones are
seeded from `BUILTIN_CATEGORIES` in `backend/starindex/indexing/classify.py`; keywords drive the rule
classifier and the name + description are embedded for the similarity classifier. User assignments and
removals are never overwritten.

## Index more file types

`backend/starindex/indexing/selection.py` decides what is indexed (`classify_path`) and
`chunking.chunk_document` picks the chunker. New manifest formats go into `indexing/manifests.py`.

## Change the database

Add `backend/starindex/db/migrations/NNNN_description.sql`. Migrations run in filename order inside a
transaction at start-up. Never edit an applied migration.

## Change the HTTP API

Edit `backend/starindex/api/schemas.py` and the route module, then regenerate the frontend types:

```bash
python scripts/export_schemas.py && (cd frontend && npm run gen:api)
```

## Tune ranking

Fusion weights and the reranker blend are constants at the top of
`backend/starindex/search/service.py`; user-facing weights are in `SearchSettings`. Change them only
together with a run of `python eval/run_eval.py --ablations` and record the numbers in
[EVALUATION.md](EVALUATION.md).

## Useful development tools

- `python scripts/demo_instance.py` — StarIndex against a local fake GitHub serving the evaluation corpus,
  with an optional scripted LLM (`--with-llm`).
- `starindex.testing.fake_github.FakeGitHub` — pagination, ETags, rate limits, renames, deletions, failures.
- `starindex.testing.fake_llm.FakeLLM` — deterministic OpenAI-compatible model, including a model that
  behaves as if prompt injection succeeded.
