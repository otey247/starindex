from __future__ import annotations

import json

import pytest

from starindex.providers import pricing
from starindex.providers.anthropic import AnthropicProvider
from starindex.providers.azure_openai import AzureOpenAIProvider
from starindex.providers.base import ProviderConfig, ProviderError, Usage
from starindex.providers.gemini import GeminiProvider
from starindex.providers.hardware import detect_hardware, parse_nvidia_smi, recommend
from starindex.providers.ollama import OllamaProvider
from starindex.providers.openai_compat import OpenAICompatProvider
from starindex.providers.registry import (
    PROVIDER_TEMPLATES,
    ProviderStore,
    build_provider,
    is_cloud_provider,
)
from starindex.secrets import provider_key
from starindex.settings_store import ModelPrice


@pytest.fixture
def store(ctx):
    return ProviderStore(ctx.db, ctx.secrets)


def test_templates_cover_every_kind_once():
    kinds = [t.kind for t in PROVIDER_TEMPLATES]
    assert sorted(kinds) == sorted(
        ["ollama", "lmstudio", "vllm", "openai_compat", "openai", "anthropic", "gemini", "azure_openai", "chatgpt_plan"]
    )
    by_kind = {t.kind: t for t in PROVIDER_TEMPLATES}
    assert by_kind["lmstudio"].default_base_url == "http://127.0.0.1:1234/v1"
    assert by_kind["vllm"].default_base_url == "http://127.0.0.1:8000/v1"
    assert by_kind["ollama"].default_base_url == "http://127.0.0.1:11434"
    assert by_kind["openai"].needs_api_key and not by_kind["openai"].base_url_editable
    assert by_kind["chatgpt_plan"].category == "chatgpt_plan" and not by_kind["chatgpt_plan"].needs_api_key
    assert all(t.category == "local" for t in PROVIDER_TEMPLATES if t.kind in ("ollama", "lmstudio", "vllm", "openai_compat"))


@pytest.mark.parametrize(
    ("kind", "cls", "base_url"),
    [
        ("ollama", OllamaProvider, None), ("lmstudio", OpenAICompatProvider, None), ("vllm", OpenAICompatProvider, None),
        ("openai_compat", OpenAICompatProvider, "http://127.0.0.1:8080/v1"), ("openai", OpenAICompatProvider, None),
        ("anthropic", AnthropicProvider, None), ("gemini", GeminiProvider, None),
        ("azure_openai", AzureOpenAIProvider, "https://r.openai.azure.com"),
    ],
)
def test_factory_builds_the_right_adapter(kind, cls, base_url):
    provider = build_provider(ProviderConfig(id="x", kind=kind, name="x", base_url=base_url), "key")
    assert type(provider) is cls
    provider.close()


def test_factory_requires_the_chatgpt_service_and_rejects_unknown_kinds():
    with pytest.raises(ProviderError) as info:
        build_provider(ProviderConfig(id="chatgpt-plan", kind="chatgpt_plan", name="ChatGPT plan"), None)
    assert info.value.kind == "not_configured"


def test_create_stores_key_in_secret_store_only(store, ctx):
    config = store.create("openai", "My OpenAI", None, {}, api_key="  sk-secret-value  ")
    assert config.id == "my-openai" and config.base_url is None and config.enabled
    assert store.has_secret(config.id) and store.get_secret(config.id) == "sk-secret-value"
    assert ctx.secrets.get(provider_key(config.id)) == "sk-secret-value"
    dump = json.dumps([dict(r) for r in ctx.db.all("SELECT * FROM providers")])
    assert "sk-secret-value" not in dump
    second = store.create("openai", "My OpenAI", None, {})
    assert second.id == "my-openai-2" and not store.has_secret(second.id)


def test_fixed_endpoints_cannot_be_repointed(store):
    config = store.create("anthropic", "Claude", "https://attacker.example", {}, api_key="k")
    assert config.base_url is None   # the override is dropped, so the key only ever goes to the vendor
    with pytest.raises(ProviderError):
        store.update(config.id, base_url="https://attacker.example")


def test_create_validates_kind_url_and_options(store):
    with pytest.raises(ProviderError):
        store.create("made_up", "X", None, {})
    with pytest.raises(ProviderError):
        store.create("chatgpt_plan", "X", None, {})
    with pytest.raises(ProviderError):
        store.create("openai_compat", "X", "ftp://nope", {})
    with pytest.raises(ProviderError):
        store.create("openai_compat", "X", "http://user:pw@127.0.0.1:8000", {})
    with pytest.raises(ProviderError):
        store.create("azure_openai", "X", None, {})          # no default endpoint
    with pytest.raises(ProviderError) as info:
        store.create("openai_compat", "X", "http://127.0.0.1:8000/v1", {"headers": {"Authorization": "Bearer abc"}})
    assert "credentials" in info.value.message.lower()
    with pytest.raises(ProviderError):
        store.create("openai_compat", "X", "http://127.0.0.1:8000/v1", {"api_key": "abc"})


def test_update_delete_and_secret_lifecycle(store):
    config = store.create("lmstudio", "LM", None, {"timeout_s": 30})
    updated = store.update(config.id, name="LM Studio", enabled=False, options={"timeout_s": 60}, base_url="http://127.0.0.1:5555/v1/")
    assert (updated.name, updated.enabled, updated.options, updated.base_url) == ("LM Studio", False, {"timeout_s": 60}, "http://127.0.0.1:5555/v1")
    store.set_secret(config.id, "abc")
    store.delete_secret(config.id)
    assert not store.has_secret(config.id)
    store.set_secret(config.id, "abc")
    store.delete(config.id)
    assert store.get(config.id) is None and store.get_secret(config.id) is None
    with pytest.raises(ProviderError):
        store.update("missing", name="x")


def test_chatgpt_plan_row_is_builtin_and_protected(store):
    listed = store.list()
    plan = next(p for p in listed if p.id == "chatgpt-plan")
    assert plan.kind == "chatgpt_plan" and plan.enabled
    assert len([p for p in store.list() if p.id == "chatgpt-plan"]) == 1   # idempotent
    with pytest.raises(ProviderError):
        store.delete("chatgpt-plan")
    with pytest.raises(ProviderError):
        store.set_secret("chatgpt-plan", "k")
    assert store.update("chatgpt-plan", enabled=False).enabled is False


def test_cloud_classification():
    def conf(kind, url=None):
        return ProviderConfig(id="x", kind=kind, name="x", base_url=url)

    assert not is_cloud_provider(conf("ollama"))
    assert not is_cloud_provider(conf("lmstudio"))
    assert not is_cloud_provider(conf("vllm", "http://10.0.0.8:8000/v1"))
    assert is_cloud_provider(conf("vllm", "https://gpu.example.com/v1"))
    assert is_cloud_provider(conf("ollama", "https://ollama.example.com"))
    for kind in ("openai", "anthropic", "gemini", "azure_openai", "chatgpt_plan"):
        assert is_cloud_provider(conf(kind))


# -- pricing -------------------------------------------------------------------------------------


def test_builtin_prices_exist_only_for_anthropic():
    kinds = {p.provider_kind for p in pricing.builtin_prices()}
    assert kinds == {"anthropic"}
    for kind in ("openai", "gemini", "azure_openai", "openai_compat", "ollama"):
        assert pricing.estimate_cost([], kind, "any-model", Usage(input_tokens=1000, output_tokens=1000)) is None


def test_longest_prefix_wins_and_snapshots_match():
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert pricing.estimate_cost([], "anthropic", "claude-opus-5-5", usage) == pytest.approx(24.0)
    assert pricing.estimate_cost([], "anthropic", "claude-opus-5", usage) == pytest.approx(30.0)
    assert pricing.estimate_cost([], "anthropic", "claude-sonnet-5-5", usage) == pytest.approx(12.0)
    assert pricing.estimate_cost([], "anthropic", "claude-haiku-4-5-20251001", usage) == pytest.approx(6.0)
    assert pricing.estimate_cost([], "anthropic", "claude-fable-5-2", usage) == pytest.approx(48.0)
    assert pricing.estimate_cost([], "anthropic", "claude-unknown-9", usage) is None


def test_long_prompt_tier_and_user_override():
    small = Usage(input_tokens=50_000, output_tokens=10_000)
    large = Usage(input_tokens=200_000, output_tokens=10_000)
    assert pricing.estimate_cost([], "anthropic", "claude-haiku-5-5", small) == pytest.approx(0.01)
    assert pricing.estimate_cost([], "anthropic", "claude-haiku-5-5", large) == pytest.approx(0.125)
    override = [ModelPrice(provider_kind="anthropic", model_prefix="claude-haiku-5-5", input_per_mtok=1.0, output_per_mtok=1.0)]
    assert pricing.estimate_cost(override, "anthropic", "claude-haiku-5-5", large) == pytest.approx(0.21)


def test_no_cost_without_token_counts_or_for_chatgpt_plan():
    assert pricing.estimate_cost([], "anthropic", "claude-opus-5-5", Usage()) is None
    price = [ModelPrice(provider_kind="chatgpt_plan", model_prefix="", input_per_mtok=1, output_per_mtok=1)]
    assert pricing.estimate_cost(price, "chatgpt_plan", "plan-model", Usage(input_tokens=10, output_tokens=10)) is None


# -- hardware ------------------------------------------------------------------------------------


def test_nvidia_smi_output_parsing():
    gpus = parse_nvidia_smi("NVIDIA GeForce RTX 4090, 24564, 23010, 566.14\nNVIDIA RTX A2000, 6138, 6000, 551.61\n\n")
    assert [(g.name, g.memory_total_mb, g.memory_free_mb, g.driver) for g in gpus] == [
        ("NVIDIA GeForce RTX 4090", 24564, 23010, "566.14"), ("NVIDIA RTX A2000", 6138, 6000, "551.61"),
    ]
    assert parse_nvidia_smi("") == [] and parse_nvidia_smi("garbage") == []


def test_recommendations_distinguish_cuda_from_cpu_fallback():
    from starindex.api.schemas import GpuInfo

    gpu = recommend([GpuInfo(name="RTX 4090", memory_total_mb=24564)], 65536, 16, ["CUDAExecutionProvider", "CPUExecutionProvider"])
    assert any("CUDA" in tip for tip in gpu) and any("24 GB" in tip for tip in gpu)
    cpu = recommend([], 16384, 4, ["CPUExecutionProvider"])
    assert any("No NVIDIA GPU" in tip for tip in cpu) and any("CPU" in tip for tip in cpu)
    assert any("discovered from your local server" in tip for tip in gpu + cpu)


def test_detect_hardware_runs_on_this_machine():
    info = detect_hardware()
    assert info.cpu_count >= 1 and info.os and info.recommendations
    assert info.cuda_available == bool(info.gpus)
