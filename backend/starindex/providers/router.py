"""Task-based model routing with privacy enforcement and usage accounting.

Rules that hold everywhere in StarIndex:

* A request runs on exactly the provider/model the routing settings (or an explicit
  per-request override) name. A failure is raised to the caller; there is **no** automatic
  fallback to another provider, paid or otherwise.
* A provider counts as *cloud* when its kind is a hosted API or its endpoint is outside this
  machine and private networks. Cloud requests are refused while outbound model traffic is
  disabled, and need an explicit confirmation flag when confirmation is required.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from ..api.schemas import LlmStatus, ProviderOption, UsageRow, UsageSummary
from ..db import Database, utcnow
from ..settings_store import TASK_TYPES, AppSettings, Route, SettingsStore, TaskType
from . import pricing
from .base import ChatProvider, ProviderConfig, ProviderError, Usage
from .chatgpt_plan.service import ChatGPTPlanService
from .registry import ProviderStore, build_provider, is_cloud_provider

TASK_LABELS: dict[TaskType, str] = {
    "classify": "Fast metadata classification",
    "summarize": "Repository summaries",
    "expand_query": "Query expansion",
    "answer": "Answer synthesis (Ask)",
    "reasoning": "Multi-repository reasoning (Research)",
    "compare": "Comparison reports",
}


@dataclass
class ResolvedModel:
    provider: ChatProvider
    config: ProviderConfig
    model: str
    is_cloud: bool

    @property
    def provider_id(self) -> str:
        return self.config.id

    @property
    def provider_kind(self) -> str:
        return self.config.kind


class ModelRouter:
    def __init__(
        self,
        db: Database,
        settings_store: SettingsStore,
        providers: ProviderStore,
        chatgpt: ChatGPTPlanService | None,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self._transport = transport   # tests inject a mock transport; production leaves it None
        self.db = db
        self.settings_store = settings_store
        self.providers = providers
        self.chatgpt = chatgpt
        self._cache: dict[str, tuple[str, ChatProvider]] = {}
        self._lock = threading.RLock()

    # -- provider instances -------------------------------------------------------------------

    def _fingerprint(self, config: ProviderConfig, secret: str | None) -> str:
        digest = hashlib.sha256()
        digest.update(config.model_dump_json().encode())
        digest.update(b"\x00")
        digest.update((secret or "").encode())
        return digest.hexdigest()

    def provider(self, provider_id: str) -> ChatProvider:
        """Live adapter for a configured provider (cached until its configuration changes)."""
        config = self.providers.get(provider_id)
        if config is None:
            raise ProviderError("not_configured", "That provider is not configured.", provider_id=provider_id)
        return self._instance(config)

    def _instance(self, config: ProviderConfig) -> ChatProvider:
        secret = None if config.kind == "chatgpt_plan" else self.providers.get_secret(config.id)
        fingerprint = self._fingerprint(config, secret)
        with self._lock:
            cached = self._cache.get(config.id)
            if cached and cached[0] == fingerprint:
                return cached[1]
            if cached:
                cached[1].close()
            instance = build_provider(config, secret, chatgpt=self.chatgpt, transport=self._transport)
            self._cache[config.id] = (fingerprint, instance)
            return instance

    def invalidate(self, provider_id: str | None = None) -> None:
        with self._lock:
            ids = [provider_id] if provider_id else list(self._cache)
            for pid in ids:
                cached = self._cache.pop(pid, None)
                if cached:
                    cached[1].close()

    # -- routing -----------------------------------------------------------------------------

    @staticmethod
    def _route_for(settings: AppSettings, task: TaskType) -> Route | None:
        return settings.routing.tasks.get(task) or settings.routing.default

    def effective_routes(self) -> dict[TaskType, Route | None]:
        settings = self.settings_store.load()
        return {task: self._route_for(settings, task) for task in TASK_TYPES}

    def resolve(
        self, task: TaskType, override: Route | None = None, *, confirm_cloud: bool = False
    ) -> ResolvedModel:
        """Pick the provider and model for a task. Raises ``ProviderError``; never falls back."""
        settings = self.settings_store.load()
        route = override or self._route_for(settings, task)
        if route is None:
            raise ProviderError(
                "not_configured",
                "No model is configured for this task. Choose a provider and model in Settings → Models.",
            )
        config = self.providers.get(route.provider_id)
        if config is None:
            raise ProviderError(
                "not_configured",
                f"The provider '{route.provider_id}' selected for this task no longer exists. Pick another in Settings.",
                provider_id=route.provider_id,
            )
        if not config.enabled:
            raise ProviderError(
                "not_configured", f"{config.name} is disabled. Enable it or choose another provider.",
                provider_id=config.id, provider_kind=config.kind,
            )
        if not route.model:
            raise ProviderError(
                "not_configured", f"No model is selected for {config.name}.",
                provider_id=config.id, provider_kind=config.kind,
            )
        cloud = is_cloud_provider(config)
        if cloud:
            if not settings.privacy.allow_cloud_models or settings.privacy.offline_mode:
                raise ProviderError(
                    "cloud_disabled",
                    f"{config.name} is a cloud provider and outbound model traffic is turned off in Privacy "
                    "settings. Nothing was sent.",
                    provider_id=config.id, provider_kind=config.kind,
                )
            if settings.privacy.confirm_before_cloud and not confirm_cloud:
                raise ProviderError(
                    "confirmation_required",
                    f"This request would send repository content to {config.name}. Confirm to continue.",
                    provider_id=config.id, provider_kind=config.kind,
                )
        if config.kind == "chatgpt_plan":
            if self.chatgpt is None:
                raise ProviderError(
                    "not_configured", "ChatGPT plan support is not available.",
                    provider_id=config.id, provider_kind=config.kind,
                )
            self.chatgpt.ready_account_id()   # raises auth / consent_required / not_configured
        return ResolvedModel(provider=self._instance(config), config=config, model=route.model, is_cloud=cloud)

    # -- information for the UI --------------------------------------------------------------

    def _default_model_for(self, settings: AppSettings, provider_id: str) -> str | None:
        if settings.routing.default and settings.routing.default.provider_id == provider_id:
            return settings.routing.default.model
        for task in TASK_TYPES:
            route = settings.routing.tasks.get(task)
            if route and route.provider_id == provider_id:
                return route.model
        return None

    def _usable(self, settings: AppSettings, config: ProviderConfig) -> bool:
        if not config.enabled:
            return False
        if is_cloud_provider(config) and (not settings.privacy.allow_cloud_models or settings.privacy.offline_mode):
            return False
        if config.kind == "chatgpt_plan":
            return bool(self.chatgpt and self.chatgpt.status().available)
        return True

    def options(self) -> list[ProviderOption]:
        """Providers the user may explicitly switch to right now. Never chosen automatically."""
        settings = self.settings_store.load()
        result: list[ProviderOption] = []
        for config in self.providers.list():
            if not self._usable(settings, config):
                continue
            cloud = is_cloud_provider(config)
            result.append(
                ProviderOption(
                    provider_id=config.id,
                    provider_kind=config.kind,
                    name=config.name,
                    is_local=not cloud,
                    is_paid=cloud,
                    default_model=self._default_model_for(settings, config.id),
                )
            )
        result.sort(key=lambda o: (not o.is_local, o.name.lower()))
        return result

    def active(self, task: TaskType = "answer") -> LlmStatus:
        """What would serve ``task`` right now. Never raises."""
        try:
            settings = self.settings_store.load()
            route = self._route_for(settings, task)
            if route is None:
                return LlmStatus(configured=False)
            config = self.providers.get(route.provider_id)
            if config is None or not config.enabled or not route.model:
                return LlmStatus(configured=False)
            return LlmStatus(
                configured=True,
                provider_id=config.id,
                provider_kind=config.kind,
                provider_name=config.name,
                model=route.model,
                is_cloud=is_cloud_provider(config),
            )
        except Exception:
            return LlmStatus(configured=False)

    # -- usage ---------------------------------------------------------------------------------

    def estimate_cost(self, provider_kind: str, model: str, usage: Usage) -> float | None:
        return pricing.estimate_cost(self.settings_store.load().pricing, provider_kind, model, usage)

    def record_usage(
        self, resolved: ResolvedModel, task: str, usage: Usage, report_id: int | None = None
    ) -> float | None:
        """Append to the usage log. Returns the estimated cost, or ``None`` when no price is known."""
        cost = self.estimate_cost(resolved.provider_kind, resolved.model, usage)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO usage_log(created_at, provider_id, provider_kind, model, task, input_tokens, "
                "output_tokens, estimated, cost_usd, report_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    utcnow(), resolved.provider_id, resolved.provider_kind, resolved.model, task,
                    usage.input_tokens, usage.output_tokens, int(usage.estimated), cost, report_id,
                ),
            )
        return cost

    def usage_summary(self, days: int = 30) -> UsageSummary:
        since = (datetime.now(UTC) - timedelta(days=max(1, days))).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self.db.all(
            "SELECT provider_id, provider_kind, model, task, COUNT(*) AS calls, "
            "COALESCE(SUM(input_tokens),0) AS input_tokens, COALESCE(SUM(output_tokens),0) AS output_tokens, "
            "SUM(cost_usd) AS cost_usd, MAX(estimated) AS estimated "
            "FROM usage_log WHERE created_at >= ? GROUP BY provider_id, provider_kind, model, task "
            "ORDER BY calls DESC",
            (since,),
        )
        items = [
            UsageRow(
                provider_id=r["provider_id"],
                provider_kind=r["provider_kind"],
                model=r["model"],
                task=r["task"],
                calls=r["calls"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                cost_usd=round(r["cost_usd"], 6) if r["cost_usd"] is not None else None,
                estimated=bool(r["estimated"]),
            )
            for r in rows
        ]
        priced = [i.cost_usd for i in items if i.cost_usd is not None]
        return UsageSummary(
            since=since,
            rows=items,
            total_cost_usd=round(sum(priced), 6) if priced else None,
            pricing_note=pricing.PRICING_NOTE,
        )

    def close(self) -> None:
        self.invalidate()
