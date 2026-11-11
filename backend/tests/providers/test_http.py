from __future__ import annotations

import httpx
import pytest

from starindex.providers.base import ProviderError, parse_json_output
from starindex.providers.http import (
    error_from_response,
    is_local_url,
    is_private_host,
    iter_ndjson,
    iter_sse,
    kind_for_status,
    map_transport_error,
    validate_base_url,
)


def test_sse_parser_handles_events_comments_and_multiline_data():
    lines = iter(
        [": keep-alive", "event: delta", "data: {\"a\":1}", "", "data: line1", "data: line2", "", "data: tail"]
    )
    messages = list(iter_sse(lines))
    assert [(m.event, m.data) for m in messages] == [("delta", '{"a":1}'), (None, "line1\nline2"), (None, "tail")]


def test_ndjson_parser_skips_blank_lines_and_rejects_garbage():
    assert list(iter_ndjson(iter(['{"a": 1}', "", '{"b": 2}']))) == [{"a": 1}, {"b": 2}]
    with pytest.raises(ValueError):
        list(iter_ndjson(iter(["not json"])))


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com", "file:///etc/passwd", "https://user:pass@example.com/v1", "http://", "", "javascript:alert(1)"],
)
def test_base_url_validation_rejects_unsafe_urls(url):
    with pytest.raises(ProviderError) as info:
        validate_base_url(url)
    assert info.value.kind == "bad_request"


def test_base_url_validation_normalises():
    assert validate_base_url(" http://127.0.0.1:1234/v1/ ") == "http://127.0.0.1:1234/v1"


@pytest.mark.parametrize(
    ("host", "private"),
    [
        ("127.0.0.1", True), ("localhost", True), ("::1", True), ("192.168.1.20", True), ("10.0.0.5", True),
        ("gpu-box", True), ("my-nas.local", True), ("api.openai.com", False), ("8.8.8.8", False), ("example.com", False),
    ],
)
def test_private_host_classification(host, private):
    assert is_private_host(host) is private


def test_is_local_url():
    assert is_local_url("http://127.0.0.1:11434")
    assert is_local_url("http://[::1]:8000/v1")
    assert not is_local_url("https://llm.example.com/v1")
    assert not is_local_url(None)


@pytest.mark.parametrize(
    ("status", "message", "kind", "retryable"),
    [
        (401, "bad key", "auth", False),
        (403, "forbidden", "auth", False),
        (429, "slow down", "rate_limit", True),
        (429, "You exceeded your current quota", "usage_limit", False),
        (400, "This model's maximum context length is 8192 tokens", "context_length", False),
        (400, "unknown field", "bad_request", False),
        (413, "too big", "context_length", False),
        (404, "no such model", "unsupported", False),
        (500, "boom", "unavailable", True),
        (503, "overloaded", "unavailable", True),
        (408, "timeout", "timeout", True),
    ],
)
def test_status_mapping(status, message, kind, retryable):
    assert kind_for_status(status, message) == (kind, retryable)


def test_error_from_response_extracts_code_request_id_and_retry_after():
    response = httpx.Response(
        429,
        json={"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded", "param": None}},
        headers={"x-request-id": "req_123", "retry-after": "7"},
    )
    error = error_from_response(response, provider_id="p", provider_kind="openai")
    assert (error.kind, error.code, error.request_id, error.retry_after_s, error.retryable) == (
        "rate_limit", "rate_limit_exceeded", "req_123", 7.0, True,
    )
    assert error.status == 429 and "Rate limit reached" in error.message


def test_transport_error_mapping():
    timeout = map_transport_error(httpx.ReadTimeout("t"), provider_id="p", provider_kind="ollama")
    assert timeout.kind == "timeout" and timeout.retryable
    connect = map_transport_error(
        httpx.ConnectError("refused"), provider_id="p", provider_kind="ollama", base_url="http://127.0.0.1:11434"
    )
    assert connect.kind == "network" and "Is the server running" in connect.message
    dropped = map_transport_error(httpx.ReadError("reset"), provider_id="p", provider_kind="x", mid_stream=True)
    assert dropped.kind == "stream_incomplete"


def test_parse_json_output_tolerates_fences_and_prose():
    assert parse_json_output('{"a": 1}') == {"a": 1}
    assert parse_json_output('Sure!\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    assert parse_json_output('Here you go: {"a": "x\\"y"} thanks') == {"a": 'x"y'}
    with pytest.raises(ValueError):
        parse_json_output("no json here")
