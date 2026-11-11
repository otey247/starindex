"""Ask, Research, Compare, reports and evidence. Streaming endpoints use server-sent events."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from ...agent import ask as ask_flow
from ...agent import research as research_flow
from ...agent.compare import build_comparison, comparison_as_text
from ...agent.evidence import EvidenceSet, check_answer
from ...context import AppContext
from ...library import reports as report_store
from ...library import repos as repo_store
from ...providers.base import ChatMessage, GenerationRequest, ProviderError
from ..deps import ApiError, get_ctx, not_found
from ..schemas import (
    AskRequest,
    Comparison,
    CompareNarrativeRequest,
    CompareRequest,
    EvidenceDetail,
    LlmStatus,
    Ok,
    ProviderOption,
    Report,
    ReportPatch,
    ReportSummary,
    RepoSummaryText,
    ResearchRequest,
    RouteOverride,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["agent"])


def sse(events: Iterator[tuple[str, dict[str, Any]]]) -> StreamingResponse:
    def frames() -> Iterator[str]:
        try:
            for event, payload in events:
                yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception:
            log.exception("stream failed")
            body = {"error": {"kind": "internal", "message": "The request failed unexpectedly. See the application log."}, "alternatives": []}
            yield f"event: error\ndata: {json.dumps(body)}\n\n"

    return StreamingResponse(
        frames(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


class ModelOptions(BaseModel):
    active: LlmStatus
    options: list[ProviderOption]
    cloud_allowed: bool
    confirm_before_cloud: bool


@router.get("/model-options", response_model=ModelOptions)
def model_options(ctx: AppContext = Depends(get_ctx)) -> ModelOptions:
    settings = ctx.settings
    return ModelOptions(
        active=ctx.router.active("answer"), options=ctx.router.options(),
        cloud_allowed=settings.privacy.allow_cloud_models, confirm_before_cloud=settings.privacy.confirm_before_cloud,
    )


@router.post("/ask")
def ask(req: AskRequest, ctx: AppContext = Depends(get_ctx)) -> StreamingResponse:
    return sse(ask_flow.run_ask(ctx, req))


@router.post("/research")
def research(req: ResearchRequest, ctx: AppContext = Depends(get_ctx)) -> StreamingResponse:
    return sse(research_flow.run_research(ctx, req))


@router.post("/research/{report_id}/cancel", response_model=Ok)
def research_cancel(report_id: int, ctx: AppContext = Depends(get_ctx)) -> Ok:
    research_flow.cancel(report_id)
    return Ok(message="Cancellation requested.")


# ---------------------------------------------------------------------------------- repository summaries


class SummaryRequest(BaseModel):
    route: RouteOverride | None = None
    confirm_cloud: bool = False


@router.get("/repos/{repo_id}/summary", response_model=RepoSummaryText)
def get_summary(repo_id: int, kind: Literal["summary", "usage_ideas"] = "summary", ctx: AppContext = Depends(get_ctx)) -> RepoSummaryText:
    if repo_store.get_detail(ctx.db, repo_id) is None:
        raise not_found("Repository")
    return RepoSummaryText.model_validate(ask_flow.cached_summary(ctx, repo_id, kind))


@router.post("/repos/{repo_id}/summary")
def make_summary(
    repo_id: int, body: SummaryRequest, kind: Literal["summary", "usage_ideas"] = "summary", ctx: AppContext = Depends(get_ctx)
) -> StreamingResponse:
    if repo_store.get_detail(ctx.db, repo_id) is None:
        raise not_found("Repository")
    return sse(ask_flow.run_repo_summary(ctx, repo_id, kind, body.route, body.confirm_cloud))


# ---------------------------------------------------------------------------------- compare


def _check_ids(ctx: AppContext, repo_ids: list[int]) -> list[int]:
    ids = list(dict.fromkeys(repo_ids))
    found = {r["id"] for r in ctx.db.all(f"SELECT id FROM repos WHERE id IN ({','.join(str(int(i)) for i in ids)})")}
    if len(found) < 2 or found != set(ids):
        raise ApiError(404, "not_found", "Choose at least two repositories from your library.")
    return ids


@router.post("/compare", response_model=Comparison)
def compare(req: CompareRequest, ctx: AppContext = Depends(get_ctx)) -> Comparison:
    return build_comparison(ctx, _check_ids(ctx, req.repo_ids))


def _save_comparison(ctx: AppContext, comparison: Comparison, *, narrative: str = "", provider: Any = None, usage: dict | None = None,
                     question: str = "", extra: dict | None = None) -> int:
    names = [r.full_name for r in comparison.repos]
    report_id = report_store.create_report(
        ctx.db, kind="compare", title="Compare " + " vs ".join(n.split("/")[-1] for n in names)[:150],
        question=question or "Compare " + ", ".join(names),
        provider_id=provider.config.id if provider else None, provider_kind=provider.config.kind if provider else None,
        model=provider.model if provider else None, repo_ids=[r.id for r in comparison.repos], status="completed", saved=True,
    )
    table = comparison_as_text(comparison)
    body = (narrative.strip() + "\n\n" if narrative.strip() else "") + "## Comparison table\n\n" + table
    body += "\n\n## License observations\n\n" + "\n".join(f"- {n}" for n in comparison.license_notes)
    data = {"comparison": comparison.model_dump(exclude={"evidence"}), **(extra or {})}
    report_store.finish_report(ctx.db, report_id, answer_md=body, evidence=[e.model_dump() for e in comparison.evidence], data=data, usage=usage or {})
    return report_id


@router.post("/compare/save", response_model=Report)
def compare_save(req: CompareRequest, ctx: AppContext = Depends(get_ctx)) -> Report:
    comparison = build_comparison(ctx, _check_ids(ctx, req.repo_ids))
    return report_store.get_report(ctx.db, _save_comparison(ctx, comparison))  # type: ignore[return-value]


@router.post("/compare/narrative")
def compare_narrative(req: CompareNarrativeRequest, ctx: AppContext = Depends(get_ctx)) -> StreamingResponse:
    ids = _check_ids(ctx, req.repo_ids)

    def events() -> Iterator[tuple[str, dict[str, Any]]]:
        comparison = build_comparison(ctx, ids)
        evidence = EvidenceSet(ctx, max_chars=min(ctx.settings.research.max_context_chars, 24_000))
        for rid in ids:
            evidence.add_metadata(rid)
            evidence.add_dependencies(rid)
            for ch in ctx.db.all("SELECT id FROM chunks WHERE repo_id=? AND kind='readme' ORDER BY document_id, ordinal LIMIT 3", (rid,)):
                evidence.add_chunk(ch["id"])
        yield "evidence", {"evidence": [e.model_dump() for e in evidence.items], "notices": []}
        try:
            resolved = ctx.router.resolve("compare", ask_flow.to_route(req.route), confirm_cloud=req.confirm_cloud)
        except ProviderError as exc:
            yield ask_flow.error_event(ctx, exc)
            return
        yield "status", ask_flow.status_payload(resolved, "Writing comparison")
        meter = ask_flow.UsageMeter(ctx, resolved, "compare", None)
        focus = f"\nThe user's angle: {req.focus.strip()[:400]}" if req.focus.strip() else ""
        prompt = (
            "Compare these repositories for a developer choosing between or combining them. Cover: what each is for, capabilities "
            "each project documents, architecture and dependencies, maturity and maintenance, licensing, and when to prefer each. "
            f"End with the open questions the evidence cannot answer.{focus}\n\nFacts table (observed metadata):\n"
            f"{comparison_as_text(comparison)}\n\nEvidence:\n{evidence.render()}"
        )
        request = GenerationRequest(
            model=resolved.model, system=ask_flow.ASK_SYSTEM, messages=[ChatMessage(role="user", content=prompt)],
            temperature=0.2, max_output_tokens=min(ctx.settings.research.max_output_tokens, 2000),
        )
        parts: list[str] = []
        try:
            for delta in ask_flow.stream_text(resolved, request, meter):
                parts.append(delta)
                yield "delta", {"text": delta}
        except ProviderError as exc:
            yield ask_flow.error_event(ctx, exc)
            return
        text, unsupported, warnings = check_answer(ctx, "".join(parts), evidence.items)
        comparison.evidence = evidence.items
        report_id = _save_comparison(
            ctx, comparison, narrative=text, provider=resolved, usage=meter.info.model_dump(), question=req.focus,
            extra={"warnings": warnings, "unsupported": [u.model_dump() for u in unsupported]},
        )
        yield "done", {"report": report_store.get_report(ctx.db, report_id).model_dump()}  # type: ignore[union-attr]

    return sse(events())


# ---------------------------------------------------------------------------------- reports and evidence


@router.get("/reports", response_model=list[ReportSummary])
def reports(kind: str | None = None, saved: bool | None = None, limit: int = Query(100, ge=1, le=500), ctx: AppContext = Depends(get_ctx)) -> list[ReportSummary]:
    return report_store.list_reports(ctx.db, kind=kind or None, saved=saved, limit=limit)


def _report(ctx: AppContext, report_id: int) -> Report:
    report = report_store.get_report(ctx.db, report_id)
    if report is None:
        raise not_found("Report")
    return report


@router.get("/reports/{report_id}", response_model=Report)
def report(report_id: int, ctx: AppContext = Depends(get_ctx)) -> Report:
    return _report(ctx, report_id)


@router.patch("/reports/{report_id}", response_model=Report)
def patch_report(report_id: int, body: ReportPatch, ctx: AppContext = Depends(get_ctx)) -> Report:
    _report(ctx, report_id)
    report_store.patch_report(ctx.db, report_id, title=body.title, saved=body.saved, collection_ids=body.collection_ids)
    return _report(ctx, report_id)


@router.delete("/reports/{report_id}", response_model=Ok)
def delete_report(report_id: int, ctx: AppContext = Depends(get_ctx)) -> Ok:
    report_store.delete_report(ctx.db, report_id)
    return Ok()


@router.get("/reports/{report_id}/export")
def export_report(report_id: int, ctx: AppContext = Depends(get_ctx)) -> PlainTextResponse:
    rep = _report(ctx, report_id)
    return PlainTextResponse(
        report_store.export_markdown(rep), media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="starindex-report-{report_id}.md"'},
    )


@router.get("/evidence/chunk/{chunk_id}", response_model=EvidenceDetail)
def evidence_chunk(chunk_id: int, context_lines: int = Query(12, ge=0, le=80), ctx: AppContext = Depends(get_ctx)) -> EvidenceDetail:
    """The cited passage with surrounding lines from the indexed file."""
    es = EvidenceSet(ctx, max_chars=10**9, max_item_chars=10**9)
    if es.add_chunk(chunk_id) is None:
        raise not_found("Evidence")
    ev = es.items[0]
    content = ctx.db.scalar("SELECT content FROM documents WHERE id=?", (ev.document_id,), "")
    lines = content.split("\n")
    start, end = ev.start_line or 1, ev.end_line or 1
    before_start = max(1, start - context_lines)
    return EvidenceDetail(
        **{**ev.model_dump(), "text": "\n".join(lines[start - 1 : end])},     # raw file lines for the viewer
        context_before="\n".join(lines[before_start - 1 : start - 1]),
        context_after="\n".join(lines[end : end + context_lines]),
        context_start_line=before_start,
        repo=repo_store.get_summaries(ctx.db, [ev.repo_id]).get(ev.repo_id),
    )
