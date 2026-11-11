"""ChatGPT plan inference through the Responses API (mocked)."""

from __future__ import annotations

import httpx
import pytest

from starindex.providers.base import ProviderConfig, ProviderError
from starindex.providers.chatgpt_plan.client import UNSUPPORTED_FIELDS, ChatGPTPlanProvider
from starindex.providers.chatgpt_plan.service import ChatGPTPlanService

from .chatgpt_fakes import NO_PLAN_SCOPES, FakeOpenAI, responses_sse
from .conftest import SCHEMA, BrokenStream, request
from .test_chatgpt_service import sign_in

CONFIG = ProviderConfig(id="chatgpt-plan", kind="chatgpt_plan", name="ChatGPT plan")
COMPLETED = {"type": "response.completed", "response": {"status": "completed", "usage": {"input_tokens": 33, "output_tokens": 8}}}


def delta(text):
    return {"type": "response.output_text.delta", "delta": text}


@pytest.fixture
def fake():
    return FakeOpenAI()


@pytest.fixture
def service(ctx, fake):
    svc = ChatGPTPlanService(ctx.db, ctx.secrets, ctx.settings_store, ctx.config.paths.data_dir, transport=fake.transport, callback_port=0)
    ctx.settings_store.patch({"privacy": {"allow_cloud_models": True}})
    yield svc
    svc.close()


@pytest.fixture
def provider(service, fake):
    sign_in(service, fake)
    p = ChatGPTPlanProvider(CONFIG, service, transport=fake.transport)
    yield p
    p.close()


def test_model_catalog_uses_models_array_and_visibility_filter(provider, fake):
    fake.models_response = httpx.Response(200, json={"models": [
        {"slug": "model-b", "display_name": "Model B", "visibility": "list"},
        {"slug": "hidden-model", "display_name": "Hidden", "visibility": "hide"},
        {"slug": "model-a", "display_name": "Model A", "visibility": "list"},
        {"display_name": "No slug", "visibility": "list"},
    ]})
    models = provider.list_models()
    assert [(m.id, m.display_name) for m in models] == [("model-b", "Model B"), ("model-a", "Model A")]  # server order kept
    sent = fake.sent("https://api.openai.com/v1/models")[0]
    assert sent.headers["authorization"] == "Bearer access-0"


def test_model_catalog_without_models_array_is_an_error(provider, fake):
    fake.models_response = httpx.Response(200, json={"data": [{"id": "x"}]})
    with pytest.raises(ProviderError) as info:
        provider.list_models()
    assert info.value.kind == "invalid_response"


def test_stream_to_completion_and_request_body(provider, fake):
    fake.responses_response = httpx.Response(200, content=responses_sse(
        {"type": "response.created", "response": {}}, delta("Hel"), delta("lo"), COMPLETED,
    ))
    events = list(provider.stream(request(temperature=0.9, max_output_tokens=500)))
    assert [e.text for e in events if e.type == "delta"] == ["Hel", "lo"]
    assert events[-1].type == "completed" and (events[-1].usage.input_tokens, events[-1].usage.output_tokens) == (33, 8)
    sent = fake.sent("https://api.openai.com/v1/responses")[0]
    assert sent.method == "POST" and sent.headers["authorization"] == "Bearer access-0"
    body = __import__("json").loads(sent.content)
    assert body["store"] is False and body["stream"] is True
    assert body["model"] == "test-model"
    assert body["input"] == [{"role": "user", "content": "Hello"}]
    assert body["instructions"] == "Be brief."
    assert not UNSUPPORTED_FIELDS & set(body)
    assert set(body) == {"model", "input", "store", "stream", "instructions"}
    assert all(item.get("role") != "system" for item in body["input"])
    assert "tools" not in body


def test_capabilities_reflect_preview_limits(provider):
    caps = provider.capabilities()
    assert caps.structured_output == "prompt" and not caps.accepts_temperature and not caps.accepts_max_output_tokens
    assert caps.is_paid and not caps.is_local and not caps.embeddings


def test_structured_output_is_prompt_only(provider, fake):
    fake.responses_response = httpx.Response(200, content=responses_sse(delta('{"answer": "ok"}'), COMPLETED))
    parsed, _ = provider.generate_json(request(schema=SCHEMA))
    assert parsed == {"answer": "ok"}
    body = __import__("json").loads(fake.sent("https://api.openai.com/v1/responses")[0].content)
    assert "JSON Schema" in body["instructions"] and "text" not in body and "response_format" not in body


def test_usage_limit_failure_after_streaming_began(provider, fake):
    fake.responses_response = httpx.Response(200, content=responses_sse(
        delta("partial"),
        {"type": "response.failed", "response": {"status": "failed", "error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}}},
    ))
    seen = []
    with pytest.raises(ProviderError) as info:
        for event in provider.stream(request()):
            seen.append(event)
    error = info.value
    assert [e.text for e in seen] == ["partial"]
    assert error.kind == "usage_limit" and error.code == "subscription_sharing_usage_limit_exceeded"
    assert error.action_url == "https://chatgpt.com/settings/usage" and error.action_label == "Manage usage"
    assert not error.retryable


def test_usage_unavailable_after_streaming_began_is_retryable(provider, fake):
    fake.responses_response = httpx.Response(200, content=responses_sse(
        delta("x"), {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_unavailable", "message": "later"}}},
    ))
    with pytest.raises(ProviderError) as info:
        list(provider.stream(request()))
    assert info.value.kind == "unavailable" and info.value.retryable


def test_stream_that_ends_without_response_completed_is_not_success(provider, fake):
    fake.responses_response = httpx.Response(200, content=responses_sse(delta("partial")))
    with pytest.raises(ProviderError) as info:
        list(provider.stream(request()))
    assert info.value.kind == "stream_incomplete"


def test_interrupted_stream(provider, fake):
    fake.responses_response = httpx.Response(200, stream=BrokenStream(responses_sse(delta("partial"))))
    with pytest.raises(ProviderError) as info:
        list(provider.stream(request()))
    assert info.value.kind == "stream_incomplete"


def test_response_incomplete_is_handled_separately(provider, fake):
    fake.responses_response = httpx.Response(200, content=responses_sse(
        delta("partial"), {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}},
    ))
    with pytest.raises(ProviderError) as info:
        list(provider.stream(request()))
    assert info.value.kind == "stream_incomplete" and info.value.code == "max_output_tokens"


def test_explicit_error_event(provider, fake):
    fake.responses_response = httpx.Response(200, content=responses_sse(
        {"type": "error", "code": "subscription_sharing_user_unavailable", "message": "try later"},
    ))
    with pytest.raises(ProviderError) as info:
        list(provider.stream(request()))
    assert info.value.kind == "unavailable" and info.value.retryable


@pytest.mark.parametrize(
    ("status", "code", "kind", "retryable"),
    [
        (403, "subscription_sharing_user_not_eligible", "not_eligible", False),
        (429, "subscription_sharing_usage_limit_exceeded", "usage_limit", False),
        (503, "subscription_sharing_usage_unavailable", "unavailable", True),
        (400, "subscription_sharing_unsupported_capability", "unsupported", False),
        (403, "subscription_sharing_route_not_supported", "unsupported", False),
        (401, "subscription_sharing_invalid_user", "auth", False),
        (403, "chatpass_v2_scope_not_authorized", "consent_required", False),
        (403, "chatpass_v2_invalid_authorization_context", "consent_required", False),
        (503, "subscription_sharing_user_unavailable", "unavailable", True),
    ],
)
def test_structured_error_codes(provider, fake, status, code, kind, retryable):
    fake.responses_response = httpx.Response(
        status, json={"error": {"code": code, "message": "details", "param": "tools"}}, headers={"x-request-id": "req_abc"},
    )
    with pytest.raises(ProviderError) as info:
        list(provider.stream(request()))
    error = info.value
    assert (error.kind, error.retryable, error.code, error.status, error.request_id) == (kind, retryable, code, status, "req_abc")
    if code == "subscription_sharing_unsupported_capability":
        assert error.param == "tools" and "tools" in error.message
    if kind == "usage_limit":
        assert error.action_url == "https://chatgpt.com/settings/usage"


@pytest.mark.parametrize(
    ("status", "kind", "retryable"), [(401, "auth", False), (403, "not_eligible", False), (503, "unavailable", True)]
)
def test_pre_stream_admission_failures_with_detail_body(provider, fake, status, kind, retryable):
    fake.responses_response = httpx.Response(status, json={"detail": "direct route admission failed"})
    with pytest.raises(ProviderError) as info:
        list(provider.stream(request()))
    error = info.value
    assert (error.kind, error.retryable, error.status, error.code) == (kind, retryable, status, None)
    assert "direct route admission failed" in error.message  # diagnostic text is preserved, not treated as a code


def test_plain_rate_limit_and_timeout(provider, fake):
    fake.responses_response = httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "message": "slow down"}}, headers={"retry-after": "9"})
    with pytest.raises(ProviderError) as info:
        list(provider.stream(request()))
    assert info.value.kind == "rate_limit" and info.value.retry_after_s == 9

    def timeout_handler(req):
        raise httpx.ReadTimeout("t", request=req)

    slow = ChatGPTPlanProvider(CONFIG, provider.service, transport=httpx.MockTransport(timeout_handler))
    with pytest.raises(ProviderError) as info:
        list(slow.stream(request()))
    assert info.value.kind == "timeout"


def test_context_length_error(provider, fake):
    fake.responses_response = httpx.Response(400, json={"error": {"code": "context_length_exceeded", "message": "Your input exceeds the context window"}})
    with pytest.raises(ProviderError) as info:
        list(provider.stream(request()))
    assert info.value.kind == "context_length"


def test_inference_is_refused_without_plan_usage_scope(service, fake):
    sign_in(service, fake, scope=NO_PLAN_SCOPES)
    p = ChatGPTPlanProvider(CONFIG, service, transport=fake.transport)
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "consent_required"
    with pytest.raises(ProviderError):
        p.list_models()
    assert fake.sent("https://api.openai.com/v1/") == []   # nothing was sent to the API
    assert not p.health().ok


def test_inference_is_refused_when_not_signed_in(service, fake):
    p = ChatGPTPlanProvider(CONFIG, service, transport=fake.transport)
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "not_configured" and fake.requests == []


def test_expiring_token_is_refreshed_before_inference(service, fake):
    sign_in(service, fake, expires_in=30)
    fake.responses_response = httpx.Response(200, content=responses_sse(delta("ok"), COMPLETED))
    p = ChatGPTPlanProvider(CONFIG, service, transport=fake.transport)
    assert p.generate(request()).text == "ok"
    assert fake.sent("https://api.openai.com/v1/responses")[0].headers["authorization"] == "Bearer access-1"


def test_only_api_openai_com_receives_the_access_token(provider, fake):
    fake.responses_response = httpx.Response(200, content=responses_sse(delta("ok"), COMPLETED))
    provider.generate(request())
    bearer_hosts = {r.url.host for r in fake.requests if r.headers.get("authorization", "").startswith("Bearer ")}
    assert bearer_hosts == {"api.openai.com"}
    assert all("backend-api" not in str(r.url) for r in fake.requests)
