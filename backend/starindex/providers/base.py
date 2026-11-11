"""Provider contracts shared by every model backend.

Application code (ask, research, compare, classification) depends only on this module and
on ``ModelRouter``. Adapters translate these types to each vendor's wire format and declare
what they can and cannot do through ``ProviderCapabilities`` -- nothing assumes that two
providers share tool-calling, structured-output or streaming semantics.

Everything is synchronous: adapters use ``httpx.Client`` and expose streams as generators.
FastAPI runs them in its thread pool and the worker calls them directly.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any, Literal

from pydantic import BaseModel, Field

ProviderKind = Literal[
    "ollama", "lmstudio", "vllm", "openai_compat",              # local / self-hosted
    "openai", "anthropic", "gemini", "azure_openai",            # paid cloud APIs
    "chatgpt_plan",                                             # Sign in with ChatGPT plan usage
]
LOCAL_KINDS: frozenset[str] = frozenset({"ollama", "lmstudio", "vllm", "openai_compat"})
CLOUD_KINDS: frozenset[str] = frozenset({"openai", "anthropic", "gemini", "azure_openai", "chatgpt_plan"})

ErrorKind = Literal[
    "not_configured",     # provider/model missing or disabled
    "cloud_disabled",     # privacy settings forbid outbound model traffic
    "confirmation_required",  # cloud request needs explicit user confirmation
    "auth",               # bad/expired credentials
    "consent_required",   # ChatGPT plan usage scope not granted
    "not_eligible",       # account/workspace cannot use this capability
    "rate_limit",         # short-term throttling; retry later
    "usage_limit",        # plan/quota exhausted
    "timeout",
    "network",
    "unavailable",        # 5xx / service down / model not loaded
    "unsupported",        # capability, parameter or model not supported
    "context_length",
    "bad_request",
    "stream_incomplete",  # stream ended without a terminal event
    "invalid_response",   # could not parse what the model returned
    "refused",            # the model or a safety filter declined the request
]


class ProviderError(Exception):
    """Normalised failure. Never retried on a different provider automatically."""

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        provider_id: str | None = None,
        provider_kind: str | None = None,
        status: int | None = None,
        code: str | None = None,
        param: str | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        retry_after_s: float | None = None,
        action_url: str | None = None,
        action_label: str | None = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.provider_id = provider_id
        self.provider_kind = provider_kind
        self.status = status
        self.code = code
        self.param = param
        self.request_id = request_id
        self.retryable = retryable
        self.retry_after_s = retry_after_s
        self.action_url = action_url
        self.action_label = action_label

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "provider_id": self.provider_id,
            "provider_kind": self.provider_kind,
            "status": self.status,
            "code": self.code,
            "param": self.param,
            "request_id": self.request_id,
            "retryable": self.retryable,
            "retry_after_s": self.retry_after_s,
            "action_url": self.action_url,
            "action_label": self.action_label,
        }


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class GenerationRequest(BaseModel):
    """Provider-neutral request. Adapters drop fields their API does not accept."""

    model: str
    system: str | None = None
    messages: list[ChatMessage]
    temperature: float | None = 0.2
    max_output_tokens: int | None = 2048
    # When set, the adapter uses its best structured-output mechanism (see capabilities)
    # and the caller parses the text with ``parse_json_output``.
    json_schema: dict[str, Any] | None = None
    json_schema_name: str = "result"
    timeout_s: float = 180.0


class Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated: bool = False   # True when the provider reported nothing and we counted characters


class StreamEvent(BaseModel):
    """delta* then exactly one terminal ``completed``. Failures raise ``ProviderError``."""

    type: Literal["delta", "completed"]
    text: str = ""
    usage: Usage | None = None
    finish_reason: str | None = None   # stop | length | content_filter | ...


class GenerationResult(BaseModel):
    text: str
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = None
    model: str


class ModelInfo(BaseModel):
    id: str
    display_name: str | None = None
    kind: Literal["chat", "embedding", "rerank", "unknown"] = "unknown"
    context_window: int | None = None
    size_bytes: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ProviderCapabilities(BaseModel):
    is_local: bool
    is_paid: bool
    streaming: bool = True
    model_discovery: bool = True
    embeddings: bool = False
    # json_schema: server-enforced schema; json_object: valid JSON, schema via prompt;
    # tool: forced tool call carrying the schema; prompt: instructions only.
    structured_output: Literal["json_schema", "json_object", "tool", "prompt"] = "prompt"
    accepts_temperature: bool = True
    accepts_max_output_tokens: bool = True
    notes: list[str] = Field(default_factory=list)


class HealthStatus(BaseModel):
    ok: bool
    latency_ms: int | None = None
    detail: str = ""
    model_count: int | None = None
    error_kind: str | None = None


class ProviderConfig(BaseModel):
    """Non-secret configuration row. The API key (if any) is passed separately."""

    id: str
    kind: ProviderKind
    name: str
    base_url: str | None = None
    enabled: bool = True
    # kind-specific: azure_openai {api_version, deployments}; openai_compat {headers}; all {timeout_s}
    options: dict[str, Any] = Field(default_factory=dict)


class ChatProvider(ABC):
    """One configured model backend."""

    def __init__(self, config: ProviderConfig):
        self.config = config

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def kind(self) -> str:
        return self.config.kind

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Models available to this account/server. Raises ProviderError."""

    @abstractmethod
    def stream(self, request: GenerationRequest) -> Iterator[StreamEvent]:
        """Yield deltas and one terminal ``completed`` event. Raises ProviderError.

        A stream that ends without the provider's terminal marker must raise
        ``ProviderError('stream_incomplete')`` rather than silently succeeding.
        """

    def generate(self, request: GenerationRequest) -> GenerationResult:
        parts: list[str] = []
        usage = Usage()
        finish: str | None = None
        for ev in self.stream(request):
            if ev.type == "delta":
                parts.append(ev.text)
            else:
                usage = ev.usage or usage
                finish = ev.finish_reason
        text = "".join(parts)
        if usage.input_tokens is None and usage.output_tokens is None:
            usage = estimate_usage(request, text)
        return GenerationResult(text=text, usage=usage, finish_reason=finish, model=request.model)

    def generate_json(self, request: GenerationRequest) -> tuple[dict[str, Any], GenerationResult]:
        """Structured generation with one repair attempt. ``request.json_schema`` must be set."""
        if request.json_schema is None:
            raise ValueError("generate_json requires request.json_schema")
        result = self.generate(request)
        try:
            return parse_json_output(result.text), result
        except ValueError:
            repair = request.model_copy(
                update={
                    "messages": [
                        *request.messages,
                        ChatMessage(role="assistant", content=result.text[:4000]),
                        ChatMessage(
                            role="user",
                            content="That was not a single valid JSON object. Reply again with ONLY the JSON "
                            "object that matches the schema, with no prose and no code fences.",
                        ),
                    ]
                }
            )
            second = self.generate(repair)
            try:
                parsed = parse_json_output(second.text)
            except ValueError as exc:
                raise ProviderError(
                    "invalid_response",
                    "The model did not return valid JSON for a structured step.",
                    provider_id=self.id,
                    provider_kind=self.kind,
                ) from exc
            second.usage = add_usage(result.usage, second.usage)
            return parsed, second

    def health(self) -> HealthStatus:
        import time

        start = time.monotonic()
        try:
            models = self.list_models()
        except ProviderError as exc:
            return HealthStatus(ok=False, detail=exc.message, error_kind=exc.kind)
        return HealthStatus(
            ok=True,
            latency_ms=int((time.monotonic() - start) * 1000),
            model_count=len(models),
            detail=f"{len(models)} model(s) available",
        )

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise ProviderError(
            "unsupported", f"{self.kind} does not provide embeddings through StarIndex.",
            provider_id=self.id, provider_kind=self.kind,
        )

    def close(self) -> None:  # adapters holding an httpx.Client override this
        return None


# -- helpers shared by adapters and callers ------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_json_output(text: str) -> dict[str, Any]:
    """Extract one JSON object from model output (tolerates fences and surrounding prose)."""
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    candidates.extend(m.group(1).strip() for m in _FENCE.finditer(text))
    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    for cand in candidates:
        try:
            value = json.loads(cand)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON object found in model output")


def estimate_tokens(text: str) -> int:
    """Rough token count (about four characters per token) for budgets and estimates."""
    return max(1, len(text) // 4) if text else 0


def estimate_usage(request: GenerationRequest, output_text: str) -> Usage:
    prompt = (request.system or "") + "".join(m.content for m in request.messages)
    return Usage(input_tokens=estimate_tokens(prompt), output_tokens=estimate_tokens(output_text), estimated=True)


def add_usage(a: Usage, b: Usage) -> Usage:
    def plus(x: int | None, y: int | None) -> int | None:
        if x is None and y is None:
            return None
        return (x or 0) + (y or 0)

    return Usage(
        input_tokens=plus(a.input_tokens, b.input_tokens),
        output_tokens=plus(a.output_tokens, b.output_tokens),
        estimated=a.estimated or b.estimated,
    )


def schema_instruction(schema: dict[str, Any]) -> str:
    """Prompt suffix for providers without server-side schema enforcement."""
    return (
        "Respond with a single JSON object and nothing else (no prose, no code fences). "
        "It must conform to this JSON Schema:\n" + json.dumps(schema, separators=(",", ":"))
    )
