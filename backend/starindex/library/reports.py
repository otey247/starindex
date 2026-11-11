"""Storage for Ask answers, research reports, comparisons and usage ideas."""

from __future__ import annotations

from typing import Any

from ..api.schemas import (
    CollectionRef,
    Evidence,
    Report,
    ReportSummary,
    TraceStep,
    UnsupportedCitation,
    UsageInfo,
)
from ..db import Database, dumps, loads, utcnow
from . import repos as repo_store


def create_report(db: Database, *, kind: str, title: str, question: str, provider_id: str | None = None,
                  provider_kind: str | None = None, model: str | None = None, repo_ids: list[int] | None = None,
                  status: str = "running", saved: bool = False) -> int:
    with db.tx() as c:
        return int(
            c.execute(
                "INSERT INTO reports(kind, title, question, status, saved, provider_id, provider_kind, model, repo_ids, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (kind, title[:200], question, status, int(saved), provider_id, provider_kind, model, dumps(repo_ids or []), utcnow()),
            ).lastrowid
        )


def update_report(db: Database, report_id: int, **fields: Any) -> None:
    enc = {"evidence", "trace", "data", "usage", "repo_ids"}
    sets = {k: (dumps(v) if k in enc else v) for k, v in fields.items()}
    if not sets:
        return
    with db.tx() as c:
        c.execute(f"UPDATE reports SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), report_id))


def finish_report(db: Database, report_id: int, *, status: str = "completed", **fields: Any) -> None:
    update_report(db, report_id, status=status, finished_at=utcnow(), **fields)


def _summary(row) -> ReportSummary:
    return ReportSummary(
        id=row["id"], kind=row["kind"], title=row["title"], question=row["question"], status=row["status"], saved=bool(row["saved"]),
        provider_id=row["provider_id"], provider_kind=row["provider_kind"], model=row["model"], repo_ids=loads(row["repo_ids"], []),
        created_at=row["created_at"], finished_at=row["finished_at"],
    )


def list_reports(db: Database, *, kind: str | None = None, saved: bool | None = None, limit: int = 100) -> list[ReportSummary]:
    where, params = [], []
    if kind:
        where.append("kind=?")
        params.append(kind)
    if saved is not None:
        where.append("saved=?")
        params.append(int(saved))
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return [_summary(r) for r in db.all(f"SELECT * FROM reports {clause} ORDER BY id DESC LIMIT ?", (*params, limit))]


def get_report(db: Database, report_id: int) -> Report | None:
    row = db.one("SELECT * FROM reports WHERE id=?", (report_id,))
    if row is None:
        return None
    data = loads(row["data"], {})
    base = _summary(row)
    return Report(
        **base.model_dump(),
        answer_md=row["answer_md"],
        evidence=[Evidence.model_validate(e) for e in loads(row["evidence"], [])],
        trace=[TraceStep.model_validate(t) for t in loads(row["trace"], [])],
        usage=UsageInfo.model_validate(loads(row["usage"], {}) or {}),
        data=data,
        warnings=list(data.get("warnings", [])),
        unsupported=[UnsupportedCitation.model_validate(u) for u in data.get("unsupported", [])],
        error=row["error"],
        collections=[
            CollectionRef(id=c["id"], name=c["name"])
            for c in db.all("SELECT c.id, c.name FROM collection_reports cr JOIN collections c ON c.id=cr.collection_id WHERE cr.report_id=?", (report_id,))
        ],
        repos=repo_store.ordered_summaries(db, base.repo_ids[:60]),
    )


def patch_report(db: Database, report_id: int, *, title: str | None, saved: bool | None, collection_ids: list[int] | None) -> None:
    with db.tx() as c:
        if title is not None:
            c.execute("UPDATE reports SET title=? WHERE id=?", (title, report_id))
        if saved is not None:
            c.execute("UPDATE reports SET saved=? WHERE id=?", (int(saved), report_id))
        if collection_ids is not None:
            c.execute("DELETE FROM collection_reports WHERE report_id=?", (report_id,))
            for cid in dict.fromkeys(collection_ids):
                if c.execute("SELECT 1 FROM collections WHERE id=?", (cid,)).fetchone():
                    c.execute("INSERT OR IGNORE INTO collection_reports(collection_id, report_id) VALUES (?,?)", (cid, report_id))
            if collection_ids:
                c.execute("UPDATE reports SET saved=1 WHERE id=?", (report_id,))


def delete_report(db: Database, report_id: int) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM reports WHERE id=?", (report_id,))


def export_markdown(report: Report) -> str:
    """Self-contained Markdown: answer, sources with permalinks, and the research trace."""
    lines = [f"# {report.title}", "", f"> {report.question}", ""]
    meta = [f"Generated {report.created_at}"]
    if report.model:
        meta.append(f"model: {report.model} ({report.provider_kind})")
    lines += ["_" + " · ".join(meta) + "_", "", report.answer_md.strip(), ""]
    if report.warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in report.warnings] + [""]
    if report.evidence:
        lines += ["## Sources", ""]
        labels = {"observed": "observed", "documented": "documented by the project", "inferred": "inferred", "unverified": "unverified"}
        for ev in report.evidence:
            where = ev.repo_full_name + (f" · `{ev.path}`" if ev.path else "")
            if ev.start_line:
                where += f" L{ev.start_line}" + (f"–{ev.end_line}" if ev.end_line and ev.end_line != ev.start_line else "")
            if ev.commit_sha:
                where += f" @ {ev.commit_sha[:7]}"
            link = f" — <{ev.html_url}>" if ev.html_url else ""
            lines.append(f"- **[{ev.ref}]** {where} ({labels.get(ev.evidence_class, ev.evidence_class)}){link}")
        lines.append("")
    if report.trace:
        lines += ["## Research trace", ""]
        for step in report.trace:
            tool = f" `{step.tool}`" if step.tool else ""
            lines.append(f"{step.index}. **{step.title}**{tool} — {step.summary}")
        lines.append("")
    return "\n".join(lines)
