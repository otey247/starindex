"""End-to-end verification of a running StarIndex installation (`starindex verify`).

Walks the checklist that could not be executed in the build environment: installation and
launch, the GitHub connection and the real starred list, search in every mode, each configured
model provider, a grounded answer (and optionally a research run) with the default model,
ChatGPT plan status, GPU detection and the MCP endpoints. It only reads, except for the
optional --sync (starts the normal synchronisation job) and the reports that Ask/Research
store. The result is printed and written as a Markdown report.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from . import __version__

PASS, FAIL, SKIP, MANUAL = "pass", "FAIL", "skipped", "manual step"


@dataclass
class Check:
    area: str
    name: str
    status: str
    detail: str


class Verifier:
    def __init__(self, base_url: str, token: str | None = None, *, timeout: float = 60.0):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base, headers=headers, timeout=timeout)
        self.checks: list[Check] = []

    def add(self, area: str, name: str, status: str, detail: str = "") -> None:
        self.checks.append(Check(area, name, status, detail))
        print(f"[{status:^11}] {area}: {name}" + (f" — {detail}" if detail else ""), flush=True)

    def get(self, path: str, **kw: Any) -> Any:
        return self.http.get(path, **kw).raise_for_status().json()

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.http.post(path, json=body if body is not None else {}, **kw).raise_for_status().json()

    def stream(self, path: str, body: dict[str, Any], timeout: float) -> list[tuple[str, dict[str, Any]]]:
        events: list[tuple[str, dict[str, Any]]] = []
        name = ""
        with self.http.stream("POST", path, json=body, timeout=timeout) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    events.append((name, json.loads(line[5:])))
        return events

    # -- checks -----------------------------------------------------------------------------

    def installation(self) -> bool:
        self.add("Install", "Platform", PASS, f"{platform.system()} {platform.release()}, Python {platform.python_version()}, StarIndex {__version__}")
        try:
            health = self.get("/api/health")
        except Exception as exc:
            self.add("Install", "Application is running", FAIL, f"{self.base} did not answer ({type(exc).__name__}). Start it with scripts\\start.ps1.")
            return False
        self.add("Install", "Application is running", PASS, f"{self.base}, version {health.get('version')}")
        page = self.http.get("/")
        ok = page.status_code == 200 and "<title>StarIndex</title>" in page.text
        self.add("Install", "Web interface is served", PASS if ok else FAIL, f"HTTP {page.status_code}" + ("" if ok else " (run npm run build in frontend/)"))
        status = self.get("/api/system/status")
        self.add("Install", "Background worker", PASS if status["worker"]["alive"] else FAIL, f"mode {status['worker']['mode']}")
        secret = status["secret_backend"]
        if secret.startswith("keyring"):
            self.add("Install", "Secret storage", PASS, secret)
        elif platform.system() == "Windows":
            self.add("Install", "Secret storage", FAIL, f"{secret} (expected Windows Credential Manager)")
        else:
            self.add("Install", "Secret storage", SKIP, f"{secret}: no OS keyring here; secrets are in a user-only file")
        for check in self.get("/api/system/diagnostics")["checks"]:
            if check["status"] == "fail":
                self.add("Install", f"Diagnostics: {check['name']}", FAIL, check["detail"])
        hw = self.get("/api/system/hardware")
        gpus = ", ".join(f"{g['name']} ({g.get('memory_total_mb') or '?'} MB)" for g in hw["gpus"]) or "none detected"
        self.add("Hardware", "GPU detection", PASS if hw["gpus"] else SKIP, gpus)
        cuda = "CUDAExecutionProvider" in hw.get("onnx_providers", [])
        self.add("Hardware", "GPU embeddings (ONNX Runtime CUDA)", PASS if cuda else SKIP,
                 "available" if cuda else "CPU build installed; run scripts\\install.ps1 -Gpu to enable")
        return True

    def github(self, do_sync: bool, wait_minutes: float) -> None:
        auth = self.get("/api/github/auth")
        if auth["method"] == "none":
            self.add("GitHub", "Connection", MANUAL, "Not connected. Open the app and connect (GitHub CLI, token or public username), then run verify again.")
            return
        self.add("GitHub", "Connection", PASS if auth["ok"] else FAIL, f"{auth['method']}: {auth['detail']}")
        if not auth["ok"]:
            return
        if do_sync:
            self.post("/api/sync/start", {"kind": "full"})
            deadline = time.time() + wait_minutes * 60
            while time.time() < deadline:
                st = self.get("/api/sync/status")
                if st["current_run"] is None and st["last_full"] and not st["queue"]["by_type"].get("sync_full", {}).get("queued"):
                    if not st["queue"]["by_type"].get("sync_full", {}).get("running"):
                        break
                time.sleep(3)
        st = self.get("/api/sync/status")
        last = st["last_full"]
        if not last:
            self.add("GitHub", "Full synchronisation", MANUAL, "No full sync has run yet. Start it in the app, or run verify with --sync.")
            return
        complete = last["status"] == "succeeded" and (last["total_expected"] is None or last["repos_seen"] == last["total_expected"])
        self.add("GitHub", "Full synchronisation", PASS if complete else FAIL,
                 f"{last['status']}: {last['repos_seen']} of {last['total_expected']} starred repositories in {last['pages_fetched']} pages"
                 + (f"; error: {last['error']}" if last.get("error") else ""))
        idx = st["index"]
        self.add("GitHub", "Library size", PASS if idx["total"] > 0 else FAIL, f"{idx['total']} starred repositories stored")
        pending = idx["pending"] + idx["queued"] + idx["indexing"]
        self.add("Indexing", "Documentation indexing", PASS if idx["failed"] == 0 and pending == 0 else (FAIL if idx["failed"] else SKIP),
                 f"{idx['indexed']} indexed, {pending} in progress, {idx['failed']} failed, {idx['skipped']} skipped; {idx['documents']} documents, {idx['chunks']} passages")
        self.add("Indexing", "Embeddings", PASS if idx["chunks"] and idx["chunks_embedded"] == idx["chunks"] else SKIP,
                 f"{idx['chunks_embedded']} of {idx['chunks']} passages, {idx['repos_embedded']} of {idx['total']} repositories embedded")
        if st["queue"]["failed"]:
            self.add("Indexing", "Failed jobs", FAIL, f"{st['queue']['failed']} failed job(s); see the Sync page")

    def search(self, query: str) -> None:
        status = self.get("/api/system/status")
        if status["counts"]["starred"] == 0:
            self.add("Search", "Search", SKIP, "The library is empty.")
            return
        for mode in ("keyword", "semantic", "hybrid"):
            t0 = time.time()
            res = self.post("/api/search", {"query": query, "mode": mode, "limit": 5, "record": False})
            ms = int((time.time() - t0) * 1000)
            top = ", ".join(r["repo"]["full_name"] for r in res["results"][:3]) or "no results"
            ran = res["mode"] == mode
            self.add("Search", f"{mode.capitalize()} search", PASS if ran and res["results"] else (SKIP if not ran else FAIL),
                     f"{ms} ms; {top}" + ("" if ran else f"; ran as {res['mode']}: {'; '.join(res['notices'])}"))

    def models(self, question: str, do_research: bool) -> None:
        providers = self.get("/api/providers")
        usable = [p for p in providers if p["enabled"] and p["kind"] != "chatgpt_plan"]
        if not usable:
            self.add("Models", "Providers", MANUAL, "No model provider configured. Add Ollama or LM Studio in Settings → Models.")
        for p in usable:
            if p.get("blocked_by_privacy"):
                self.add("Models", f"Provider {p['name']}", SKIP, "cloud provider; blocked by the privacy setting")
                continue
            res = self.post(f"/api/providers/{p['id']}/test", {})
            detail = res["detail"] or (res.get("error") or {}).get("message", "")
            self.add("Models", f"Provider {p['name']} ({p['kind']})", PASS if res["ok"] else FAIL, detail)
        chat = self.get("/api/chatgpt/status")
        accounts = [a for a in chat["accounts"] if a["signed_in"]]
        if not accounts:
            self.add("Models", "ChatGPT plan", MANUAL, "Not signed in. Settings → Models → Continue with ChatGPT (see docs/CHATGPT_PLAN.md).")
        else:
            enabled = [a for a in accounts if a["plan_usage_enabled"]]
            self.add("Models", "ChatGPT plan", PASS if enabled else FAIL,
                     f"signed in as {accounts[0].get('email') or accounts[0]['label']}; plan usage {'granted' if enabled else 'NOT granted'}")
        active = self.get("/api/model-options")["active"]
        if not active["configured"]:
            self.add("Ask", "Grounded answer", MANUAL, "No default model is set (Settings → Models → Routing).")
            return
        if active["is_cloud"]:
            self.add("Ask", "Grounded answer", SKIP, f"default model is a cloud provider ({active['provider_name']}); verify it from the app, where you confirm what is sent")
            return
        label = f"{active['provider_name']} / {active['model']}"
        self._generation("Ask", "/api/ask", {"question": question}, label, 900)
        if do_research:
            self._generation("Research", "/api/research", {"question": question, "capability": "auto"}, label, 3600)

    def _generation(self, area: str, path: str, body: dict[str, Any], label: str, timeout: float) -> None:
        t0 = time.time()
        try:
            events = self.stream(path, body, timeout)
        except Exception as exc:
            self.add(area, f"{area} with {label}", FAIL, f"{type(exc).__name__}: {exc}")
            return
        seconds = int(time.time() - t0)
        errors = [d for name, d in events if name == "error"]
        done = [d for name, d in events if name == "done"]
        if errors or not done:
            message = errors[0]["error"]["message"] if errors else "the stream ended without a result"
            self.add(area, f"{area} with {label}", FAIL, message)
            return
        report = done[0]["report"]
        cited = set(re.findall(r"\[E\d+\]", report["answer_md"]))
        known = {f"[{e['ref']}]" for e in report["evidence"]}
        ok = bool(cited) and cited <= known and not report["unsupported"]
        detail = f"{seconds}s, {len(cited)} distinct citations, {len(report['evidence'])} evidence items, {len(report['unsupported'])} unsupported"
        if area == "Research":
            detail += f", {report['data']['limits']['tool_calls_used']} tool calls"
        if report["warnings"]:
            detail += "; warnings: " + " | ".join(report["warnings"])
        self.add(area, f"{area} with {label}", PASS if ok else FAIL, detail)

    def mcp(self) -> None:
        def rpc(method: str, params: dict[str, Any] | None = None, msg_id: int = 1) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}

        try:
            init = self.http.post("/mcp", json=rpc("initialize", {"protocolVersion": "2025-06-18"})).raise_for_status().json()
            tools = self.http.post("/mcp", json=rpc("tools/list", msg_id=2)).raise_for_status().json()["result"]["tools"]
            self.add("MCP", "HTTP endpoint", PASS, f"{self.base}/mcp, protocol {init['result']['protocolVersion']}, {len(tools)} tools")
        except Exception as exc:
            self.add("MCP", "HTTP endpoint", FAIL, f"{type(exc).__name__}: {exc}")
        try:
            messages = [rpc("initialize", {"protocolVersion": "2025-06-18"}), rpc("tools/call", {"name": "search_repositories", "arguments": {"query": "search", "limit": 3}}, 2)]
            proc = subprocess.run(
                [sys.executable, "-m", "starindex.cli", "mcp", "--url", self.base],
                input="\n".join(json.dumps(m) for m in messages) + "\n", capture_output=True, text=True, timeout=120,
            )
            lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
            ok = len(lines) == 2 and lines[1]["result"]["isError"] is False
            self.add("MCP", "stdio server (starindex mcp)", PASS if ok else FAIL, "initialize + tools/call answered" if ok else (proc.stderr or proc.stdout)[:300])
        except Exception as exc:
            self.add("MCP", "stdio server (starindex mcp)", FAIL, f"{type(exc).__name__}: {exc}")

    # -- report -----------------------------------------------------------------------------

    def report(self) -> str:
        counts = {s: sum(1 for c in self.checks if c.status == s) for s in (PASS, FAIL, SKIP, MANUAL)}
        lines = [
            "# StarIndex verification report", "",
            f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')} on {platform.system()} {platform.release()} "
            f"(Python {platform.python_version()}, StarIndex {__version__}) against {self.base}.", "",
            f"**{counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIP]} skipped, {counts[MANUAL]} need a manual step.**", "",
            "| Area | Check | Result | Detail |", "|---|---|---|---|",
        ]
        for c in self.checks:
            lines.append(f"| {c.area} | {c.name} | {c.status} | {c.detail.replace('|', '/').replace(chr(10), ' ')} |")
        return "\n".join(lines) + "\n"


def run(base_url: str, token: str | None, *, sync: bool, research: bool, query: str, question: str, report_path: Path, wait_minutes: float) -> int:
    v = Verifier(base_url, token)
    if v.installation():
        v.github(sync, wait_minutes)
        v.search(query)
        v.models(question, research)
        v.mcp()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(v.report(), encoding="utf-8")
    print(f"\nReport written to {report_path}")
    return 1 if any(c.status == FAIL for c in v.checks) else 0
