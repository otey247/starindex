"""Library, repository detail, search, collections, tags and categories."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...context import AppContext
from ...db import loads, utcnow
from ...indexing.pipeline import enqueue_index
from ...library import annotations as ann
from ...library import insights
from ...library import repos as repo_store
from ...search.service import permalink
from ..deps import ApiError, get_ctx, not_found
from ..schemas import (
    AnnotationPatch,
    BatchIndexRequest,
    BatchIndexResponse,
    CategoriesUpdate,
    Category,
    CategoryCreate,
    CategoryPatch,
    ChunkSpan,
    Collection,
    CollectionCreate,
    CollectionDetail,
    CollectionPatch,
    Dependency,
    DocumentContent,
    DocumentInfo,
    DuplicateGroup,
    Facets,
    IndexRequest,
    Job,
    LibraryOverview,
    Ok,
    RelatedRepo,
    RepoDetail,
    RepoIds,
    RepoListRequest,
    RepoPage,
    SavedSearch,
    SavedSearchCreate,
    SearchHistoryEntry,
    SearchRequest,
    SearchResponse,
    Suggestion,
    Tag,
    TagRename,
    TagsUpdate,
)
from .jobs import job_to_schema

router = APIRouter(tags=["library"])


def _detail(ctx: AppContext, repo_id: int) -> RepoDetail:
    detail = repo_store.get_detail(ctx.db, repo_id)
    if detail is None:
        raise not_found("Repository")
    return detail


# ---------------------------------------------------------------------------------- listing


@router.post("/repos/list", response_model=RepoPage)
def list_repos(req: RepoListRequest, ctx: AppContext = Depends(get_ctx)) -> RepoPage:
    return repo_store.list_repos(ctx.db, req, smart_resolver=ctx.search._smart_resolver)


@router.post("/repos/facets", response_model=Facets)
def facets(req: RepoListRequest, ctx: AppContext = Depends(get_ctx)) -> Facets:
    return repo_store.compute_facets(ctx.db, req.filters, text=req.text, smart_resolver=ctx.search._smart_resolver)


@router.post("/repos/index-batch", response_model=BatchIndexResponse)
def index_batch(req: BatchIndexRequest, ctx: AppContext = Depends(get_ctx)) -> BatchIndexResponse:
    """Upgrade (or change) the indexing level of many repositories at once."""
    ids: list[int] = list(req.repo_ids)
    if req.collection_id is not None:
        resolved = ctx.search._smart_resolver(req.collection_id)
        ids += resolved if resolved is not None else [
            r["repo_id"] for r in ctx.db.all("SELECT repo_id FROM collection_repos WHERE collection_id=?", (req.collection_id,))
        ]
    if req.category_slug:
        ids += [
            r["repo_id"] for r in ctx.db.all(
                "SELECT rc.repo_id FROM repo_categories rc JOIN categories c ON c.id=rc.category_id WHERE c.slug=? AND rc.excluded=0", (req.category_slug,)
            )
        ]
    if req.filters is not None:
        ids += repo_store.compile_filters(req.filters, smart_resolver=ctx.search._smart_resolver).ids(ctx.db)
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise ApiError(400, "empty_selection", "No repositories matched the selection.")
    queued, skipped, jobs = 0, 0, []
    for repo_id in ids:
        row = ctx.db.one("SELECT index_level, indexed_level, star_state FROM repos WHERE id=?", (repo_id,))
        if row is None:
            skipped += 1
            continue
        if row["indexed_level"] == req.level and row["index_level"] == req.level and not req.force:
            skipped += 1
            continue
        jobs.append(enqueue_index(ctx, repo_id, req.level, force=req.force))
        queued += 1
    return BatchIndexResponse(queued=queued, skipped=skipped, job_ids=jobs[:200])


@router.get("/repos/by-name/{owner}/{name}", response_model=RepoDetail)
def repo_by_name(owner: str, name: str, ctx: AppContext = Depends(get_ctx)) -> RepoDetail:
    repo_id = repo_store.find_by_name(ctx.db, f"{owner}/{name}")
    if repo_id is None:
        raise not_found("Repository")
    return _detail(ctx, repo_id)


@router.get("/repos/{repo_id}", response_model=RepoDetail)
def repo_detail(repo_id: int, ctx: AppContext = Depends(get_ctx)) -> RepoDetail:
    return _detail(ctx, repo_id)


# ---------------------------------------------------------------------------------- documents


def _doc_info(row, repo) -> dict:
    return dict(
        id=row["id"], path=row["path"], kind=row["kind"], language=row["language"], size_bytes=row["size_bytes"], level=row["level"],
        commit_sha=row["commit_sha"], indexed_at=row["indexed_at"], truncated=bool(row["truncated"]), chunk_count=row["chunk_count"],
        html_url=permalink(repo["html_url"], row["commit_sha"] or repo["default_branch"], row["path"], None, None),
    )


_DOC_SQL = (
    "SELECT d.id, d.path, d.kind, d.language, d.size_bytes, d.level, d.commit_sha, d.indexed_at, d.truncated, "
    "(SELECT count(*) FROM chunks c WHERE c.document_id=d.id) AS chunk_count{extra} FROM documents d WHERE d.repo_id=?"
)


@router.get("/repos/{repo_id}/documents", response_model=list[DocumentInfo])
def documents(repo_id: int, ctx: AppContext = Depends(get_ctx)) -> list[DocumentInfo]:
    repo = ctx.db.one("SELECT html_url, default_branch FROM repos WHERE id=?", (repo_id,))
    if repo is None:
        raise not_found("Repository")
    rows = ctx.db.all(
        _DOC_SQL.format(extra="") + " ORDER BY CASE d.kind WHEN 'readme' THEN 0 WHEN 'doc' THEN 1 WHEN 'example' THEN 2 WHEN 'api' THEN 3 "
        "WHEN 'manifest' THEN 4 WHEN 'config' THEN 5 ELSE 6 END, d.path",
        (repo_id,),
    )
    return [DocumentInfo(**_doc_info(r, repo)) for r in rows]


def _document(ctx: AppContext, repo_id: int, where: str, params: tuple) -> DocumentContent:
    repo = ctx.db.one("SELECT html_url, default_branch FROM repos WHERE id=?", (repo_id,))
    if repo is None:
        raise not_found("Repository")
    row = ctx.db.one(_DOC_SQL.format(extra=", d.content") + f" AND {where}", (repo_id, *params))
    if row is None:
        raise not_found("Document")
    spans = [
        ChunkSpan(chunk_id=c["id"], start_line=c["start_line"], end_line=c["end_line"], heading=c["heading"])
        for c in ctx.db.all("SELECT id, start_line, end_line, heading FROM chunks WHERE document_id=? ORDER BY ordinal", (row["id"],))
    ]
    return DocumentContent(**_doc_info(row, repo), content=row["content"], chunks=spans)


@router.get("/repos/{repo_id}/readme", response_model=DocumentContent)
def readme(repo_id: int, ctx: AppContext = Depends(get_ctx)) -> DocumentContent:
    return _document(ctx, repo_id, "d.kind='readme' ORDER BY length(d.path) LIMIT 1", ())


@router.get("/repos/{repo_id}/documents/{doc_id}", response_model=DocumentContent)
def document(repo_id: int, doc_id: int, ctx: AppContext = Depends(get_ctx)) -> DocumentContent:
    return _document(ctx, repo_id, "d.id=?", (doc_id,))


@router.get("/repos/{repo_id}/dependencies", response_model=list[Dependency])
def dependencies(repo_id: int, ctx: AppContext = Depends(get_ctx)) -> list[Dependency]:
    return insights.dependencies(ctx, repo_id)


@router.get("/repos/{repo_id}/related", response_model=list[RelatedRepo])
def related(repo_id: int, limit: int = Query(12, ge=1, le=40), ctx: AppContext = Depends(get_ctx)) -> list[RelatedRepo]:
    return insights.related(ctx, repo_id, limit=limit)


# ---------------------------------------------------------------------------------- annotations


@router.patch("/repos/{repo_id}/annotation", response_model=RepoDetail)
def patch_annotation(repo_id: int, patch: AnnotationPatch, ctx: AppContext = Depends(get_ctx)) -> RepoDetail:
    _detail(ctx, repo_id)
    ann.patch_annotation(ctx.db, repo_id, patch)
    return _detail(ctx, repo_id)


@router.put("/repos/{repo_id}/tags", response_model=RepoDetail)
def put_tags(repo_id: int, body: TagsUpdate, ctx: AppContext = Depends(get_ctx)) -> RepoDetail:
    _detail(ctx, repo_id)
    ann.set_tags(ctx.db, repo_id, body.tags)
    return _detail(ctx, repo_id)


@router.put("/repos/{repo_id}/categories", response_model=RepoDetail)
def put_categories(repo_id: int, body: CategoriesUpdate, ctx: AppContext = Depends(get_ctx)) -> RepoDetail:
    _detail(ctx, repo_id)
    ann.set_repo_categories(ctx.db, repo_id, body.category_ids)
    return _detail(ctx, repo_id)


@router.post("/repos/{repo_id}/opened", response_model=Ok)
def opened(repo_id: int, ctx: AppContext = Depends(get_ctx)) -> Ok:
    _detail(ctx, repo_id)
    ann.record_opened(ctx.db, repo_id)
    return Ok()


@router.post("/repos/{repo_id}/index", response_model=Job)
def index_repo(repo_id: int, body: IndexRequest, ctx: AppContext = Depends(get_ctx)) -> Job:
    _detail(ctx, repo_id)
    job_id = enqueue_index(ctx, repo_id, body.level, force=body.force, priority=20)
    return job_to_schema(ctx, ctx.jobs.get(job_id))  # type: ignore[arg-type]


@router.get("/library/overview", response_model=LibraryOverview)
def overview(ctx: AppContext = Depends(get_ctx)) -> LibraryOverview:
    return insights.overview(ctx)


@router.get("/library/duplicates", response_model=list[DuplicateGroup])
def duplicates(ctx: AppContext = Depends(get_ctx)) -> list[DuplicateGroup]:
    return insights.duplicates(ctx)


# ---------------------------------------------------------------------------------- search


@router.post("/search", response_model=SearchResponse)
def search(req: SearchRequest, ctx: AppContext = Depends(get_ctx)) -> SearchResponse:
    return ctx.search.search(req)


@router.get("/search/suggest", response_model=list[Suggestion])
def suggest(q: str = Query("", max_length=200), ctx: AppContext = Depends(get_ctx)) -> list[Suggestion]:
    return ctx.search.suggest(q)


@router.get("/search/history", response_model=list[SearchHistoryEntry])
def history(limit: int = Query(50, ge=1, le=500), ctx: AppContext = Depends(get_ctx)) -> list[SearchHistoryEntry]:
    return [
        SearchHistoryEntry(id=r["id"], query=r["query"], mode=r["mode"], scope=r["scope"], filters=loads(r["filters"], {}),
                           result_count=r["result_count"], created_at=r["created_at"])
        for r in ctx.db.all("SELECT * FROM search_history ORDER BY id DESC LIMIT ?", (limit,))
    ]


@router.delete("/search/history", response_model=Ok)
def clear_history(ctx: AppContext = Depends(get_ctx)) -> Ok:
    with ctx.db.tx() as c:
        c.execute("DELETE FROM search_history")
    return Ok()


def _saved(row) -> SavedSearch:
    return SavedSearch(id=row["id"], name=row["name"], request=SearchRequest.model_validate(loads(row["request"], {})),
                       created_at=row["created_at"], last_run_at=row["last_run_at"])


@router.get("/saved-searches", response_model=list[SavedSearch])
def saved_searches(ctx: AppContext = Depends(get_ctx)) -> list[SavedSearch]:
    return [_saved(r) for r in ctx.db.all("SELECT * FROM saved_searches ORDER BY id DESC")]


@router.post("/saved-searches", response_model=SavedSearch, status_code=201)
def save_search(body: SavedSearchCreate, ctx: AppContext = Depends(get_ctx)) -> SavedSearch:
    from ...db import dumps

    with ctx.db.tx() as c:
        sid = c.execute(
            "INSERT INTO saved_searches(name, request, created_at) VALUES (?,?,?)",
            (body.name.strip(), dumps(body.request.model_dump(exclude_defaults=True)), utcnow()),
        ).lastrowid
    return _saved(ctx.db.one("SELECT * FROM saved_searches WHERE id=?", (sid,)))


@router.delete("/saved-searches/{search_id}", response_model=Ok)
def delete_saved(search_id: int, ctx: AppContext = Depends(get_ctx)) -> Ok:
    with ctx.db.tx() as c:
        c.execute("DELETE FROM saved_searches WHERE id=?", (search_id,))
    return Ok()


# ---------------------------------------------------------------------------------- collections


def _smart(ctx: AppContext):
    return ctx.search._smart_resolver


@router.get("/collections", response_model=list[Collection])
def collections(ctx: AppContext = Depends(get_ctx)) -> list[Collection]:
    return ann.list_collections(ctx.db, _smart(ctx))


@router.post("/collections", response_model=Collection, status_code=201)
def create_collection(body: CollectionCreate, ctx: AppContext = Depends(get_ctx)) -> Collection:
    if ctx.db.one("SELECT 1 FROM collections WHERE name=? COLLATE NOCASE", (body.name.strip(),)):
        raise ApiError(409, "conflict", f"A collection named “{body.name.strip()}” already exists.")
    if body.kind == "smart" and body.smart_query is None:
        raise ApiError(422, "validation", "A smart collection needs saved search conditions.")
    cid = ann.create_collection(ctx.db, body)
    return next(c for c in ann.list_collections(ctx.db, _smart(ctx)) if c.id == cid)


@router.get("/collections/{collection_id}", response_model=CollectionDetail)
def collection(collection_id: int, ctx: AppContext = Depends(get_ctx)) -> CollectionDetail:
    detail = ann.get_collection(ctx.db, collection_id, _smart(ctx))
    if detail is None:
        raise not_found("Collection")
    return detail


def _collection_or_404(ctx: AppContext, collection_id: int) -> Collection:
    found = [c for c in ann.list_collections(ctx.db, _smart(ctx)) if c.id == collection_id]
    if not found:
        raise not_found("Collection")
    return found[0]


@router.patch("/collections/{collection_id}", response_model=Collection)
def patch_collection(collection_id: int, body: CollectionPatch, ctx: AppContext = Depends(get_ctx)) -> Collection:
    _collection_or_404(ctx, collection_id)
    if body.name and ctx.db.one("SELECT 1 FROM collections WHERE name=? COLLATE NOCASE AND id<>?", (body.name.strip(), collection_id)):
        raise ApiError(409, "conflict", f"A collection named “{body.name.strip()}” already exists.")
    ann.patch_collection(ctx.db, collection_id, body)
    return _collection_or_404(ctx, collection_id)


@router.delete("/collections/{collection_id}", response_model=Ok)
def delete_collection(collection_id: int, ctx: AppContext = Depends(get_ctx)) -> Ok:
    ann.delete_collection(ctx.db, collection_id)
    return Ok()


@router.post("/collections/{collection_id}/repos", response_model=Collection)
def add_to_collection(collection_id: int, body: RepoIds, ctx: AppContext = Depends(get_ctx)) -> Collection:
    current = _collection_or_404(ctx, collection_id)
    if current.kind == "smart":
        raise ApiError(400, "smart_collection", "Smart collections are defined by their search conditions; repositories cannot be added by hand.")
    ann.add_repos(ctx.db, collection_id, body.repo_ids)
    return _collection_or_404(ctx, collection_id)


@router.post("/collections/{collection_id}/repos/remove", response_model=Collection)
def remove_from_collection(collection_id: int, body: RepoIds, ctx: AppContext = Depends(get_ctx)) -> Collection:
    _collection_or_404(ctx, collection_id)
    ann.remove_repos(ctx.db, collection_id, body.repo_ids)
    return _collection_or_404(ctx, collection_id)


@router.get("/tags", response_model=list[Tag])
def tags(ctx: AppContext = Depends(get_ctx)) -> list[Tag]:
    return ann.list_tags(ctx.db)


@router.patch("/tags/{name}", response_model=list[Tag])
def rename_tag(name: str, body: TagRename, ctx: AppContext = Depends(get_ctx)) -> list[Tag]:
    if not ann.rename_tag(ctx.db, name, body.name):
        raise not_found("Tag")
    return ann.list_tags(ctx.db)


@router.delete("/tags/{name}", response_model=list[Tag])
def delete_tag(name: str, ctx: AppContext = Depends(get_ctx)) -> list[Tag]:
    if not ann.delete_tag(ctx.db, name):
        raise not_found("Tag")
    return ann.list_tags(ctx.db)


@router.get("/categories", response_model=list[Category])
def categories(ctx: AppContext = Depends(get_ctx)) -> list[Category]:
    return ann.list_categories(ctx.db)


@router.post("/categories", response_model=Category, status_code=201)
def create_category(body: CategoryCreate, ctx: AppContext = Depends(get_ctx)) -> Category:
    cid = ann.create_category(ctx.db, body.name, body.description, body.keywords)
    ctx.jobs.enqueue("classify_repos", dedupe_key="classify_repos")
    return next(c for c in ann.list_categories(ctx.db) if c.id == cid)


@router.patch("/categories/{category_id}", response_model=Category)
def patch_category(category_id: int, body: CategoryPatch, ctx: AppContext = Depends(get_ctx)) -> Category:
    if not ctx.db.one("SELECT 1 FROM categories WHERE id=?", (category_id,)):
        raise not_found("Category")
    ann.update_category(ctx.db, category_id, name=body.name, description=body.description, keywords=body.keywords)
    if body.keywords is not None or body.description is not None:
        ctx.jobs.enqueue("classify_repos", dedupe_key="classify_repos")
    return next(c for c in ann.list_categories(ctx.db) if c.id == category_id)


@router.delete("/categories/{category_id}", response_model=Ok)
def delete_category(category_id: int, ctx: AppContext = Depends(get_ctx)) -> Ok:
    ann.delete_category(ctx.db, category_id)
    return Ok()
