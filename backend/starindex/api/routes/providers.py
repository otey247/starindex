"""Model provider settings: configuration, health checks, model discovery, routing, usage.

Secrets flow one way: they are accepted in request bodies, written to the secret store and
never returned. Paths are relative to the API prefix (``/api``).
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query

from ...context import AppContext
from ...providers.base import (
    ChatMessage,
    GenerationRequest,
    ProviderCapabilities,
    ProviderConfig,
    ProviderError,
)
from ...providers.hardware import detect_hardware
from ...providers.registry import (
    CHATGPT_PROVIDER_ID,
    PROVIDER_TEMPLATES,
    category_for,
    is_cloud_provider,
)
from ...providers.router import TASK_LABELS
from ...settings_store import TASK_TYPES, Route
from ..deps import ApiError, get_ctx, not_found
from ..schemas import (
    HardwareInfo,
    Ok,
    ProviderCreate,
    ProviderErrorInfo,
    ProviderInfo,
    ProviderModels,
    ProviderPatch,
    ProviderTemplate,
    ProviderTestRequest,
    ProviderTestResult,
    RoutingInfo,
    RoutingUpdate,
    SecretUpdate,
    UsageSummary,
)

router = APIRouter(tags=["providers"])

_STATUS_FOR_KIND = {"not_configured": 404, "bad_request": 400, "unsupported": 400, "cloud_disabled": 403}


def error_info(exc: ProviderError) -> ProviderErrorInfo:
    return ProviderErrorInfo(
        kind=exc.kind,
        message=exc.message,
        provider_id=exc.provider_id,
        provider_kind=exc.provider_kind,
        status=exc.status,
        code=exc.code,
        retryable=exc.retryable,
        retry_after_s=exc.retry_after_s,
        action_url=exc.action_url,
        action_label=exc.action_label,
    )


def _api_error(exc: ProviderError) -> ApiError:
    return ApiError(_STATUS_FOR_KIND.get(exc.kind, 400), exc.kind, exc.message)


def _cloud_blocked(ctx: AppContext, config: ProviderConfig) -> bool:
    privacy = ctx.settings.privacy
    return is_cloud_provider(config) and (not privacy.allow_cloud_models or privacy.offline_mode)


def _capabilities(ctx: AppContext, config: ProviderConfig) -> ProviderCapabilities:
    try:
        return ctx.router.provider(config.id).capabilities()
    except ProviderError as exc:
        cloud = is_cloud_provider(config)
        return ProviderCapabilities(
            is_local=not cloud, is_paid=cloud, streaming=True, model_discovery=False, notes=[exc.message]
        )


def _info(ctx: AppContext, config: ProviderConfig) -> ProviderInfo:
    options = dict(config.options)
    has_secret = ctx.providers.has_secret(config.id)
    if config.kind == "chatgpt_plan":
        status = ctx.chatgpt.status()
        active = next((a for a in status.accounts if a.is_active), None)
        options = {
            **options,
            "available": status.available,
            "reason": status.reason,
            "account": active.label if active else None,
            "plan_usage_enabled": bool(active and active.plan_usage_enabled),
        }
        has_secret = bool(active and active.signed_in)
    return ProviderInfo(
        id=config.id,
        kind=config.kind,
        name=config.name,
        category=category_for(config.kind),  # type: ignore[arg-type]
        base_url=config.base_url or next((t.default_base_url for t in PROVIDER_TEMPLATES if t.kind == config.kind), None),
        enabled=config.enabled,
        options=options,
        has_secret=has_secret,
        capabilities=_capabilities(ctx, config),
        blocked_by_privacy=_cloud_blocked(ctx, config),
        created_at=ctx.providers.created_at(config.id),
    )


def _get(ctx: AppContext, provider_id: str) -> ProviderConfig:
    config = ctx.providers.get(provider_id)
    if config is None:
        raise not_found("Provider")
    return config


@router.get("/providers/templates", response_model=list[ProviderTemplate])
def list_templates() -> list[ProviderTemplate]:
    return PROVIDER_TEMPLATES


@router.get("/providers", response_model=list[ProviderInfo])
def list_providers(ctx: AppContext = Depends(get_ctx)) -> list[ProviderInfo]:
    return [_info(ctx, config) for config in ctx.providers.list()]


@router.post("/providers", response_model=ProviderInfo, status_code=201)
def create_provider(body: ProviderCreate, ctx: AppContext = Depends(get_ctx)) -> ProviderInfo:
    try:
        config = ctx.providers.create(
            body.kind, body.name, body.base_url, body.options, enabled=body.enabled, api_key=body.api_key
        )
    except ProviderError as exc:
        raise _api_error(exc) from exc
    return _info(ctx, config)


@router.patch("/providers/{provider_id}", response_model=ProviderInfo)
def update_provider(provider_id: str, body: ProviderPatch, ctx: AppContext = Depends(get_ctx)) -> ProviderInfo:
    _get(ctx, provider_id)
    fields = body.model_dump(exclude_unset=True)
    try:
        config = ctx.providers.update(provider_id, **fields)
    except ProviderError as exc:
        raise _api_error(exc) from exc
    ctx.router.invalidate(provider_id)
    return _info(ctx, config)


@router.delete("/providers/{provider_id}", response_model=Ok)
def delete_provider(provider_id: str, ctx: AppContext = Depends(get_ctx)) -> Ok:
    _get(ctx, provider_id)
    try:
        ctx.providers.delete(provider_id)
    except ProviderError as exc:
        raise _api_error(exc) from exc
    ctx.router.invalidate(provider_id)
    # Routes that pointed at the deleted provider are removed rather than silently re-pointed.
    settings = ctx.settings
    routing = settings.routing
    default = routing.default if routing.default and routing.default.provider_id != provider_id else None
    tasks = {task: route for task, route in routing.tasks.items() if route.provider_id != provider_id}
    if default != routing.default or tasks != routing.tasks:
        settings.routing.default = default
        settings.routing.tasks = tasks
        ctx.settings_store.save(settings)
    return Ok(message="Provider removed.")


@router.put("/providers/{provider_id}/secret", response_model=ProviderInfo)
def set_secret(provider_id: str, body: SecretUpdate, ctx: AppContext = Depends(get_ctx)) -> ProviderInfo:
    config = _get(ctx, provider_id)
    try:
        ctx.providers.set_secret(provider_id, body.api_key)
    except ProviderError as exc:
        raise _api_error(exc) from exc
    ctx.router.invalidate(provider_id)
    return _info(ctx, config)


@router.delete("/providers/{provider_id}/secret", response_model=ProviderInfo)
def delete_secret(provider_id: str, ctx: AppContext = Depends(get_ctx)) -> ProviderInfo:
    config = _get(ctx, provider_id)
    if provider_id != CHATGPT_PROVIDER_ID:
        ctx.providers.delete_secret(provider_id)
        ctx.router.invalidate(provider_id)
    return _info(ctx, config)


def _blocked_error(config: ProviderConfig) -> ProviderErrorInfo:
    return ProviderErrorInfo(
        kind="cloud_disabled",
        message=f"{config.name} is a cloud provider and outbound model traffic is turned off in Privacy settings. "
        "Nothing was sent.",
        provider_id=config.id,
        provider_kind=config.kind,
    )


@router.post("/providers/{provider_id}/test", response_model=ProviderTestResult)
def test_provider(
    provider_id: str, body: ProviderTestRequest | None = None, ctx: AppContext = Depends(get_ctx)
) -> ProviderTestResult:
    """Connectivity check (model listing). With ``model`` set, also a one-line generation.

    The test prompt contains no repository content. Cloud providers are not contacted while
    outbound model traffic is disabled.
    """
    config = _get(ctx, provider_id)
    if _cloud_blocked(ctx, config):
        blocked = _blocked_error(config)
        return ProviderTestResult(ok=False, detail=blocked.message, error=blocked)
    try:
        provider = ctx.router.provider(provider_id)
    except ProviderError as exc:
        return ProviderTestResult(ok=False, detail=exc.message, error=error_info(exc))
    start = time.monotonic()
    try:
        models = provider.list_models()
    except ProviderError as exc:
        return ProviderTestResult(ok=False, detail=exc.message, error=error_info(exc))
    latency = int((time.monotonic() - start) * 1000)
    result = ProviderTestResult(
        ok=True, latency_ms=latency, model_count=len(models), detail=f"Connected. {len(models)} model(s) available."
    )
    model = body.model if body else None
    if model:
        try:
            generation = provider.generate(
                GenerationRequest(
                    model=model,
                    messages=[ChatMessage(role="user", content="Reply with the single word: ready")],
                    max_output_tokens=64,
                    temperature=0.0,
                    timeout_s=60,
                )
            )
        except ProviderError as exc:
            return ProviderTestResult(
                ok=False, latency_ms=latency, model_count=len(models),
                detail=f"Connected, but {model} did not respond: {exc.message}", error=error_info(exc),
            )
        result.sample = generation.text.strip()[:200]
        result.detail = f"Connected. {model} responded."
    return result


@router.get("/providers/{provider_id}/models", response_model=ProviderModels)
def provider_models(provider_id: str, ctx: AppContext = Depends(get_ctx)) -> ProviderModels:
    config = _get(ctx, provider_id)
    if _cloud_blocked(ctx, config):
        return ProviderModels(provider_id=provider_id, models=[], error=_blocked_error(config))
    try:
        models = ctx.router.provider(provider_id).list_models()
    except ProviderError as exc:
        return ProviderModels(provider_id=provider_id, models=[], error=error_info(exc))
    return ProviderModels(provider_id=provider_id, models=models)


def _routing_info(ctx: AppContext) -> RoutingInfo:
    routing = ctx.settings.routing
    return RoutingInfo(
        default=routing.default,
        tasks=dict(routing.tasks),
        effective=ctx.router.effective_routes(),
        task_labels=dict(TASK_LABELS),
    )


@router.get("/routing", response_model=RoutingInfo)
def get_routing(ctx: AppContext = Depends(get_ctx)) -> RoutingInfo:
    return _routing_info(ctx)


def _check_route(ctx: AppContext, route: Route) -> None:
    if ctx.providers.get(route.provider_id) is None:
        raise ApiError(400, "bad_request", f"Unknown provider: {route.provider_id}")
    if not route.model.strip():
        raise ApiError(400, "bad_request", "A model must be selected for the route.")


@router.put("/routing", response_model=RoutingInfo)
def put_routing(body: RoutingUpdate, ctx: AppContext = Depends(get_ctx)) -> RoutingInfo:
    """Replace the routing table. A task mapped to ``null`` (or omitted) follows the default."""
    settings = ctx.settings
    if body.default is not None:
        _check_route(ctx, body.default)
    tasks: dict = {}
    for task, route in body.tasks.items():
        if task not in TASK_TYPES:
            raise ApiError(400, "bad_request", f"Unknown task: {task}")
        if route is None:
            continue
        _check_route(ctx, route)
        tasks[task] = route
    settings.routing.default = body.default
    settings.routing.tasks = tasks
    ctx.settings_store.save(settings)
    return _routing_info(ctx)


@router.get("/usage", response_model=UsageSummary)
def get_usage(days: int = Query(30, ge=1, le=3650), ctx: AppContext = Depends(get_ctx)) -> UsageSummary:
    return ctx.router.usage_summary(days)


@router.get("/system/hardware", response_model=HardwareInfo)
def get_hardware() -> HardwareInfo:
    return detect_hardware()
