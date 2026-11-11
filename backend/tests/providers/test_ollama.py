from __future__ import annotations

import json

import httpx
import pytest

from starindex.providers.base import ProviderError
from starindex.providers.ollama import OllamaProvider

from .conftest import SCHEMA, BrokenStream, Recorder, cfg, ndjson, request

TAGS = {
    "models": [
        {"name": "nomic-embed-text:latest", "size": 274302450, "details": {"family": "nomic-bert", "parameter_size": "137M"}},
        {"name": "qwen3:8b", "size": 5200000000, "details": {"family": "qwen3", "parameter_size": "8.2B", "quantization_level": "Q4_K_M"}},
    ]
}
DONE = {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop", "prompt_eval_count": 21, "eval_count": 9}


def provider(handler, **options):
    recorder = Recorder(handler)
    return OllamaProvider(cfg("ollama", **options), None, transport=recorder.transport), recorder


def test_discovery_reports_sizes_and_separates_embedding_models():
    p, rec = provider(lambda req: httpx.Response(200, json=TAGS))
    models = p.list_models()
    assert rec.requests[-1].url == "http://127.0.0.1:11434/api/tags"
    assert [(m.id, m.kind) for m in models] == [("qwen3:8b", "chat"), ("nomic-embed-text:latest", "embedding")]
    assert models[0].size_bytes == 5200000000 and models[0].details["quantization_level"] == "Q4_K_M"
    caps = p.capabilities()
    assert caps.is_local and not caps.is_paid and caps.structured_output == "json_schema" and caps.embeddings


def test_streaming_happy_path_and_request_body():
    def handler(req):
        return httpx.Response(200, content=ndjson(
            {"message": {"content": "Hel"}, "done": False}, {"message": {"content": "lo"}, "done": False}, DONE
        ))

    p, rec = provider(handler, num_ctx=8192)
    result = p.generate(request(temperature=0.3, max_output_tokens=77))
    assert result.text == "Hello" and result.finish_reason == "stop"
    assert (result.usage.input_tokens, result.usage.output_tokens, result.usage.estimated) == (21, 9, False)
    body = rec.json()
    assert rec.requests[-1].url.path == "/api/chat"
    assert body["stream"] is True and body["messages"][0] == {"role": "system", "content": "Be brief."}
    assert body["options"] == {"temperature": 0.3, "num_predict": 77, "num_ctx": 8192}


def test_structured_output_passes_schema_as_format():
    def handler(req):
        return httpx.Response(200, content=ndjson({"message": {"content": '{"answer": "yes"}'}, "done": False}, DONE))

    p, rec = provider(handler)
    parsed, _ = p.generate_json(request(schema=SCHEMA))
    assert parsed == {"answer": "yes"} and rec.json()["format"] == SCHEMA


def test_missing_model_gives_pull_hint():
    p, _ = provider(lambda req: httpx.Response(404, json={"error": "model 'qwen3:8b' not found"}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request(model="qwen3:8b")))
    assert info.value.kind == "unsupported" and info.value.status == 404


def test_error_line_inside_stream():
    p, _ = provider(lambda req: httpx.Response(200, content=ndjson({"error": "model 'x' not found, try pulling it first"})))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request(model="x")))
    assert info.value.kind == "unsupported" and "ollama pull x" in info.value.message


def test_auth_failure_behind_a_proxy():
    p, _ = provider(lambda req: httpx.Response(401, json={"error": "unauthorized"}))
    with pytest.raises(ProviderError) as info:
        p.list_models()
    assert info.value.kind == "auth"


def test_rate_limit():
    p, _ = provider(lambda req: httpx.Response(429, json={"error": "too many requests"}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "rate_limit"


def test_timeout_and_not_running():
    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)

    def down(req):
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(ProviderError) as info:
        list(provider(slow)[0].stream(request()))
    assert info.value.kind == "timeout"
    health = provider(down)[0].health()
    assert not health.ok and health.error_kind == "network" and "Is the server running" in health.detail


def test_context_length_bad_request():
    p, _ = provider(lambda req: httpx.Response(400, json={"error": "prompt is too long for the context window"}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "context_length"


def test_stream_without_done_line_is_incomplete():
    p, _ = provider(lambda req: httpx.Response(200, content=ndjson({"message": {"content": "par"}, "done": False})))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "stream_incomplete"


def test_dropped_connection_mid_stream_is_incomplete():
    p, _ = provider(lambda req: httpx.Response(200, stream=BrokenStream(ndjson({"message": {"content": "par"}, "done": False}))))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "stream_incomplete"


def test_embeddings_and_health():
    def handler(req):
        if req.url.path == "/api/embed":
            assert json.loads(req.content) == {"model": "nomic-embed-text", "input": ["a", "b"]}
            return httpx.Response(200, json={"embeddings": [[1, 2], [3, 4]]})
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.0"})
        if req.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        return httpx.Response(200, json=TAGS)

    p, _ = provider(handler)
    assert p.embed("nomic-embed-text", ["a", "b"]) == [[1.0, 2.0], [3.0, 4.0]]
    health = p.health()
    assert health.ok and health.model_count == 2 and "1 chat model" in health.detail and "1 loaded" in health.detail


def test_pasted_v1_url_is_tolerated():
    recorder = Recorder(lambda req: httpx.Response(200, json=TAGS))
    p = OllamaProvider(cfg("ollama", base_url="http://localhost:11434/v1/"), None, transport=recorder.transport)
    p.list_models()
    assert recorder.requests[-1].url == "http://localhost:11434/api/tags"
