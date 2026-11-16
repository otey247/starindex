"""Relevance feedback, personal search evaluation, and tool access for other agents (MCP)."""

from __future__ import annotations

import sys
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from ...context import AppContext
from ...mcp_server import DirectBackend, McpHandler, execute_tool, tool_specs
from ...search.evaluation import (
    MIN_QUERIES_FOR_RECOMMENDATION,
    latest_run,
    normalize_query,
    personal_judgments,
)
from ...settings_store import AppSettings
from ..deps import ApiError, get_ctx, not_found
from ..schemas import (
    EvalRun,
    EvalStatus,
    FeedbackQuery,
    FeedbackRepo,
    Job,
    McpInfo,
    Ok,
    SearchFeedback,
    SearchFeedbackCreate,
    ToolCall,
    ToolCallResult,
    ToolInfo,
)
from .jobs import job_to_schema

router = APIRouter(tags=["extensions"])
mcp_router = APIRouter()

_FEEDBACK_SQL = (
    "SELECT f.id, f.query, f.repo_id, r.full_name, f.verdict, f.mode, f.rank, f.created_at "
    "FROM search_feedback f JOIN repos r ON r.id=f.repo_id"
)


def _feedback(row) -> SearchFeedback:
    return SearchFeedback(id=row["id"], query=row["query"], repo_id=row["repo_id"], repo_full_name=row["full_name"],
                          verdict=row["verdict"], mode=row["mode"], rank=row["rank"], created_at=row["created_at"])


@router.post("/search/feedback", response_model=SearchFeedback)
def add_feedback(body: SearchFeedbackCreate, ctx: AppContext = Depends(get_ctx)) -> SearchFeedback:
    """Record (or change) the verdict for one repository on one query."""
    from ...db import utcnow

    if not ctx.db.one("SELECT 1 FROM repos WHERE id=?", (body.repo_id,)):
        raise not_found("Repository")
    norm = normalize_query(body.query)
    with ctx.db.tx() as c:
        c.execute(
            "INSERT INTO search_feedback(query, query_norm, repo_id, verdict, mode, rank, created_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(query_norm, repo_id) DO UPDATE SET verdict=excluded.verdict, mode=excluded.mode, rank=excluded.rank, "
            "query=excluded.query, created_at=excluded.created_at",
            (body.query.strip(), norm, body.repo_id, body.verdict, body.mode, body.rank, utcnow()),
        )
    return _feedback(ctx.db.one(_FEEDBACK_SQL + " WHERE f.query_norm=? AND f.repo_id=?", (norm, body.repo_id)))


@router.get("/search/feedback", response_model=list[SearchFeedback])
def list_feedback(query: str | None = Query(None, max_length=500), limit: int = Query(500, ge=1, le=5000), ctx: AppContext = Depends(get_ctx)) -> list[SearchFeedback]:
    """Verdicts for one query (to mark results in the UI), or the most recent ones."""
    if query is not None:
        rows = ctx.db.all(_FEEDBACK_SQL + " WHERE f.query_norm=? ORDER BY f.id", (normalize_query(query),))
    else:
        rows = ctx.db.all(_FEEDBACK_SQL + " ORDER BY f.id DESC LIMIT ?", (limit,))
    return [_feedback(r) for r in rows]


@router.get("/search/feedback/queries", response_model=list[FeedbackQuery])
def feedback_queries(ctx: AppContext = Depends(get_ctx)) -> list[FeedbackQuery]:
    grouped: dict[str, FeedbackQuery] = {}
    for r in ctx.db.all(_FEEDBACK_SQL.replace("f.created_at", "f.created_at, f.query_norm") + " ORDER BY f.created_at DESC, f.id DESC"):
        entry = grouped.setdefault(r["query_norm"], FeedbackQuery(query=r["query"], judgments=[], updated_at=r["created_at"]))
        entry.judgments.append(FeedbackRepo(feedback_id=r["id"], repo_id=r["repo_id"], full_name=r["full_name"], verdict=r["verdict"]))
    return list(grouped.values())


@router.delete("/search/feedback/{feedback_id}", response_model=Ok)
def delete_feedback(feedback_id: int, ctx: AppContext = Depends(get_ctx)) -> Ok:
    with ctx.db.tx() as c:
        c.execute("DELETE FROM search_feedback WHERE id=?", (feedback_id,))
    return Ok()


@router.get("/search/feedback/export")
def export_feedback(ctx: AppContext = Depends(get_ctx)) -> JSONResponse:
    """The personal evaluation set in the shape of eval/queries.yaml (query + graded repositories)."""
    names = {r["id"]: r["full_name"] for r in ctx.db.all("SELECT id, full_name FROM repos")}
    out = [
        {"q": v["query"], "kind": "personal", "relevant": {names[i]: g for i, g in v["relevant"].items() if i in names},
         "not_relevant": sorted(names[i] for i in v["rejected"] if i in names)}
        for v in personal_judgments(ctx).values()
    ]
    return JSONResponse(out, headers={"Content-Disposition": 'attachment; filename="starindex-relevance-judgments.json"'})


@router.get("/eval/status", response_model=EvalStatus)
def eval_status(ctx: AppContext = Depends(get_ctx)) -> EvalStatus:
    judged = personal_judgments(ctx)
    run = latest_run(ctx)
    return EvalStatus(
        queries=sum(1 for v in judged.values() if v["relevant"]),
        judgments=sum(len(v["relevant"]) + len(v["rejected"]) for v in judged.values()),
        min_queries_for_recommendation=MIN_QUERIES_FOR_RECOMMENDATION,
        running=ctx.jobs.has_active("evaluate_search"),
        latest=EvalRun.model_validate(run) if run else None,
    )


@router.post("/eval/run", response_model=Job, status_code=201)
def eval_run(ctx: AppContext = Depends(get_ctx)) -> Job:
    job_id = ctx.jobs.enqueue("evaluate_search", dedupe_key="evaluate_search", priority=15)
    return job_to_schema(ctx, ctx.jobs.get(job_id))  # type: ignore[arg-type]


@router.post("/eval/apply", response_model=AppSettings)
def eval_apply(ctx: AppContext = Depends(get_ctx)) -> AppSettings:
    """Apply the settings suggested by the latest evaluation run. Only ever happens on this explicit call."""
    run = latest_run(ctx)
    if not run or not run.get("suggested_settings"):
        raise ApiError(409, "nothing_to_apply", "The latest evaluation run does not suggest a change.")
    after = ctx.settings_store.patch(run["suggested_settings"])
    ctx.reset_service("reranker")
    return after


# ---------------------------------------------------------------------------------- tools and MCP


@router.get("/tools", response_model=list[ToolInfo])
def tools() -> list[ToolInfo]:
    return [ToolInfo(**t) for t in tool_specs()]


@router.post("/tools/{name}", response_model=ToolCallResult)
def call_tool(name: str, body: ToolCall, ctx: AppContext = Depends(get_ctx)) -> ToolCallResult:
    if name not in {t["name"] for t in tool_specs()}:
        raise not_found("Tool")
    return ToolCallResult(**execute_tool(ctx, name, body.arguments))


@router.get("/mcp/info", response_model=McpInfo)
def mcp_info(request: Request, ctx: AppContext = Depends(get_ctx)) -> McpInfo:
    base = f"http://{ctx.config.host}:{ctx.config.port}"
    command = [sys.executable, "-m", "starindex.cli", "mcp"]
    quoted = " ".join(f'"{part}"' if " " in part else part for part in command)
    return McpInfo(
        http_url=f"{base}/mcp",
        stdio_command=command,
        claude_code_http=f"claude mcp add --transport http starindex {base}/mcp",
        claude_code_stdio=f"claude mcp add starindex -- {quoted}",
        desktop_config={"mcpServers": {"starindex": {"command": command[0], "args": command[1:]}}},
        tools=[ToolInfo(**t) for t in tool_specs()],
        requires_token=bool(ctx.config.access_token),
    )


class _AppBackend(DirectBackend):
    """In the application process the full index is available, so no keyword-only restriction."""

    def __init__(self, ctx):
        self.ctx = ctx


@mcp_router.post("/mcp", include_in_schema=False)
async def mcp_http(request: Request):
    """MCP Streamable HTTP endpoint (stateless, JSON responses)."""
    from starlette.concurrency import run_in_threadpool

    try:
        message: Any = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)
    handler = McpHandler(_AppBackend(request.app.state.ctx))
    if isinstance(message, list):
        responses = [r for r in [await run_in_threadpool(handler.handle, m) for m in message] if r is not None]
        return JSONResponse(responses) if responses else Response(status_code=202)
    response = await run_in_threadpool(handler.handle, message)
    return JSONResponse(response) if response is not None else Response(status_code=202)


@mcp_router.get("/mcp", include_in_schema=False)
async def mcp_http_get():
    return Response(status_code=405, headers={"Allow": "POST"})     # no server-initiated stream is offered
