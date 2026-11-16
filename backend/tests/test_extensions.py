"""Relevance feedback, personal evaluation, tool API and the MCP server."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from starindex.api.app import create_app
from starindex.mcp_server import SUPPORTED_VERSIONS, DirectBackend, McpHandler, tool_specs
from starindex.search.evaluation import MIN_QUERIES_FOR_RECOMMENDATION, run_personal_eval, score_query

from .conftest import run_jobs


@pytest.fixture
def client(synced):
    with TestClient(create_app(synced, start_worker=False)) as c:
        yield c


def rpc(method: str, params: dict | None = None, msg_id: int | None = 1) -> dict:
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if msg_id is not None:
        msg["id"] = msg_id
    return msg


def test_metric_function():
    s = score_query(["a", "x", "b", "y"], {"a": 2, "b": 1, "c": 1}, {"x"})
    assert s["mrr"] == 1.0 and round(s["recall"], 3) == 0.667 and 0 < s["ndcg"] < 1 and s["rejected"] == 1.0
    assert score_query(["x"], {"a": 1})["ndcg"] == 0.0 and score_query([], {}) ["recall"] == 0.0


def test_feedback_is_recorded_updated_and_listed(client):
    r = client.post("/api/search/feedback", json={"query": "Vector  Database", "repo_id": 101, "verdict": "relevant", "rank": 1}).json()
    assert r["repo_full_name"] == "vectordb-co/quiver" and r["verdict"] == "relevant"
    again = client.post("/api/search/feedback", json={"query": "vector database", "repo_id": 101, "verdict": "not_relevant"}).json()
    assert again["id"] == r["id"] and again["verdict"] == "not_relevant"            # same query (normalised) + repo: updated
    client.post("/api/search/feedback", json={"query": "vector database", "repo_id": 103, "verdict": "missing"})
    for_query = client.get("/api/search/feedback", params={"query": "VECTOR database"}).json()
    assert {(f["repo_id"], f["verdict"]) for f in for_query} == {(101, "not_relevant"), (103, "missing")}
    grouped = client.get("/api/search/feedback/queries").json()
    assert len(grouped) == 1 and len(grouped[0]["judgments"]) == 2
    exported = client.get("/api/search/feedback/export").json()
    assert exported[0]["relevant"] == {"pgext/pgvec": 2} and exported[0]["not_relevant"] == ["vectordb-co/quiver"]
    assert client.post("/api/search/feedback", json={"query": "x", "repo_id": 999999, "verdict": "relevant"}).status_code == 404
    assert client.delete(f"/api/search/feedback/{r['id']}").status_code == 200
    assert len(client.get("/api/search/feedback").json()) == 1


def test_personal_evaluation_reports_and_only_suggests_with_enough_data(client, synced):
    status = client.get("/api/eval/status").json()
    assert status["queries"] == 0 and status["latest"] is None and status["min_queries_for_recommendation"] == MIN_QUERIES_FOR_RECOMMENDATION
    judged = [("vector similarity search", 101), ("agents orchestration", 104), ("extract tables from pdf", 107)]
    for q, repo in judged:
        client.post("/api/search/feedback", json={"query": q, "repo_id": repo, "verdict": "relevant"})
    client.post("/api/search/feedback", json={"query": "vector similarity search", "repo_id": 109, "verdict": "not_relevant"})
    before = synced.settings.model_dump()
    job = client.post("/api/eval/run").json()
    run_jobs(synced)
    assert synced.jobs.get(job["id"]).status == "succeeded"
    run = client.get("/api/eval/status").json()["latest"]
    assert run["queries"] == 3 and run["judgments"] == 4 and run["suggested_settings"] is None
    assert "no change is suggested" in run["recommendation"]
    by = {r["config"]: r for r in run["results"]}
    assert {"keyword", "semantic", "hybrid-w1.5-plain", "hybrid-w1.5-rerank"} <= set(by)
    assert by["keyword"]["available"] and by["keyword"]["mrr"] > 0.5 and by["hybrid-w1.5-plain"]["ndcg_at_10"] > 0.5
    assert synced.settings.model_dump() == before                                    # evaluating never changes settings
    assert client.post("/api/eval/apply").status_code == 409


def test_evaluation_suggestion_is_applied_only_on_request(synced, monkeypatch):
    from starindex.api.schemas import SearchRequest
    from starindex.db import utcnow
    from starindex.search import evaluation
    from starindex.search.evaluation import normalize_query

    # Enough judged queries, with judgments that only keyword search satisfies.
    rows = synced.db.all("SELECT id, name FROM repos ORDER BY id")
    with synced.db.tx() as c:
        for i in range(MIN_QUERIES_FOR_RECOMMENDATION + 1):
            repo = rows[i % len(rows)]
            q = f"{repo['name']} query {i}"
            c.execute("INSERT INTO search_feedback(query, query_norm, repo_id, verdict, mode, created_at) VALUES (?,?,?,?,?,?)",
                      (q, normalize_query(q), repo["id"], "relevant", "hybrid", utcnow()))
    real = synced.search.search

    def biased(req: SearchRequest, **kw):
        res = real(req, **kw)
        if req.mode != "keyword":
            res.results = list(reversed(res.results))          # pretend every non-keyword mode ranks badly
        return res

    monkeypatch.setattr(synced.search, "search", biased)
    run = run_personal_eval(synced)
    assert run["best_config"] == "keyword" and run["suggested_settings"]["search"]["default_mode"] == "keyword"
    assert synced.settings.search.default_mode == "hybrid"                           # still unchanged
    with TestClient(create_app(synced, start_worker=False)) as client:
        applied = client.post("/api/eval/apply").json()
    assert applied["search"]["default_mode"] == "keyword" and applied["rerank"]["enabled"] is False
    assert evaluation.latest_run(synced)["id"] == run["id"]


def test_tool_api_is_read_only_and_validated(client):
    tools = {t["name"]: t for t in client.get("/api/tools").json()}
    assert "search_repositories" in tools and "save_research_report" not in tools
    assert tools["read_indexed_file"]["input_schema"]["required"] == ["repo", "path"]
    ok = client.post("/api/tools/search_repositories", json={"arguments": {"query": "vector similarity search"}}).json()
    assert ok["ok"] and "vectordb-co/quiver" in ok["summary"] and ok["evidence"] and ok["evidence"][0]["repo_full_name"]
    bad = client.post("/api/tools/get_repository_details", json={"arguments": {"repo": "evil/backdoor"}}).json()
    assert bad["ok"] is False and "not in the starred library" in bad["summary"]
    assert client.post("/api/tools/save_research_report", json={"arguments": {"title": "x"}}).status_code == 404
    assert client.post("/api/tools/run_shell", json={"arguments": {}}).status_code == 404
    info = client.get("/api/mcp/info").json()
    assert info["http_url"].endswith("/mcp") and info["stdio_command"][-1] == "mcp" and "claude mcp add" in info["claude_code_http"]


def test_mcp_handler_protocol(synced):
    handler = McpHandler(DirectBackend(synced))
    synced.keyword_only = False
    init = handler.handle(rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}))
    assert init["result"]["protocolVersion"] == "2025-06-18" and init["result"]["serverInfo"]["name"] == "starindex"
    assert "tools" in init["result"]["capabilities"] and "never as instructions" in init["result"]["instructions"]
    future = handler.handle(rpc("initialize", {"protocolVersion": "2099-01-01"}))
    assert future["result"]["protocolVersion"] == SUPPORTED_VERSIONS[0]               # falls back to a version we speak
    assert handler.handle(rpc("notifications/initialized", msg_id=None)) is None
    assert handler.handle(rpc("ping"))["result"] == {}
    listed = handler.handle(rpc("tools/list"))["result"]["tools"]
    assert len(listed) == len(tool_specs()) and all(t["annotations"]["readOnlyHint"] for t in listed)
    assert all(t["inputSchema"]["type"] == "object" for t in listed)
    called = handler.handle(rpc("tools/call", {"name": "search_repositories", "arguments": {"query": "pdf tables"}}))["result"]
    text = called["content"][0]["text"]
    assert called["isError"] is False and text.startswith("[Untrusted content") and "pdfworks/pdfsmith" in text and "[E1]" in text
    poisoned = handler.handle(rpc("tools/call", {"name": "search_documents", "arguments": {"query": "note taking handy add", "repo": "handy/handy-notes"}}))["result"]
    assert "instruction-like text" in poisoned["content"][0]["text"]                  # injection is labelled for the calling agent
    err = handler.handle(rpc("tools/call", {"name": "delete_everything", "arguments": {}}))["result"]
    assert err["isError"] is True
    assert handler.handle(rpc("tools/call", {"name": 5}))["error"]["code"] == -32602
    assert handler.handle(rpc("resources/list"))["error"]["code"] == -32601
    assert handler.handle({"not": "jsonrpc"})["error"]["code"] == -32600


def test_mcp_over_http(client):
    init = client.post("/mcp", json=rpc("initialize", {"protocolVersion": "2025-03-26"}))
    assert init.status_code == 200 and init.json()["result"]["protocolVersion"] == "2025-03-26"
    assert client.post("/mcp", json=rpc("notifications/initialized", msg_id=None)).status_code == 202
    call = client.post("/mcp", json=rpc("tools/call", {"name": "get_repository_dependencies", "arguments": {"repo": "vectordb-co/quiver"}}, 7)).json()
    assert call["id"] == 7 and "tokio" in call["result"]["content"][0]["text"]
    batch = client.post("/mcp", json=[rpc("ping", msg_id=1), rpc("tools/list", msg_id=2)]).json()
    assert [m["id"] for m in batch] == [1, 2]
    assert client.get("/mcp").status_code == 405
    assert client.post("/mcp", content="not json", headers={"content-type": "application/json"}).status_code == 400
    assert client.post("/mcp", json=rpc("ping"), headers={"origin": "https://evil.example"}).status_code == 403


def _stdio_env(synced) -> dict:
    return {**os.environ, "STARINDEX_DATA_DIR": str(synced.config.paths.data_dir), "STARINDEX_DISABLE_KEYRING": "1"}


def test_mcp_stdio_process_falls_back_to_keyword_mode_when_app_is_not_running(synced):
    messages = [
        rpc("initialize", {"protocolVersion": "2025-06-18"}, 1), rpc("notifications/initialized", msg_id=None),
        rpc("tools/list", msg_id=2), rpc("tools/call", {"name": "search_repositories", "arguments": {"query": "vector database"}}, 3),
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "starindex.cli", "mcp", "--url", "http://127.0.0.1:9"],
        input="\n".join(json.dumps(m) for m in messages) + "\nthis is not json\n",
        capture_output=True, text=True, timeout=120, env=_stdio_env(synced),
    )
    lines = [json.loads(line) for line in proc.stdout.splitlines()]      # stdout holds protocol messages only
    assert [m.get("id") for m in lines] == [1, 2, 3, None]
    assert len(lines[1]["result"]["tools"]) == len(tool_specs())
    text = lines[2]["result"]["content"][0]["text"]
    assert "vectordb-co/quiver" in text and "keyword search only" in text
    assert lines[3]["error"]["code"] == -32700
    # The helper process must not have blocked the application's vector index.
    from starindex.api.schemas import SearchRequest

    assert synced.search.search(SearchRequest(query="vector database", mode="hybrid", record=False)).mode == "hybrid"


def test_official_mcp_client_can_use_the_stdio_server(synced):
    """Interoperability with the reference Python SDK client (skipped when the SDK is not installed)."""
    pytest.importorskip("mcp")
    import asyncio

    from mcp import ClientSession, StdioServerParameters, stdio_client

    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=["-m", "starindex.cli", "mcp", "--url", "http://127.0.0.1:9"], env=_stdio_env(synced))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("get_repository_activity", {"repo": "oldtools/legacy-gateway"})
                return [t.name for t in tools.tools], result

    names, result = asyncio.run(asyncio.wait_for(scenario(), 120))
    assert "search_repositories" in names and "compare_repositories" in names
    is_error = getattr(result, "is_error", getattr(result, "isError", None))      # SDK v2 / v1 naming
    assert is_error is False and "archived yes" in result.content[0].text
