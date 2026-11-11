"""Render docs/EVALUATION.md from eval/results/latest.json (numbers are never typed by hand)."""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
data = json.loads((HERE / "results" / "latest.json").read_text(encoding="utf-8"))
corpus = data["corpus"]
by_name = {r["config"]: r for r in data["results"]}


def row(name: str, label: str | None = None) -> str:
    r = by_name[name]
    return (f"| {label or name} | {r['recall@10']:.3f} | {r['recall@10_capped']:.3f} | {r['ndcg@10']:.3f} | {r['mrr']:.3f} | "
            f"{r['latency_ms_median']:.0f} ms | {r['latency_ms_p95']:.0f} ms |")


HEAD = "| Configuration | Recall@10 | Recall@10 (capped) | nDCG@10 | MRR | median latency | p95 latency |\n|---|---|---|---|---|---|---|"
main = ["keyword", "semantic", "hybrid", "hybrid+rerank"]
ablations = [n for n in by_name if n not in main]
kinds = list(by_name["hybrid"]["by_kind"])
kind_head = "| Configuration | " + " | ".join(f"{k} (n={by_name['hybrid']['by_kind'][k]['n']})" for k in kinds) + " |\n|---|" + "---|" * len(kinds)
kind_rows = "\n".join(f"| {n} | " + " | ".join(f"{by_name[n]['by_kind'][k]['ndcg@10']:.3f}" for k in kinds) + " |" for n in main)
final = by_name["hybrid+rerank"]
hardest = sorted(final["queries"], key=lambda q: q["ndcg"])[:6]
hard_rows = "\n".join(f"| {q['q']} | {q['ndcg']:.2f} | {', '.join(q['top'][:3])} |" for q in hardest)

text = f"""# Search evaluation

Generated from `eval/results/latest.json` ({data['generated']}) by `eval/write_report.py`.
Reproduce with:

```bash
python eval/build_corpus.py          # downloads public metadata and READMEs (needs internet)
python eval/run_eval.py --ablations
python eval/write_report.py
```

## Setup

- **Corpus**: {corpus['repositories']} real public repositories from `eval/corpus_repos.txt`, chosen to cover the
  subject areas of the target library (agent frameworks, MCP servers, RAG and search, model serving,
  evaluation and observability, document processing, API gateways, UI components, visualisation, 3D,
  animation, games, automation, data engineering, security). For each: GitHub metadata, the README and one
  manifest, indexed through the real pipeline (fake GitHub API → sync → documentation indexing → embedding):
  {corpus['documents']} documents, {corpus['chunks']} passages.
- **Queries**: {corpus['queries']} queries in `eval/queries.yaml` — natural-language questions (including the examples
  from the product brief), keyword queries, exact repository names and misspellings — each with graded
  relevance judgments (2 = directly answers the query, 1 = relevant alternative).
- **Judgments** were written by the build agent from knowledge of the projects. They are not from
  independent annotators and they are incomplete in the usual way: an unjudged repository counts as
  not relevant even when it arguably is, which depresses all numbers equally.
- **Models**: embeddings `{corpus['embedding_model']}`, reranker `{corpus['reranker']}`, both local (ONNX, CPU).
- **Metrics**: Recall@10 (relevant repositories in the top ten ÷ all relevant; cannot reach 1.0 when more
  than ten are relevant), a capped variant (÷ min(10, relevant)), nDCG@10 with graded gains, and MRR.

## Results

{HEAD}
{chr(10).join(row(n) for n in main)}

`hybrid+rerank` is the default configuration of the application.

### nDCG@10 by query style

{kind_head}
{kind_rows}

### Ablations

{HEAD}
{chr(10).join(row(n) for n in ablations)}

## What the numbers decided

- **Hybrid beats either retriever alone** on Recall@10 and nDCG@10; lexical retrieval contributes exact
  names and rare terms, dense retrieval contributes paraphrase ("alternatives to …", "… from code").
- **Reranking is on by default.** A local cross-encoder over the top 20 gives the largest single gain in
  nDCG@10 and MRR. It is blended 1:1 with the fused rank, and it judges each repository by its own
  summary (description, topics, language, README opening): reranking on the best-matching passage instead
  was measurably worse because passages that merely mention the query's entities score highly.
- **Semantic weight 1.5.** Weighting the dense lists above the lexical ones helps without reranking and is
  neutral with it; doubling the lexical weight hurts.
- **Repository-level lists carry most of the signal** at the documentation level; passage-level lists
  alone are clearly worse, so they enter the fusion with lower weights.
- **Typo tolerance stays on**: it helps keyword search and the misspelled queries and costs nothing elsewhere.
- **Query expansion stays off by default**: the built-in abbreviation expansion did not improve ranking on
  this set. (Model-assisted expansion exists as an option for local models and was not measured here.)
- **Popularity and freshness priors are neutral** on these judgments. They are kept small (at most +3 % each)
  so that they can only break near-ties.

## Hardest queries for the default configuration

| Query | nDCG@10 | Top results |
|---|---|---|
{hard_rows}

## Latency

Latencies above were measured on the build machine, a shared 4-core cloud VM without a GPU, in a run with no
other workload. Keyword search takes tens of milliseconds, semantic and hybrid search about a tenth of a
second, and reranking adds the cost of 20 cross-encoder passes on the CPU. Reranking can be
turned off per search or globally (Settings → Search).

## Limits of this evaluation

- One corpus of {corpus['repositories']} well-known repositories; a personal library has a longer tail of small projects.
- Documentation-level indexing only (README + one manifest per repository); code search is not evaluated.
- English queries and documents.
- Single annotator, no inter-annotator agreement, judgments written before the final tuning but by the
  same party that tuned: the fusion weights and reranker settings were chosen on this same set, so the
  reported numbers are optimistic for unseen queries.
"""
(HERE.parent / "docs" / "EVALUATION.md").write_text(text, encoding="utf-8")
print("wrote docs/EVALUATION.md")
