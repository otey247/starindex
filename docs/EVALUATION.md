# Search evaluation

Generated from `eval/results/latest.json` (2026-11-11 19:48 UTC) by `eval/write_report.py`.
Reproduce with:

```bash
python eval/build_corpus.py          # downloads public metadata and READMEs (needs internet)
python eval/run_eval.py --ablations
python eval/write_report.py
```

## Setup

- **Corpus**: 242 real public repositories from `eval/corpus_repos.txt`, chosen to cover the
  subject areas of the target library (agent frameworks, MCP servers, RAG and search, model serving,
  evaluation and observability, document processing, API gateways, UI components, visualisation, 3D,
  animation, games, automation, data engineering, security). For each: GitHub metadata, the README and one
  manifest, indexed through the real pipeline (fake GitHub API → sync → documentation indexing → embedding):
  446 documents, 5151 passages.
- **Queries**: 52 queries in `eval/queries.yaml` — natural-language questions (including the examples
  from the product brief), keyword queries, exact repository names and misspellings — each with graded
  relevance judgments (2 = directly answers the query, 1 = relevant alternative).
- **Judgments** were written by the build agent from knowledge of the projects. They are not from
  independent annotators and they are incomplete in the usual way: an unjudged repository counts as
  not relevant even when it arguably is, which depresses all numbers equally.
- **Models**: embeddings `fastembed:BAAI/bge-small-en-v1.5`, reranker `Xenova/ms-marco-MiniLM-L-6-v2`, both local (ONNX, CPU).
- **Metrics**: Recall@10 (relevant repositories in the top ten ÷ all relevant; cannot reach 1.0 when more
  than ten are relevant), a capped variant (÷ min(10, relevant)), nDCG@10 with graded gains, and MRR.

## Results

| Configuration | Recall@10 | Recall@10 (capped) | nDCG@10 | MRR | median latency | p95 latency |
|---|---|---|---|---|---|---|
| keyword | 0.669 | 0.691 | 0.676 | 0.870 | 36 ms | 48 ms |
| semantic | 0.667 | 0.690 | 0.721 | 0.917 | 108 ms | 125 ms |
| hybrid | 0.759 | 0.784 | 0.754 | 0.893 | 112 ms | 142 ms |
| hybrid+rerank | 0.774 | 0.801 | 0.819 | 0.962 | 616 ms | 1379 ms |

`hybrid+rerank` is the default configuration of the application.

### nDCG@10 by query style

| Configuration | keyword (n=32) | name (n=3) | nl (n=14) | typo (n=3) |
|---|---|---|---|---|
| keyword | 0.742 | 0.896 | 0.440 | 0.860 |
| semantic | 0.727 | 0.900 | 0.660 | 0.764 |
| hybrid | 0.788 | 0.939 | 0.602 | 0.918 |
| hybrid+rerank | 0.839 | 0.969 | 0.715 | 0.946 |

### Ablations

| Configuration | Recall@10 | Recall@10 (capped) | nDCG@10 | MRR | median latency | p95 latency |
|---|---|---|---|---|---|---|
| hybrid (repo lists only) | 0.743 | 0.769 | 0.756 | 0.907 | 105 ms | 137 ms |
| hybrid (chunk lists only) | 0.637 | 0.658 | 0.654 | 0.853 | 112 ms | 147 ms |
| hybrid (equal weights) | 0.733 | 0.758 | 0.750 | 0.909 | 88 ms | 146 ms |
| hybrid (semantic x1.0) | 0.748 | 0.773 | 0.750 | 0.887 | 117 ms | 142 ms |
| hybrid (semantic x2) | 0.757 | 0.783 | 0.765 | 0.920 | 122 ms | 156 ms |
| hybrid (lexical x2) | 0.745 | 0.770 | 0.742 | 0.882 | 116 ms | 143 ms |
| hybrid (no priors) | 0.757 | 0.782 | 0.754 | 0.896 | 116 ms | 140 ms |
| hybrid (typo tolerance off) | 0.754 | 0.779 | 0.746 | 0.893 | 92 ms | 136 ms |
| hybrid (query expansion auto) | 0.759 | 0.783 | 0.754 | 0.902 | 117 ms | 157 ms |
| keyword (typo tolerance off) | 0.641 | 0.662 | 0.642 | 0.831 | 36 ms | 48 ms |
| semantic+rerank | 0.720 | 0.744 | 0.777 | 0.962 | 578 ms | 1342 ms |
| rerank blend 1:0.5 best_chunk | 0.767 | 0.794 | 0.815 | 0.944 | 662 ms | 909 ms |
| rerank blend 1:1 best_chunk | 0.766 | 0.793 | 0.808 | 0.923 | 661 ms | 922 ms |
| rerank blend 1:0.5 intro | 0.759 | 0.784 | 0.820 | 0.962 | 631 ms | 1375 ms |
| rerank blend 1:1 intro | 0.774 | 0.801 | 0.819 | 0.962 | 592 ms | 1277 ms |
| rerank blend 0.5:1 intro | 0.771 | 0.796 | 0.804 | 0.947 | 628 ms | 1360 ms |
| rerank blend 1:2 intro | 0.769 | 0.794 | 0.803 | 0.947 | 627 ms | 1454 ms |

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
| physics engine for the web | 0.33 | threlte/threlte, dimforge/rapier, playcanvas/engine |
| run large language models locally on my GPU | 0.38 | rasbt/llms-from-scratch, mudler/localai, huggingface/text-generation-inference |
| evaluate RAG pipeline quality | 0.42 | comet-ml/opik, infiniflow/ragflow, evidentlyai/evidently |
| open-source alternatives to Azure API Management | 0.49 | berriai/litellm, gravitee-io/gravitee-api-management, langfuse/langfuse |
| agentic orchestration with MCP support | 0.50 | lastmile-ai/mcp-agent, mcp-use/mcp-use, oraios/serena |
| browser automation for AI agents | 0.63 | browser-use/browser-use, skyvern-ai/skyvern, crewaiinc/crewai |

## Latency

Latencies above were measured on the build machine, a shared 4-core cloud VM without a GPU, in a run with no
other workload. Keyword search takes tens of milliseconds, semantic and hybrid search about a tenth of a
second, and reranking adds the cost of 20 cross-encoder passes on the CPU. Reranking can be
turned off per search or globally (Settings → Search).

## Limits of this evaluation

- One corpus of 242 well-known repositories; a personal library has a longer tail of small projects.
- Documentation-level indexing only (README + one manifest per repository); code search is not evaluated.
- English queries and documents.
- Single annotator, no inter-annotator agreement, judgments written before the final tuning but by the
  same party that tuned: the fusion weights and reranker settings were chosen on this same set, so the
  reported numbers are optimistic for unseen queries.
