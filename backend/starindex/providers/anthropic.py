"""Anthropic Messages API adapter (raw HTTP; StarIndex ships no vendor SDKs).

Follows the current API surface:

* ``x-api-key`` + ``anthropic-version`` headers, ``POST /v1/messages`` with SSE streaming,
  ``GET /v1/models`` for discovery (ids, display names, context windows).
* Sampling parameters are **not** sent. Current Claude models reject ``temperature`` /
  ``top_p`` / ``top_k``, and API version ``2026-10-21`` rejects them on every model.
* Thinking is left at the model's default (adaptive on current models). ``max_tokens`` caps
  thinking *and* text, so the adapter adds headroom on top of the caller's output budget.
  ``options.effort`` (low|medium|high|xhigh|max) is passed through as ``output_config.effort``.
* Structured output uses ``output_config.format`` (JSON outputs). Forced tool calls are not
  used: current models reject ``tool_choice`` ``any``/``tool``. A model that does not support
  JSON outputs degrades to prompt-only instructions, remembered per model.
* A safety refusal (``stop_reason: "refusal"``) is surfaced as ``ProviderError('refused')``.
  Server-side refusal fallbacks are opt-in (``options.refusal_fallback``) because StarIndex
  never changes the active model without the user knowing.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

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

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2026-10-21"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_THINKING_HEADROOM = 8192
_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
_UNSUPPORTED_SCHEMA_KEYS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "maxItems", "uniqueItems", "minProperties", "maxProperties",
}
_ERROR_TYPES: dict[str, tuple[ErrorKind, bool]] = {
    "authentication_error": ("auth", False),
    "permission_error": ("auth", False),
    "billing_error": ("usage_limit", False),
    "rate_limit_error": ("rate_limit", True),
    "overloaded_error": ("unavailable", True),
    "api_error": ("unavailable", True),
    "timeout_error": ("timeout", True),
    "request_too_large": ("context_length", False),
    "not_found_error": ("unsupported", False),
}


def prepare_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt a JSON Schema to the JSON-outputs subset.

    Every object must declare ``additionalProperties: false``; numeric/string length
    constraints are not accepted and are removed (callers validate the parsed result).
    """

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        out = {k: walk(v) for k, v in node.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
        if out.get("type") == "object" or "properties" in out:
            out["additionalProperties"] = False
        if isinstance(out.get("minItems"), int) and out["minItems"] > 1:
            del out["minItems"]
        return out

    return walk(copy.deepcopy(schema))


class AnthropicProvider(HttpChatProvider):
    request_id_headers = ("request-id", "x-request-id")

    def __init__(
        self,
        config: ProviderConfig,
        secret: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        super().__init__(config, secret, transport=transport)
        self._prompt_only_models: set[str] = set()

    @property
    def base_url(self) -> str:
        url = validate_base_url(self.config.base_url or DEFAULT_BASE_URL, provider_kind="anthropic")
        return url[:-3] if url.endswith("/v1") else url

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._require_secret(), "anthropic-version": API_VERSION}

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            is_local=False,
            is_paid=True,
            streaming=True,
            model_discovery=True,
            embeddings=False,
            structured_output="json_schema",
            accepts_temperature=False,
            accepts_max_output_tokens=True,
            notes=[
                "Sampling parameters are not sent; current Claude models reject them.",
                "Thinking runs at the model's default; the output cap includes headroom for it.",
                "Structured output uses JSON outputs (output_config.format), not forced tool calls.",
            ],
        )

    def list_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        params: dict[str, Any] = {"limit": 1000}
        for _ in range(20):
            payload = self._request_json("GET", join_url(self.base_url, "v1/models"), params=params)
            items = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise self._error("invalid_response", "Anthropic returned an unexpected model list.")
            for item in items:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                caps = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
                structured = (caps.get("structured_outputs") or {}).get("supported")
                models.append(
                    ModelInfo(
                        id=str(item["id"]),
                        display_name=item.get("display_name") or None,
                        kind="chat",
                        context_window=item.get("max_input_tokens") if isinstance(item.get("max_input_tokens"), int) else None,
                        details={
                            k: v
                            for k, v in {
                                "max_output_tokens": item.get("max_tokens"),
                                "structured_outputs": structured,
                                "created_at": item.get("created_at"),
                            }.items()
                            if v is not None
                        },
                    )
                )
            if not payload.get("has_more") or not payload.get("last_id"):
                break
            params = {"limit": 1000, "after_id": payload["last_id"]}
        return models

    # -- generation -------------------------------------------------------------------------

    def _build_body(self, request: GenerationRequest, *, prompt_only: bool) -> dict[str, Any]:
        headroom = self.config.options.get("thinking_headroom_tokens")
        headroom = int(headroom) if isinstance(headroom, int) and headroom >= 0 else DEFAULT_THINKING_HEADROOM
        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": max(256, request.max_output_tokens or 4096) + headroom,
            "stream": True,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        system = request.system or ""
        output_config: dict[str, Any] = {}
        if request.json_schema is not None:
            if prompt_only:
                system = (system + "\n\n" if system else "") + schema_instruction(request.json_schema)
            else:
                output_config["format"] = {"type": "json_schema", "schema": prepare_schema(request.json_schema)}
        effort = self.config.options.get("effort")
        if isinstance(effort, str) and effort in _EFFORTS:
            output_config["effort"] = effort
        if system:
            body["system"] = system
        if output_config:
            body["output_config"] = output_config
        if self.config.options.get("refusal_fallback") is True:
            body["fallbacks"] = "default"
        return body

    def stream(self, request: GenerationRequest) -> Iterator[StreamEvent]:
        prompt_only = request.model in self._prompt_only_models
        try:
            yield from self._stream_once(request, self._build_body(request, prompt_only=prompt_only))
            return
        except _FormatUnsupported:
            # This model has no JSON outputs: fall back to prompt instructions (same provider, same model).
            self._prompt_only_models.add(request.model)
        yield from self._stream_once(request, self._build_body(request, prompt_only=True))

    def _stream_once(self, request: GenerationRequest, body: dict[str, Any]) -> Iterator[StreamEvent]:
        headers = {"anthropic-beta": FALLBACK_BETA} if "fallbacks" in body else None
        input_tokens: int | None = None
        output_tokens: int | None = None
        stop_reason: str | None = None
        stop_details: dict[str, Any] | None = None
        finished = False
        try:
            with self._open_stream(
                join_url(self.base_url, "v1/messages"), json_body=body, timeout_s=request.timeout_s, headers=headers
            ) as response:
                for message in iter_sse(response.iter_lines()):
                    if not message.data:
                        continue
                    try:
                        event = json.loads(message.data)
                    except ValueError:
                        continue
                    kind = event.get("type") or message.event
                    if kind == "message_start":
                        usage = (event.get("message") or {}).get("usage") or {}
                        input_tokens = _sum(
                            usage.get("input_tokens"),
                            usage.get("cache_creation_input_tokens"),
                            usage.get("cache_read_input_tokens"),
                        )
                        output_tokens = usage.get("output_tokens") if isinstance(usage.get("output_tokens"), int) else None
                    elif kind == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield StreamEvent(type="delta", text=str(delta["text"]))
                        # thinking_delta / signature_delta / input_json_delta are not user-visible text
                    elif kind == "message_delta":
                        delta = event.get("delta") or {}
                        stop_reason = delta.get("stop_reason") or stop_reason
                        if isinstance(delta.get("stop_details"), dict):
                            stop_details = delta["stop_details"]
                        usage = event.get("usage") or {}
                        if isinstance(usage.get("output_tokens"), int):
                            output_tokens = usage["output_tokens"]
                        if isinstance(usage.get("input_tokens"), int) and usage["input_tokens"]:
                            input_tokens = _sum(
                                usage.get("input_tokens"),
                                usage.get("cache_creation_input_tokens"),
                                usage.get("cache_read_input_tokens"),
                            )
                    elif kind == "message_stop":
                        finished = True
                        break
                    elif kind == "error":
                        raise self._event_error(event.get("error") or {})
        except ProviderError as exc:
            if (
                exc.status == 400
                and exc.kind == "bad_request"
                and "format" in (body.get("output_config") or {})
                and _mentions_format(exc.message)
            ):
                raise _FormatUnsupported() from exc
            raise
        if not finished:
            raise self._error(
                "stream_incomplete", "Anthropic's stream ended before message_stop.", retryable=True
            )
        if stop_reason == "refusal":
            category = (stop_details or {}).get("category")
            raise self._error(
                "refused",
                "Claude declined this request" + (f" (category: {category})." if category else "."),
                code="refusal",
            )
        finish = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length"}.get(stop_reason or "", stop_reason)
        yield StreamEvent(
            type="completed",
            usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
            finish_reason=finish,
        )

    def _event_error(self, error: dict[str, Any]) -> ProviderError:
        etype = str(error.get("type") or "")
        kind, retryable = _ERROR_TYPES.get(etype, ("unavailable", True))
        return self._error(
            kind, f"Anthropic reported an error mid-stream: {error.get('message') or etype}",
            code=etype or None, retryable=retryable,
        )

    def _refine_error(self, status: int, message: str, code: str | None, body: Any) -> tuple[ErrorKind, bool] | None:
        if code in _ERROR_TYPES:
            return _ERROR_TYPES[code]
        if status == 529:
            return "unavailable", True
        return None


class _FormatUnsupported(Exception):
    """Internal: this model rejected output_config.format."""


def _mentions_format(message: str) -> bool:
    text = message.lower()
    return any(
        token in text
        for token in ("output_config", "output_format", "structured output", "json_schema", "schema", "format")
    )


def _sum(*values: Any) -> int | None:
    numbers = [v for v in values if isinstance(v, int)]
    return sum(numbers) if numbers else None
