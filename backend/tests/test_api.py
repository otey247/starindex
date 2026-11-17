"""HTTP API through FastAPI's test client: contract, security middleware, streaming."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from starindex.api.app import create_app
from starindex.config import reset_config_cache
from starindex.context import AppContext
from starindex.testing.fake_llm import FakeLLM

from .conftest import run_jobs


@pytest.fixture
def client(ctx):
    app = create_app(ctx, start_worker=False)
    with TestClient(app) as c:
        yield c


def sse_events(response) -> list[tuple[str, dict]]:
    out, event = [], None
    for line in response.text.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            out.append((event, json.loads(line[5:])))
    return out


def sync(ctx):
    ctx.jobs.enqueue("sync_full", dedupe_key="sync")
    run_jobs(ctx)


def test_first_run_status_and_onboarding_flow(client, ctx, fake):
    ctx.settings_store.patch({"github": {"auth_method": "none"}})
    status = client.get("/api/system/status").json()
    assert status["first_run"] is True and status["counts"]["repos"] == 0 and status["llm"]["configured"] is False
    assert client.post("/api/sync/start", json={"kind": "full"}).status_code == 409           # not connected yet
    bad = client.put("/api/github/auth", json={"method": "token", "token": "short"})
    assert bad.status_code == 422 and bad.json()["error"]["kind"] == "validation"
    rejected = client.put("/api/github/auth", json={"method": "token", "token": "wrong-token-0123456789"})
    assert rejected.status_code == 200 and rejected.json()["ok"] is False and rejected.json()["token_stored"]      # stored, but GitHub rejects it
    ctx.secrets.set("github:token", fake.token)                                               # the short fixture token, set directly
    from starindex.github import auth as gh_auth

    gh_auth.invalidate()
    ctx.github.token_resolver.invalidate()
    auth = client.get("/api/github/auth").json()
    assert auth["ok"] is True and auth["login"] == "octo" and fake.token not in json.dumps(auth)      # the token is never returned
    job = client.post("/api/sync/start", json={"kind": "full"}).json()
    assert job["type"] == "sync_full" and job["status"] == "queued"
    run_jobs(ctx)
    st = client.get("/api/sync/status").json()
    assert st["last_full"]["status"] == "succeeded" and st["last_full"]["repos_seen"] == len(fake.repos)
    assert st["index"]["indexed"] == len(fake.repos) and st["index"]["chunks_embedded"] == st["index"]["chunks"] > 0
    assert client.get("/api/system/status").json()["first_run"] is False


def test_library_listing_detail_and_annotations(client, ctx, fake):
    sync(ctx)
    page = client.post("/api/repos/list", json={"sort": "stars", "limit": 5}).json()
    assert page["total"] == len(fake.repos) and page["items"][0]["full_name"] == "crewlabs/crewkit"
    facets = client.post("/api/repos/facets", json={}).json()
    assert {"value": "Python", "label": None, "count": 6} in facets["languages"]
    detail = client.get("/api/repos/by-name/vectordb-co/quiver").json()
    assert detail["id"] == 101 and detail["doc_count"] == 3 and detail["languages"] == {"Rust": 10000}
    assert client.get("/api/repos/999999").status_code == 404

    r = client.patch("/api/repos/101/annotation", json={"note": "evaluate for the search project", "favorite": True, "priority": 2}).json()
    assert r["note"] == "evaluate for the search project" and r["favorite"] and r["has_note"]
    assert client.put("/api/repos/101/tags", json={"tags": ["search", "rust", "search"]}).json()["tags"] == ["rust", "search"]
    fake.repos[101].data["description"] = "Changed upstream description"
    sync(ctx)                                                                         # sync must not touch annotations
    again = client.get("/api/repos/101").json()
    assert again["description"] == "Changed upstream description" and again["note"] == "evaluate for the search project" and again["tags"] == ["rust", "search"]

    readme = client.get("/api/repos/101/readme").json()
    assert readme["path"] == "README.md" and readme["content"].startswith("# Quiver") and readme["chunks"] and "/blob/" in readme["html_url"]
    docs = client.get("/api/repos/101/documents").json()
    assert [d["kind"] for d in docs] == ["readme", "doc", "manifest"]
    assert {d["name"] for d in client.get("/api/repos/101/dependencies").json()} == {"tokio", "serde", "tonic"}
    assert client.get("/api/repos/101/related").json()[0]["repo"]["full_name"] in {"someone/quiver", "embedlabs/chromatic", "pgext/pgvec"}
    assert client.post("/api/repos/101/opened").status_code == 200
    overview = client.get("/api/library/overview").json()
    assert overview["recently_rediscovered"][0]["id"] == 101 and overview["favorites"][0]["id"] == 101
    dup = client.get("/api/library/duplicates").json()
    assert any(g["reason"] == "same_name" and {r["full_name"] for r in g["repos"]} == {"vectordb-co/quiver", "someone/quiver"} for g in dup)
    renamed = client.patch("/api/tags/search", json={"name": "retrieval"}).json()
    assert {t["name"] for t in renamed} == {"rust", "retrieval"}
    assert {t["name"] for t in client.delete("/api/tags/rust").json()} == {"retrieval"}


def test_search_endpoints_and_evidence_viewer(client, ctx):
    sync(ctx)
    res = client.post("/api/search", json={"query": "payload filtering", "mode": "hybrid", "explain": True}).json()
    top = res["results"][0]
    assert top["repo"]["full_name"] == "vectordb-co/quiver" and res["mode"] == "hybrid" and res["facets"]["languages"]
    match = top["matches"][0]
    ev = client.get(f"/api/evidence/chunk/{match['chunk_id']}?context_lines=3").json()
    assert ev["path"] == match["path"] and ev["commit_sha"] and ev["repo"]["id"] == 101 and "Payload filtering" in ev["text"]
    assert client.get("/api/evidence/chunk/987654").status_code == 404
    assert client.get("/api/search/suggest?q=qui").json()[0]["text"] == "vectordb-co/quiver"
    assert client.get("/api/search/history").json()[0]["query"] == "payload filtering"
    saved = client.post("/api/saved-searches", json={"name": "Vector DBs", "request": {"query": "vector database lang:rust", "mode": "hybrid"}}).json()
    assert client.get("/api/saved-searches").json()[0]["request"]["query"] == "vector database lang:rust"
    assert client.delete(f"/api/saved-searches/{saved['id']}").status_code == 200
    bad = client.post("/api/search", json={"query": "x", "mode": "telepathy"})
    assert bad.status_code == 422 and bad.json()["error"]["kind"] == "validation"
    odd = client.post("/api/search", json={"query": 'weird "unbalanced AND ( OR * NEAR', "mode": "keyword"})
    assert odd.status_code == 200                                                      # odd input never breaks FTS


def test_collections_categories_and_batch_indexing(client, ctx):
    sync(ctx)
    col = client.post("/api/collections", json={"name": "Search stack", "repo_ids": [101, 102]}).json()
    assert col["repo_count"] == 2
    assert client.post("/api/collections", json={"name": "search stack"}).status_code == 409
    client.post(f"/api/collections/{col['id']}/repos", json={"repo_ids": [103]})
    client.post(f"/api/collections/{col['id']}/repos/remove", json={"repo_ids": [102]})
    assert {r["id"] for r in client.get(f"/api/collections/{col['id']}").json()["repos"]} == {101, 103}
    smart = client.post("/api/collections", json={"name": "Agents", "kind": "smart", "smart_query": {"query": "topic:agents"}}).json()
    assert smart["kind"] == "smart" and smart["repo_count"] == 2
    assert client.post(f"/api/collections/{smart['id']}/repos", json={"repo_ids": [101]}).status_code == 400
    cats = client.get("/api/categories").json()
    assert any(c["slug"] == "rag-search" and c["repo_count"] >= 2 for c in cats)
    new_cat = client.post("/api/categories", json={"name": "House builder", "keywords": ["3d", "threejs"]}).json()
    assert new_cat["slug"] == "house-builder" and not new_cat["is_builtin"]
    batch = client.post("/api/repos/index-batch", json={"level": "deep", "collection_id": col["id"]}).json()
    assert batch["queued"] == 2
    run_jobs(ctx)
    assert ctx.db.scalar("SELECT count(*) FROM repos WHERE indexed_level='deep'") == 2
    assert ctx.db.scalar("SELECT count(*) FROM documents WHERE repo_id=101 AND kind='code'") >= 1
    code = client.post("/api/search", json={"query": "search_collection", "mode": "keyword", "scope": "code"}).json()
    assert code["results"][0]["matches"][0]["path"] == "src/main.rs"


def test_ask_research_compare_and_reports_over_http(client, ctx):
    sync(ctx)
    no_model = sse_events(client.post("/api/ask", json={"question": "vector search"}))
    assert [e for e, _ in no_model] == ["evidence", "error"] and no_model[1][1]["error"]["kind"] == "not_configured"

    fake_llm = FakeLLM()
    cfg = ctx.providers.create("openai_compat", "Local test model", "http://127.0.0.1:9/v1", {}, True, None)
    ctx.settings_store.patch({"routing": {"default": {"provider_id": cfg.id, "model": "fake-good"}}})
    from starindex.providers.router import ModelRouter

    ctx.__dict__["router"] = ModelRouter(ctx.db, ctx.settings_store, ctx.providers, ctx.chatgpt, transport=fake_llm.transport())
    opts = client.get("/api/model-options").json()
    assert opts["active"]["model"] == "fake-good" and opts["cloud_allowed"] is False

    ask = sse_events(client.post("/api/ask", json={"question": "Which repositories do vector similarity search?"}))
    kinds = [e for e, _ in ask]
    assert kinds[0] == "evidence" and kinds[1] == "status" and "delta" in kinds and kinds[-1] == "done"
    research = sse_events(client.post("/api/research", json={"question": "Design a local search stack", "capability": "composition"}))
    report = research[-1][1]["report"]
    assert research[-1][0] == "done" and report["kind"] == "research" and report["trace"]
    rid = report["id"]
    patched = client.patch(f"/api/reports/{rid}", json={"saved": True, "title": "Local search stack"}).json()
    assert patched["saved"] and patched["title"] == "Local search stack"
    assert [r["id"] for r in client.get("/api/reports?saved=true").json()] == [rid]
    md = client.get(f"/api/reports/{rid}/export")
    assert md.status_code == 200 and md.text.startswith("# Local search stack") and "## Sources" in md.text and "## Research trace" in md.text

    cmp = client.post("/api/compare", json={"repo_ids": [101, 102]}).json()
    assert [r["full_name"] for r in cmp["repos"]] == ["vectordb-co/quiver", "embedlabs/chromatic"] and cmp["license_notes"]
    assert client.post("/api/compare", json={"repo_ids": [101]}).status_code == 422
    saved = client.post("/api/compare/save", json={"repo_ids": [101, 102]}).json()
    assert saved["kind"] == "compare" and saved["saved"] and "| License |" in saved["answer_md"] and saved["data"]["comparison"]["rows"]
    narrative = sse_events(client.post("/api/compare/narrative", json={"repo_ids": [101, 102], "focus": "for a local RAG stack"}))
    assert narrative[-1][0] == "done" and narrative[-1][1]["report"]["kind"] == "compare"
    summary = sse_events(client.post("/api/repos/101/summary?kind=usage_ideas", json={}))
    assert summary[-1][0] == "done" and summary[-1][1]["summary"]["kind"] == "usage_ideas"
    assert client.get("/api/repos/101/summary?kind=usage_ideas").json()["content"]
    usage = client.get("/api/usage").json()
    assert usage["rows"] and usage["total_cost_usd"] is None


def test_settings_validation_and_secret_hygiene(client, ctx):
    s = client.get("/api/settings").json()
    assert s["privacy"]["allow_cloud_models"] is False and s["indexing"]["default_level"] == "docs"
    ok = client.patch("/api/settings", json={"patch": {"search": {"default_mode": "keyword"}, "sync": {"unstarred_retention": "keep_annotated"}}}).json()
    assert ok["search"]["default_mode"] == "keyword" and ok["sync"]["unstarred_retention"] == "keep_annotated"
    bad = client.patch("/api/settings", json={"patch": {"research": {"max_tool_calls": 100000}}})
    assert bad.status_code == 422 and client.get("/api/settings").json()["research"]["max_tool_calls"] == 12

    created = client.post("/api/providers", json={"kind": "openai", "name": "OpenAI", "api_key": "sk-secret-value-1234567890abcdef"}).json()
    dump = json.dumps(client.get("/api/providers").json()) + json.dumps(client.get("/api/settings").json()) + json.dumps(client.get("/api/system/status").json())
    assert created["has_secret"] is True and "sk-secret-value" not in dump                     # never echoed
    blocked = client.post(f"/api/providers/{created['id']}/test", json={}).json()
    assert blocked["ok"] is False and blocked["error"]["kind"] == "cloud_disabled"             # nothing sent while cloud is off
    diag = client.get("/api/system/diagnostics").json()
    assert {c["name"] for c in diag["checks"]} >= {"SQLite", "Full-text search (FTS5)", "Vector index", "GitHub", "Embedding model"}
    hw = client.get("/api/system/hardware").json()
    assert hw["cpu_count"] >= 1 and isinstance(hw["recommendations"], list)
    assert client.get("/api/nope").status_code == 404 and client.get("/api/nope").json()["error"]["kind"] == "not_found"


def test_jobs_schedules_storage_and_export(client, ctx):
    sync(ctx)
    jobs = client.get("/api/jobs?type=index_repo&limit=3").json()
    assert jobs["total"] == 14 and jobs["items"][0]["repo_full_name"]
    assert client.get(f"/api/jobs/{jobs['items'][0]['id']}").json() == jobs["items"][0]
    assert client.get("/api/jobs/999999999").status_code == 404
    schedules = client.get("/api/schedules").json()
    assert {s["name"] for s in schedules} == {"incremental_sync", "full_sync", "backup", "cleanup"}
    patched = client.patch("/api/schedules/incremental_sync", json={"interval_minutes": 60, "enabled": False}).json()
    assert patched["interval_minutes"] == 60 and patched["enabled"] is False
    job = client.post("/api/storage/backups").json()
    run_jobs(ctx)
    assert client.get("/api/jobs").json()["items"] and ctx.jobs.get(job["id"]).status == "succeeded"
    backups = client.get("/api/storage/backups").json()
    assert len(backups) == 1 and client.post("/api/storage/restore", json={"name": backups[0]["name"]}).status_code == 400    # needs confirm
    assert client.post("/api/storage/restore", json={"name": backups[0]["name"], "confirm": True}).status_code == 200
    storage = client.get("/api/storage").json()
    assert storage["database_bytes"] > 0 and storage["embeddings_rows"] > 0
    export = client.get("/api/export?format=csv")
    assert export.headers["content-disposition"].startswith("attachment") and "vectordb-co/quiver" in export.text
    failed = client.post("/api/jobs", json={"type": "cleanup"}).json()
    assert client.post(f"/api/jobs/{failed['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{failed['id']}/retry").json()["status"] == "queued"


def test_security_middleware(client, ctx, tmp_path, monkeypatch):
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 403            # DNS rebinding
    assert client.post("/api/search", json={"query": "x"}, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/search", json={"query": "x"}, headers={"origin": "http://127.0.0.1:5173"}).status_code == 200   # dev server
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    page = client.get("/some/page")
    assert page.status_code in (200, 503)
    if page.status_code == 200:
        assert "default-src 'self'" in page.headers["content-security-policy"]

    monkeypatch.setenv("STARINDEX_HOST", "0.0.0.0")
    reset_config_cache()
    with pytest.raises(RuntimeError):                                                                 # LAN mode requires a token
        create_app()
    monkeypatch.setenv("STARINDEX_ACCESS_TOKEN", "s3cret-token")
    monkeypatch.setenv("STARINDEX_DATA_DIR", str(tmp_path / "lan"))
    reset_config_cache()
    lan_ctx = AppContext()
    with TestClient(create_app(lan_ctx, start_worker=False)) as lan:
        assert lan.get("/api/health").status_code == 200
        assert lan.get("/api/system/status").status_code == 401
        assert lan.get("/api/system/status", headers={"authorization": "Bearer wrong"}).status_code == 401
        assert lan.get("/api/system/status", headers={"authorization": "Bearer s3cret-token"}).status_code == 200
    lan_ctx.close()


def test_logs_never_contain_secrets():
    from starindex.logsetup import mask

    line = "GET https://auth.example/authorize?id_token_hint=eyJhbGciOi.abc.def&state=1 token ghp_abcdefghijklmnopqrstuvwxyz0123 Bearer abcdefghijklmnopqrstuvwxyz sk-abcdefghijklmnopqrstuv"
    masked = mask(line)
    assert "eyJhbGciOi" not in masked and "ghp_" not in masked and "sk-abc" not in masked and masked.count("<redacted>") == 4
