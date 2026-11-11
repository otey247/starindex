from __future__ import annotations

import httpx
import pytest

from starindex.providers.base import ProviderError, parse_json_output
from starindex.providers.openai_compat import OpenAICompatProvider, classify_model

from .conftest import SCHEMA, BrokenStream, Recorder, cfg, request, sse


def chunk(text: str | None = None, finish: str | None = None) -> dict:
    delta = {"content": text} if text is not None else {}
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


USAGE = {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5}}


def provider(handler, kind="lmstudio", secret=None, **options):
    recorder = Recorder(handler)
    return OpenAICompatProvider(cfg(kind, **options), secret, transport=recorder.transport), recorder


def test_model_discovery_classifies_and_sorts():
    def handler(req):
        assert req.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [
            {"id": "text-embedding-nomic"}, {"id": "qwen-coder", "max_model_len": 32768}, {"id": "whisper-large"},
        ]})

    p, _ = provider(handler)
    models = p.list_models()
    assert [(m.id, m.kind) for m in models] == [
        ("qwen-coder", "chat"), ("text-embedding-nomic", "embedding"), ("whisper-large", "unknown"),
    ]
    assert models[0].context_window == 32768
    assert classify_model("bge-reranker-v2") == "rerank"


def test_streaming_happy_path_reports_usage_and_sends_expected_body():
    def handler(req):
        return httpx.Response(200, content=sse(chunk("Hel"), chunk("lo"), chunk(finish="stop"), USAGE, done=True))

    p, rec = provider(handler)
    events = list(p.stream(request(temperature=0.1, max_output_tokens=99)))
    assert [e.text for e in events if e.type == "delta"] == ["Hel", "lo"]
    final = events[-1]
    assert final.type == "completed" and final.finish_reason == "stop"
    assert (final.usage.input_tokens, final.usage.output_tokens) == (12, 5)
    body = rec.json()
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["messages"][0] == {"role": "system", "content": "Be brief."}
    assert body["temperature"] == 0.1 and body["max_tokens"] == 99
    assert "authorization" not in rec.requests[-1].headers  # local server, no key configured


def test_openai_kind_uses_bearer_key_and_max_completion_tokens():
    def handler(req):
        return httpx.Response(200, content=sse(chunk("ok"), chunk(finish="stop"), done=True))

    p, rec = provider(handler, kind="openai", secret="sk-test")
    result = p.generate(request(max_output_tokens=10))
    assert result.text == "ok"
    assert rec.requests[-1].url == "https://api.openai.com/v1/chat/completions"
    assert rec.requests[-1].headers["authorization"] == "Bearer sk-test"
    assert rec.json()["max_completion_tokens"] == 10 and "max_tokens" not in rec.json()
    assert p.capabilities().is_paid and not p.capabilities().is_local


def test_openai_without_key_is_not_configured():
    p, rec = provider(lambda req: httpx.Response(200, json={"data": []}), kind="openai")
    with pytest.raises(ProviderError) as info:
        p.list_models()
    assert info.value.kind == "not_configured" and rec.requests == []


def test_structured_output_uses_json_schema_then_degrades_to_json_object_then_prompt():
    seen: list[str | None] = []

    def handler(req):
        body = __import__("json").loads(req.content)
        fmt = (body.get("response_format") or {}).get("type")
        seen.append(fmt)
        if fmt == "json_schema":
            return httpx.Response(400, json={"error": {"message": "response_format json_schema is not supported"}})
        if fmt == "json_object":
            return httpx.Response(400, json={"error": {"message": "'response_format.type' json_object unsupported"}})
        return httpx.Response(200, content=sse(chunk('{"answer": "42"}'), chunk(finish="stop"), done=True))

    p, rec = provider(handler)
    parsed, _ = p.generate_json(request(schema=SCHEMA))
    assert parsed == {"answer": "42"}
    assert seen == ["json_schema", "json_object", None]
    assert p.capabilities().structured_output == "prompt"
    assert "JSON Schema" in rec.json()["messages"][0]["content"]
    # The working level is remembered: the next call goes straight to prompt-only.
    p.generate_json(request(schema=SCHEMA))
    assert seen[-1] is None and len(seen) == 4


def test_structured_output_json_schema_body_when_supported():
    def handler(req):
        return httpx.Response(200, content=sse(chunk('{"answer": "x"}'), chunk(finish="stop"), done=True))

    p, rec = provider(handler)
    parsed, _ = p.generate_json(request(schema=SCHEMA, json_schema_name="my result!"))
    assert parsed == {"answer": "x"}
    fmt = rec.json()["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["schema"] == SCHEMA
    assert fmt["json_schema"]["name"] == "my_result_"


def test_generate_json_repairs_once_then_fails_with_invalid_response():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, content=sse(chunk("definitely not json"), chunk(finish="stop"), done=True))

    p, _ = provider(handler)
    with pytest.raises(ProviderError) as info:
        p.generate_json(request(schema=SCHEMA))
    assert info.value.kind == "invalid_response" and calls["n"] == 2


def test_unsupported_optional_parameters_are_dropped_and_remembered():
    def handler(req):
        body = __import__("json").loads(req.content)
        if "stream_options" in body:
            return httpx.Response(400, json={"error": {"message": "Unrecognized request argument: stream_options"}})
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature'", "param": "temperature"}})
        return httpx.Response(200, content=sse(chunk("fine"), chunk(finish="stop"), done=True))

    p, rec = provider(handler)
    assert p.generate(request()).text == "fine"
    assert len(rec.requests) == 3
    assert p.capabilities().accepts_temperature is False
    p.generate(request())
    assert len(rec.requests) == 4  # no renegotiation the second time


def test_auth_failure():
    p, _ = provider(lambda req: httpx.Response(401, json={"error": {"message": "Incorrect API key", "code": "invalid_api_key"}}), kind="openai", secret="bad")
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "auth" and info.value.status == 401 and not info.value.retryable


def test_rate_limit_carries_retry_after():
    p, _ = provider(lambda req: httpx.Response(429, json={"error": {"message": "Too many requests"}}, headers={"retry-after": "3"}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "rate_limit" and info.value.retry_after_s == 3 and info.value.retryable


def test_quota_exhaustion_is_usage_limit():
    p, _ = provider(lambda req: httpx.Response(429, json={"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}), kind="openai", secret="k")
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "usage_limit"


def test_timeout():
    def handler(req):
        raise httpx.ReadTimeout("timed out", request=req)

    p, _ = provider(handler)
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "timeout" and info.value.retryable


def test_connection_refused_is_network_error_with_hint():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    p, _ = provider(handler)
    with pytest.raises(ProviderError) as info:
        p.list_models()
    assert info.value.kind == "network" and "Is the server running" in info.value.message


def test_context_length_error():
    p, _ = provider(lambda req: httpx.Response(400, json={"error": {"message": "This model's maximum context length is 4096 tokens", "code": "context_length_exceeded"}}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "context_length"


def test_bad_request_is_not_renegotiated():
    p, rec = provider(lambda req: httpx.Response(400, json={"error": {"message": "messages must not be empty"}}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "bad_request" and len(rec.requests) == 1


def test_stream_cut_off_without_done_or_finish_reason_is_incomplete():
    p, _ = provider(lambda req: httpx.Response(200, content=sse(chunk("partial"))))
    events = []
    with pytest.raises(ProviderError) as info:
        for event in p.stream(request()):
            events.append(event)
    assert info.value.kind == "stream_incomplete"
    assert [e.text for e in events] == ["partial"]


def test_connection_dropped_mid_stream_is_incomplete():
    p, _ = provider(lambda req: httpx.Response(200, stream=BrokenStream(sse(chunk("par")))))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "stream_incomplete"


def test_error_object_inside_stream():
    p, _ = provider(lambda req: httpx.Response(200, content=sse(chunk("a"), {"error": {"message": "Rate limit hit", "type": "rate_limit"}})))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "rate_limit"


def test_content_filter_is_refused():
    p, _ = provider(lambda req: httpx.Response(200, content=sse(chunk(finish="content_filter"), done=True)))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "refused"


def test_embeddings_are_returned_in_request_order():
    def handler(req):
        assert req.url.path == "/v1/embeddings"
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0.3, 0.4]}, {"index": 0, "embedding": [0.1, 0.2]}]})

    p, _ = provider(handler)
    assert p.embed("embed-model", ["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert p.embed("embed-model", []) == []


def test_base_url_gets_v1_appended_and_remote_endpoints_are_cloud():
    local = OpenAICompatProvider(cfg("openai_compat", base_url="http://192.168.1.50:8080"), None, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert local.base_url == "http://192.168.1.50:8080/v1" and local.capabilities().is_local
    remote = OpenAICompatProvider(cfg("openai_compat", base_url="https://llm.example.com/api/v1"), "k", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert remote.base_url == "https://llm.example.com/api/v1"
    assert not remote.capabilities().is_local and remote.capabilities().is_paid


def test_extra_headers_cannot_override_authentication():
    def handler(req):
        return httpx.Response(200, json={"data": []})

    recorder = Recorder(handler)
    p = OpenAICompatProvider(
        cfg("openai_compat", base_url="http://127.0.0.1:9000/v1", headers={"X-Org": "team", "Authorization": "Bearer evil"}),
        "real-key", transport=recorder.transport,
    )
    p.list_models()
    headers = recorder.requests[-1].headers
    assert headers["x-org"] == "team" and headers["authorization"] == "Bearer real-key"


def test_parse_helper_is_shared():
    assert parse_json_output('{"k": 1}') == {"k": 1}


def test_server_that_answers_500_to_unknown_response_format_is_renegotiated():
    """llama-cpp-python's server rejects response_format json_schema with HTTP 500 (found in a live run)."""
    seen: list[str | None] = []

    def handler(req):
        body = __import__("json").loads(req.content)
        fmt = (body.get("response_format") or {}).get("type")
        seen.append(fmt)
        if fmt == "json_schema":
            detail = "1 validation error: {'loc': ('body', 'response_format', 'type'), 'msg': \"Input should be 'text' or 'json_object'\", 'input': 'json_schema'}"
            return httpx.Response(500, json={"error": {"message": detail}})
        return httpx.Response(200, content=sse(chunk('{"answer": "42"}'), chunk(finish="stop"), done=True))

    p, _ = provider(handler)
    parsed, _ = p.generate_json(request(schema=SCHEMA))
    assert parsed == {"answer": "42"} and seen == ["json_schema", "json_object"]
    assert p.capabilities().structured_output == "json_object"


def test_plain_server_error_is_not_renegotiated():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500, json={"error": {"message": "internal error while loading the model"}})

    p, _ = provider(handler)
    with pytest.raises(ProviderError) as err:
        p.generate_json(request(schema=SCHEMA))
    assert err.value.kind == "unavailable" and len(calls) == 1
