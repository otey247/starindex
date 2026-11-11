"""Google Gemini adapter (Generative Language API, ``v1beta``).

Uses ``x-goog-api-key`` authentication, ``GET /models`` for discovery and
``POST /models/{model}:streamGenerateContent?alt=sse`` for generation, with the system
prompt in ``systemInstruction`` and structured output through
``generationConfig.responseMimeType`` + ``responseSchema``.

Verification note: Google's documentation was not reachable from the build environment, so
this adapter is implemented from the published API shape and verified against mocked
responses only. Structured output therefore degrades defensively: a 400 that blames the
schema drops to JSON mode, and a 400 that blames JSON mode drops to prompt-only.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any, Literal
from urllib.parse import quote

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
from .http import HttpChatProvider, iter_sse, join_url, validate_base_url

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
StructuredLevel = Literal["json_schema", "json_object", "prompt"]
_LEVELS: tuple[StructuredLevel, ...] = ("json_schema", "json_object", "prompt")
# responseSchema accepts an OpenAPI-style subset; these JSON Schema keywords are not part of it.
_DROP_KEYS = {"additionalProperties", "$schema", "$id", "title", "default", "examples", "const", "$defs", "definitions"}
_STATUS_KINDS: dict[str, tuple[ErrorKind, bool]] = {
    "UNAUTHENTICATED": ("auth", False),
    "PERMISSION_DENIED": ("auth", False),
    "RESOURCE_EXHAUSTED": ("rate_limit", True),
    "NOT_FOUND": ("unsupported", False),
    "UNAVAILABLE": ("unavailable", True),
    "DEADLINE_EXCEEDED": ("timeout", True),
    "INTERNAL": ("unavailable", True),
    "FAILED_PRECONDITION": ("not_eligible", False),
}
_BLOCKING_FINISH = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}


def prepare_schema(schema: dict[str, Any]) -> dict[str, Any]:
    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _DROP_KEYS:
                continue
            if key == "type" and isinstance(value, list):
                non_null = [t for t in value if t != "null"]
                out["type"] = non_null[0] if non_null else "string"
                if "null" in value:
                    out["nullable"] = True
                continue
            if key == "properties" and isinstance(value, dict):
                out[key] = {name: walk(sub) for name, sub in value.items()}
                continue
            out[key] = walk(value)
        return out

    return walk(copy.deepcopy(schema))


def model_path(model: str) -> str:
    name = model if model.startswith("models/") or model.startswith("tunedModels/") else f"models/{model}"
    return quote(name, safe="/")


class GeminiProvider(HttpChatProvider):
    def __init__(
        self,
        config: ProviderConfig,
        secret: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        super().__init__(config, secret, transport=transport)
        self._structured_level: StructuredLevel = "json_schema"

    @property
    def base_url(self) -> str:
        return validate_base_url(self.config.base_url or DEFAULT_BASE_URL, provider_kind="gemini")

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._require_secret()}

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            is_local=False,
            is_paid=True,
            streaming=True,
            model_discovery=True,
            embeddings=False,
            structured_output=self._structured_level,
            notes=[
                "Verified against mocked responses only; Google's docs were unreachable at build time.",
                "Structured output degrades from response schema to JSON mode to prompt-only if rejected.",
            ],
        )

    def list_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        params: dict[str, Any] = {"pageSize": 1000}
        for _ in range(20):
            payload = self._request_json("GET", join_url(self.base_url, "models"), params=params)
            items = payload.get("models") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise self._error("invalid_response", "Gemini returned an unexpected model list.")
            for item in items:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                methods = item.get("supportedGenerationMethods") or []
                if "generateContent" in methods:
                    kind: Literal["chat", "embedding", "rerank", "unknown"] = "chat"
                elif "embedContent" in methods or "batchEmbedContents" in methods:
                    kind = "embedding"
                else:
                    kind = "unknown"
                name = str(item["name"])
                models.append(
                    ModelInfo(
                        id=name.removeprefix("models/"),
                        display_name=item.get("displayName") or None,
                        kind=kind,
                        context_window=item.get("inputTokenLimit") if isinstance(item.get("inputTokenLimit"), int) else None,
                        details={
                            k: v
                            for k, v in {
                                "max_output_tokens": item.get("outputTokenLimit"),
                                "version": item.get("version"),
                            }.items()
                            if v is not None
                        },
                    )
                )
            token = payload.get("nextPageToken")
            if not token:
                break
            params = {"pageSize": 1000, "pageToken": token}
        models.sort(key=lambda m: (m.kind != "chat", m.id.lower()))
        return models

    # -- generation -------------------------------------------------------------------------

    def _build_body(self, request: GenerationRequest) -> dict[str, Any]:
        contents = [
            {"role": "user" if m.role == "user" else "model", "parts": [{"text": m.content}]}
            for m in request.messages
        ]
        system = request.system or ""
        config: dict[str, Any] = {}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            config["maxOutputTokens"] = request.max_output_tokens
        if request.json_schema is not None:
            system = (system + "\n\n" if system else "") + schema_instruction(request.json_schema)
            if self._structured_level in ("json_schema", "json_object"):
                config["responseMimeType"] = "application/json"
            if self._structured_level == "json_schema":
                config["responseSchema"] = prepare_schema(request.json_schema)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if config:
            body["generationConfig"] = config
        return body

    def stream(self, request: GenerationRequest) -> Iterator[StreamEvent]:
        for _ in range(len(_LEVELS)):
            body = self._build_body(request)
            try:
                yield from self._stream_once(request, body)
                return
            except _Degrade:
                continue
        raise self._error("bad_request", "Gemini kept rejecting the structured-output settings.")

    def _stream_once(self, request: GenerationRequest, body: dict[str, Any]) -> Iterator[StreamEvent]:
        url = join_url(self.base_url, f"{model_path(request.model)}:streamGenerateContent")
        usage: Usage | None = None
        finish: str | None = None
        emitted = False
        try:
            with self._open_stream(url, json_body=body, params={"alt": "sse"}, timeout_s=request.timeout_s) as response:
                for message in iter_sse(response.iter_lines()):
                    if not message.data:
                        continue
                    try:
                        chunk = json.loads(message.data)
                    except ValueError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    if chunk.get("error"):
                        raise self._payload_error(chunk["error"])
                    feedback = chunk.get("promptFeedback") or {}
                    if feedback.get("blockReason"):
                        raise self._error(
                            "refused", f"Gemini blocked the prompt ({feedback['blockReason']}).",
                            code=str(feedback["blockReason"]),
                        )
                    meta = chunk.get("usageMetadata")
                    if isinstance(meta, dict):
                        usage = Usage(
                            input_tokens=_int(meta.get("promptTokenCount")),
                            output_tokens=_plus(meta.get("candidatesTokenCount"), meta.get("thoughtsTokenCount")),
                        )
                    for candidate in chunk.get("candidates") or []:
                        for part in (candidate.get("content") or {}).get("parts") or []:
                            if part.get("thought"):
                                continue
                            text = part.get("text")
                            if isinstance(text, str) and text:
                                emitted = True
                                yield StreamEvent(type="delta", text=text)
                        if candidate.get("finishReason"):
                            finish = str(candidate["finishReason"])
        except ProviderError as error:
            if not emitted and self._degrade(error, body):
                raise _Degrade() from error
            raise
        if finish is None:
            raise self._error(
                "stream_incomplete", "Gemini's stream ended without a finish reason.", retryable=True
            )
        if finish in _BLOCKING_FINISH:
            raise self._error("refused", f"Gemini stopped the response ({finish}).", code=finish)
        yield StreamEvent(
            type="completed", usage=usage, finish_reason={"STOP": "stop", "MAX_TOKENS": "length"}.get(finish, finish.lower())
        )

    def _degrade(self, error: ProviderError, body: dict[str, Any]) -> bool:
        config = body.get("generationConfig") or {}
        if error.status != 400 or error.kind != "bad_request" or "responseMimeType" not in config:
            return False
        text = error.message.lower()
        if not any(token in text for token in ("schema", "response_mime_type", "responsemimetype", "json mode", "mime")):
            return False
        index = _LEVELS.index(self._structured_level)
        if index + 1 >= len(_LEVELS):
            return False
        self._structured_level = _LEVELS[index + 1]
        return True

    def _payload_error(self, error: Any) -> ProviderError:
        status = str(error.get("status") or "") if isinstance(error, dict) else ""
        message = error.get("message") if isinstance(error, dict) else str(error)
        kind, retryable = _STATUS_KINDS.get(status, ("unavailable", True))
        return self._error(kind, f"Gemini reported an error mid-stream: {message}", code=status or None, retryable=retryable)

    def _refine_error(self, status: int, message: str, code: str | None, body: Any) -> tuple[ErrorKind, bool] | None:
        text = message.lower()
        if "api key not valid" in text or "api_key_invalid" in text or "api key expired" in text:
            return "auth", False
        if status == 429 and ("billing" in text or "plan" in text and "quota" in text):
            return "usage_limit", False
        google_status = None
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            google_status = body["error"].get("status")
        if google_status in _STATUS_KINDS and status != 400:
            return _STATUS_KINDS[google_status]
        if status == 400 and ("token" in text and ("exceed" in text or "limit" in text)):
            return "context_length", False
        return None


class _Degrade(Exception):
    """Internal: structured-output level lowered; rebuild and retry on the same model."""


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _plus(a: Any, b: Any) -> int | None:
    values = [int(v) for v in (a, b) if isinstance(v, int | float)]
    return sum(values) if values else None
