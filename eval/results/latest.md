# Search evaluation (2026-11-11 19:48 UTC)

Corpus: 242 repositories, 446 documents, 5151 passages (5151 embedded). Embedding model: `fastembed:BAAI/bge-small-en-v1.5`. Reranker: `Xenova/ms-marco-MiniLM-L-6-v2`. 52 queries with graded judgments.

| Configuration | Recall@10 | Recall@10 (capped) | nDCG@10 | MRR | median latency | p95 latency |
|---|---|---|---|---|---|---|
| keyword | 0.669 | 0.691 | 0.676 | 0.870 | 36 ms | 48 ms |
| semantic | 0.667 | 0.690 | 0.721 | 0.917 | 108 ms | 125 ms |
| hybrid | 0.759 | 0.784 | 0.754 | 0.893 | 112 ms | 142 ms |
| hybrid+rerank | 0.774 | 0.801 | 0.819 | 0.962 | 616 ms | 1379 ms |
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

## nDCG@10 by query style

| Configuration | keyword (n=32) | name (n=3) | nl (n=14) | typo (n=3) |
|---|---|---|---|---|
| keyword | 0.742 | 0.896 | 0.440 | 0.860 |
| semantic | 0.727 | 0.900 | 0.660 | 0.764 |
| hybrid | 0.788 | 0.939 | 0.602 | 0.918 |
| hybrid+rerank | 0.839 | 0.969 | 0.715 | 0.946 |
| hybrid (repo lists only) | 0.795 | 0.952 | 0.589 | 0.927 |
| hybrid (chunk lists only) | 0.680 | 0.913 | 0.508 | 0.795 |
| hybrid (equal weights) | 0.785 | 0.949 | 0.591 | 0.922 |
| hybrid (semantic x1.0) | 0.784 | 0.949 | 0.592 | 0.922 |
| hybrid (semantic x2) | 0.795 | 0.942 | 0.626 | 0.915 |
| hybrid (lexical x2) | 0.777 | 0.949 | 0.576 | 0.937 |
| hybrid (no priors) | 0.785 | 0.944 | 0.606 | 0.921 |
| hybrid (typo tolerance off) | 0.788 | 0.939 | 0.602 | 0.764 |
| hybrid (query expansion auto) | 0.777 | 0.939 | 0.625 | 0.918 |
| keyword (typo tolerance off) | 0.742 | 0.896 | 0.440 | 0.275 |
| semantic+rerank | 0.787 | 0.946 | 0.714 | 0.795 |
| rerank blend 1:0.5 best_chunk | 0.845 | 0.964 | 0.686 | 0.951 |
| rerank blend 1:1 best_chunk | 0.840 | 0.964 | 0.670 | 0.959 |
| rerank blend 1:0.5 intro | 0.840 | 0.963 | 0.720 | 0.933 |
| rerank blend 1:1 intro | 0.839 | 0.969 | 0.715 | 0.946 |
| rerank blend 0.5:1 intro | 0.831 | 0.969 | 0.677 | 0.951 |
| rerank blend 1:2 intro | 0.830 | 0.963 | 0.675 | 0.951 |
