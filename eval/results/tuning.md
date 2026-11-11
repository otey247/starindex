# Search evaluation (2026-11-11 19:07 UTC)

Corpus: 242 repositories, 446 documents, 5128 passages (5128 embedded). Embedding model: `fastembed:BAAI/bge-small-en-v1.5`. Reranker: `Xenova/ms-marco-MiniLM-L-6-v2`. 52 queries with graded judgments.

| Configuration | Recall@10 | Recall@10 (capped) | nDCG@10 | MRR | median latency | p95 latency |
|---|---|---|---|---|---|---|
| hybrid | 0.759 | 0.784 | 0.751 | 0.889 | 108 ms | 177 ms |
| hybrid+rerank | 0.778 | 0.805 | 0.822 | 0.962 | 694 ms | 1449 ms |

## nDCG@10 by query style

| Configuration | keyword (n=32) | name (n=3) | nl (n=14) | typo (n=3) |
|---|---|---|---|---|
| hybrid | 0.786 | 0.939 | 0.594 | 0.918 |
| hybrid+rerank | 0.844 | 0.969 | 0.716 | 0.946 |
