# Search evaluation (2026-11-11 18:55 UTC)

Corpus: 242 repositories, 446 documents, 5128 passages (5128 embedded). Embedding model: `fastembed:BAAI/bge-small-en-v1.5`. Reranker: `Xenova/ms-marco-MiniLM-L-6-v2`. 52 queries with graded judgments.

| Configuration | Recall@10 | Recall@10 (capped) | nDCG@10 | MRR | median latency | p95 latency |
|---|---|---|---|---|---|---|
| keyword | 0.669 | 0.691 | 0.676 | 0.870 | 33 ms | 42 ms |
| semantic | 0.667 | 0.690 | 0.721 | 0.917 | 201 ms | 280 ms |
| hybrid | 0.748 | 0.773 | 0.750 | 0.887 | 244 ms | 326 ms |
| hybrid+rerank | 0.743 | 0.769 | 0.793 | 0.945 | 2221 ms | 4353 ms |
| hybrid (repo lists only) | 0.748 | 0.772 | 0.754 | 0.901 | 218 ms | 290 ms |
| hybrid (chunk lists only) | 0.643 | 0.665 | 0.659 | 0.860 | 208 ms | 298 ms |
| hybrid (equal weights) | 0.739 | 0.765 | 0.735 | 0.885 | 199 ms | 269 ms |
| hybrid (semantic x2) | 0.757 | 0.783 | 0.764 | 0.920 | 201 ms | 264 ms |
| hybrid (lexical x2) | 0.747 | 0.772 | 0.736 | 0.876 | 232 ms | 311 ms |
| hybrid (no priors) | 0.748 | 0.773 | 0.749 | 0.895 | 218 ms | 353 ms |
| hybrid (typo tolerance off) | 0.743 | 0.767 | 0.741 | 0.887 | 207 ms | 285 ms |
| hybrid (query expansion auto) | 0.743 | 0.767 | 0.745 | 0.900 | 203 ms | 278 ms |
| keyword (typo tolerance off) | 0.641 | 0.662 | 0.642 | 0.832 | 37 ms | 54 ms |
| semantic+rerank | 0.744 | 0.772 | 0.804 | 0.955 | 2226 ms | 4384 ms |

## nDCG@10 by query style

| Configuration | keyword (n=32) | name (n=3) | nl (n=14) | typo (n=3) |
|---|---|---|---|---|
| keyword | 0.742 | 0.896 | 0.440 | 0.860 |
| semantic | 0.727 | 0.900 | 0.660 | 0.764 |
| hybrid | 0.784 | 0.949 | 0.592 | 0.922 |
| hybrid+rerank | 0.830 | 0.960 | 0.660 | 0.846 |
| hybrid (repo lists only) | 0.810 | 0.955 | 0.546 | 0.931 |
| hybrid (chunk lists only) | 0.692 | 0.908 | 0.502 | 0.790 |
| hybrid (equal weights) | 0.761 | 0.949 | 0.586 | 0.924 |
| hybrid (semantic x2) | 0.794 | 0.939 | 0.626 | 0.915 |
| hybrid (lexical x2) | 0.775 | 0.960 | 0.556 | 0.933 |
| hybrid (no priors) | 0.782 | 0.944 | 0.593 | 0.922 |
| hybrid (typo tolerance off) | 0.784 | 0.949 | 0.592 | 0.764 |
| hybrid (query expansion auto) | 0.764 | 0.949 | 0.619 | 0.922 |
| keyword (typo tolerance off) | 0.742 | 0.896 | 0.440 | 0.275 |
| semantic+rerank | 0.826 | 0.963 | 0.728 | 0.774 |
