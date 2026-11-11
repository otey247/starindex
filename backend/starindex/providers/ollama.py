"""Ollama adapter using the native API (``/api/tags``, ``/api/chat``, ``/api/embed``).

The native API is used instead of Ollama's OpenAI-compatible shim because it reports model
sizes and families, accepts a JSON schema directly in ``format``, and returns token counts
on the final stream line.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from .base import (
    ErrorKind,
    GenerationRequest,
    HealthStatus,
    ModelInfo,
    ProviderCapabilities,
    ProviderError,
    StreamEvent,
    Usage,
)
from .http import HttpChatProvider, join_url, validate_base_url

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
_EMBED_HINT = re.compile(r"embed|bge|minilm|e5-|gte-|arctic-embed|paraphrase", re.IGNORECASE)


def _is_embedding_model(name: str, details: dict[str, Any]) -> bool:
    families = [str(details.get("family") or ""), *map(str, details.get("families") or [])]
    if any("bert" in f.lower() for f in families):
        return True
    return bool(_EMBED_HINT.search(name))


class OllamaProvider(HttpChatProvider):
    @property
    def base_url(self) -> str:
        url = validate_base_url(self.config.base_url or DEFAULT_BASE_URL, provider_kind="ollama")
        # Tolerate a pasted OpenAI-compatible URL (".../v1") or ".../api".
        for suffix in ("/v1", "/api"):
            if url.lower().endswith(suffix):
                url = url[: -len(suffix)]
        return url

    def _headers(self) -> dict[str, str]:
        # Plain Ollama needs no key; a reverse proxy in front of it might.
        return {"Authorization": f"Bearer {self._secret}"} if self._secret else {}

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            is_local=True,
            is_paid=False,
            streaming=True,
            model_discovery=True,
            embeddings=True,
            structured_output="json_schema",
            notes=["Models are whatever you have pulled with `ollama pull`; GPU use is decided by Ollama."],
        )

    def list_models(self) -> list[ModelInfo]:
        payload = self._request_json("GET", join_url(self.base_url, "api/tags"))
        items = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise self._error("invalid_response", "Ollama returned an unexpected model list.")
        models: list[ModelInfo] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "")
            if not name:
                continue
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            size = item.get("size")
            models.append(
                ModelInfo(
                    id=name,
                    display_name=name,
                    kind="embedding" if _is_embedding_model(name, details) else "chat",
                    size_bytes=int(size) if isinstance(size, int | float) else None,
                    details={
                        k: details[k]
                        for k in ("family", "parameter_size", "quantization_level", "format")
                        if details.get(k)
                    },
                )
            )
        models.sort(key=lambda m: (m.kind != "chat", m.id.lower()))
        return models

    def health(self) -> HealthStatus:
        import time

        start = time.monotonic()
        try:
            version = self._request_json("GET", join_url(self.base_url, "api/version"), timeout_s=5)
            models = self.list_models()
        except ProviderError as exc:
            return HealthStatus(ok=False, detail=exc.message, error_kind=exc.kind)
        loaded = 0
        try:
            running = self._request_json("GET", join_url(self.base_url, "api/ps"), timeout_s=5)
            loaded = len(running.get("models") or []) if isinstance(running, dict) else 0
        except ProviderError:
            pass
        chat = sum(1 for m in models if m.kind == "chat")
        return HealthStatus(
            ok=True,
            latency_ms=int((time.monotonic() - start) * 1000),
            model_count=len(models),
            detail=f"Ollama {version.get('version', '?') if isinstance(version, dict) else '?'}: "
            f"{chat} chat model(s), {len(models) - chat} embedding model(s), {loaded} loaded in memory",
        )

    def stream(self, request: GenerationRequest) -> Iterator[StreamEvent]:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend({"role": m.role, "content": m.content} for m in request.messages)
        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            options["num_predict"] = request.max_output_tokens
        context = self.config.options.get("num_ctx")
        if isinstance(context, int) and context > 0:
            options["num_ctx"] = context
        body: dict[str, Any] = {"model": request.model, "messages": messages, "stream": True}
        if options:
            body["options"] = options
        if request.json_schema is not None:
            body["format"] = request.json_schema
        keep_alive = self.config.options.get("keep_alive")
        if keep_alive:
            body["keep_alive"] = keep_alive

        done = False
        usage: Usage | None = None
        finish: str | None = None
        with self._open_stream(join_url(self.base_url, "api/chat"), json_body=body, timeout_s=request.timeout_s) as response:
            for raw in response.iter_lines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except ValueError as exc:
                    raise self._error("invalid_response", "Ollama sent a malformed stream line.") from exc
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    raise self._line_error(str(chunk["error"]), request.model)
                text = (chunk.get("message") or {}).get("content")
                if isinstance(text, str) and text:
                    yield StreamEvent(type="delta", text=text)
                if chunk.get("done"):
                    done = True
                    finish = str(chunk.get("done_reason") or "stop")
                    usage = Usage(
                        input_tokens=_int(chunk.get("prompt_eval_count")),
                        output_tokens=_int(chunk.get("eval_count")),
                    )
                    break
        if not done:
            raise self._error(
                "stream_incomplete", "Ollama's stream ended before the model finished.", retryable=True
            )
        yield StreamEvent(type="completed", usage=usage, finish_reason="length" if finish == "length" else "stop")

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = self._request_json(
            "POST", join_url(self.base_url, "api/embed"), json_body={"model": model, "input": texts},
            timeout_s=self._timeout_s,
        )
        vectors = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise self._error("invalid_response", "Ollama's embedding response does not match the request.")
        return [list(map(float, v)) for v in vectors]

    def _line_error(self, message: str, model: str) -> ProviderError:
        kind, _ = self._classify(message)
        return self._error(kind, self._hint(message, model))

    @staticmethod
    def _classify(message: str) -> tuple[ErrorKind, bool]:
        text = message.lower()
        if "not found" in text and "model" in text:
            return "unsupported", False
        if "context" in text or "too long" in text:
            return "context_length", False
        if "memory" in text or "resource" in text:
            return "unavailable", True
        return "unavailable", True

    @staticmethod
    def _hint(message: str, model: str) -> str:
        if "not found" in message.lower():
            return f"Ollama does not have the model '{model}'. Pull it first: ollama pull {model}"
        return f"Ollama error: {message}"

    def _refine_error(self, status: int, message: str, code: str | None, body: Any) -> tuple[ErrorKind, bool] | None:
        text = message.lower()
        if status == 404 and "model" in text:
            return "unsupported", False
        if status == 400 and ("does not support" in text or "unsupported" in text):
            return "unsupported", False
        if status >= 500 and ("memory" in text or "resource" in text):
            return "unavailable", True
        return None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None
