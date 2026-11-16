"""Grounded question answering and the shared generation plumbing for agent workflows.

Workflows are generators of ``(event, payload)`` pairs that the API relays as server-sent
events. Retrieval always happens first and locally; a model is contacted only after the
router has confirmed the provider is allowed (privacy settings, explicit cloud confirmation).
A provider failure ends the workflow with an error event that lists the providers the user
may switch to -- nothing is retried elsewhere automatically.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from ..api.schemas import AskRequest, RepoFilters, RouteOverride, SearchRequest, UsageInfo
from ..db import loads, utcnow
from ..library import reports as report_store
from ..providers.base import ChatMessage, GenerationRequest, ProviderError, Usage, estimate_usage
from ..settings_store import Route
from .evidence import EvidenceSet, check_answer
from .safety import UNTRUSTED_NOTICE

if TYPE_CHECKING:
    from ..context import AppContext

log = logging.getLogger(__name__)
Event = tuple[str, dict[str, Any]]

GROUNDING_RULES = f"""Rules you must follow:
1. Use only the evidence blocks provided in this conversation. Cite the supporting evidence after each claim with its ref in square brackets, for example [E3]. Use only refs that exist.
2. Every evidence block has a class. "observed" is GitHub metadata, a dependency manifest or source code that StarIndex indexed. "documented" is what a project's own README or docs say; attribute such statements to the project ("its README states ...") and do not present them as verified behaviour.
3. When you conclude something from several sources, say that it is an inference.
4. Never invent a repository, feature, API, compatibility claim or maintenance status. If the evidence does not answer something, say exactly what remains unknown.
5. Only repositories that appear in the evidence may be named or recommended. Write them as owner/name.
6. {UNTRUSTED_NOTICE}"""

# Repeated after the evidence: small local models follow the instruction nearest to the end of the prompt.
CITE_REMINDER = (
    "Now write the answer using only the evidence above. After every statement, add the ref of the evidence block "
    "that supports it in square brackets, in the form [E<number>]. A statement without such a ref is not allowed. "
    "Cover every relevant repository found in the evidence, not just one."
)

ASK_SYSTEM = (
    "You are StarIndex, a research assistant for one developer's library of starred GitHub repositories. "
    "Answer the question concisely in GitHub-flavoured Markdown.\n\n" + GROUNDING_RULES
)


def to_route(override: RouteOverride | None) -> Route | None:
    return Route(provider_id=override.provider_id, model=override.model) if override else None


def error_event(ctx: AppContext, exc: ProviderError, *, report_id: int | None = None) -> Event:
    """Error payload plus the providers the user could explicitly choose instead."""
    try:
        options = [o.model_dump() for o in ctx.router.options() if o.provider_id != exc.provider_id]
    except Exception:
        options = []
    return "error", {"error": {k: v for k, v in exc.to_dict().items() if k != "param"}, "report_id": report_id, "alternatives": options}


def status_payload(resolved: Any, message: str) -> dict[str, Any]:
    return {
        "message": message, "provider_id": resolved.config.id, "provider_kind": resolved.config.kind,
        "provider_name": resolved.config.name, "model": resolved.model, "is_cloud": resolved.is_cloud,
    }


class UsageMeter:
    """Accumulates token usage and cost across the calls of one workflow."""

    def __init__(self, ctx: AppContext, resolved: Any, task: str, report_id: int | None):
        self.ctx, self.resolved, self.task, self.report_id = ctx, resolved, task, report_id
        self.info = UsageInfo(input_tokens=0, output_tokens=0)

    def add(self, usage: Usage) -> None:
        self.info.input_tokens = (self.info.input_tokens or 0) + (usage.input_tokens or 0)
        self.info.output_tokens = (self.info.output_tokens or 0) + (usage.output_tokens or 0)
        self.info.estimated = self.info.estimated or usage.estimated
        self.info.calls += 1
        try:
            cost = self.ctx.router.record_usage(self.resolved, self.task, usage, report_id=self.report_id)
        except Exception:
            log.debug("usage logging failed", exc_info=True)
            cost = None
        if cost is not None:
            self.info.cost_usd = round((self.info.cost_usd or 0.0) + cost, 6)

    @property
    def total_tokens(self) -> int:
        return (self.info.input_tokens or 0) + (self.info.output_tokens or 0)


def stream_text(resolved: Any, request: GenerationRequest, meter: UsageMeter) -> Iterator[str]:
    """Yield text deltas; record usage when the provider reports completion. Raises ProviderError."""
    parts: list[str] = []
    usage: Usage | None = None
    for ev in resolved.provider.stream(request):
        if ev.type == "delta":
            if ev.text:
                parts.append(ev.text)
                yield ev.text
        else:
            usage = ev.usage
    if usage is None or (usage.input_tokens is None and usage.output_tokens is None):
        usage = estimate_usage(request, "".join(parts))
    meter.add(usage)
    if not "".join(parts).strip():
        # A "successful" stream with no text is not an answer; report it instead of saving an empty result.
        raise ProviderError(
            "invalid_response", "The model returned an empty answer. Try again, or choose a different model.",
            provider_id=resolved.config.id, provider_kind=resolved.config.kind,
        )


def retrieve_for_question(ctx: AppContext, question: str, evidence: EvidenceSet, *, repo_ids: list[int], filters: RepoFilters | None) -> list[str]:
    """Deterministic retrieval for a question. Returns notices (e.g. semantic index not ready)."""
    notices: list[str] = []
    base = (filters or RepoFilters()).model_copy(deep=True)
    if repo_ids:
        base.repo_ids = list(repo_ids)
        base.star_state = "all"
    if repo_ids and len(repo_ids) <= 3:
        # Focused question about specific repositories: start from their own material.
        for rid in repo_ids:
            evidence.add_metadata(rid)
            evidence.add_dependencies(rid)
        for scope in ("docs", "code"):
            res = ctx.search.search(SearchRequest(query=question, mode="hybrid", scope=scope, filters=base, limit=8, record=False), _nested=True)  # type: ignore[arg-type]
            notices += res.notices
            for r in res.results:
                evidence.add_chunk(r.matches[0].chunk_id)
        for rid in repo_ids:
            for ch in ctx.db.all("SELECT id FROM chunks WHERE repo_id=? AND kind='readme' ORDER BY document_id, ordinal LIMIT 2", (rid,)):
                evidence.add_chunk(ch["id"])
        return list(dict.fromkeys(notices))
    res = ctx.search.search(SearchRequest(query=question, mode="hybrid", filters=base, limit=8, record=False), _nested=True)
    notices += res.notices
    for r in res.results[:8]:
        evidence.add_metadata(r.repo.id)
        if r.matches:
            evidence.add_chunk(r.matches[0].chunk_id)
    passages = ctx.search.search(SearchRequest(query=question, mode="hybrid", scope="docs", filters=base, limit=6, record=False), _nested=True)
    for r in passages.results:
        evidence.add_chunk(r.matches[0].chunk_id)
    return list(dict.fromkeys(notices))


def run_ask(ctx: AppContext, req: AskRequest) -> Iterator[Event]:
    settings = ctx.settings
    evidence = EvidenceSet(ctx, max_chars=min(settings.research.max_context_chars, 18_000))
    notices = retrieve_for_question(ctx, req.question, evidence, repo_ids=req.repo_ids, filters=req.filters)
    yield "evidence", {"evidence": [e.model_dump() for e in evidence.items], "notices": notices}

    try:
        resolved = ctx.router.resolve("answer", to_route(req.route), confirm_cloud=req.confirm_cloud)
    except ProviderError as exc:
        yield error_event(ctx, exc)
        return
    if not evidence.items:
        # Nothing retrieved: do not ask a model to answer from nothing.
        report_id = report_store.create_report(ctx.db, kind="ask", title=req.question[:80], question=req.question, status="completed")
        text = "Nothing in your indexed library matches this question, so there is no evidence to answer from. Try different wording, or check that synchronisation and indexing have finished."
        report_store.finish_report(ctx.db, report_id, answer_md=text)
        yield "delta", {"text": text}
        yield "done", {"report": report_store.get_report(ctx.db, report_id).model_dump()}  # type: ignore[union-attr]
        return

    report_id = report_store.create_report(
        ctx.db, kind="ask", title=req.question[:80], question=req.question, provider_id=resolved.config.id,
        provider_kind=resolved.config.kind, model=resolved.model, repo_ids=evidence.repo_ids,
    )
    yield "status", status_payload(resolved, "Generating answer")
    meter = UsageMeter(ctx, resolved, "answer", report_id)
    messages: list[ChatMessage] = []
    for turn in req.history[-6:]:
        role = turn.get("role")
        if role in ("user", "assistant") and turn.get("content"):
            messages.append(ChatMessage(role=role, content=str(turn["content"])[:4000]))  # type: ignore[arg-type]
    messages.append(ChatMessage(role="user", content=f"Question: {req.question}\n\nEvidence:\n{evidence.render()}\n\nQuestion again: {req.question}\n{CITE_REMINDER}"))
    request = GenerationRequest(
        model=resolved.model, system=ASK_SYSTEM, messages=messages, temperature=0.2,
        max_output_tokens=min(settings.research.max_output_tokens, 1500),
    )
    parts: list[str] = []
    try:
        for delta in stream_text(resolved, request, meter):
            parts.append(delta)
            yield "delta", {"text": delta}
    except ProviderError as exc:
        report_store.finish_report(
            ctx.db, report_id, status="failed", error=exc.message, answer_md="".join(parts),
            evidence=[e.model_dump() for e in evidence.items], usage=meter.info.model_dump(),
        )
        yield error_event(ctx, exc, report_id=report_id)
        return
    text, unsupported, warnings = check_answer(ctx, "".join(parts), evidence.items)
    report_store.finish_report(
        ctx.db, report_id, answer_md=text, evidence=[e.model_dump() for e in evidence.items], usage=meter.info.model_dump(),
        data={"warnings": warnings + notices, "unsupported": [u.model_dump() for u in unsupported]},
    )
    yield "done", {"report": report_store.get_report(ctx.db, report_id).model_dump()}  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------- repository summaries

SUMMARY_PROMPTS = {
    "summary": (
        "Write a factual description of this repository for a developer deciding whether it is relevant: what it is, what it is "
        "for, how it is built (languages, notable dependencies) and its current maintenance state. 120-180 words, no marketing language."
    ),
    "usage_ideas": (
        "Suggest three to five concrete ways this developer could use this repository in their own projects. For each idea give a "
        "one-line title, what it would take to implement using what the evidence shows (APIs, packages, configuration), and any "
        "constraint visible in the evidence (license, maintenance, missing documentation). Do not suggest uses the evidence cannot support."
    ),
}


def summary_source_hash(ctx: AppContext, repo_id: int) -> str:
    """Changes when the material a summary is generated from changes."""
    row = ctx.db.one("SELECT metadata_hash, indexed_commit_sha, indexed_level FROM repos WHERE id=?", (repo_id,))
    readme = ctx.db.scalar("SELECT group_concat(content_hash) FROM documents WHERE repo_id=? AND kind='readme'", (repo_id,), "")
    raw = f"{row['metadata_hash'] if row else ''}|{row['indexed_commit_sha'] if row else ''}|{row['indexed_level'] if row else ''}|{readme}"
    return hashlib.sha1(raw.encode()).hexdigest()


def cached_summary(ctx: AppContext, repo_id: int, kind: str) -> dict[str, Any]:
    row = ctx.db.one("SELECT * FROM repo_summaries WHERE repo_id=? AND kind=?", (repo_id, kind))
    if row is None:
        return {"repo_id": repo_id, "kind": kind, "content": None, "evidence": [], "stale": False}
    return {
        "repo_id": repo_id, "kind": kind, "content": row["content"], "evidence": loads(row["evidence"], []),
        "provider": row["provider"], "model": row["model"], "created_at": row["created_at"],
        "stale": row["source_hash"] != summary_source_hash(ctx, repo_id),
    }


def run_repo_summary(ctx: AppContext, repo_id: int, kind: str, route: RouteOverride | None, confirm_cloud: bool) -> Iterator[Event]:
    evidence = EvidenceSet(ctx, max_chars=12_000)
    evidence.add_metadata(repo_id)
    evidence.add_dependencies(repo_id)
    for ch in ctx.db.all("SELECT id FROM chunks WHERE repo_id=? AND kind='readme' ORDER BY document_id, ordinal LIMIT 6", (repo_id,)):
        evidence.add_chunk(ch["id"])
    if kind == "usage_ideas":
        for ch in ctx.db.all(
            "SELECT id FROM chunks WHERE repo_id=? AND kind IN ('doc','example','api') ORDER BY document_id, ordinal LIMIT 4", (repo_id,)
        ):
            evidence.add_chunk(ch["id"])
    yield "evidence", {"evidence": [e.model_dump() for e in evidence.items], "notices": []}
    try:
        resolved = ctx.router.resolve("summarize", to_route(route), confirm_cloud=confirm_cloud)
    except ProviderError as exc:
        yield error_event(ctx, exc)
        return
    yield "status", status_payload(resolved, "Generating")
    meter = UsageMeter(ctx, resolved, "summarize", None)
    request = GenerationRequest(
        model=resolved.model, system=ASK_SYSTEM,
        messages=[ChatMessage(role="user", content=f"{SUMMARY_PROMPTS[kind]}\n\nEvidence:\n{evidence.render()}\n\n{CITE_REMINDER}")],
        temperature=0.2, max_output_tokens=700,
    )
    parts: list[str] = []
    try:
        for delta in stream_text(resolved, request, meter):
            parts.append(delta)
            yield "delta", {"text": delta}
    except ProviderError as exc:
        yield error_event(ctx, exc)
        return
    text, _, _ = check_answer(ctx, "".join(parts), evidence.items)
    with ctx.db.tx() as c:
        from ..db import dumps

        c.execute(
            "INSERT OR REPLACE INTO repo_summaries(repo_id, kind, source_hash, provider, model, content, evidence, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (repo_id, kind, summary_source_hash(ctx, repo_id), resolved.config.kind, resolved.model, text,
             dumps([e.model_dump() for e in evidence.items]), utcnow()),
        )
    yield "done", {"summary": cached_summary(ctx, repo_id, kind), "usage": meter.info.model_dump()}
