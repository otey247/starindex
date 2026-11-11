"""FastAPI dependencies shared by all route modules."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from ..context import AppContext


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


class ApiError(HTTPException):
    """Raise for any expected failure; rendered as ``{"error": {kind, message, detail}}``."""

    def __init__(self, status_code: int, kind: str, message: str, detail: dict[str, Any] | None = None):
        super().__init__(status_code=status_code, detail={"kind": kind, "message": message, "detail": detail or {}})
        self.kind = kind
        self.message = message


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} was not found.")
