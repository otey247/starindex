"""Live checks against real model servers. Skipped unless the environment points at one.

    STARINDEX_TEST_OPENAI_COMPAT_URL   base URL of an OpenAI-compatible server (LM Studio, vLLM, llama.cpp)
    STARINDEX_TEST_OPENAI_COMPAT_MODEL model id to use (default: first chat model the server lists)
    STARINDEX_TEST_OPENAI_COMPAT_KEY   optional API key
    STARINDEX_TEST_OLLAMA_URL          base URL of an Ollama server, e.g. http://127.0.0.1:11434
    STARINDEX_TEST_OLLAMA_MODEL        model to use (default: first chat model that is pulled)

Run with:  pytest backend/tests/providers/test_live.py -m live
"""

from __future__ import annotations

import os

import pytest

from starindex.providers.base import ChatMessage, GenerationRequest, ProviderConfig
from starindex.providers.ollama import OllamaProvider
from starindex.providers.openai_compat import OpenAICompatProvider

pytestmark = pytest.mark.live

SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}


def _pick(provider, env_name):
    chosen = os.environ.get(env_name)
    if chosen:
        return chosen
    chat = [m for m in provider.list_models() if m.kind == "chat"]
    if not chat:
        pytest.skip("the server lists no chat models")
    return chat[0].id


def _exercise(provider, model):
    request = GenerationRequest(
        model=model, system="You are terse.", messages=[ChatMessage(role="user", content="Say the word: ready")],
        max_output_tokens=32, temperature=0.0, timeout_s=300,
    )
    events = list(provider.stream(request))
    assert events[-1].type == "completed"
    assert "".join(e.text for e in events if e.type == "delta").strip()
    parsed, _ = provider.generate_json(
        GenerationRequest(
            model=model, messages=[ChatMessage(role="user", content="Which city is the capital of France? Answer as JSON.")],
            json_schema=SCHEMA, max_output_tokens=64, temperature=0.0, timeout_s=300,
        )
    )
    assert isinstance(parsed, dict) and "city" in parsed


@pytest.mark.skipif(not os.environ.get("STARINDEX_TEST_OPENAI_COMPAT_URL"), reason="STARINDEX_TEST_OPENAI_COMPAT_URL not set")
def test_live_openai_compatible_server():
    provider = OpenAICompatProvider(
        ProviderConfig(id="live", kind="openai_compat", name="live", base_url=os.environ["STARINDEX_TEST_OPENAI_COMPAT_URL"]),
        os.environ.get("STARINDEX_TEST_OPENAI_COMPAT_KEY"),
    )
    try:
        assert provider.health().ok
        _exercise(provider, _pick(provider, "STARINDEX_TEST_OPENAI_COMPAT_MODEL"))
    finally:
        provider.close()


@pytest.mark.skipif(not os.environ.get("STARINDEX_TEST_OLLAMA_URL"), reason="STARINDEX_TEST_OLLAMA_URL not set")
def test_live_ollama():
    provider = OllamaProvider(
        ProviderConfig(id="live", kind="ollama", name="live", base_url=os.environ["STARINDEX_TEST_OLLAMA_URL"]), None
    )
    try:
        assert provider.health().ok
        _exercise(provider, _pick(provider, "STARINDEX_TEST_OLLAMA_MODEL"))
    finally:
        provider.close()
