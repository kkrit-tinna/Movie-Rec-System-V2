
## Spot check — full-catalog (77,281 movies), (gathered 2026-09-25)
**The Godfather** (id 238)

- 1. The Godfather Part II — 0.328
- 2. The Godfather Trilogy: 1901-1980 — 0.216
- 3. The Godfather Legacy — 0.183
- 4. The Godfather Part III — 0.157
- 5. The Family — 0.154

**Toy Story** (id 862)

- 1. Toy Story 3 — 0.452
- 2. Toy Story 2 — 0.433
- 3. Beyond Infinity: Buzz and the Journey to Lightyear — 0.320
- 4. Buzz Lightyear of Star Command: The Adventure Begins — 0.310
- 5. Wild and Woody! — 0.295


## Primary report - full-catalog (77,281 movies), (gathered 2026-09-25)

Source: `artifacts/comparison/2026-09-23/metrics.json`, `artifacts/comparison/2026-09-23/comparison.json`.

| method | Keyword Jaccard@10 | Genre P@10 | fit_seconds | peak_rss_mb | artifact_mb |
|---|---|---|---|---|---|
| tfidf | 0.0376 | 0.7171 | 4.62 | 2969.4 | 34.2 |
| word2vec | 0.0241 | 0.7277 | 16.74 | 2028.5 | 23.1 |
| random | 0.0025 | 0.3906 | n/a | n/a | n/a |

n/a = null in metrics.json, or no row for that method in comparison.json (random has none).

**Charts** (`scripts/plot_comparison.py`, from metrics.json)

- `docs/img/accuracy_by_method.png`: Keyword Jaccard@10 for tfidf, word2vec, random
- `docs/img/cost_vs_accuracy.png`: fit_seconds vs Keyword Jaccard@10, artifact_mb in point labels, random as floor line

Cost vs accuracy replaces §5's latency vs accuracy, which is degenerate:
latency is a dict lookup of a precomputed list, p50 8.3e-05 ms / p95 0.000167 ms
for both tfidf and word2vec, so it cannot separate the methods.

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

**Decision:** choose TF-IDF as the default embedder, and this decision is benchmarked against the random baseline model in terms of accuracy and cost.

I use two accuracy metrics:  Genre Precision@10  and Keyword Jaccard@10. Genre Precision@10 measures what fraction of a movie's ten recommended neighbours share at least one genre with it — so at 0.717, about seven of every ten recommendations match on a label the model read during training.
Keyword Jaccard@10 measures the average overlap between a movie's TMDB keyword tags and each neighbour's tags, as shared tags divided by total distinct tags. 

Genre P@10 comes out at 0.717 for TF-IDF against 0.728 for Word2Vec — a 1.5% difference that cannot separate the two models, and one measured on features both were trained on. Keyword Jaccard@10 does separate them: TF-IDF scores 0.0376 against Word2Vec's 0.0241, a 56% margin, and 15× the 0.0025 random floor. The absolute value is low, but with a median of 5 keywords per film drawn from thousands of possible tags, two genuinely similar movies often share none — that is a property of sparse multi-label ground truth, not weak retrieval. The ratio to random is the meaningful figure.

For cost I measure fit time on the full catalog (fit_seconds) and the total size on disk of everything a run produces (artifact_mb). TF-IDF fits in 4.62s against Word2Vec's 16.74s on 77,281 movies; both are trivial against a 20-minute Fargate budget, and most of Word2Vec's time is loading the GloVe file rather than work that scales with the catalog. On disk, TF-IDF writes 34.2 MB and Word2Vec 23.1 MB. TF-IDF's directory is matrix.npz (24 MB), neighbours.parquet (7 MB), row_index.parquet (960 KB), and vectorizer.joblib (1.9 MB); Word2Vec's has the same shape, with matrix.npy at 15.5 MB and embedder.json holding the IDF map in place of the vectorizer. Word2Vec is smaller because its matrix is dense but narrow — 77,281 × 100 float16 — while TF-IDF's is sparse but wide, up to 100,000 vocabulary columns, and storing the nonzero entries costs more than 100 dense dimensions per row.

***In summary, TF-IDF wins: it wins the primary accuracy metric by 56%, fits faster, and its only disadvantage — 11 MB more on disk — is negligible at this scale.***

**Caveats:** 290 of the 2,000 sampled queries were skipped because they carry no keywords at all; that is a property of TMDB's tagging coverage, not of either model, and scoring those queries as zero would have measured the dataset rather than the embedder.

**Future work:** This comparison assumes the user selects an existing title, so neighbours can be precomputed; a free-text query path would need embedding at request time and would likely favour Word2Vec or a sentence encoder, which means rerunning this comparison against a metric built for that task.

