"""ChatGPT plan (Sign in with ChatGPT) account management. Tokens never leave the backend."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...context import AppContext
from ...providers.base import ProviderError
from ..deps import ApiError, get_ctx
from ..schemas import ChatGPTLoginAttempt, ChatGPTLoginStart, ChatGPTStatus, Ok

router = APIRouter(tags=["chatgpt"])

_STATUS = {"not_configured": 404, "cloud_disabled": 403, "auth": 409, "unavailable": 503}


def _api_error(exc: ProviderError) -> ApiError:
    return ApiError(_STATUS.get(exc.kind, 400), exc.kind, exc.message)


@router.get("/chatgpt/status", response_model=ChatGPTStatus)
def chatgpt_status(ctx: AppContext = Depends(get_ctx)) -> ChatGPTStatus:
    return ctx.chatgpt.status()


@router.post("/chatgpt/login", response_model=ChatGPTLoginAttempt)
def chatgpt_login(body: ChatGPTLoginStart | None = None, ctx: AppContext = Depends(get_ctx)) -> ChatGPTLoginAttempt:
    """Start a sign-in. The client opens ``auth_url`` in the system browser and polls the attempt."""
    request = body or ChatGPTLoginStart()
    try:
        return ctx.chatgpt.start_login(account_id=request.account_id, enable_plan_usage=request.enable_plan_usage)
    except ProviderError as exc:
        raise _api_error(exc) from exc


@router.get("/chatgpt/login/{attempt_id}", response_model=ChatGPTLoginAttempt)
def chatgpt_login_status(attempt_id: str, ctx: AppContext = Depends(get_ctx)) -> ChatGPTLoginAttempt:
    attempt = ctx.chatgpt.login_status(attempt_id)
    if attempt.status == "completed":
        ctx.router.invalidate("chatgpt-plan")
    return attempt


@router.post("/chatgpt/accounts/{account_id}/activate", response_model=ChatGPTStatus)
def chatgpt_activate(account_id: str, ctx: AppContext = Depends(get_ctx)) -> ChatGPTStatus:
    try:
        return ctx.chatgpt.activate(account_id)
    except ProviderError as exc:
        raise _api_error(exc) from exc


@router.post("/chatgpt/accounts/{account_id}/sign-out", response_model=ChatGPTStatus)
def chatgpt_sign_out(account_id: str, ctx: AppContext = Depends(get_ctx)) -> ChatGPTStatus:
    try:
        return ctx.chatgpt.sign_out(account_id)
    except ProviderError as exc:
        raise _api_error(exc) from exc


@router.delete("/chatgpt/accounts/{account_id}", response_model=ChatGPTStatus)
def chatgpt_delete(account_id: str, ctx: AppContext = Depends(get_ctx)) -> ChatGPTStatus:
    try:
        return ctx.chatgpt.delete(account_id)
    except ProviderError as exc:
        raise _api_error(exc) from exc


@router.post("/chatgpt/accounts/{account_id}/welcome-shown", response_model=Ok)
def chatgpt_welcome_shown(account_id: str, ctx: AppContext = Depends(get_ctx)) -> Ok:
    try:
        ctx.chatgpt.mark_welcome_shown(account_id)
    except ProviderError as exc:
        raise _api_error(exc) from exc
    return Ok()
