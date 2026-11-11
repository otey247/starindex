"""Routing, privacy enforcement, explicit switching and usage accounting."""

from __future__ import annotations

import json

import httpx
import pytest

from starindex.providers.base import ProviderError, Usage
from starindex.providers.chatgpt_plan.service import ChatGPTPlanService
from starindex.providers.registry import ProviderStore
from starindex.providers.router import ModelRouter
from starindex.settings_store import ModelPrice, Route

from .chatgpt_fakes import NO_PLAN_SCOPES, FakeOpenAI
from .conftest import request, sse
from .test_chatgpt_service import sign_in

OK = sse({"choices": [{"delta": {"content": "local answer"}, "finish_reason": "stop"}]}, done=True)


class Backend:
    """Routes mock requests by host so several providers can share one transport."""

    def __init__(self):
        self.hits: list[str] = []
        self.fail_hosts: set[str] = set()

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.hits.append(req.url.host)
        if req.url.host in self.fail_hosts:
            return httpx.Response(500, json={"error": {"message": "exploded"}})
        if req.url.path.endswith("/api/chat"):
            return httpx.Response(200, content=(json.dumps({"message": {"content": "ollama answer"}, "done": True, "prompt_eval_count": 5, "eval_count": 3}) + "\n").encode())
        return httpx.Response(200, content=OK)


@pytest.fixture
def backend():
    return Backend()


@pytest.fixture
def store(ctx):
    return ProviderStore(ctx.db, ctx.secrets)


@pytest.fixture
def router(ctx, store, backend):
    r = ModelRouter(ctx.db, ctx.settings_store, store, None, transport=httpx.MockTransport(backend))
    yield r
    r.close()


def set_routing(ctx, default=None, **tasks):
    ctx.settings_store.patch({"routing": {
        "default": default.model_dump() if default else None,
        "tasks": {k: v.model_dump() for k, v in tasks.items()},
    }})


def test_nothing_configured_is_reported_not_guessed(router):
    with pytest.raises(ProviderError) as info:
        router.resolve("answer")
    assert info.value.kind == "not_configured"
    assert router.active().configured is False


def test_task_routing_with_default_fallback(ctx, store, router):
    ollama = store.create("ollama", "Ollama", None, {})
    lm = store.create("lmstudio", "LM Studio", None, {})
    set_routing(ctx, default=Route(provider_id=ollama.id, model="small"), reasoning=Route(provider_id=lm.id, model="big"))
    answer = router.resolve("answer")
    assert (answer.provider_id, answer.model, answer.is_cloud) == (ollama.id, "small", False)
    reasoning = router.resolve("reasoning")
    assert (reasoning.provider_id, reasoning.model) == (lm.id, "big")
    assert router.effective_routes()["classify"] == Route(provider_id=ollama.id, model="small")
    active = router.active("reasoning")
    assert active.configured and active.provider_name == "LM Studio" and active.model == "big" and not active.is_cloud
    assert reasoning.provider.generate(request(model="big")).text == "local answer"


def test_explicit_override_switches_provider_for_one_request(ctx, store, router):
    ollama = store.create("ollama", "Ollama", None, {})
    lm = store.create("lmstudio", "LM Studio", None, {})
    set_routing(ctx, default=Route(provider_id=ollama.id, model="small"))
    switched = router.resolve("answer", Route(provider_id=lm.id, model="other"))
    assert (switched.provider_id, switched.model) == (lm.id, "other")
    assert router.resolve("answer").provider_id == ollama.id   # the default is untouched


def test_missing_disabled_or_modelless_provider_is_not_configured(ctx, store, router):
    lm = store.create("lmstudio", "LM Studio", None, {})
    set_routing(ctx, default=Route(provider_id="gone", model="m"))
    with pytest.raises(ProviderError) as info:
        router.resolve("answer")
    assert info.value.kind == "not_configured" and "no longer exists" in info.value.message
    store.update(lm.id, enabled=False)
    with pytest.raises(ProviderError) as info:
        router.resolve("answer", Route(provider_id=lm.id, model="m"))
    assert info.value.kind == "not_configured" and "disabled" in info.value.message
    store.update(lm.id, enabled=True)
    with pytest.raises(ProviderError) as info:
        router.resolve("answer", Route(provider_id=lm.id, model=""))
    assert info.value.kind == "not_configured"


def test_cloud_provider_is_blocked_while_cloud_traffic_is_disabled(ctx, store, router, backend):
    cloud = store.create("openai", "OpenAI", None, {}, api_key="sk-x")
    set_routing(ctx, default=Route(provider_id=cloud.id, model="some-model"))
    assert ctx.settings.privacy.allow_cloud_models is False   # the shipped default
    with pytest.raises(ProviderError) as info:
        router.resolve("answer", confirm_cloud=True)
    assert info.value.kind == "cloud_disabled" and "Nothing was sent" in info.value.message
    assert backend.hits == []
    assert router.options() == []


def test_cloud_provider_requires_confirmation_unless_disabled_in_settings(ctx, store, router):
    cloud = store.create("anthropic", "Anthropic", None, {}, api_key="sk-ant")
    set_routing(ctx, default=Route(provider_id=cloud.id, model="some-model"))
    ctx.settings_store.patch({"privacy": {"allow_cloud_models": True}})
    with pytest.raises(ProviderError) as info:
        router.resolve("answer")
    assert info.value.kind == "confirmation_required"
    assert router.resolve("answer", confirm_cloud=True).is_cloud
    ctx.settings_store.patch({"privacy": {"confirm_before_cloud": False}})
    assert router.resolve("answer").is_cloud


def test_offline_mode_blocks_cloud_even_when_allowed(ctx, store, router):
    cloud = store.create("gemini", "Gemini", None, {}, api_key="k")
    ctx.settings_store.patch({"privacy": {"allow_cloud_models": True, "offline_mode": True}})
    with pytest.raises(ProviderError) as info:
        router.resolve("answer", Route(provider_id=cloud.id, model="m"), confirm_cloud=True)
    assert info.value.kind == "cloud_disabled"


def test_remote_openai_compatible_endpoint_is_treated_as_cloud(ctx, store, router):
    remote = store.create("openai_compat", "Hosted gateway", "https://llm.example.com/v1", {}, api_key="k")
    lan = store.create("openai_compat", "LAN box", "http://192.168.1.40:8000/v1", {})
    with pytest.raises(ProviderError) as info:
        router.resolve("answer", Route(provider_id=remote.id, model="m"))
    assert info.value.kind == "cloud_disabled"
    local = router.resolve("answer", Route(provider_id=lan.id, model="m"))
    assert not local.is_cloud
    ctx.settings_store.patch({"privacy": {"allow_cloud_models": True}})
    assert router.resolve("answer", Route(provider_id=remote.id, model="m"), confirm_cloud=True).is_cloud


def test_no_silent_fallback_after_a_provider_error(ctx, store, router, backend):
    """A failing local provider raises; the configured paid provider is never contacted."""
    ollama = store.create("ollama", "Ollama", None, {})
    paid = store.create("openai", "OpenAI", None, {}, api_key="sk-x")
    ctx.settings_store.patch({"privacy": {"allow_cloud_models": True, "confirm_before_cloud": False}})
    set_routing(ctx, default=Route(provider_id=ollama.id, model="small"), reasoning=Route(provider_id=paid.id, model="paid-model"))
    backend.fail_hosts.add("127.0.0.1")
    resolved = router.resolve("answer")
    with pytest.raises(ProviderError) as info:
        resolved.provider.generate(request(model=resolved.model))
    assert info.value.kind == "unavailable" and info.value.provider_id == ollama.id
    assert "api.openai.com" not in backend.hits and set(backend.hits) == {"127.0.0.1"}
    # resolving again still names the same provider; switching is only ever explicit
    assert router.resolve("answer").provider_id == ollama.id
    assert {o.provider_id for o in router.options()} == {ollama.id, paid.id}


def test_options_list_only_what_the_user_can_switch_to(ctx, store, router):
    ollama = store.create("ollama", "Ollama", None, {})
    cloud = store.create("openai", "OpenAI", None, {}, api_key="sk-x")
    disabled = store.create("vllm", "vLLM", None, {}, enabled=False)
    set_routing(ctx, default=Route(provider_id=ollama.id, model="small"))
    options = router.options()
    assert [o.provider_id for o in options] == [ollama.id]
    assert options[0].is_local and not options[0].is_paid and options[0].default_model == "small"
    ctx.settings_store.patch({"privacy": {"allow_cloud_models": True}})
    ids = [o.provider_id for o in router.options()]
    assert ids == [ollama.id, cloud.id] and disabled.id not in ids and "chatgpt-plan" not in ids


def test_provider_instances_are_cached_until_configuration_changes(ctx, store, router):
    lm = store.create("lmstudio", "LM Studio", None, {})
    first = router.provider(lm.id)
    assert router.provider(lm.id) is first
    store.update(lm.id, base_url="http://127.0.0.1:4321/v1")
    second = router.provider(lm.id)
    assert second is not first and second.base_url == "http://127.0.0.1:4321/v1"
    store.set_secret(lm.id, "new-key")
    assert router.provider(lm.id) is not second
    with pytest.raises(ProviderError):
        router.provider("missing")


def test_usage_logging_cost_is_none_without_a_price(ctx, store, router):
    ollama = store.create("ollama", "Ollama", None, {})
    resolved = router.resolve("answer", Route(provider_id=ollama.id, model="small"))
    assert router.record_usage(resolved, "answer", Usage(input_tokens=1000, output_tokens=200)) is None
    assert router.record_usage(resolved, "answer", Usage(input_tokens=10, output_tokens=5, estimated=True), report_id=7) is None
    summary = router.usage_summary(30)
    assert summary.total_cost_usd is None and len(summary.rows) == 1
    row = summary.rows[0]
    assert (row.calls, row.input_tokens, row.output_tokens, row.cost_usd, row.estimated) == (2, 1010, 205, None, True)
    assert "estimates" in summary.pricing_note.lower()
    assert ctx.db.scalar("SELECT report_id FROM usage_log WHERE report_id IS NOT NULL") == 7


def test_usage_cost_uses_user_prices_and_builtin_anthropic_prices(ctx, store, router):
    ctx.settings_store.patch({"privacy": {"allow_cloud_models": True, "confirm_before_cloud": False}})
    openai = store.create("openai", "OpenAI", None, {}, api_key="k")
    anthropic = store.create("anthropic", "Anthropic", None, {}, api_key="k")
    unpriced = router.resolve("answer", Route(provider_id=openai.id, model="some-openai-model"))
    assert router.record_usage(unpriced, "answer", Usage(input_tokens=1_000_000, output_tokens=1_000_000)) is None
    settings = ctx.settings
    settings.pricing = [ModelPrice(provider_kind="openai", model_prefix="some-openai", input_per_mtok=1.5, output_per_mtok=6.0)]
    ctx.settings_store.save(settings)
    assert router.record_usage(unpriced, "answer", Usage(input_tokens=1_000_000, output_tokens=500_000)) == pytest.approx(4.5)
    claude = router.resolve("answer", Route(provider_id=anthropic.id, model="claude-opus-5-5"))
    assert router.record_usage(claude, "answer", Usage(input_tokens=1_000_000, output_tokens=100_000)) == pytest.approx(6.0)
    assert router.usage_summary(1).total_cost_usd == pytest.approx(10.5)


def test_chatgpt_plan_route_requires_sign_in_and_plan_permission(ctx, store, backend):
    fake = FakeOpenAI()
    chatgpt = ChatGPTPlanService(ctx.db, ctx.secrets, ctx.settings_store, ctx.config.paths.data_dir, transport=fake.transport, callback_port=0)
    router = ModelRouter(ctx.db, ctx.settings_store, store, chatgpt, transport=fake.transport)
    try:
        route = Route(provider_id="chatgpt-plan", model="plan-model")
        assert any(p.id == "chatgpt-plan" for p in store.list())
        with pytest.raises(ProviderError) as info:
            router.resolve("answer", route, confirm_cloud=True)
        assert info.value.kind == "cloud_disabled"            # privacy comes first
        ctx.settings_store.patch({"privacy": {"allow_cloud_models": True}})
        with pytest.raises(ProviderError) as info:
            router.resolve("answer", route, confirm_cloud=True)
        assert info.value.kind == "not_configured"            # not signed in
        assert "chatgpt-plan" not in [o.provider_id for o in router.options()]
        view, *_ = sign_in(chatgpt, fake, scope=NO_PLAN_SCOPES)
        with pytest.raises(ProviderError) as info:
            router.resolve("answer", route, confirm_cloud=True)
        assert info.value.kind == "consent_required"
        account_id = chatgpt.login_status(view.attempt_id).account_id
        sign_in(chatgpt, fake, account_id=account_id, enable_plan_usage=True)
        with pytest.raises(ProviderError) as info:
            router.resolve("answer", route)                   # still needs per-request confirmation
        assert info.value.kind == "confirmation_required"
        resolved = router.resolve("answer", route, confirm_cloud=True)
        assert resolved.is_cloud and resolved.provider_kind == "chatgpt_plan"
        assert "chatgpt-plan" in [o.provider_id for o in router.options()]
        # plan usage is never priced per token
        assert router.record_usage(resolved, "answer", Usage(input_tokens=5000, output_tokens=500)) is None
    finally:
        router.close()
        chatgpt.close()
