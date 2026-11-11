from __future__ import annotations

import httpx
import pytest

from starindex.providers.azure_openai import AzureOpenAIProvider
from starindex.providers.base import ProviderError

from .conftest import Recorder, cfg, request, sse

OK = sse({"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]}, {"choices": [{"delta": {}, "finish_reason": "stop"}]}, done=True)
ENDPOINT = "https://my-res.openai.azure.com"


def provider(handler, base_url=ENDPOINT, secret="az-key", **options):
    recorder = Recorder(handler)
    return AzureOpenAIProvider(cfg("azure_openai", base_url=base_url, **options), secret, transport=recorder.transport), recorder


@pytest.mark.parametrize("pasted", [ENDPOINT, ENDPOINT + "/", ENDPOINT + "/openai/v1/", ENDPOINT + "/openai"])
def test_v1_api_is_the_default_path(pasted):
    p, rec = provider(lambda req: httpx.Response(200, content=OK), base_url=pasted)
    assert p.generate(request(model="my-deployment", max_output_tokens=20)).text == "hi"
    sent = rec.requests[-1]
    assert str(sent.url) == ENDPOINT + "/openai/v1/chat/completions"
    assert "api-version" not in sent.url.params
    assert sent.headers["api-key"] == "az-key" and "authorization" not in sent.headers
    assert rec.json()["model"] == "my-deployment" and rec.json()["max_completion_tokens"] == 20


def test_deployment_scoped_path_when_api_version_is_configured():
    p, rec = provider(lambda req: httpx.Response(200, content=OK), api_version="2024-10-21")
    p.generate(request(model="my deployment"))
    sent = rec.requests[-1]
    assert sent.url.path == "/openai/deployments/my%20deployment/chat/completions" or sent.url.raw_path.startswith(b"/openai/deployments/my%20deployment/chat/completions")
    assert sent.url.params["api-version"] == "2024-10-21"


def test_configured_deployments_are_the_model_list_without_a_request():
    p, rec = provider(lambda req: httpx.Response(500), deployments="chat-prod, text-embedding-prod")
    models = p.list_models()
    assert [(m.id, m.kind, m.details["source"]) for m in models] == [
        ("chat-prod", "chat", "deployment"), ("text-embedding-prod", "embedding", "deployment"),
    ]
    assert rec.requests == []


def test_model_listing_falls_back_to_resource_models_with_a_note():
    def handler(req):
        assert req.url.path == "/openai/v1/models"
        return httpx.Response(200, json={"data": [{"id": "some-base-model"}]})

    models = provider(handler)[0].list_models()
    assert models[0].details["source"] == "resource_models" and "deployment name" in models[0].details["note"]


def test_missing_endpoint_or_key():
    with pytest.raises(ProviderError) as info:
        AzureOpenAIProvider(cfg("azure_openai"), "k")
    assert info.value.kind == "not_configured"
    p, rec = provider(lambda req: httpx.Response(200, content=OK), secret=None)
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "not_configured" and rec.requests == []


def test_auth_rate_limit_and_deployment_not_found():
    p, _ = provider(lambda req: httpx.Response(401, json={"error": {"code": "401", "message": "Access denied due to invalid subscription key"}}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "auth"
    p, _ = provider(lambda req: httpx.Response(429, json={"error": {"code": "429", "message": "Requests exceeded token rate limit"}}, headers={"retry-after": "20"}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "rate_limit" and info.value.retry_after_s == 20
    p, _ = provider(lambda req: httpx.Response(404, json={"error": {"code": "DeploymentNotFound", "message": "The API deployment for this resource does not exist."}}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "unsupported"


def test_timeout_context_length_and_incomplete_stream():
    def slow(req):
        raise httpx.ReadTimeout("t", request=req)

    with pytest.raises(ProviderError) as info:
        list(provider(slow)[0].stream(request()))
    assert info.value.kind == "timeout"
    p, _ = provider(lambda req: httpx.Response(400, json={"error": {"code": "context_length_exceeded", "message": "maximum context length exceeded"}}))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "context_length"
    p, _ = provider(lambda req: httpx.Response(200, content=sse({"choices": [{"delta": {"content": "par"}, "finish_reason": None}]})))
    with pytest.raises(ProviderError) as info:
        list(p.stream(request()))
    assert info.value.kind == "stream_incomplete"


def test_structured_output_and_capabilities():
    body = sse({"choices": [{"delta": {"content": '{"answer": "a"}'}, "finish_reason": "stop"}]}, done=True)
    p, rec = provider(lambda req: httpx.Response(200, content=body))
    parsed, _ = p.generate_json(request(schema={"type": "object", "properties": {"answer": {"type": "string"}}}))
    assert parsed == {"answer": "a"} and rec.json()["response_format"]["type"] == "json_schema"
    caps = p.capabilities()
    assert caps.is_paid and not caps.is_local
