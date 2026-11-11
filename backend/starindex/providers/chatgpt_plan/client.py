"""Inference with a ChatGPT plan through the public Responses API.

Per https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference and
.../preview-limitations:

* models come from ``GET https://api.openai.com/v1/models`` with the user's OAuth access token
  (the ``models`` array; entries with ``visibility == "list"``; ``slug`` is the model id);
* inference is ``POST https://api.openai.com/v1/responses`` with ``store: false`` and
  ``stream: true``, ``input`` as an array, and the system prompt in ``instructions``;
* the fields the preview rejects (``temperature``, ``max_output_tokens``, ``metadata``, ...)
  are never sent, and no tools are sent;
* a request is successful only when ``response.completed`` arrives.

The base URL is fixed: access tokens are never sent anywhere but api.openai.com.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from ..base import (
    ChatProvider,
    ErrorKind,
    GenerationRequest,
    HealthStatus,
    ModelInfo,
    ProviderCapabilities,
    ProviderConfig,
    ProviderError,
    StreamEvent,
    Usage,
    schema_instruction,
)
from ..http import (
    extract_error,
    iter_sse,
    kind_for_status,
    make_client,
    map_transport_error,
    parse_retry_after,
    read_error_body,
    request_timeout,
)
from .oauth import PROVIDER_ID, PROVIDER_KIND, RESOURCE, USAGE_URL
from .service import ChatGPTPlanService

MODELS_URL = f"{RESOURCE}/models"
RESPONSES_URL = f"{RESOURCE}/responses"

# Request-body fields the preview documents as unsupported on this route. Never sent.
UNSUPPORTED_FIELDS = frozenset(
    {
        "background", "conversation", "max_output_tokens", "max_tool_calls", "metadata", "moderation",
        "multi_agent", "prompt", "prompt_cache_retention", "safety_identifier", "temperature",
        "top_logprobs", "top_p", "truncation", "user", "previous_response_id",
    }
)

# code -> (kind, retryable, user-facing explanation)
_CODE_MAP: dict[str, tuple[ErrorKind, bool, str]] = {
    "subscription_sharing_user_not_eligible": (
        "not_eligible", False,
        "ChatGPT plan usage is not available for this account, workspace or policy. Choose another provider.",
    ),
    "subscription_sharing_usage_limit_exceeded": (
        "usage_limit", False,
        "A ChatGPT usage limit was reached. It may be your plan's limit or a limit set for StarIndex; "
        "review it in ChatGPT settings.",
    ),
    "subscription_sharing_usage_unavailable": (
        "unavailable", True, "ChatGPT could not check usage availability right now. Try again shortly.",
    ),
    "subscription_sharing_unsupported_capability": (
        "unsupported", False, "The request used something ChatGPT plan usage does not support",
    ),
    "subscription_sharing_route_not_supported": (
        "unsupported", False, "This endpoint is not available for ChatGPT plan usage.",
    ),
    "subscription_sharing_invalid_user": (
        "auth", False, "The ChatGPT account could not be validated. Sign in again if this persists.",
    ),
    "chatpass_v2_scope_not_authorized": (
        "consent_required", False,
        "The granted permission does not authorize this request. Enable ChatGPT plan usage again in Settings.",
    ),
    "chatpass_v2_invalid_authorization_context": (
        "consent_required", False,
        "The granted permission does not authorize this request. Enable ChatGPT plan usage again in Settings.",
    ),
    "subscription_sharing_user_unavailable": (
        "unavailable", True, "ChatGPT account information is temporarily unavailable. Try again shortly.",
    ),
}
# Pre-stream direct-admission failures: body is {"detail": "..."} with no machine-readable code.
_ADMISSION: dict[int, tuple[ErrorKind, bool, str]] = {
    401: (
        "auth", False,
        "ChatGPT did not accept the sign-in or the plan-usage permission. Check the selected account and its permissions.",
    ),
    403: (
        "not_eligible", False,
        "A policy or permission check (for example the permitted region) prevented this request.",
    ),
    503: (
        "unavailable", True, "ChatGPT plan routing is unavailable or not enabled right now. Try again later.",
    ),
}


def plan_error(
    *,
    status: int | None,
    code: str | None,
    message: str,
    param: str | None = None,
    request_id: str | None = None,
    retry_after_s: float | None = None,
    has_error_object: bool = True,
) -> ProviderError:
    """Translate a ChatGPT-plan failure into a ``ProviderError`` with a recovery hint."""
    kind: ErrorKind
    action_url: str | None = None
    action_label: str | None = None
    if code and code in _CODE_MAP:
        kind, retryable, text = _CODE_MAP[code]
        if code == "subscription_sharing_usage_limit_exceeded":
            action_url, action_label = USAGE_URL, "Manage usage"
        if code == "subscription_sharing_unsupported_capability":
            text = f"{text} ({param or 'see details'}): {message}"
    elif not has_error_object and status in _ADMISSION:
        kind, retryable, text = _ADMISSION[status]  # type: ignore[index]
        if message:
            text = f"{text} (details: {message})"
    else:
        kind, retryable = kind_for_status(status or 500, message, code)
        text = message or f"HTTP {status} from the Responses API."
        if kind == "context_length":
            text = f"The request is too large for this model: {text}"
    return ProviderError(
        kind,
        text,
        provider_id=PROVIDER_ID,
        provider_kind=PROVIDER_KIND,
        status=status,
        code=code,
        param=param,
        request_id=request_id,
        retryable=retryable,
        retry_after_s=retry_after_s,
        action_url=action_url,
        action_label=action_label,
    )


class ChatGPTPlanProvider(ChatProvider):
    def __init__(
        self,
        config: ProviderConfig,
        service: ChatGPTPlanService,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        super().__init__(config)
        self.service = service
        self._client = make_client(transport=transport)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            is_local=False,
            is_paid=True,
            streaming=True,
            model_discovery=True,
            embeddings=False,
            structured_output="prompt",
            accepts_temperature=False,
            accepts_max_output_tokens=False,
            notes=[
                "Uses your ChatGPT plan through the Responses API (store: false, stream: true).",
                "Preview limits: no temperature or output-token cap, no hosted tools, no embeddings.",
                "Usage counts against your ChatGPT plan limits; manage it in ChatGPT settings.",
            ],
        )

    def _auth(self) -> dict[str, str]:
        # Raises consent_required / auth / not_configured when plan usage cannot be used.
        return {"Authorization": f"Bearer {self.service.access_token()}"}

    def _response_error(self, response: httpx.Response) -> ProviderError:
        body = read_error_body(response)
        message, code, param = extract_error(body)
        has_error_object = isinstance(body, dict) and isinstance(body.get("error"), dict)
        request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
        return plan_error(
            status=response.status_code,
            code=code if has_error_object else None,
            message=message,
            param=param,
            request_id=request_id,
            retry_after_s=parse_retry_after(response.headers),
            has_error_object=has_error_object,
        )

    def list_models(self) -> list[ModelInfo]:
        headers = self._auth()
        try:
            response = self._client.get(MODELS_URL, headers=headers, timeout=request_timeout(20))
        except httpx.HTTPError as exc:
            raise map_transport_error(exc, provider_id=self.id, provider_kind=self.kind, base_url=RESOURCE) from exc
        if response.status_code >= 400:
            raise self._response_error(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                "invalid_response", "The model catalog response was not JSON.", provider_id=self.id, provider_kind=self.kind
            ) from exc
        items = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ProviderError(
                "invalid_response", "The model catalog did not contain a models array.",
                provider_id=self.id, provider_kind=self.kind,
            )
        models: list[ModelInfo] = []
        for item in items:   # keep the server's ordering
            if not isinstance(item, dict) or item.get("visibility") != "list" or not item.get("slug"):
                continue
            context = item.get("context_window")
            models.append(
                ModelInfo(
                    id=str(item["slug"]),
                    display_name=item.get("display_name") or None,
                    kind="chat",
                    context_window=int(context) if isinstance(context, int | float) else None,
                )
            )
        return models

    def health(self) -> HealthStatus:
        status = self.service.status()
        if not status.available:
            return HealthStatus(ok=False, detail=status.reason, error_kind="not_configured")
        return super().health()

    @staticmethod
    def build_body(request: GenerationRequest) -> dict[str, Any]:
        """Responses request body. Only documented, supported fields are present."""
        instructions = request.system or ""
        if request.json_schema is not None:
            instructions = (instructions + "\n\n" if instructions else "") + schema_instruction(request.json_schema)
        body: dict[str, Any] = {
            "model": request.model,
            "input": [{"role": m.role, "content": m.content} for m in request.messages],
            "store": False,
            "stream": True,
        }
        if instructions:
            body["instructions"] = instructions
        return body

    def stream(self, request: GenerationRequest) -> Iterator[StreamEvent]:
        headers = {**self._auth(), "Accept": "text/event-stream"}
        body = self.build_body(request)
        completed = False
        usage: Usage | None = None
        opened = False
        try:
            with self._client.stream(
                "POST", RESPONSES_URL, json=body, headers=headers, timeout=request_timeout(request.timeout_s)
            ) as response:
                if response.status_code >= 400:
                    raise self._response_error(response)
                opened = True
                request_id = response.headers.get("x-request-id")
                for message in iter_sse(response.iter_lines()):
                    if not message.data or message.data.strip() == "[DONE]":
                        continue
                    try:
                        event = json.loads(message.data)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    etype = event.get("type") or message.event
                    if etype == "response.output_text.delta":
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            yield StreamEvent(type="delta", text=delta)
                    elif etype == "response.completed":
                        completed = True
                        usage = _usage((event.get("response") or {}).get("usage"))
                        break
                    elif etype == "response.failed":
                        error = (event.get("response") or {}).get("error") or {}
                        raise plan_error(
                            status=None,
                            code=str(error.get("code")) if error.get("code") else "unknown_error",
                            message=str(error.get("message") or "The response failed."),
                            param=error.get("param"),
                            request_id=request_id,
                        )
                    elif etype == "response.incomplete":
                        details = (event.get("response") or {}).get("incomplete_details") or {}
                        reason = str(details.get("reason") or "unknown")
                        raise ProviderError(
                            "stream_incomplete",
                            f"The model stopped before finishing its response (reason: {reason}).",
                            provider_id=self.id, provider_kind=self.kind, code=reason, request_id=request_id,
                        )
                    elif etype == "error":
                        error = event.get("error") if isinstance(event.get("error"), dict) else event
                        raise plan_error(
                            status=None,
                            code=str(error.get("code")) if error.get("code") else None,
                            message=str(error.get("message") or "The stream reported an error."),
                            param=error.get("param"),
                            request_id=request_id,
                        )
        except httpx.HTTPError as exc:
            raise map_transport_error(
                exc, provider_id=self.id, provider_kind=self.kind, base_url=RESOURCE, mid_stream=opened
            ) from exc
        except ProviderError as exc:
            self.service.note_error(exc.message)
            raise
        if not completed:
            raise ProviderError(
                "stream_incomplete", "The stream ended without response.completed.",
                provider_id=self.id, provider_kind=self.kind, retryable=True,
            )
        yield StreamEvent(type="completed", usage=usage, finish_reason="stop")

    def close(self) -> None:
        self._client.close()


def _usage(raw: Any) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    inp, out = raw.get("input_tokens"), raw.get("output_tokens")
    return Usage(
        input_tokens=int(inp) if isinstance(inp, int | float) else None,
        output_tokens=int(out) if isinstance(out, int | float) else None,
    )
