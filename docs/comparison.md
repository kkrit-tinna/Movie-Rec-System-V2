
## T1.5 spot check — 262-row sample, 2026-09-18

## Scratch — inputs for Sunday's report (gathered 2026-09-25)

Source: `artifacts/comparison/2026-09-23/metrics.json`, `artifacts/comparison/2026-09-23/comparison.json`.

| method | Keyword Jaccard@10 | Genre P@10 | latency p50 / p95 (ms) | fit_seconds | peak_rss_mb | artifact_mb |
|---|---|---|---|---|---|---|
| tfidf | 0.0376 | 0.7171 | 8.3e-05 / 0.000167 | 4.62 | 2969.4 | 34.2 |
| word2vec | 0.0241 | 0.7277 | 8.3e-05 / 0.000167 | 16.74 | 2028.5 | 23.1 |
| random | 0.0025 | 0.3906 | n/a | n/a | n/a | n/a |

n/a = null in metrics.json, or no row for that method in comparison.json (random has none).

**Run parameters**

- run_date scored: 2026-09-23
- catalog size: 77,281
- n_queries: 2,000 (target 2,000)
- seed: 42
- query_min_vote_count: 50
- k: 10
- queries with keywords: 1,710 of 2,000 (queries with genres: 1,998)

**Empty-keyword rule:** `empty_label_rule: "skip"` — queries with no keywords are skipped, not scored zero.

**Reference points**

- T1.1 random-pair Keyword Jaccard baseline: 0.0027
- §7 Fargate memory budget: 4 GiB
