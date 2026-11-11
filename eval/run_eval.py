"""Measure retrieval quality of StarIndex on the evaluation corpus.

Builds a real StarIndex instance (sync -> documentation indexing -> embeddings -> vector index)
from the downloaded corpus through the fake GitHub API, then runs every query in queries.yaml
in each configuration and reports Recall@10, nDCG@10 and MRR.

    python eval/build_corpus.py                 # once (needs internet)
    python eval/run_eval.py                     # builds eval/.eval-data on first run, then reuses it
    python eval/run_eval.py --rebuild           # re-index from scratch
    python eval/run_eval.py --ablations         # also try alternative fusion weights

Numbers are written to eval/results/. Nothing in this file talks to a language model.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "backend"))

K = 10


def dcg(gains: list[int]) -> float:
    return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gains))


def score_query(ranked: list[str], relevant: dict[str, int]) -> dict[str, float]:
    """ranked: repository names (lower-case) in result order; relevant: name -> grade."""
    top = ranked[:K]
    gains = [relevant.get(name, 0) for name in top]
    ideal = sorted(relevant.values(), reverse=True)[:K]
    ndcg = dcg(gains) / dcg(ideal) if ideal else 0.0
    found = sum(1 for name in top if relevant.get(name, 0) > 0)
    recall = found / len(relevant) if relevant else 0.0
    # Recall@10 cannot reach 1.0 for queries with more than ten relevant repositories;
    # the capped variant divides by min(10, relevant) to show how full the first page is.
    recall_capped = found / min(K, len(relevant)) if relevant else 0.0
    rr = 0.0
    for i, name in enumerate(ranked[:50]):
        if relevant.get(name, 0) > 0:
            rr = 1.0 / (i + 1)
            break
    return {"recall": recall, "recall_capped": recall_capped, "ndcg": ndcg, "mrr": rr}


def build(data_dir: Path, *, seed_db: str | None, models_dir: str | None, model: str | None) -> AppContext:  # noqa: F821
    from starindex.jobs.worker import Worker
    from starindex.testing.corpus import fake_from_corpus
    from starindex.testing.harness import make_context

    if models_dir:
        os.environ["STARINDEX_MODELS_DIR"] = models_dir
    fake = fake_from_corpus()
    fresh = not (data_dir / "starindex.db").exists()
    embedding: dict = {"backend": "fastembed"}
    if model:
        embedding["model"] = model
    ctx = make_context(data_dir, fake, embedding="fastembed", settings={"embedding": embedding, "rerank": {"enabled": False}})
    if fresh or ctx.db.scalar("SELECT count(*) FROM repos", default=0) == 0:
        t0 = time.time()
        if seed_db and Path(seed_db).is_file():
            # Reuse vectors computed by another instance: the cache is keyed by content hash + model.
            src = sqlite3.connect(Path(seed_db).resolve().as_uri() + "?mode=ro", uri=True)
            rows = src.execute("SELECT content_hash, model_key, dim, vector, created_at FROM embeddings").fetchall()
            src.close()
            with ctx.db.tx() as c:
                c.executemany("INSERT OR IGNORE INTO embeddings(content_hash, model_key, dim, vector, created_at) VALUES (?,?,?,?,?)", rows)
            print(f"seeded {len(rows)} cached embeddings", flush=True)
        ctx.jobs.enqueue("sync_full", dedupe_key="sync")
        Worker(ctx, mode="embedded").drain(include_deferred=True)
        print(f"indexed corpus in {time.time() - t0:.0f}s", flush=True)
    failed = ctx.db.scalar("SELECT count(*) FROM jobs WHERE status='failed'", default=0)
    if failed:
        for r in ctx.db.all("SELECT type, error FROM jobs WHERE status='failed' LIMIT 5"):
            print("FAILED JOB", r["type"], r["error"])
        raise SystemExit("indexing failed; see above")
    return ctx


CONFIGS = {
    "keyword": {"mode": "keyword", "rerank": False},
    "semantic": {"mode": "semantic", "rerank": False},
    "hybrid": {"mode": "hybrid", "rerank": False},
    "hybrid+rerank": {"mode": "hybrid", "rerank": True},
}
ABLATIONS = {
    "hybrid (repo lists only)": {"mode": "hybrid", "rerank": False, "weights": (1.0, 0.0, 1.0, 0.0)},
    "hybrid (chunk lists only)": {"mode": "hybrid", "rerank": False, "weights": (0.0, 1.0, 0.0, 1.0)},
    "hybrid (equal weights)": {"mode": "hybrid", "rerank": False, "weights": (1.0, 1.0, 1.0, 1.0)},
    "hybrid (semantic x1.0)": {"mode": "hybrid", "rerank": False, "semantic_weight": 1.0},
    "hybrid (semantic x2)": {"mode": "hybrid", "rerank": False, "semantic_weight": 2.0},
    "hybrid (lexical x2)": {"mode": "hybrid", "rerank": False, "lexical_weight": 2.0},
    "hybrid (no priors)": {"mode": "hybrid", "rerank": False, "priors": 0.0},
    "hybrid (typo tolerance off)": {"mode": "hybrid", "rerank": False, "typo": False},
    "hybrid (query expansion auto)": {"mode": "hybrid", "rerank": False, "expansion": "auto"},
    "keyword (typo tolerance off)": {"mode": "keyword", "rerank": False, "typo": False},
    "semantic+rerank": {"mode": "semantic", "rerank": True},
    "rerank blend 1:0.5 best_chunk": {"mode": "hybrid", "rerank": True, "blend": (1.0, 0.5), "passage": "best_chunk"},
    "rerank blend 1:1 best_chunk": {"mode": "hybrid", "rerank": True, "blend": (1.0, 1.0), "passage": "best_chunk"},
    "rerank blend 1:0.5 intro": {"mode": "hybrid", "rerank": True, "blend": (1.0, 0.5), "passage": "intro"},
    "rerank blend 1:1 intro": {"mode": "hybrid", "rerank": True, "blend": (1.0, 1.0), "passage": "intro"},
    "rerank blend 0.5:1 intro": {"mode": "hybrid", "rerank": True, "blend": (0.5, 1.0), "passage": "intro"},
    "rerank blend 1:2 intro": {"mode": "hybrid", "rerank": True, "blend": (1.0, 2.0), "passage": "intro"},
}


def run_config(ctx, name: str, cfg: dict, queries: list[dict], known: set[str]) -> dict:
    from starindex.api.schemas import SearchRequest
    from starindex.search import service

    defaults = (service.W_LEX_REPO, service.W_LEX_CHUNK, service.W_SEM_REPO, service.W_SEM_CHUNK)
    rerank_defaults = (service.RERANK_WEIGHT, service.RERANK_FUSED_WEIGHT, service.RERANK_PASSAGE)
    if "blend" in cfg:
        service.RERANK_WEIGHT, service.RERANK_FUSED_WEIGHT = cfg["blend"]
    if "passage" in cfg:
        service.RERANK_PASSAGE = cfg["passage"]
    patch = {
        "search": {
            "lexical_weight": cfg.get("lexical_weight", 1.0),
            "semantic_weight": cfg.get("semantic_weight", 1.5),
            "typo_tolerance": cfg.get("typo", True),
            "query_expansion": cfg.get("expansion", "off"),
            **({"popularity_boost": 0.0, "freshness_boost": 0.0} if cfg.get("priors") == 0.0 else {"popularity_boost": 0.03, "freshness_boost": 0.03}),
        },
        "rerank": {"enabled": bool(cfg.get("rerank"))},
    }
    ctx.settings_store.patch(patch)
    if "weights" in cfg:
        service.W_LEX_REPO, service.W_LEX_CHUNK, service.W_SEM_REPO, service.W_SEM_CHUNK = cfg["weights"]
    per_query = []
    latencies = []
    try:
        for q in queries:
            relevant = {k.lower(): v for k, v in q["relevant"].items() if k.lower() in known}
            t0 = time.perf_counter()
            res = ctx.search.search(SearchRequest(query=q["q"], mode=cfg["mode"], limit=50, rerank=bool(cfg.get("rerank")), record=False))
            latencies.append((time.perf_counter() - t0) * 1000)
            if res.mode != cfg["mode"]:
                raise SystemExit(f"{name}: requested {cfg['mode']} but ran {res.mode}: {res.notices}")
            ranked = [r.repo.full_name.lower() for r in res.results]
            per_query.append({"q": q["q"], "kind": q.get("kind", "nl"), **score_query(ranked, relevant), "top": ranked[:5]})
    finally:
        service.W_LEX_REPO, service.W_LEX_CHUNK, service.W_SEM_REPO, service.W_SEM_CHUNK = defaults
        service.RERANK_WEIGHT, service.RERANK_FUSED_WEIGHT, service.RERANK_PASSAGE = rerank_defaults

    def mean(key: str, rows: list[dict]) -> float:
        return round(statistics.fmean(r[key] for r in rows), 4) if rows else 0.0

    kinds = sorted({r["kind"] for r in per_query})
    return {
        "config": name,
        "recall@10": mean("recall", per_query),
        "recall@10_capped": mean("recall_capped", per_query),
        "ndcg@10": mean("ndcg", per_query),
        "mrr": mean("mrr", per_query),
        "latency_ms_median": round(statistics.median(latencies), 1),
        "latency_ms_p95": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 1),
        "by_kind": {k: {"n": sum(1 for r in per_query if r["kind"] == k), "ndcg@10": mean("ndcg", [r for r in per_query if r["kind"] == k]),
                        "recall@10_capped": mean("recall_capped", [r for r in per_query if r["kind"] == k]),
                        "mrr": mean("mrr", [r for r in per_query if r["kind"] == k])} for k in kinds},
        "queries": per_query,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(HERE / ".eval-data"))
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--seed-embeddings", help="Path to another StarIndex database whose embedding cache can be reused.")
    parser.add_argument("--models-dir")
    parser.add_argument("--model", help="Embedding model (fastembed name). Default: the application default.")
    parser.add_argument("--ablations", action="store_true")
    parser.add_argument("--only", nargs="*", help="Run only these configurations.")
    parser.add_argument("--tag", default="latest")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if args.rebuild and data_dir.exists():
        shutil.rmtree(data_dir)
    ctx = build(data_dir, seed_db=args.seed_embeddings, models_dir=args.models_dir, model=args.model)
    queries = yaml.safe_load((HERE / "queries.yaml").read_text(encoding="utf-8"))
    known = {r["full_name"].lower() for r in ctx.db.all("SELECT full_name FROM repos")}
    missing = sorted({k for q in queries for k in q["relevant"] if k.lower() not in known})
    stats = {
        "repositories": len(known),
        "documents": ctx.db.scalar("SELECT count(*) FROM documents", default=0),
        "chunks": ctx.db.scalar("SELECT count(*) FROM chunks", default=0),
        "chunks_embedded": ctx.db.scalar("SELECT count(*) FROM chunks WHERE embedded_model_key IS NOT NULL", default=0),
        "embedding_model": ctx.embeddings.model_key,
        "reranker": ctx.settings.rerank.model,
        "queries": len(queries),
        "judged_repositories_missing_from_corpus": missing,
    }
    print(json.dumps(stats, indent=1), flush=True)
    configs = dict(CONFIGS)
    if args.ablations:
        configs.update(ABLATIONS)
    if args.only:
        configs = {k: v for k, v in {**CONFIGS, **ABLATIONS}.items() if k in args.only}
    results = []
    for name, cfg in configs.items():
        r = run_config(ctx, name, cfg, queries, known)
        results.append(r)
        print(f"{name:32} Recall@10 {r['recall@10']:.3f} (capped {r['recall@10_capped']:.3f})  nDCG@10 {r['ndcg@10']:.3f}  MRR {r['mrr']:.3f}"
              f"  median {r['latency_ms_median']:.0f} ms  p95 {r['latency_ms_p95']:.0f} ms", flush=True)

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    (out / f"{args.tag}.json").write_text(json.dumps({"generated": stamp, "corpus": stats, "results": results}, indent=1), encoding="utf-8")
    lines = [
        f"# Search evaluation ({stamp})", "",
        f"Corpus: {stats['repositories']} repositories, {stats['documents']} documents, {stats['chunks']} passages "
        f"({stats['chunks_embedded']} embedded). Embedding model: `{stats['embedding_model']}`. Reranker: `{stats['reranker']}`. "
        f"{stats['queries']} queries with graded judgments.", "",
        "| Configuration | Recall@10 | Recall@10 (capped) | nDCG@10 | MRR | median latency | p95 latency |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['config']} | {r['recall@10']:.3f} | {r['recall@10_capped']:.3f} | {r['ndcg@10']:.3f} | {r['mrr']:.3f} | "
                     f"{r['latency_ms_median']:.0f} ms | {r['latency_ms_p95']:.0f} ms |")
    lines += ["", "## nDCG@10 by query style", "", "| Configuration | " + " | ".join(f"{k} (n={v['n']})" for k, v in results[0]["by_kind"].items()) + " |",
              "|---|" + "---|" * len(results[0]["by_kind"])]
    for r in results:
        lines.append(f"| {r['config']} | " + " | ".join(f"{v['ndcg@10']:.3f}" for v in r["by_kind"].values()) + " |")
    if missing:
        lines += ["", "Judged repositories not present in this corpus build (ignored): " + ", ".join(missing)]
    (out / f"{args.tag}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out / (args.tag + '.md')}")
    ctx.close()


if __name__ == "__main__":
    main()
