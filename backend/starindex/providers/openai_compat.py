"""OpenAI-style Chat Completions adapter.

Serves four provider kinds that share the wire format but not the details:

* ``openai``         - api.openai.com (paid; ``max_completion_tokens``)
* ``lmstudio``       - LM Studio's local server
* ``vllm``           - vLLM's OpenAI-compatible server
* ``openai_compat``  - any other compatible endpoint (llama.cpp server, LiteLLM, gateways...)

Servers disagree about which optional parameters they accept, so the adapter negotiates:
structured output degrades ``json_schema`` -> ``json_object`` -> prompt-only, and optional
parameters a server rejects with a 400 are dropped once and remembered for this instance.
Model identifiers are always discovered from ``/models``; none are hard-coded.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any, Literal

import httpx

from .base import (
    ErrorKind,
    GenerationRequest,
    ModelInfo,
    ProviderCapabilities,
    ProviderConfig,
    ProviderError,
    StreamEvent,
    Usage,
    schema_instruction,
)
from .http import HttpChatProvider, is_local_url, iter_sse, join_url, validate_base_url

DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "lmstudio": "http://127.0.0.1:1234/v1",
    "vllm": "http://127.0.0.1:8000/v1",
}
StructuredLevel = Literal["json_schema", "json_object", "prompt"]
_LEVELS: tuple[StructuredLevel, ...] = ("json_schema", "json_object", "prompt")

_EMBED_HINT = re.compile(r"embed|bge-|e5-|gte-|minilm|nomic", re.IGNORECASE)
_RERANK_HINT = re.compile(r"rerank", re.IGNORECASE)
_NON_CHAT_HINT = re.compile(
    r"whisper|tts|dall-e|moderation|transcribe|realtime|audio|image|sora|speech|davinci-00|babbage-00",
    re.IGNORECASE,
)
_RESERVED_HEADERS = {"authorization", "api-key", "x-api-key", "content-type", "host", "content-length"}


def classify_model(model_id: str) -> Literal["chat", "embedding", "rerank", "unknown"]:
    if _RERANK_HINT.search(model_id):
        return "rerank"
    if _EMBED_HINT.search(model_id):
        return "embedding"
    if _NON_CHAT_HINT.search(model_id):
        return "unknown"
    return "chat"


def normalise_v1_base(url: str) -> str:
    """Append ``/v1`` when the user entered only scheme://host[:port]."""
    cleaned = validate_base_url(url)
    parts = httpx.URL(cleaned)
    if parts.path in ("", "/"):
        return cleaned + "/v1"
    return cleaned


class OpenAICompatProvider(HttpChatProvider):
    def __init__(
        self,
        config: ProviderConfig,
        secret: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        super().__init__(config, secret, transport=transport)
        self._base = self._resolve_base_url()
        self._structured_level: StructuredLevel = "json_schema"
        # Optional request parameters this server rejected (remembered for the instance).
        self._dropped: set[str] = set()
        self._token_param = "max_completion_tokens" if config.kind in ("openai", "azure_openai") else "max_tokens"

    # -- endpoint layout (Azure overrides these) -------------------------------------------

    def _resolve_base_url(self) -> str:
        url = self.config.base_url or DEFAULT_BASE_URLS.get(self.config.kind)
        if not url:
            raise self._error("not_configured", f"{self.config.name} has no base URL configured.")
        return normalise_v1_base(url)

    @property
    def base_url(self) -> str:
        return self._base

    def _chat_url(self, model: str) -> str:
        return join_url(self._base, "chat/completions")

    def _embeddings_url(self, model: str) -> str:
        return join_url(self._base, "embeddings")

    def _models_url(self) -> str:
        return join_url(self._base, "models")

    def _query(self) -> dict[str, Any] | None:
        return None

    def _auth_headers(self) -> dict[str, str]:
        if self.config.kind == "openai":
            return {"Authorization": f"Bearer {self._require_secret()}"}
        return {"Authorization": f"Bearer {self._secret}"} if self._secret else {}

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        extra = self.config.options.get("headers")
        if isinstance(extra, dict):
            for key, value in extra.items():
                if isinstance(key, str) and isinstance(value, str) and key.lower() not in _RESERVED_HEADERS:
                    headers[key] = value
        headers.update(self._auth_headers())
        return headers

    # -- contract ---------------------------------------------------------------------------

    def capabilities(self) -> ProviderCapabilities:
        local = is_local_url(self._base)
        return ProviderCapabilities(
            is_local=local,
            is_paid=self.config.kind in ("openai", "azure_openai") or not local,
            streaming=True,
            model_discovery=True,
            embeddings=True,
            structured_output=self._structured_level,
            accepts_temperature="temperature" not in self._dropped,
            accepts_max_output_tokens=True,
            notes=self._capability_notes(local),
        )

    def _capability_notes(self, local: bool) -> list[str]:
        notes = ["Structured output is negotiated: JSON schema, then JSON mode, then prompt-only."]
        if not local and self.config.kind == "openai_compat":
            notes.append("This endpoint is not on your machine or local network, so it is treated as a cloud provider.")
        return notes

    def list_models(self) -> list[ModelInfo]:
        payload = self._request_json("GET", self._models_url(), params=self._query())
        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise self._error("invalid_response", "The server's model list has an unexpected shape.")
        models: list[ModelInfo] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            model_id = str(item["id"])
            context = item.get("context_window") or item.get("max_model_len") or item.get("context_length")
            models.append(
                ModelInfo(
                    id=model_id,
                    display_name=item.get("display_name") or None,
                    kind=classify_model(model_id),
                    context_window=int(context) if isinstance(context, int | float) else None,
                    details={k: item[k] for k in ("owned_by", "created") if k in item},
                )
            )
        models.sort(key=lambda m: (m.kind != "chat", m.id.lower()))
        return models

    def stream(self, request: GenerationRequest) -> Iterator[StreamEvent]:
        # Negotiate optional parameters: each 400 that names one drops it and retries once.
        for _ in range(6):
            body = self._build_body(request)
            try:
                yield from self._stream_once(request, body)
                return
            except _Renegotiate:
                continue
        raise self._error("bad_request", "The server kept rejecting the request parameters.")

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = self._request_json(
            "POST", self._embeddings_url(model), json_body={"model": model, "input": texts},
            params=self._query(), timeout_s=self._timeout_s,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise self._error("invalid_response", "The embeddings response does not match the request.")
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        return [list(map(float, d["embedding"])) for d in ordered]

    # -- request construction ---------------------------------------------------------------

    def _build_body(self, request: GenerationRequest) -> dict[str, Any]:
        system = request.system or ""
        if request.json_schema is not None:
            system = (system + "\n\n" if system else "") + schema_instruction(request.json_schema)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend({"role": m.role, "content": m.content} for m in request.messages)

        body: dict[str, Any] = {"model": request.model, "messages": messages, "stream": True}
        if "stream_options" not in self._dropped:
            body["stream_options"] = {"include_usage": True}
        if request.temperature is not None and "temperature" not in self._dropped:
            body["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            body[self._token_param] = request.max_output_tokens
        if request.json_schema is not None:
            if self._structured_level == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": _safe_name(request.json_schema_name), "schema": request.json_schema},
                }
            elif self._structured_level == "json_object":
                body["response_format"] = {"type": "json_object"}
        return body

    def _renegotiate(self, error: ProviderError, body: dict[str, Any]) -> bool:
        """Adjust instance state after a 400 that blames an optional parameter. True = retry."""
        if error.status not in (400, 404, 422) or error.kind in ("auth", "context_length"):
            return False
        text = f"{error.message} {error.param or ''} {error.code or ''}".lower()
        if "response_format" in body and re.search(r"response_format|json_schema|json_object|json mode|structured|schema", text):
            index = _LEVELS.index(self._structured_level)
            if index + 1 < len(_LEVELS):
                self._structured_level = _LEVELS[index + 1]
                return True
            return False
        if "stream_options" in body and ("stream_options" in text or "include_usage" in text):
            self._dropped.add("stream_options")
            return True
        if "temperature" in body and "temperature" in text:
            self._dropped.add("temperature")
            return True
        if "max_completion_tokens" in text and self._token_param == "max_tokens" and "max_tokens" in body:
            self._token_param = "max_completion_tokens"
            return True
        if "max_tokens" in text and self._token_param == "max_completion_tokens" and "max_completion_tokens" in body:
            self._token_param = "max_tokens"
            return True
        return False

    # -- streaming --------------------------------------------------------------------------

    def _stream_once(self, request: GenerationRequest, body: dict[str, Any]) -> Iterator[StreamEvent]:
        usage: Usage | None = None
        finish: str | None = None
        saw_done = False
        emitted = False
        try:
            with self._open_stream(
                self._chat_url(request.model), json_body=body, params=self._query(), timeout_s=request.timeout_s
            ) as response:
                for message in iter_sse(response.iter_lines()):
                    data = message.data.strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        saw_done = True
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    if chunk.get("error"):
                        raise self._stream_error(chunk)
                    if isinstance(chunk.get("usage"), dict):
                        usage = _usage(chunk["usage"])
                    for choice in chunk.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if isinstance(delta, str) and delta:
                            emitted = True
                            yield StreamEvent(type="delta", text=delta)
                        if choice.get("finish_reason"):
                            finish = str(choice["finish_reason"])
        except ProviderError as error:
            if not emitted and self._renegotiate(error, body):
                raise _Renegotiate() from error
            raise
        if not saw_done and finish is None:
            raise self._error(
                "stream_incomplete", "The stream ended before the model finished its response.", retryable=True
            )
        if finish == "content_filter":
            raise self._error("refused", "The provider's content filter blocked this response.", code="content_filter")
        yield StreamEvent(type="completed", usage=usage, finish_reason=_finish(finish))

    def _stream_error(self, chunk: dict[str, Any]) -> ProviderError:
        err = chunk["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        code = str(err.get("code") or err.get("type") or "") if isinstance(err, dict) else None
        text = f"{message} {code}".lower()
        kind: ErrorKind = "unavailable"
        if "rate" in text and "limit" in text:
            kind = "rate_limit"
        elif "context" in text or "too long" in text or "too many tokens" in text:
            kind = "context_length"
        elif "quota" in text or "billing" in text:
            kind = "usage_limit"
        return self._error(kind, f"The provider reported an error mid-stream: {message}", code=code or None)

    def _refine_error(self, status: int, message: str, code: str | None, body: Any) -> tuple[ErrorKind, bool] | None:
        text = f"{message} {code or ''}".lower()
        if status == 404 and "model" in text:
            return "unsupported", False
        if code in ("invalid_api_key", "incorrect_api_key"):
            return "auth", False
        if code == "insufficient_quota":
            return "usage_limit", False
        if code == "context_length_exceeded":
            return "context_length", False
        if code in ("model_not_found",):
            return "unsupported", False
        return None


class _Renegotiate(Exception):
    """Internal: request parameters were adjusted; rebuild the body and try again."""


def _usage(raw: dict[str, Any]) -> Usage:
    prompt = raw.get("prompt_tokens")
    completion = raw.get("completion_tokens")
    return Usage(
        input_tokens=int(prompt) if isinstance(prompt, int | float) else None,
        output_tokens=int(completion) if isinstance(completion, int | float) else None,
    )


def _finish(reason: str | None) -> str | None:
    return {"stop": "stop", "length": "length", "tool_calls": "stop", "eos": "stop"}.get(reason or "", reason)


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]
    return cleaned or "result"
