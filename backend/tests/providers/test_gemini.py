from __future__ import annotations

import json

import httpx
import pytest

from starindex.providers.base import ProviderError
from starindex.providers.gemini import GeminiProvider, prepare_schema

from .conftest import SCHEMA, BrokenStream, Recorder, cfg, request, sse


def part(text: str, finish: str | None = None, **extra):
    candidate = {"content": {"role": "model", "parts": [{"text": text, **extra}]}}
    if finish:
        candidate["finishReason"] = finish
    return {"candidates": [candidate]}


USAGE = {"candidates": [{"content": {"parts": [{"text": ""}]}, "finishReason": "STOP"}],
         "usageMetadata": {"promptTokenCount": 14, "candidatesTokenCount": 6, "thoughtsTokenCount": 4}}


def provider(handler, secret="g-key"):
    recorder = Recorder(handler)
    return GeminiProvider(cfg("gemini"), secret, transport=recorder.transport), recorder


def test_model_discovery_filters_by_generation_method_and_paginates():
    pages = {
        None: {"models": [{"name": "models/chat-one", "displayName": "Chat One", "inputTokenLimit": 1048576, "outputTokenLimit": 8192,
                           "supportedGenerationMethods": ["generateContent", "countTokens"]}], "nextPageToken": "p2"},
        "p2": {"models": [{"name": "models/embed-one", "supportedGenerationMethods": ["embedContent"]},
                          {"name": "models/aqa", "supportedGenerationMethods": ["generateAnswer"]}]},
    }

    def handler(req):
        assert req.url.path == "/v1beta/models"
        return httpx.Response(200, json=pages[req.url.params.get("pageToken")])

    p, rec = provider(handler)
    models = p.list_models()
    assert [(m.id, m.kind) for m in models] == [("chat-one", "chat"), ("aqa", "unknown"), ("embed-one", "embedding")]
    assert models[0].context_window == 1048576 and models[0].display_name == "Chat One"
    assert rec.requests[0].headers["x-goog-api-key"] == "g-key"
    assert "key" not in rec.requests[0].url.params  # the key is never put in the URL


def test_streaming_happy_path_and_body_shape():
    p, rec = provider(lambda req: httpx.Response(200, content=sse(part("Hel"), part("hidden", thought=True), part("lo"), USAGE)))
    result = p.generate(request(temperature=0.4, max_output_tokens=256))
    assert result.text == "Hello" and result.finish_reason == "stop"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (14, 10)
    sent = rec.requests[-1]
    assert sent.url.path == "/v1beta/models/test-model:streamGenerateContent" and sent.url.params["alt"] == "sse"
    body = rec.json()
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Hello"}]}]
    assert body["systemInstruction"] == {"parts": [{"text": "Be brief."}]}
    assert body["generationConfig"] == {"temperature": 0.4, "maxOutputTokens": 256}


def test_assistant_turns_use_model_role():
    from starindex.providers.base import ChatMessage, GenerationRequest

    p, rec = provider(lambda req: httpx.Response(200, content=sse(part("ok", "STOP"))))
    p.generate(GenerationRequest(model="m", messages=[ChatMessage(role="user", content="a"), ChatMessage(role="assistant", content="b"), ChatMessage(role="user", content="c")]))
    assert [c["role"] for c in rec.json()["contents"]] == ["user", "model", "user"]


def test_structured_output_sends_response_schema():
    p, rec = provider(lambda req: httpx.Response(200, content=sse(part('{"answer": "y"}', "STOP"))))
    parsed, _ = p.generate_json(request(schema={**SCHEMA, "additionalProperties": False, "title": "T"}))
    assert parsed == {"answer": "y"}
    config = rec.json()["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert "additionalProperties" not in config["responseSchema"] and "title" not in config["responseSchema"]
    assert config["responseSchema"]["properties"]["answer"] == {"type": "string"}


def test_prepare_schema_converts_nullable_types():
    assert prepare_schema({"type": ["string", "null"], "default": "x"}) == {"type": "string", "nullable": True}
    # a property literally named "title" must survive
    assert "title" in prepare_schema({"type": "object", "properties": {"title": {"type": "string"}}})["properties"]


def test_structured_output_degrades_schema_then_json_mode_then_prompt():
    seen = []

    def handler(req):
        config = json.loads(req.content).get("generationConfig", {})
        level = "schema" if "responseSchema" in config else "json" if "responseMimeType" in config else "prompt"
        seen.append(level)
        if level == "schema":
            return httpx.Response(400, json={"error": {"code": 400, "message": "Invalid JSON payload: unknown field in response_schema", "status": "INVALID_ARGUMENT"}})
        if level == "json":
            return httpx.Response(400, json={"error": {"code": 400, "message": "response_mime_type application/json is not supported for this model", "status": "INVALID_ARGUMENT"}})
        return httpx.Response(200, content=sse(part('{"answer": "z"}', "STOP")))

    p, rec = provider(handler)
    parsed, _ = p.generate_json(request(schema=SCHEMA))
    assert parsed == {"answer": "z"} and seen == ["schema", "json", "prompt"]
    assert p.capabilities().structured_output == "prompt"
    assert "JSON Schema" in rec.json()["systemInstruction"]["parts"][0]["text"]


def test_auth_failure_variants():
    p, _ = provider(lambda req: httpx.Response(400, json={"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "auth"
    p, _ = provider(lambda req: httpx.Response(403, json={"error": {"code": 403, "message": "denied", "status": "PERMISSION_DENIED"}}))
    with pytest.raises(ProviderError) as info:
        p.list_models()
    assert info.value.kind == "auth"


def test_rate_limit():
    p, _ = provider(lambda req: httpx.Response(429, json={"error": {"code": 429, "message": "Resource has been exhausted", "status": "RESOURCE_EXHAUSTED"}}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "rate_limit" and info.value.retryable


def test_timeout():
    def handler(req):
        raise httpx.ReadTimeout("t", request=req)

    with pytest.raises(ProviderError) as info:
        list(provider(handler)[0].stream(request()))
    assert info.value.kind == "timeout"


def test_context_length():
    p, _ = provider(lambda req: httpx.Response(400, json={"error": {"code": 400, "message": "The input token count (2000000) exceeds the maximum number of tokens allowed (1048576).", "status": "INVALID_ARGUMENT"}}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "context_length"


def test_stream_without_finish_reason_is_incomplete():
    p, _ = provider(lambda req: httpx.Response(200, content=sse(part("partial"))))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "stream_incomplete"


def test_dropped_connection_is_incomplete():
    p, _ = provider(lambda req: httpx.Response(200, stream=BrokenStream(sse(part("partial")))))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "stream_incomplete"


def test_safety_block_is_refused():
    p, _ = provider(lambda req: httpx.Response(200, content=sse({"promptFeedback": {"blockReason": "SAFETY"}})))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "refused"
    p, _ = provider(lambda req: httpx.Response(200, content=sse(part("", "SAFETY"))))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "refused"


def test_model_names_with_prefix_are_not_doubled():
    p, rec = provider(lambda req: httpx.Response(200, content=sse(part("ok", "STOP"))))
    p.generate(request(model="models/chat-one"))
    assert rec.requests[-1].url.path == "/v1beta/models/chat-one:streamGenerateContent"
