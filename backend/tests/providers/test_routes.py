"""HTTP routes for provider settings and ChatGPT plan, mounted on a throwaway app."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from starindex.api.routes import chatgpt as chatgpt_routes
from starindex.api.routes import providers as provider_routes
from starindex.providers.chatgpt_plan.service import ChatGPTPlanService
from starindex.providers.router import ModelRouter

from .chatgpt_fakes import ISSUED_CLIENT, FakeOpenAI, query
from .conftest import sse

SECRET = "sk-super-secret-value-123"


class Upstream:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        req.read()
        self.requests.append(req)
        if req.url.path.endswith("/models"):
            if req.headers.get("authorization") == "Bearer wrong":
                return httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}})
            return httpx.Response(200, json={"data": [{"id": "chat-model"}, {"id": "embed-model"}]})
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ready"}, "finish_reason": "stop"}]}, done=True))


@pytest.fixture
def upstream():
    return Upstream()


@pytest.fixture
def fake():
    return FakeOpenAI()


@pytest.fixture
def client(ctx, upstream, fake):
    ctx.__dict__["chatgpt"] = ChatGPTPlanService(
        ctx.db, ctx.secrets, ctx.settings_store, ctx.config.paths.data_dir, transport=fake.transport, callback_port=0
    )
    ctx.__dict__["router"] = ModelRouter(
        ctx.db, ctx.settings_store, ctx.providers, ctx.chatgpt, transport=httpx.MockTransport(upstream)
    )
    app = FastAPI()
    app.state.ctx = ctx
    app.include_router(provider_routes.router, prefix="/api")
    app.include_router(chatgpt_routes.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


def test_templates_and_initial_listing(client):
    templates = client.get("/api/providers/templates").json()
    assert {t["kind"] for t in templates} >= {"ollama", "openai", "chatgpt_plan"}
    providers = client.get("/api/providers").json()
    assert [p["id"] for p in providers] == ["chatgpt-plan"]
    plan = providers[0]
    assert plan["category"] == "chatgpt_plan" and plan["options"]["available"] is False
    assert "Not signed in" in plan["options"]["reason"] and plan["blocked_by_privacy"] is True
    assert plan["capabilities"]["accepts_temperature"] is False


def test_create_patch_secret_and_delete_never_echo_the_key(client, ctx):
    created = client.post("/api/providers", json={"kind": "lmstudio", "name": "LM Studio", "api_key": SECRET})
    assert created.status_code == 201
    info = created.json()
    assert info["id"] == "lm-studio" and info["has_secret"] is True and info["category"] == "local"
    assert info["base_url"] == "http://127.0.0.1:1234/v1" and info["blocked_by_privacy"] is False
    assert info["capabilities"]["is_local"] is True
    assert SECRET not in created.text

    patched = client.patch("/api/providers/lm-studio", json={"name": "LM Studio (desk)", "enabled": False})
    assert patched.json()["name"] == "LM Studio (desk)" and patched.json()["enabled"] is False

    cleared = client.delete("/api/providers/lm-studio/secret")
    assert cleared.json()["has_secret"] is False
    reset = client.put("/api/providers/lm-studio/secret", json={"api_key": SECRET})
    assert reset.json()["has_secret"] is True and SECRET not in reset.text

    listing = client.get("/api/providers")
    assert SECRET not in listing.text
    assert SECRET not in json.dumps([dict(r) for r in ctx.db.all("SELECT * FROM providers")])

    assert client.delete("/api/providers/lm-studio").json()["ok"] is True
    assert client.get("/api/providers/lm-studio/models").status_code == 404
    assert ctx.providers.get_secret("lm-studio") is None


def test_validation_errors(client):
    assert client.post("/api/providers", json={"kind": "nope", "name": "x"}).status_code == 400
    bad_url = client.post("/api/providers", json={"kind": "openai_compat", "name": "x", "base_url": "file:///etc/passwd"})
    assert bad_url.status_code == 400 and bad_url.json()["detail"]["kind"] == "bad_request"
    assert client.patch("/api/providers/missing", json={"name": "x"}).status_code == 404
    assert client.delete("/api/providers/chatgpt-plan").status_code == 400
    assert client.put("/api/providers/chatgpt-plan/secret", json={"api_key": "k"}).status_code == 400


def test_test_endpoint_and_model_listing_for_a_local_provider(client, upstream):
    client.post("/api/providers", json={"kind": "lmstudio", "name": "LM"})
    result = client.post("/api/providers/lm/test", json={}).json()
    assert result["ok"] is True and result["model_count"] == 2 and result["sample"] is None
    with_model = client.post("/api/providers/lm/test", json={"model": "chat-model"}).json()
    assert with_model["ok"] is True and with_model["sample"] == "ready"
    prompt = json.loads(upstream.requests[-1].content)["messages"][-1]["content"]
    assert "ready" in prompt   # the test prompt carries no repository content
    models = client.get("/api/providers/lm/models").json()
    assert [m["id"] for m in models["models"]] == ["chat-model", "embed-model"] and models["error"] is None
    assert [m["kind"] for m in models["models"]] == ["chat", "embedding"]


def test_test_endpoint_reports_auth_failure_as_a_result_not_a_crash(client):
    client.post("/api/providers", json={"kind": "lmstudio", "name": "LM", "api_key": "wrong"})
    result = client.post("/api/providers/lm/test").json()
    assert result["ok"] is False and result["error"]["kind"] == "auth" and result["error"]["status"] == 401
    models = client.get("/api/providers/lm/models").json()
    assert models["models"] == [] and models["error"]["kind"] == "auth"


def test_cloud_provider_is_not_contacted_while_cloud_traffic_is_disabled(client, upstream, ctx):
    client.post("/api/providers", json={"kind": "openai", "name": "OpenAI", "api_key": SECRET})
    info = next(p for p in client.get("/api/providers").json() if p["id"] == "openai")
    assert info["blocked_by_privacy"] is True and info["category"] == "cloud"
    blocked = client.post("/api/providers/openai/test", json={"model": "x"}).json()
    assert blocked["ok"] is False and blocked["error"]["kind"] == "cloud_disabled"
    assert client.get("/api/providers/openai/models").json()["error"]["kind"] == "cloud_disabled"
    assert upstream.requests == []
    ctx.settings_store.patch({"privacy": {"allow_cloud_models": True}})
    assert client.post("/api/providers/openai/test").json()["ok"] is True
    assert upstream.requests[-1].headers["authorization"] == f"Bearer {SECRET}"


def test_routing_round_trip_and_cleanup_on_delete(client, ctx):
    client.post("/api/providers", json={"kind": "ollama", "name": "Ollama"})
    client.post("/api/providers", json={"kind": "lmstudio", "name": "LM"})
    empty = client.get("/api/routing").json()
    assert empty["default"] is None and empty["effective"]["answer"] is None
    assert empty["task_labels"]["reasoning"]
    updated = client.put("/api/routing", json={
        "default": {"provider_id": "ollama", "model": "small"},
        "tasks": {"reasoning": {"provider_id": "lm", "model": "big"}, "classify": None},
    }).json()
    assert updated["effective"]["answer"] == {"provider_id": "ollama", "model": "small"}
    assert updated["effective"]["reasoning"] == {"provider_id": "lm", "model": "big"}
    assert "classify" not in updated["tasks"]
    assert client.put("/api/routing", json={"default": {"provider_id": "ghost", "model": "m"}}).status_code == 400
    assert client.put("/api/routing", json={"tasks": {"answer": {"provider_id": "lm", "model": " "}}}).status_code == 400
    # switching the default to another provider is an explicit settings change
    switched = client.put("/api/routing", json={"default": {"provider_id": "lm", "model": "big"}, "tasks": {}}).json()
    assert switched["default"]["provider_id"] == "lm" and switched["tasks"] == {}
    client.delete("/api/providers/lm")
    after = client.get("/api/routing").json()
    assert after["default"] is None and after["effective"]["answer"] is None


def test_usage_and_hardware_endpoints(client, ctx):
    client.post("/api/providers", json={"kind": "ollama", "name": "Ollama"})
    from starindex.providers.base import Usage
    from starindex.settings_store import Route

    resolved = ctx.router.resolve("answer", Route(provider_id="ollama", model="small"))
    ctx.router.record_usage(resolved, "answer", Usage(input_tokens=120, output_tokens=30))
    usage = client.get("/api/usage", params={"days": 7}).json()
    assert usage["rows"][0]["calls"] == 1 and usage["rows"][0]["cost_usd"] is None and usage["total_cost_usd"] is None
    hardware = client.get("/api/system/hardware").json()
    assert hardware["cpu_count"] >= 1 and isinstance(hardware["recommendations"], list)


def test_chatgpt_login_flow_over_http(client, ctx, fake):
    ctx.settings_store.patch({"privacy": {"allow_cloud_models": True}})
    status = client.get("/api/chatgpt/status").json()
    assert status["available"] is False and status["accounts"] == [] and status["usage_url"].endswith("/settings/usage")

    started = client.post("/api/chatgpt/login", json={}).json()
    assert started["status"] == "pending" and query(started["auth_url"])["client_id"] == "dynamic_agent_client"
    attempt = ctx.chatgpt._attempts[started["attempt_id"]]
    fake.queue_token(fake.token_body(nonce=attempt.nonce))
    ctx.chatgpt._on_callback(attempt, {"code": "c", "state": attempt.state, "client_id": ISSUED_CLIENT})

    polled = client.get(f"/api/chatgpt/login/{started['attempt_id']}").json()
    assert polled["status"] == "completed" and polled["plan_usage_enabled"] is True and polled["auth_url"] is None
    account_id = polled["account_id"]

    status = client.get("/api/chatgpt/status")
    body = status.json()
    assert body["available"] is True and body["active_account_id"] == account_id
    assert body["accounts"][0]["welcome_pending"] is True
    for secret in ("access-0", "refresh-0", attempt.code_verifier, ISSUED_CLIENT):
        assert secret not in status.text   # tokens and the full client id stay server-side
    plan = next(p for p in client.get("/api/providers").json() if p["id"] == "chatgpt-plan")
    assert plan["options"]["available"] is True and plan["has_secret"] is True

    assert client.post(f"/api/chatgpt/accounts/{account_id}/welcome-shown").json()["ok"] is True
    assert client.get("/api/chatgpt/status").json()["accounts"][0]["welcome_pending"] is False

    signed_out = client.post(f"/api/chatgpt/accounts/{account_id}/sign-out").json()
    assert signed_out["accounts"][0]["signed_in"] is False and signed_out["available"] is False
    assert client.post(f"/api/chatgpt/accounts/{account_id}/activate").status_code == 409
    reauth = client.post("/api/chatgpt/login", json={"account_id": account_id}).json()
    assert query(reauth["auth_url"])["client_id"] == ISSUED_CLIENT
    assert client.delete(f"/api/chatgpt/accounts/{account_id}").json()["accounts"] == []
    assert client.post("/api/chatgpt/accounts/unknown/activate").status_code == 404
    assert client.get("/api/chatgpt/login/unknown").json()["status"] == "expired"


def test_chatgpt_login_blocked_in_offline_mode(client, ctx):
    ctx.settings_store.patch({"privacy": {"offline_mode": True}})
    response = client.post("/api/chatgpt/login", json={})
    assert response.status_code == 403 and response.json()["detail"]["kind"] == "cloud_disabled"
