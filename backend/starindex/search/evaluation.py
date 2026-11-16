"""Retrieval metrics and the personal evaluation run.

The same metric code scores the bundled evaluation set (eval/run_eval.py) and the user's own
relevance feedback. Feedback verdicts become graded judgments: "missing" (should have matched
but did not) = 2, "relevant" (thumbs up) = 1; "not_relevant" (thumbs down) is tracked
separately as rejected results in the top ten. Evaluating never changes settings: a run
reports numbers and, with enough judged queries, a suggestion the user applies explicitly.
"""

from __future__ import annotations

import math
import re
import statistics
import time
from typing import TYPE_CHECKING, Any

from ..db import dumps, loads, utcnow

if TYPE_CHECKING:
    from ..context import AppContext

K = 10
MIN_QUERIES_FOR_RECOMMENDATION = 15
MIN_GAIN = 0.02
GRADES = {"missing": 2, "relevant": 1}


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())


def dcg(gains: list[int]) -> float:
    return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gains))


def score_query(ranked: list[Any], relevant: dict[Any, int], rejected: set[Any] | None = None) -> dict[str, float]:
    """ranked: result keys in order; relevant: key -> grade (>0). Unjudged results count as not relevant."""
    top = ranked[:K]
    gains = [relevant.get(key, 0) for key in top]
    ideal = sorted(relevant.values(), reverse=True)[:K]
    found = sum(1 for key in top if relevant.get(key, 0) > 0)
    rr = 0.0
    for i, key in enumerate(ranked[:50]):
        if relevant.get(key, 0) > 0:
            rr = 1.0 / (i + 1)
            break
    return {
        "recall": found / len(relevant) if relevant else 0.0,
        "recall_capped": found / min(K, len(relevant)) if relevant else 0.0,
        "ndcg": dcg(gains) / dcg(ideal) if ideal else 0.0,
        "mrr": rr,
        "rejected": float(sum(1 for key in top if rejected and key in rejected)),
    }


def personal_judgments(ctx: AppContext) -> dict[str, dict[str, Any]]:
    """query_norm -> {query, relevant: {repo_id: grade}, rejected: {repo_id}} from stored feedback."""
    out: dict[str, dict[str, Any]] = {}
    for row in ctx.db.all(
        "SELECT f.query, f.query_norm, f.repo_id, f.verdict FROM search_feedback f JOIN repos r ON r.id=f.repo_id ORDER BY f.id"
    ):
        entry = out.setdefault(row["query_norm"], {"query": row["query"], "relevant": {}, "rejected": set()})
        if row["verdict"] in GRADES:
            entry["relevant"][row["repo_id"]] = GRADES[row["verdict"]]
        else:
            entry["rejected"].add(row["repo_id"])
    return out


def _configs(current: dict[str, Any]) -> list[dict[str, Any]]:
    grid: list[dict[str, Any]] = [
        {"key": "keyword", "label": "Keyword", "mode": "keyword", "rerank": False, "semantic_weight": current["semantic_weight"]},
        {"key": "semantic", "label": "Semantic", "mode": "semantic", "rerank": False, "semantic_weight": current["semantic_weight"]},
    ]
    for weight in (1.0, 1.5, 2.0):
        for rerank in (False, True):
            grid.append({
                "key": f"hybrid-w{weight}-{'rerank' if rerank else 'plain'}",
                "label": f"Hybrid, semantic weight {weight}" + (", reranked" if rerank else ""),
                "mode": "hybrid", "rerank": rerank, "semantic_weight": weight,
            })
    return grid


def run_personal_eval(ctx: AppContext, job=None) -> dict[str, Any]:
    """Score every judged query under a small grid of configurations. Stores and returns the run."""
    from ..api.schemas import SearchRequest

    judgments = {k: v for k, v in personal_judgments(ctx).items() if v["relevant"]}
    settings = ctx.settings
    current = {
        "mode": settings.search.default_mode, "rerank": settings.rerank.enabled,
        "semantic_weight": settings.search.semantic_weight,
    }
    current_key = (
        current["mode"] if current["mode"] != "hybrid"
        else f"hybrid-w{current['semantic_weight']}-{'rerank' if current['rerank'] else 'plain'}"
    )
    configs = _configs(current)
    if current_key not in {c["key"] for c in configs}:          # a custom weight: evaluate it too
        configs.append({"key": current_key, "label": f"Current (semantic weight {current['semantic_weight']})",
                        "mode": "hybrid", "rerank": current["rerank"], "semantic_weight": current["semantic_weight"]})
    results: list[dict[str, Any]] = []
    for ci, cfg in enumerate(configs):
        override = settings.model_copy(deep=True)
        override.search.semantic_weight = cfg["semantic_weight"]
        rows, latencies, executed = [], [], cfg["mode"]
        for entry in judgments.values():
            t0 = time.perf_counter()
            res = ctx.search.search(
                SearchRequest(query=entry["query"], mode=cfg["mode"], limit=50, rerank=cfg["rerank"], record=False),
                _nested=True, _settings=override,
            )
            latencies.append((time.perf_counter() - t0) * 1000)
            executed = res.mode
            rows.append(score_query([r.repo.id for r in res.results], entry["relevant"], entry["rejected"]))
        if job is not None:
            job.progress(ci + 1, len(configs), f"Evaluating {cfg['label']}")

        def mean(key: str, rows: list[dict[str, float]] = rows) -> float:
            return round(statistics.fmean(r[key] for r in rows), 4) if rows else 0.0

        results.append({
            "config": cfg["key"], "label": cfg["label"], "mode": cfg["mode"], "rerank": cfg["rerank"],
            "semantic_weight": cfg["semantic_weight"], "available": executed == cfg["mode"],
            "recall_at_10": mean("recall_capped"), "ndcg_at_10": mean("ndcg"), "mrr": mean("mrr"),
            "rejected_in_top_10": mean("rejected"),
            "latency_ms_median": round(statistics.median(latencies), 1) if latencies else 0.0,
        })

    usable = [r for r in results if r["available"]]
    now = next((r for r in results if r["config"] == current_key), None)
    best = max(usable, key=lambda r: (r["ndcg_at_10"], r["mrr"], -r["latency_ms_median"])) if usable else None
    recommendation, suggested = "", None
    n = len(judgments)
    if n == 0:
        recommendation = "No judged queries yet. Mark results as relevant or not relevant while you search."
    elif n < MIN_QUERIES_FOR_RECOMMENDATION:
        recommendation = (
            f"{n} judged queries so far. Numbers are shown, but no change is suggested until there are at least "
            f"{MIN_QUERIES_FOR_RECOMMENDATION}: with fewer, differences are mostly noise."
        )
    elif best and now and best["config"] != now["config"] and best["ndcg_at_10"] - now["ndcg_at_10"] >= MIN_GAIN:
        recommendation = (
            f"“{best['label']}” scores nDCG@10 {best['ndcg_at_10']:.3f} on your judgments against {now['ndcg_at_10']:.3f} "
            f"for the current settings (median {best['latency_ms_median']:.0f} ms against {now['latency_ms_median']:.0f} ms)."
        )
        suggested = {"search": {"default_mode": best["mode"], "semantic_weight": best["semantic_weight"]}, "rerank": {"enabled": best["rerank"]}}
    else:
        recommendation = "The current settings are within noise of the best configuration on your judgments. No change suggested."
    run = {
        "created_at": utcnow(), "queries": n, "judgments": sum(len(v["relevant"]) + len(v["rejected"]) for v in judgments.values()),
        "results": results, "current_config": current_key, "best_config": best["config"] if best else None,
        "recommendation": recommendation, "suggested_settings": suggested,
    }
    with ctx.db.tx() as c:
        run["id"] = int(c.execute("INSERT INTO eval_runs(created_at, queries, judgments, result) VALUES (?,?,?,?)",
                                  (run["created_at"], run["queries"], run["judgments"], dumps(run))).lastrowid)
        c.execute("UPDATE eval_runs SET result=? WHERE id=?", (dumps(run), run["id"]))
        c.execute("DELETE FROM eval_runs WHERE id NOT IN (SELECT id FROM eval_runs ORDER BY id DESC LIMIT 20)")
    return run


def latest_run(ctx: AppContext) -> dict[str, Any] | None:
    row = ctx.db.one("SELECT result FROM eval_runs ORDER BY id DESC LIMIT 1")
    return loads(row["result"], None) if row else None
