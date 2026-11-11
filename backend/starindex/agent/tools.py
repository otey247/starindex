"""Typed research tools over the local index.

Every tool is a read-only lookup against local data, except ``save_research_report`` which
marks the current report as saved in the local database. Arguments are validated with
Pydantic before anything runs; repositories are addressed by name and must exist in the
library, so a model cannot make a tool act on something that was never starred. There is no
tool that touches the network, the file system or a shell.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationError

from ..api.schemas import RepoFilters, SearchRequest
from ..library import insights
from ..library.repos import find_by_name, maintenance_status
from .compare import build_comparison, comparison_as_text, license_family
from .evidence import EvidenceSet

if TYPE_CHECKING:
    from ..context import AppContext


class ToolError(Exception):
    """Reported back to the model as the tool result; never crashes the workflow."""


@dataclass
class ToolResult:
    summary: str                      # compact text shown to the model and in the trace
    repo_ids: list[int] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class ToolState:
    """Shared, deterministic state of one research run."""

    ctx: AppContext
    evidence: EvidenceSet
    scope_repo_ids: list[int] = field(default_factory=list)   # non-empty = research restricted to these
    report_id: int | None = None
    saved_title: str | None = None

    def filters(self, **kw: Any) -> RepoFilters:
        return RepoFilters(repo_ids=list(self.scope_repo_ids), **kw)

    def repo(self, name: str) -> int:
        repo_id = find_by_name(self.ctx.db, name.strip().removeprefix("https://github.com/").strip("/"))
        if repo_id is None:
            raise ToolError(f"'{name}' is not in the starred library. Use search_repositories to find real repository names.")
        if self.scope_repo_ids and repo_id not in self.scope_repo_ids:
            raise ToolError(f"'{name}' is outside the scope of this research request.")
        return repo_id


# ---------------------------------------------------------------------------------- argument models


class SearchRepositoriesArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    language: str | None = None
    topic: str | None = None
    license: str | None = None
    min_stars: int | None = Field(None, ge=0)
    include_archived: bool = True
    limit: int = Field(8, ge=1, le=12)


class SearchPassagesArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    repo: str | None = None
    limit: int = Field(6, ge=1, le=10)


class RepoArgs(BaseModel):
    repo: str = Field(min_length=3, max_length=200)


class ReadFileArgs(BaseModel):
    repo: str = Field(min_length=3, max_length=200)
    path: str = Field(min_length=1, max_length=400)
    start_line: int = Field(1, ge=1)
    end_line: int | None = Field(None, ge=1)


class SimilarArgs(BaseModel):
    repo: str = Field(min_length=3, max_length=200)
    limit: int = Field(6, ge=1, le=10)


class CompareArgs(BaseModel):
    repos: list[str] = Field(min_length=2, max_length=5)


class LicenseFilterArgs(BaseModel):
    repos: list[str] = Field(default_factory=list, max_length=30)
    family: str | None = None      # permissive | weak copyleft | strong copyleft | network copyleft | none
    licenses: list[str] = Field(default_factory=list, max_length=10)


class CollectionArgs(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class SaveReportArgs(BaseModel):
    title: str = Field(min_length=1, max_length=160)


# ---------------------------------------------------------------------------------- implementations


def _repo_line(row: Any) -> str:
    return (
        f"{row['full_name']} — {(row['description'] or 'no description')[:140]} "
        f"[{row['language'] or '?'}, ★{row['stars']}, {row['license_spdx'] or 'no license'}, "
        f"{maintenance_status(row['pushed_at'], bool(row['is_archived']))}]"
    )


def search_repositories(state: ToolState, a: SearchRepositoriesArgs) -> ToolResult:
    filters = state.filters(
        languages=[a.language] if a.language else [], topics=[a.topic] if a.topic else [],
        licenses=[a.license] if a.license else [], min_stars=a.min_stars, archived=None if a.include_archived else False,
    )
    res = state.ctx.search.search(SearchRequest(query=a.query, mode="hybrid", filters=filters, limit=a.limit, record=False), _nested=True)
    lines, refs, ids = [], [], []
    for r in res.results:
        ids.append(r.repo.id)
        ref = state.evidence.add_metadata(r.repo.id)
        if ref:
            refs.append(ref)
        best = None
        if r.matches:
            best = state.evidence.add_chunk(r.matches[0].chunk_id)
            if best:
                refs.append(best)
        cite = " ".join(f"[{x}]" for x in (ref, best) if x)
        lines.append(f"- {r.repo.full_name} — {(r.repo.description or '')[:140]} [{r.repo.language or '?'}, ★{r.repo.stars}, {r.repo.license_spdx or 'no license'}, {r.repo.maintenance}] {cite}")
    if not lines:
        return ToolResult(f"No starred repositories matched '{a.query}'" + (f" ({'; '.join(res.notices)})" if res.notices else "."))
    note = f" (search mode: {res.mode})" if res.mode != "hybrid" else ""
    return ToolResult(f"{len(lines)} repositories for '{a.query}'{note}:\n" + "\n".join(lines), ids, refs)


def _search_passages(state: ToolState, a: SearchPassagesArgs, scope: str) -> ToolResult:
    filters = state.filters()
    if a.repo:
        filters.repo_ids = [state.repo(a.repo)]
    res = state.ctx.search.search(SearchRequest(query=a.query, mode="hybrid", scope=scope, filters=filters, limit=a.limit, record=False), _nested=True)  # type: ignore[arg-type]
    lines, refs, ids = [], [], []
    for r in res.results:
        m = r.matches[0]
        ref = state.evidence.add_chunk(m.chunk_id)
        if not ref:
            continue
        refs.append(ref)
        ids.append(r.repo.id)
        snippet = m.snippet.replace("«", "").replace("»", "")[:200]
        lines.append(f"- [{ref}] {r.repo.full_name} {m.path}:{m.start_line}-{m.end_line} — {snippet}")
    what = "code" if scope == "code" else "documentation"
    if not lines:
        hint = " Code is only searchable for repositories indexed at the deep level." if scope == "code" else ""
        return ToolResult(f"No indexed {what} matched '{a.query}'.{hint}")
    return ToolResult(f"{len(lines)} {what} passages for '{a.query}':\n" + "\n".join(lines), list(dict.fromkeys(ids)), refs)


def search_documents(state: ToolState, a: SearchPassagesArgs) -> ToolResult:
    return _search_passages(state, a, "docs")


def search_code(state: ToolState, a: SearchPassagesArgs) -> ToolResult:
    return _search_passages(state, a, "code")


def get_repository_details(state: ToolState, a: RepoArgs) -> ToolResult:
    repo_id = state.repo(a.repo)
    db = state.ctx.db
    refs = [r for r in (state.evidence.add_metadata(repo_id),) if r]
    readme = db.all("SELECT id FROM chunks WHERE repo_id=? AND kind='readme' ORDER BY document_id, ordinal LIMIT 2", (repo_id,))
    for ch in readme:
        ref = state.evidence.add_chunk(ch["id"])
        if ref:
            refs.append(ref)
    row = db.one("SELECT * FROM repos WHERE id=?", (repo_id,))
    docs = db.all("SELECT path, kind FROM documents WHERE repo_id=? ORDER BY kind, path LIMIT 40", (repo_id,))
    listing = ", ".join(f"{d['path']}" for d in docs) or "nothing indexed beyond metadata"
    return ToolResult(
        f"{_repo_line(row)}\nIndexed level: {row['indexed_level'] or 'none'}; indexed files: {listing}\n"
        f"Evidence added: {' '.join(f'[{r}]' for r in refs)}",
        [repo_id], refs,
    )


def read_indexed_file(state: ToolState, a: ReadFileArgs) -> ToolResult:
    repo_id = state.repo(a.repo)
    doc = state.ctx.db.one("SELECT id, content FROM documents WHERE repo_id=? AND path=?", (repo_id, a.path))
    if doc is None:
        paths = [r["path"] for r in state.ctx.db.all("SELECT path FROM documents WHERE repo_id=? ORDER BY path LIMIT 60", (repo_id,))]
        raise ToolError(f"'{a.path}' is not indexed for {a.repo}. Indexed files: {', '.join(paths) or 'none'}")
    total = doc["content"].count("\n") + 1
    end = min(a.end_line or a.start_line + 119, a.start_line + 159, total)
    ref = state.evidence.add_file_slice(repo_id, doc["id"], a.start_line, end)
    if ref is None:
        return ToolResult("The evidence budget for this request is exhausted; no more file content can be added.")
    return ToolResult(f"[{ref}] {a.repo} {a.path} lines {a.start_line}-{end} of {total} added to the evidence.", [repo_id], [ref])


def get_repository_dependencies(state: ToolState, a: RepoArgs) -> ToolResult:
    repo_id = state.repo(a.repo)
    ref = state.evidence.add_dependencies(repo_id)
    deps = insights.dependencies(state.ctx, repo_id)
    if not deps:
        return ToolResult(f"No dependency manifest is indexed for {a.repo}.", [repo_id])
    runtime = [d for d in deps if d.scope in ("runtime", "peer")]
    shared = [f"{d.name} (also in {d.shared_with} starred repos)" for d in runtime if d.shared_with][:10]
    text = f"[{ref}] {a.repo}: {len(runtime)} runtime dependencies: " + ", ".join(d.name for d in runtime[:25])
    if shared:
        text += "\nShared with other starred repositories: " + ", ".join(shared)
    return ToolResult(text, [repo_id], [ref] if ref else [])


def find_similar_repositories(state: ToolState, a: SimilarArgs) -> ToolResult:
    repo_id = state.repo(a.repo)
    related = insights.related(state.ctx, repo_id, limit=a.limit)
    if state.scope_repo_ids:
        related = [r for r in related if r.repo.id in state.scope_repo_ids]
    if not related:
        return ToolResult(f"No related starred repositories were found for {a.repo}.", [repo_id])
    lines, refs, ids = [], [], [repo_id]
    for r in related:
        ref = state.evidence.add_metadata(r.repo.id)
        if ref:
            refs.append(ref)
        ids.append(r.repo.id)
        why = "; ".join(x.detail for x in r.reasons[:2])
        lines.append(f"- {r.repo.full_name} [{ref}] — {why} [{r.repo.maintenance}, ★{r.repo.stars}]")
    return ToolResult(f"Starred repositories related to {a.repo}:\n" + "\n".join(lines), ids, refs)


def compare_repositories(state: ToolState, a: CompareArgs) -> ToolResult:
    ids = list(dict.fromkeys(state.repo(n) for n in a.repos))
    if len(ids) < 2:
        raise ToolError("Give at least two different repositories to compare.")
    comparison = build_comparison(state.ctx, ids)
    refs = []
    for rid in ids:
        for ref in (state.evidence.add_metadata(rid), state.evidence.add_dependencies(rid)):
            if ref:
                refs.append(ref)
    notes = "\n".join(f"- {n}" for n in comparison.license_notes)
    shared = ", ".join(s.name for s in comparison.shared_dependencies[:10]) or "none"
    return ToolResult(
        f"{comparison_as_text(comparison)}\nShared dependencies: {shared}\nLicense observations:\n{notes}\nEvidence: {' '.join(f'[{r}]' for r in refs)}",
        ids, refs,
    )


def filter_by_license(state: ToolState, a: LicenseFilterArgs) -> ToolResult:
    db = state.ctx.db
    if a.repos:
        ids = [state.repo(n) for n in a.repos]
    else:
        ids = state.scope_repo_ids or state.evidence.repo_ids
    if not ids:
        raise ToolError("Name the repositories to check, or search first.")
    wanted = {x.upper() for x in a.licenses}
    lines, refs, kept = [], [], []
    for r in db.all(f"SELECT id, full_name, license_spdx FROM repos WHERE id IN ({','.join(str(int(i)) for i in ids)})"):
        fam = license_family(r["license_spdx"])
        ok = True
        if a.family:
            ok = a.family.lower() in fam or (a.family.lower() == "none" and fam == "no license detected")
        if wanted:
            ok = ok and (r["license_spdx"] or "").upper() in wanted
        if ok:
            kept.append(r["id"])
            ref = state.evidence.add_metadata(r["id"])
            if ref:
                refs.append(ref)
        lines.append(f"- {r['full_name']}: {r['license_spdx'] or 'none detected'} ({fam}){'' if ok else ' — excluded'}")
    return ToolResult("License check:\n" + "\n".join(lines), kept, refs)


def get_repository_activity(state: ToolState, a: RepoArgs) -> ToolResult:
    repo_id = state.repo(a.repo)
    r = state.ctx.db.one("SELECT * FROM repos WHERE id=?", (repo_id,))
    ref = state.evidence.add_metadata(repo_id)
    status = maintenance_status(r["pushed_at"], bool(r["is_archived"]))
    return ToolResult(
        f"[{ref}] {r['full_name']}: last push {(r['pushed_at'] or 'unknown')[:10]}, created {(r['created_at'] or 'unknown')[:10]}, "
        f"maintenance status '{status}', archived {'yes' if r['is_archived'] else 'no'}, ★{r['stars']}, {r['open_issues']} open issues, "
        f"metadata last synchronised {r['last_seen_at']}. (Activity is inferred from the last push date only.)",
        [repo_id], [ref] if ref else [],
    )


def get_collection(state: ToolState, a: CollectionArgs) -> ToolResult:
    db = state.ctx.db
    row = db.one("SELECT id, name, description, kind FROM collections WHERE name=? COLLATE NOCASE", (a.name,))
    if row is None:
        names = [r["name"] for r in db.all("SELECT name FROM collections ORDER BY name LIMIT 40")]
        raise ToolError(f"No collection named '{a.name}'. Existing collections: {', '.join(names) or 'none'}")
    if row["kind"] == "smart":
        ids = state.ctx.search._smart_resolver(row["id"]) or []
    else:
        ids = [r["repo_id"] for r in db.all("SELECT repo_id FROM collection_repos WHERE collection_id=?", (row["id"],))]
    rows = db.all(f"SELECT * FROM repos WHERE id IN ({','.join(str(int(i)) for i in ids) or 'NULL'}) ORDER BY stars DESC LIMIT 40")
    refs = [ref for r in rows[:15] if (ref := state.evidence.add_metadata(r["id"]))]
    return ToolResult(
        f"Collection '{row['name']}' ({len(ids)} repositories){': ' + row['description'] if row['description'] else ''}\n"
        + "\n".join(f"- {_repo_line(r)}" for r in rows),
        [r["id"] for r in rows], refs,
    )


def save_research_report(state: ToolState, a: SaveReportArgs) -> ToolResult:
    state.saved_title = a.title
    return ToolResult(f"The report will be saved to the local library as '{a.title}'.")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: type[BaseModel]
    run: Callable[[ToolState, Any], ToolResult]


TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in [
        ToolSpec("search_repositories", "Hybrid search over starred repositories (names, descriptions, topics, READMEs). Returns repositories with metadata.", SearchRepositoriesArgs, search_repositories),
        ToolSpec("search_documents", "Search indexed READMEs, docs, manifests and configuration for specific passages. Optional `repo` restricts to one repository.", SearchPassagesArgs, search_documents),
        ToolSpec("search_code", "Search indexed source code (only repositories indexed at the deep level).", SearchPassagesArgs, search_code),
        ToolSpec("get_repository_details", "Metadata, README opening and the list of indexed files for one repository.", RepoArgs, get_repository_details),
        ToolSpec("read_indexed_file", "Read a line range (max 160 lines) of an indexed file. Use paths returned by other tools.", ReadFileArgs, read_indexed_file),
        ToolSpec("get_repository_dependencies", "Dependencies declared in the repository's manifests and which other starred repositories share them.", RepoArgs, get_repository_dependencies),
        ToolSpec("find_similar_repositories", "Starred repositories related to one repository (semantic similarity, shared topics, shared dependencies).", SimilarArgs, find_similar_repositories),
        ToolSpec("compare_repositories", "Side-by-side comparison table (languages, frameworks, maturity, dependencies, licenses) for 2-5 repositories.", CompareArgs, compare_repositories),
        ToolSpec("filter_by_license", "Check or filter repositories by license family or SPDX id.", LicenseFilterArgs, filter_by_license),
        ToolSpec("get_repository_activity", "Last push, maintenance status, archive state and age of one repository.", RepoArgs, get_repository_activity),
        ToolSpec("get_collection", "Repositories in one of the user's collections.", CollectionArgs, get_collection),
        ToolSpec("save_research_report", "Save the final report in the local library under a title.", SaveReportArgs, save_research_report),
    ]
}


def tool_catalog() -> str:
    """Tool descriptions with argument schemas for the planning prompt."""
    lines = []
    for spec in TOOLS.values():
        props = spec.args.model_json_schema().get("properties", {})
        required = set(spec.args.model_json_schema().get("required", []))
        args = ", ".join(f"{k}{'' if k in required else '?'}: {v.get('type') or 'value'}" for k, v in props.items())
        lines.append(f"- {spec.name}({args}) — {spec.description}")
    return "\n".join(lines)


def execute(state: ToolState, name: str, arguments: Any) -> tuple[ToolResult, bool]:
    """Validate and run one tool call. Returns (result, ok). Never raises."""
    spec = TOOLS.get(str(name))
    if spec is None:
        return ToolResult(f"Unknown tool '{name}'. Available tools: {', '.join(TOOLS)}."), False
    if not isinstance(arguments, dict):
        return ToolResult(f"Arguments for {name} must be a JSON object."), False
    try:
        args = spec.args.model_validate(arguments)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:4])
        return ToolResult(f"Invalid arguments for {name}: {problems}"), False
    try:
        return spec.run(state, args), True
    except ToolError as exc:
        return ToolResult(str(exc)), False
    except Exception as exc:      # a tool bug must not end the research run
        return ToolResult(f"{name} failed: {type(exc).__name__}"), False
