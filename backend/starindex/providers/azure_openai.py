"""Azure OpenAI (Azure OpenAI in Microsoft Foundry Models).

Two endpoint layouts are supported, selected by ``options``:

* **v1 API (default, current).**  ``{endpoint}/openai/v1/chat/completions`` with the deployment
  name in the request ``model`` field and no ``api-version``.  Microsoft documents this as the
  generally available path since August 2025
  (https://learn.microsoft.com/azure/foundry/openai/api-version-lifecycle).
* **Deployment-scoped (legacy).**  When ``options.api_version`` is set, requests go to
  ``{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=...``.

Authentication uses the resource key in the ``api-key`` header. On Azure the value sent as
``model`` is a *deployment name*, which the user chooses; ``options.deployments`` (a list of
names) is therefore the authoritative model list when provided. Without it the adapter lists
``/openai/v1/models`` and says so in the model details, because that endpoint returns the models
available to the resource rather than the user's deployment names.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .base import ModelInfo, ProviderCapabilities, ProviderError
from .http import join_url, validate_base_url
from .openai_compat import OpenAICompatProvider, classify_model


def _endpoint_root(url: str) -> str:
    """Reduce whatever the user pasted to ``https://resource.openai.azure.com``."""
    cleaned = validate_base_url(url, provider_kind="azure_openai")
    lowered = cleaned.lower()
    for suffix in ("/openai/v1", "/openai"):
        if lowered.endswith(suffix):
            return cleaned[: -len(suffix)]
    return cleaned


class AzureOpenAIProvider(OpenAICompatProvider):
    def _resolve_base_url(self) -> str:
        if not self.config.base_url:
            raise self._error(
                "not_configured",
                "Enter your Azure OpenAI endpoint, e.g. https://YOUR-RESOURCE.openai.azure.com",
            )
        self._root = _endpoint_root(self.config.base_url)
        version = self.config.options.get("api_version")
        self._api_version: str | None = str(version).strip() if version else None
        return join_url(self._root, "openai") if self._api_version else join_url(self._root, "openai/v1")

    def _auth_headers(self) -> dict[str, str]:
        return {"api-key": self._require_secret()}

    def _query(self) -> dict[str, Any] | None:
        return {"api-version": self._api_version} if self._api_version else None

    def _chat_url(self, model: str) -> str:
        if self._api_version:
            return join_url(self._base, f"deployments/{quote(model, safe='')}/chat/completions")
        return join_url(self._base, "chat/completions")

    def _embeddings_url(self, model: str) -> str:
        if self._api_version:
            return join_url(self._base, f"deployments/{quote(model, safe='')}/embeddings")
        return join_url(self._base, "embeddings")

    def _models_url(self) -> str:
        return join_url(self._base, "models")

    def _configured_deployments(self) -> list[str]:
        raw = self.config.options.get("deployments")
        if isinstance(raw, str):
            raw = [part.strip() for part in raw.replace("\n", ",").split(",")]
        if not isinstance(raw, list):
            return []
        return [str(name).strip() for name in raw if str(name).strip()]

    def capabilities(self) -> ProviderCapabilities:
        caps = super().capabilities()
        caps.is_local = False
        caps.is_paid = True
        caps.notes = [
            "Uses the Azure OpenAI v1 API unless an api-version is configured (deployment-scoped paths).",
            "The model name is your deployment name; list your deployments in the provider options.",
            *caps.notes[:1],
        ]
        return caps

    def list_models(self) -> list[ModelInfo]:
        deployments = self._configured_deployments()
        if deployments:
            return [
                ModelInfo(id=name, display_name=name, kind=classify_model(name), details={"source": "deployment"})
                for name in deployments
            ]
        try:
            listed = super().list_models()
        except ProviderError as exc:
            if exc.kind in ("auth", "network", "timeout", "unavailable", "rate_limit"):
                raise
            raise self._error(
                "unsupported",
                "This Azure resource did not return a model list. Enter your deployment names in the "
                "provider options instead.",
                status=exc.status,
            ) from exc
        for model in listed:
            model.details = {
                **model.details,
                "source": "resource_models",
                "note": "Azure expects your deployment name as the model; it may differ from this id.",
            }
        return listed
