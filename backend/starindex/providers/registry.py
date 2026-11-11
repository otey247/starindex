"""Provider catalogue: templates shown in Settings, persistence of configured providers,
and the factory that turns a stored configuration into a live adapter."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import httpx

from ..api.schemas import ProviderTemplate
from ..db import Database, dumps, loads, utcnow
from ..secrets import SecretStore, provider_key
from .base import CLOUD_KINDS, LOCAL_KINDS, ChatProvider, ProviderConfig, ProviderError
from .http import is_local_url, validate_base_url

if TYPE_CHECKING:
    from .chatgpt_plan.service import ChatGPTPlanService

CHATGPT_PROVIDER_ID = "chatgpt-plan"
_SECRET_HEADER_NAMES = {"authorization", "api-key", "x-api-key", "x-goog-api-key", "proxy-authorization", "cookie"}

PROVIDER_TEMPLATES: list[ProviderTemplate] = [
    ProviderTemplate(
        kind="ollama",
        label="Ollama",
        category="local",
        description="Local models served by Ollama. Models you have pulled are discovered automatically; "
        "Ollama uses your NVIDIA GPU when it can and falls back to CPU.",
        default_base_url="http://127.0.0.1:11434",
        option_fields=[
            {"key": "num_ctx", "label": "Context window (tokens)", "placeholder": "model default", "required": False, "type": "number"},
            {"key": "keep_alive", "label": "Keep model loaded", "placeholder": "5m", "required": False},
        ],
        docs_url="https://docs.ollama.com/api",
    ),
    ProviderTemplate(
        kind="lmstudio",
        label="LM Studio",
        category="local",
        description="LM Studio's local server (OpenAI-compatible). Start the server in LM Studio and load a model.",
        default_base_url="http://127.0.0.1:1234/v1",
        api_key_optional=True,
        docs_url="https://lmstudio.ai/docs/developer",
    ),
    ProviderTemplate(
        kind="vllm",
        label="vLLM",
        category="local",
        description="A vLLM OpenAI-compatible server, typically on a CUDA GPU on this machine or your network.",
        default_base_url="http://127.0.0.1:8000/v1",
        api_key_optional=True,
        docs_url="https://docs.vllm.ai/",
    ),
    ProviderTemplate(
        kind="openai_compat",
        label="OpenAI-compatible server",
        category="local",
        description="Any server that speaks the Chat Completions API (llama.cpp server, LiteLLM, gateways). "
        "An address outside your machine or local network is treated as a cloud provider.",
        default_base_url="http://127.0.0.1:8080/v1",
        api_key_optional=True,
    ),
    ProviderTemplate(
        kind="openai",
        label="OpenAI API",
        category="cloud",
        description="OpenAI's paid API, billed to your API key. Separate from a ChatGPT subscription.",
        default_base_url="https://api.openai.com/v1",
        needs_api_key=True,
        base_url_editable=False,
        docs_url="https://platform.openai.com/docs",
    ),
    ProviderTemplate(
        kind="anthropic",
        label="Anthropic",
        category="cloud",
        description="Claude models through Anthropic's paid API.",
        default_base_url="https://api.anthropic.com",
        needs_api_key=True,
        base_url_editable=False,
        option_fields=[
            {"key": "effort", "label": "Effort", "placeholder": "model default", "required": False,
             "choices": ["low", "medium", "high", "xhigh", "max"]},
        ],
        docs_url="https://platform.claude.com/docs",
    ),
    ProviderTemplate(
        kind="gemini",
        label="Google Gemini",
        category="cloud",
        description="Gemini models through the Generative Language API with an API key.",
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        needs_api_key=True,
        base_url_editable=False,
        docs_url="https://ai.google.dev/gemini-api/docs",
    ),
    ProviderTemplate(
        kind="azure_openai",
        label="Azure OpenAI",
        category="cloud",
        description="Models deployed in your Azure OpenAI / Microsoft Foundry resource. The model name is your "
        "deployment name.",
        default_base_url=None,
        needs_api_key=True,
        option_fields=[
            {"key": "deployments", "label": "Deployment names", "placeholder": "my-chat-deployment, my-embedding-deployment",
             "required": False, "type": "list"},
            {"key": "api_version", "label": "API version (legacy deployments path only)",
             "placeholder": "leave empty to use the v1 API", "required": False},
        ],
        docs_url="https://learn.microsoft.com/azure/foundry/openai/api-version-lifecycle",
    ),
    ProviderTemplate(
        kind="chatgpt_plan",
        label="ChatGPT plan",
        category="chatgpt_plan",
        description="Use your ChatGPT plan for eligible requests through Sign in with ChatGPT. "
        "Usage counts against your plan's limits and is managed in ChatGPT settings.",
        default_base_url="https://api.openai.com/v1",
        base_url_editable=False,
        docs_url="https://developers.openai.com/siwc/token-sharing-open-source",
    ),
]
_TEMPLATES_BY_KIND = {t.kind: t for t in PROVIDER_TEMPLATES}


def template_for(kind: str) -> ProviderTemplate | None:
    return _TEMPLATES_BY_KIND.get(kind)


def category_for(kind: str) -> str:
    template = _TEMPLATES_BY_KIND.get(kind)
    return template.category if template else "cloud"


def effective_base_url(config: ProviderConfig) -> str | None:
    template = _TEMPLATES_BY_KIND.get(config.kind)
    return config.base_url or (template.default_base_url if template else None)


def is_cloud_provider(config: ProviderConfig) -> bool:
    """Cloud = a paid/hosted kind, or any endpoint outside this machine and private networks."""
    if config.kind in CLOUD_KINDS:
        return True
    return not is_local_url(effective_base_url(config))


def build_provider(
    config: ProviderConfig,
    secret: str | None,
    *,
    chatgpt: ChatGPTPlanService | None = None,
    transport: httpx.BaseTransport | None = None,
) -> ChatProvider:
    """Instantiate the adapter for a stored configuration. Raises ``ProviderError``."""
    kind = config.kind
    if kind == "ollama":
        from .ollama import OllamaProvider

        return OllamaProvider(config, secret, transport=transport)
    if kind in ("openai", "openai_compat", "lmstudio", "vllm"):
        from .openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(config, secret, transport=transport)
    if kind == "azure_openai":
        from .azure_openai import AzureOpenAIProvider

        return AzureOpenAIProvider(config, secret, transport=transport)
    if kind == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider(config, secret, transport=transport)
    if kind == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider(config, secret, transport=transport)
    if kind == "chatgpt_plan":
        if chatgpt is None:
            raise ProviderError(
                "not_configured", "ChatGPT plan support is not initialised.", provider_id=config.id, provider_kind=kind
            )
        from .chatgpt_plan.client import ChatGPTPlanProvider

        return ChatGPTPlanProvider(config, chatgpt, transport=transport)
    raise ProviderError("unsupported", f"Unknown provider kind: {kind}", provider_id=config.id, provider_kind=kind)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "provider"


def _clean_options(kind: str, options: dict[str, Any] | None) -> dict[str, Any]:
    cleaned = dict(options or {})
    headers = cleaned.get("headers")
    if headers is not None:
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise ProviderError("bad_request", "Extra headers must be a map of text to text.", provider_kind=kind)
        for name in headers:
            if name.lower() in _SECRET_HEADER_NAMES:
                raise ProviderError(
                    "bad_request",
                    f"Do not put credentials in extra headers ({name}); store the API key instead.",
                    provider_kind=kind,
                )
    for key in list(cleaned):
        if key.lower() in ("api_key", "apikey", "key", "secret", "token", "password"):
            raise ProviderError("bad_request", "Credentials do not belong in provider options.", provider_kind=kind)
    try:
        dumps(cleaned)
    except (TypeError, ValueError) as exc:
        raise ProviderError("bad_request", "Provider options must be JSON-serialisable.", provider_kind=kind) from exc
    return cleaned


class ProviderStore:
    """CRUD for configured providers. API keys go to the secret store, never to SQLite."""

    def __init__(self, db: Database, secrets: SecretStore):
        self.db = db
        self.secrets = secrets

    # -- reading ----------------------------------------------------------------------------

    def _ensure_builtin(self) -> None:
        """The ChatGPT plan provider is always present; sign-in state decides whether it is usable."""
        if self.db.scalar("SELECT 1 FROM providers WHERE id=?", (CHATGPT_PROVIDER_ID,)):
            return
        now = utcnow()
        with self.db.tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO providers(id, kind, name, base_url, enabled, options, created_at, updated_at) "
                "VALUES (?,?,?,?,1,'{}',?,?)",
                (CHATGPT_PROVIDER_ID, "chatgpt_plan", "ChatGPT plan", None, now, now),
            )

    @staticmethod
    def _config(row: Any) -> ProviderConfig:
        return ProviderConfig(
            id=row["id"],
            kind=row["kind"],
            name=row["name"],
            base_url=row["base_url"],
            enabled=bool(row["enabled"]),
            options=loads(row["options"], {}),
        )

    def list(self) -> list[ProviderConfig]:
        self._ensure_builtin()
        rows = self.db.all("SELECT * FROM providers ORDER BY created_at, id")
        return [self._config(r) for r in rows]

    def get(self, provider_id: str) -> ProviderConfig | None:
        if provider_id == CHATGPT_PROVIDER_ID:
            self._ensure_builtin()
        row = self.db.one("SELECT * FROM providers WHERE id=?", (provider_id,))
        return self._config(row) if row else None

    def created_at(self, provider_id: str) -> str | None:
        return self.db.scalar("SELECT created_at FROM providers WHERE id=?", (provider_id,))

    # -- writing ----------------------------------------------------------------------------

    def create(
        self,
        kind: str,
        name: str,
        base_url: str | None,
        options: dict[str, Any] | None,
        enabled: bool = True,
        api_key: str | None = None,
    ) -> ProviderConfig:
        if kind == "chatgpt_plan":
            raise ProviderError(
                "bad_request", "The ChatGPT plan provider is built in; sign in from Settings instead.", provider_kind=kind
            )
        if kind not in LOCAL_KINDS and kind not in CLOUD_KINDS:
            raise ProviderError("bad_request", f"Unknown provider kind: {kind}", provider_kind=kind)
        template = _TEMPLATES_BY_KIND[kind]
        url = validate_base_url(base_url, provider_kind=kind) if base_url and base_url.strip() else None
        if url is None and template.default_base_url is None:
            raise ProviderError("bad_request", f"{template.label} needs an endpoint URL.", provider_kind=kind)
        if not template.base_url_editable:
            url = None   # fixed endpoints are never overridden, so keys only go to the vendor
        cleaned = _clean_options(kind, options)
        provider_id = self._unique_id(_slug(name))
        now = utcnow()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO providers(id, kind, name, base_url, enabled, options, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (provider_id, kind, name.strip(), url, int(enabled), dumps(cleaned), now, now),
            )
        if api_key and api_key.strip():
            self.set_secret(provider_id, api_key)
        config = self.get(provider_id)
        assert config is not None
        return config

    def _unique_id(self, base: str) -> str:
        if base == CHATGPT_PROVIDER_ID:
            base = "provider"
        candidate, n = base, 2
        while self.db.scalar("SELECT 1 FROM providers WHERE id=?", (candidate,)):
            candidate = f"{base}-{n}"
            n += 1
        return candidate

    def update(self, provider_id: str, **fields: Any) -> ProviderConfig:
        """Update ``name``, ``base_url``, ``options`` and/or ``enabled``."""
        current = self.get(provider_id)
        if current is None:
            raise ProviderError("not_configured", "That provider does not exist.", provider_id=provider_id)
        unknown = set(fields) - {"name", "base_url", "options", "enabled"}
        if unknown:
            raise ValueError(f"unknown provider fields: {sorted(unknown)}")
        template = _TEMPLATES_BY_KIND.get(current.kind)
        name = current.name
        url = current.base_url
        options = current.options
        enabled = current.enabled
        if fields.get("name") is not None:
            name = str(fields["name"]).strip() or current.name
        if "base_url" in fields and fields["base_url"] is not None:
            if template is not None and not template.base_url_editable:
                raise ProviderError(
                    "bad_request", f"The endpoint for {template.label} is fixed.", provider_id=provider_id,
                    provider_kind=current.kind,
                )
            raw = str(fields["base_url"]).strip()
            url = validate_base_url(raw, provider_kind=current.kind) if raw else None
            if url is None and (template is None or template.default_base_url is None):
                raise ProviderError("bad_request", "This provider needs an endpoint URL.", provider_kind=current.kind)
        if fields.get("options") is not None:
            options = _clean_options(current.kind, fields["options"])
        if fields.get("enabled") is not None:
            enabled = bool(fields["enabled"])
        with self.db.tx() as c:
            c.execute(
                "UPDATE providers SET name=?, base_url=?, options=?, enabled=?, updated_at=? WHERE id=?",
                (name, url, dumps(options), int(enabled), utcnow(), provider_id),
            )
        updated = self.get(provider_id)
        assert updated is not None
        return updated

    def delete(self, provider_id: str) -> None:
        if provider_id == CHATGPT_PROVIDER_ID:
            raise ProviderError(
                "bad_request", "The ChatGPT plan provider cannot be deleted; disable it or sign out instead.",
                provider_id=provider_id, provider_kind="chatgpt_plan",
            )
        self.delete_secret(provider_id)
        with self.db.tx() as c:
            c.execute("DELETE FROM providers WHERE id=?", (provider_id,))

    # -- secrets ----------------------------------------------------------------------------

    def has_secret(self, provider_id: str) -> bool:
        return bool(self.secrets.get(provider_key(provider_id)))

    def get_secret(self, provider_id: str) -> str | None:
        return self.secrets.get(provider_key(provider_id)) or None

    def set_secret(self, provider_id: str, api_key: str) -> None:
        if provider_id == CHATGPT_PROVIDER_ID:
            raise ProviderError("bad_request", "The ChatGPT plan uses sign-in, not an API key.", provider_id=provider_id)
        self.secrets.set(provider_key(provider_id), api_key.strip())

    def delete_secret(self, provider_id: str) -> None:
        self.secrets.delete(provider_key(provider_id))
