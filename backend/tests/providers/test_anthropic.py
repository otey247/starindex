from __future__ import annotations

import httpx
import pytest

from starindex.providers.anthropic import API_VERSION, AnthropicProvider, prepare_schema
from starindex.providers.base import ProviderError

from .conftest import SCHEMA, BrokenStream, Recorder, cfg, request, sse


def events(*text: str, stop_reason: str = "end_turn", stop: bool = True, thinking: bool = False, stop_details=None):
    out = [("message_start", {"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 30, "cache_read_input_tokens": 10, "output_tokens": 1}}})]
    if thinking:
        out.append(("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private"}}))
    out.append(("ping", {"type": "ping"}))
    for piece in text:
        out.append(("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": piece}}))
    delta = {"stop_reason": stop_reason}
    if stop_details:
        delta["stop_details"] = stop_details
    out.append(("message_delta", {"type": "message_delta", "delta": delta, "usage": {"output_tokens": 17}}))
    if stop:
        out.append(("message_stop", {"type": "message_stop"}))
    return sse(*out)


def provider(handler, secret="sk-ant-test", **options):
    recorder = Recorder(handler)
    return AnthropicProvider(cfg("anthropic", **options), secret, transport=recorder.transport), recorder


def test_model_discovery_paginates_and_reads_context_window():
    pages = {
        None: {"data": [{"id": "model-a", "display_name": "Model A", "max_input_tokens": 1000000, "max_tokens": 128000,
                         "capabilities": {"structured_outputs": {"supported": True}}}], "has_more": True, "last_id": "model-a"},
        "model-a": {"data": [{"id": "model-b", "display_name": "Model B", "max_input_tokens": 200000}], "has_more": False, "last_id": "model-b"},
    }

    def handler(req):
        assert req.url.path == "/v1/models"
        return httpx.Response(200, json=pages[req.url.params.get("after_id")])

    p, rec = provider(handler)
    models = p.list_models()
    assert [(m.id, m.display_name, m.context_window, m.kind) for m in models] == [
        ("model-a", "Model A", 1000000, "chat"), ("model-b", "Model B", 200000, "chat"),
    ]
    assert models[0].details == {"max_output_tokens": 128000, "structured_outputs": True}
    headers = rec.requests[0].headers
    assert headers["x-api-key"] == "sk-ant-test" and headers["anthropic-version"] == API_VERSION
    assert "authorization" not in headers


def test_streaming_happy_path_ignores_thinking_and_sends_no_sampling_parameters():
    p, rec = provider(lambda req: httpx.Response(200, content=events("Hel", "lo", thinking=True)))
    collected = list(p.stream(request(temperature=0.7, max_output_tokens=1000)))
    assert [e.text for e in collected if e.type == "delta"] == ["Hel", "lo"]
    final = collected[-1]
    assert final.finish_reason == "stop" and (final.usage.input_tokens, final.usage.output_tokens) == (40, 17)
    body = rec.json()
    assert rec.requests[-1].url == "https://api.anthropic.com/v1/messages"
    assert body["system"] == "Be brief." and body["stream"] is True
    assert body["messages"] == [{"role": "user", "content": "Hello"}]
    for forbidden in ("temperature", "top_p", "top_k", "thinking", "tool_choice", "tools"):
        assert forbidden not in body
    assert body["max_tokens"] == 1000 + 8192  # caller's budget plus thinking headroom
    caps = p.capabilities()
    assert caps.accepts_temperature is False and caps.is_paid and caps.structured_output == "json_schema"


def test_effort_and_headroom_options():
    p, rec = provider(lambda req: httpx.Response(200, content=events("ok")), effort="low", thinking_headroom_tokens=0)
    p.generate(request(max_output_tokens=500))
    body = rec.json()
    assert body["output_config"] == {"effort": "low"} and body["max_tokens"] == 500


def test_structured_output_uses_json_outputs_with_prepared_schema():
    p, rec = provider(lambda req: httpx.Response(200, content=events('{"answer": "42"}')))
    parsed, _ = p.generate_json(request(schema=SCHEMA))
    assert parsed == {"answer": "42"}
    fmt = rec.json()["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert "minimum" not in fmt["schema"]["properties"]["score"]
    assert "tool_choice" not in rec.json()


def test_prepare_schema_is_recursive_and_does_not_mutate():
    schema = {"type": "object", "properties": {"items": {"type": "array", "minItems": 3, "items": {"type": "object", "properties": {"n": {"type": "string", "maxLength": 5}}}}}}
    prepared = prepare_schema(schema)
    inner = prepared["properties"]["items"]
    assert inner["items"]["additionalProperties"] is False and "maxLength" not in inner["items"]["properties"]["n"]
    assert "minItems" not in inner and "minItems" in schema["properties"]["items"]


def test_model_without_json_outputs_degrades_to_prompt_and_remembers():
    def handler(req):
        body = __import__("json").loads(req.content)
        if "format" in body.get("output_config", {}):
            return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "output_config.format: structured outputs are not supported by this model"}})
        return httpx.Response(200, content=events('{"answer": "x"}'))

    p, rec = provider(handler)
    parsed, _ = p.generate_json(request(schema=SCHEMA))
    assert parsed == {"answer": "x"} and len(rec.requests) == 2
    assert "JSON Schema" in rec.json()["system"]
    p.generate_json(request(schema=SCHEMA))
    assert len(rec.requests) == 3


def test_auth_failure():
    p, _ = provider(lambda req: httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}, headers={"request-id": "req_9"}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert (info.value.kind, info.value.request_id, info.value.code) == ("auth", "req_9", "authentication_error")


def test_missing_key_never_sends_a_request():
    p, rec = provider(lambda req: httpx.Response(200), secret=None)
    with pytest.raises(ProviderError) as info:
        p.list_models()
    assert info.value.kind == "not_configured" and rec.requests == []


def test_rate_limit_and_overload():
    p, _ = provider(lambda req: httpx.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}, headers={"retry-after": "12"}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "rate_limit" and info.value.retry_after_s == 12
    p, _ = provider(lambda req: httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "unavailable" and info.value.retryable


def test_billing_error_is_usage_limit():
    p, _ = provider(lambda req: httpx.Response(402, json={"type": "error", "error": {"type": "billing_error", "message": "credit balance too low"}}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "usage_limit"


def test_timeout():
    def handler(req):
        raise httpx.ReadTimeout("t", request=req)

    with pytest.raises(ProviderError) as info:
        list(provider(handler)[0].stream(request()))
    assert info.value.kind == "timeout"


def test_context_length():
    p, rec = provider(lambda req: httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "prompt is too long: 1200000 tokens > 1000000 maximum"}}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request(schema=SCHEMA)))
    assert info.value.kind == "context_length" and len(rec.requests) == 1  # not mistaken for a format problem


def test_stream_without_message_stop_is_incomplete():
    p, _ = provider(lambda req: httpx.Response(200, content=events("partial", stop=False)))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "stream_incomplete"


def test_dropped_connection_is_incomplete():
    p, _ = provider(lambda req: httpx.Response(200, stream=BrokenStream(events("partial", stop=False))))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "stream_incomplete"


def test_error_event_mid_stream():
    body = sse(("content_block_delta", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "a"}}),
               ("error", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}))
    p, _ = provider(lambda req: httpx.Response(200, content=body))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "unavailable" and info.value.code == "overloaded_error"


def test_refusal_is_reported_not_returned_as_an_answer():
    p, _ = provider(lambda req: httpx.Response(200, content=events(stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber"})))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "refused" and "cyber" in info.value.message


def test_max_tokens_stop_reason_maps_to_length():
    p, _ = provider(lambda req: httpx.Response(200, content=events("cut", stop_reason="max_tokens")))
    assert p.generate(request()).finish_reason == "length"


def test_refusal_fallback_is_opt_in():
    p, rec = provider(lambda req: httpx.Response(200, content=events("ok")))
    p.generate(request())
    assert "fallbacks" not in rec.json() and "anthropic-beta" not in rec.requests[-1].headers
    p, rec = provider(lambda req: httpx.Response(200, content=events("ok")), refusal_fallback=True)
    p.generate(request())
    assert rec.json()["fallbacks"] == "default"
    assert rec.requests[-1].headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
