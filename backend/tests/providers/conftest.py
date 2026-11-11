"""Shared helpers for provider tests. Nothing here (or in any test in this package) uses the network."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from starindex import config as app_config
from starindex.context import AppContext
from starindex.providers.base import ChatMessage, GenerationRequest, ProviderConfig

Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    """MockTransport handler wrapper that remembers every request it served."""

    def __init__(self, handler: Handler):
        self._handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        return self._handler(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def json(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def sse(*events: Any, done: bool = False) -> bytes:
    """Encode data-only SSE messages. Dicts are JSON-encoded; tuples are (event_name, data)."""
    out: list[str] = []
    for event in events:
        name = None
        if isinstance(event, tuple):
            name, event = event
        data = event if isinstance(event, str) else json.dumps(event)
        if name:
            out.append(f"event: {name}\n")
        out.append(f"data: {data}\n\n")
    if done:
        out.append("data: [DONE]\n\n")
    return "".join(out).encode()


def ndjson(*objects: dict[str, Any]) -> bytes:
    return "".join(json.dumps(o) + "\n" for o in objects).encode()


class BrokenStream(httpx.SyncByteStream):
    """Yields some bytes, then fails like a dropped connection."""

    def __init__(self, first: bytes):
        self._first = first

    def __iter__(self) -> Iterator[bytes]:
        yield self._first
        raise httpx.ReadError("connection reset")


def request(model: str = "test-model", *, schema: dict[str, Any] | None = None, system: str | None = "Be brief.", **kw: Any) -> GenerationRequest:
    return GenerationRequest(
        model=model, system=system, messages=[ChatMessage(role="user", content="Hello")], json_schema=schema, **kw
    )


def cfg(kind: str, *, base_url: str | None = None, id: str | None = None, **options: Any) -> ProviderConfig:
    return ProviderConfig(id=id or f"{kind}-test", kind=kind, name=f"{kind} test", base_url=base_url, options=options)  # type: ignore[arg-type]


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "score": {"type": "integer", "minimum": 0}},
    "required": ["answer"],
}


@pytest.fixture
def ctx(tmp_path, monkeypatch) -> Iterator[AppContext]:
    monkeypatch.setenv("STARINDEX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STARINDEX_DISABLE_KEYRING", "1")
    app_config.reset_config_cache()
    context = AppContext()
    try:
        yield context
    finally:
        for name in ("router", "chatgpt"):
            service = context.__dict__.get(name)
            if service is not None:
                service.close()
        context.close()
        app_config.reset_config_cache()
