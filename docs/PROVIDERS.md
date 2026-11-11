# Model providers

StarIndex talks to language models through one contract (`backend/starindex/providers/base.py`).
Ask, Research, Compare, summaries and classification call `ModelRouter.resolve(task)` and then
`provider.stream(...)` / `provider.generate_json(...)`; they never know which vendor is behind it.
Search does not use this layer at all, so it works with no model configured.

## Layout

| File | Role |
|---|---|
| `base.py` | Types (`GenerationRequest`, `StreamEvent`, `ProviderCapabilities`, `ProviderError`) and the `ChatProvider` base class |
| `http.py` | `httpx` client construction, SSE/NDJSON parsing, status → error mapping, base-URL validation, local-host detection |
| `openai_compat.py` | Chat Completions adapter for `openai`, `lmstudio`, `vllm`, `openai_compat` |
| `azure_openai.py` | Azure OpenAI (v1 API by default; deployment-scoped paths when `api_version` is set) |
| `ollama.py` | Ollama native API |
| `anthropic.py` | Anthropic Messages API |
| `gemini.py` | Google Generative Language API |
| `chatgpt_plan/` | Sign in with ChatGPT: OAuth (`oauth.py`), accounts and tokens (`service.py`), Responses inference (`client.py`) — see [CHATGPT_PLAN.md](CHATGPT_PLAN.md) |
| `registry.py` | Provider templates, `ProviderStore` (SQLite rows + secret store), `build_provider` factory |
| `router.py` | `ModelRouter`: task routing, privacy enforcement, explicit switching, usage log |
| `pricing.py` | Cost estimates |
| `hardware.py` | GPU/CPU detection and local-model guidance |

Everything is synchronous. Streams are generators; FastAPI runs them in its thread pool. No vendor SDKs
are used, which keeps the Windows install small and gives every adapter the same streaming and error path.

## Capability matrix

| Kind | Local | Auth | Discovery | Structured output | Temperature | Output cap | Embeddings |
|---|---|---|---|---|---|---|---|
| `ollama` | yes | none (optional bearer for a proxy) | `/api/tags` | JSON schema via `format` | yes | `num_predict` | `/api/embed` |
| `lmstudio`, `vllm`, `openai_compat` | by address | optional bearer | `/models` | negotiated: `json_schema` → `json_object` → prompt | yes, dropped if rejected | `max_tokens` | `/embeddings` |
| `openai` | no | bearer key | `/models` | negotiated, as above | yes, dropped if the model rejects it | `max_completion_tokens` | `/embeddings` |
| `azure_openai` | no | `api-key` header | deployment names from options, else `/openai/v1/models` | negotiated, as above | as above | `max_completion_tokens` | yes |
| `anthropic` | no | `x-api-key` + `anthropic-version: 2026-10-21` | `/v1/models` | JSON outputs (`output_config.format`), prompt-only for models without it | **never sent** | `max_tokens` (+ thinking headroom) | no |
| `gemini` | no | `x-goog-api-key` | `/models` | `responseSchema` → JSON mode → prompt | yes | `maxOutputTokens` | not implemented |
| `chatgpt_plan` | no | OAuth access token | `/v1/models` (`models[]`, `visibility == "list"`) | prompt only | **never sent** | **never sent** | no |

Notes that matter in practice:

- **Structured output is one abstraction.** Callers set `GenerationRequest.json_schema` and call
  `generate_json`; each adapter uses the strongest mechanism it has and the base class parses and repairs
  once. The research agent uses this for tool selection, so no provider needs native tool calling.
- **Anthropic.** Current Claude models reject sampling parameters and forced tool choice, so the adapter
  sends neither; structured output uses JSON outputs. Thinking stays at the model default and shares the
  `max_tokens` cap with the answer, so the adapter adds headroom (`options.thinking_headroom_tokens`,
  default 8192) on top of the caller's output budget. `options.effort` maps to `output_config.effort`.
  A safety refusal raises `ProviderError('refused')`. Server-side refusal fallbacks
  (`options.refusal_fallback: true`) are off by default because they change the serving model.
- **OpenAI-compatible servers disagree on details.** A 400 that blames `response_format`, `stream_options`,
  `temperature` or the token-cap parameter makes the adapter drop or swap that one parameter, retry once and
  remember the result for the life of the provider instance. That is parameter negotiation with the same
  provider and model, not a fallback.
- **Azure.** Enter the resource endpoint (`https://NAME.openai.azure.com`). The model name is your
  deployment name; list deployments in `options.deployments`. Set `options.api_version` only if you need
  the older `/openai/deployments/{name}/...` paths.
- **Gemini** was implemented from the published API shape and is verified against mocks only.
- **Streams must finish.** Every adapter raises `stream_incomplete` when a stream ends without the
  provider's terminal marker (`[DONE]`/finish reason, `done: true`, `message_stop`, a `finishReason`,
  `response.completed`), so a truncated answer is never presented as complete.

## Errors

Adapters raise `ProviderError` with a normalised `kind`:
`not_configured`, `cloud_disabled`, `confirmation_required`, `auth`, `consent_required`, `not_eligible`,
`rate_limit`, `usage_limit`, `timeout`, `network`, `unavailable`, `unsupported`, `context_length`,
`bad_request`, `stream_incomplete`, `invalid_response`, `refused`. The error carries the HTTP status, the
provider's own code, the request id when one was returned, `retryable`, `retry_after_s`, and for usage
limits an `action_url`/`action_label`.

## Routing and privacy

`settings.routing` maps each task (`classify`, `summarize`, `expand_query`, `answer`, `reasoning`,
`compare`) to a provider and model, with a shared default. A request may carry an explicit override.

- A provider is **cloud** when its kind is a hosted API (`openai`, `anthropic`, `gemini`, `azure_openai`,
  `chatgpt_plan`) or when its address is outside this machine and private networks. A vLLM box on your LAN is
  local; an OpenAI-compatible gateway on the internet is cloud.
- Cloud requests raise `cloud_disabled` while `privacy.allow_cloud_models` is off (the default) or offline
  mode is on, and `confirmation_required` unless the request carries `confirm_cloud` (or the user turned
  confirmation off). The provider settings routes refuse to contact a cloud provider at all in that state,
  including for model listing and connection tests.
- **There is no fallback.** When a provider fails, the error is returned together with the list of
  providers the user may switch to (`ModelRouter.options()`); nothing is retried elsewhere.
- Endpoints for hosted kinds are fixed, so a stored API key can only ever be sent to its vendor. Keys live
  in the OS keyring (or the owner-only secrets file) and are never returned by the API. Credentials are
  rejected in base URLs and in extra headers.

## Usage and cost

Every model call is appended to `usage_log` through `ModelRouter.record_usage`. Token counts come from the
provider when reported and are marked `estimated` otherwise. A dollar estimate appears only when a price is
known: your own entries in `settings.pricing` (matched by longest model prefix), or the bundled Anthropic
list (snapshot 2026-11-03, standard rates; the Claude Haiku 5.5 long-prompt tier is modelled; cache and batch
discounts are not). No OpenAI, Gemini or Azure prices are bundled, so those show tokens only until you add
prices. ChatGPT plan usage is never priced per token.

## Hardware

`GET /api/system/hardware` reports OS, CPU threads, RAM, NVIDIA GPUs (`nvidia-smi`, no shell) and ONNX
Runtime execution providers, with guidance for CUDA and CPU-only machines. Local servers decide GPU use
themselves; StarIndex only discovers the models they expose.

## Adding a provider

1. Subclass `HttpChatProvider` (or `OpenAICompatProvider` if the API is Chat Completions–shaped). Implement
   `capabilities()`, `list_models()` and `stream()`; override `_headers()` for authentication and
   `_refine_error()` for provider-specific error codes. Raise `stream_incomplete` if the stream ends early.
2. Add the kind to `ProviderKind` and `LOCAL_KINDS`/`CLOUD_KINDS` in `base.py`.
3. Add a `ProviderTemplate` and a branch in `build_provider` in `registry.py`.
4. Add tests with `httpx.MockTransport` covering discovery, streaming, structured output, auth, rate limit,
   timeout, context length and a cut-off stream (copy `backend/tests/providers/test_ollama.py`).

## Tests

```bash
python -m pytest backend/tests/providers -q
```

All provider tests use mock transports and a loopback listener; none use the network. Live checks against a
real server are opt-in:

```bash
STARINDEX_TEST_OLLAMA_URL=http://127.0.0.1:11434 python -m pytest backend/tests/providers/test_live.py -m live
```

`STARINDEX_TEST_OPENAI_COMPAT_URL` (plus optional `..._MODEL`, `..._KEY`) does the same for LM Studio, vLLM
or any compatible server.
