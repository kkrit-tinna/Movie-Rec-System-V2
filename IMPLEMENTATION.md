# Movie Recommender v2 — Implementation Guide (AWS)

**Status:** spec for the upgrade, Sep 8 – Oct 23, 2026 (deadline moved from
Oct 12 on 2026-10-08; see §7 "Revised schedule" and §9)
**Budget:** 15 min/day Mon–Fri; 30 min/day Fri Oct 9 and Mon–Wed Oct 12–14;
the 30–45 min long-session budget applies to Sun Sep 27, Sat Oct 17 and
Sun Oct 18 only.
**Audience:** the author, and Claude Code
**Schedule:** Phase 1 slipped one week; Phase 3 dropped Sep 15 and stays
dropped — the extra week is buffer for Phase 4, not a Phase 3 reinstatement.
No work Saturdays except Sat Oct 17. No work Sun Sep 20, Sun Oct 4,
Thu Oct 8, Sat–Sun Oct 10–11, or Thu–Fri Oct 15–16 (career fair Oct 15).
---

## 0. How to use this document

**Humans:** read §1–§3 once, then work through §4–§7 one task per day.
The 15 min/day budget is your reading time, not task runtime; if a task
exceeds it, stop, split it, and log the split in §9.

**Claude Code:** this file is the source of truth. Rules:

1. Do exactly one task per session. A task is one `T#.#` block.
2. Never start a task whose **Depends on** is not yet merged.
3. Every task ends with its **Done when** command passing. If it fails, fix it in the same session; do not proceed to the next task.
4. Never invent AWS resources outside `infra/`. All infrastructure is Terraform.
5. Never commit credentials, `.env`, `kaggle.json`, or anything in `data/`.
6. If the real data disagrees with this spec (a column is missing, a count is off), **update this file in the same commit** and note it under §9 Deviations. Do not silently work around it.

---

## 1. Goal

### User story

> A visitor opens the site, types a movie name, picks it from the suggestions, and sees 10 similar movies with poster, year, rating, and a one-line overview.

### Engineering goal

Replace the v1 Dataproc/Spark pipeline with a pluggable-embedder pipeline on AWS that:

- compares TF-IDF against Word2Vec on a **held-out** similarity metric, not vibes
- refreshes weekly, unattended, with data-quality gates that can fail the run
- costs **under $1/month** at steady state (v1 cost $30–50/month)
- can be cloned and run end-to-end by a stranger with no AWS account

### Explicit non-goals

- No Spark, no Hadoop, no cluster. 930K rows of short text fits in 4 GB of RAM.
- No user accounts, no ratings, no collaborative filtering. Content-based only.
- No free-text semantic search. The user types a *movie title*, which is a lookup, not a query embedding. This is why the serving layer never loads an embedding matrix.

---

## 2. Target architecture

```
                    ┌──────────────────────────────┐
   EventBridge      │  ECS Fargate task            │
   Scheduler   ───► │  2 vCPU / 4 GiB, ~6 min      │
   (Sun 02:00 ET)   │  PUBLIC subnet, no NAT       │
                    │                              │
                    │  1. download Kaggle → /tmp   │
                    │  2. quality gates (fail fast)│
                    │  3. fit TF-IDF + Word2Vec    │
                    │  4. top-K neighbours, both   │
                    │  5. evaluate → metrics.json  │
                    │  6. write artifacts → S3     │
                    │  7. batch-write → DynamoDB   │
                    │  8. flip current.json        │
                    └───────────┬──────────────────┘
                                │
        ┌───────────────────────┼────────────────────────┐
        ▼                       ▼                        ▼
  ┌───────────┐        ┌────────────────┐        ┌──────────────┐
  │    S3     │        │   DynamoDB     │        │  CloudWatch  │
  │ artifacts │        │ movie items +  │        │ logs + SNS   │
  │ metrics   │        │ top-K lists    │        │ on failure   │
  │ current   │        └───────┬────────┘        └──────────────┘
  └─────┬─────┘                │
        │ titles.json.gz       │
        ▼                      ▼
     ┌──────────────────────────────┐
     │  Lambda (container image)    │
     │  + Function URL              │
     │  Flask via Mangum            │
     │  rapidfuzz title search      │
     └──────────────┬───────────────┘
                    ▼
              browser (static HTML served by the same Lambda)
```

### Why each piece

| Component | Why not something else |
|---|---|
| Fargate for the batch | Lambda caps at 15 min; fitting Word2Vec over 930K docs will exceed it |
| **Public** subnet, `assignPublicIp: ENABLED` | A private subnet needs a NAT Gateway at ~$32/month — 30× the rest of the bill |
| DynamoDB for serving | Precomputed top-K means the API is a key lookup. 25 GB and 200M requests/month are always-free |
| Lambda for the API | Always-free 1M requests. No idle cost. Container image so the title index ships with the code |
| S3 for artifacts | Reproducibility and the comparison report. Not on the serving path |
| `current.json` pointer | Atomic promotion, one-line rollback, and it *is* the embedder switch |

### Cost target

Measured on the first full Fargate run, 2026-10-07 (§9); weekly ≈ 4.3 runs/month.

| Line | Per run | Monthly |
|---|---|---|
| DynamoDB — 77,281 on-demand writes (1 WRU each, items < 1 KB) | ~$0.05 | ~$0.21 |
| Fargate — ~6.5 min × 2 vCPU / 4 GiB ARM64 | ~$0.009 | ~$0.04 |
| S3 — ~100 MB per run × 60-day window + `models/` (~1.1 GB) + requests | — | ~$0.03 |
| ECR — batch image 246.5 MB compressed, inside the 500 MB free allowance | — | $0 (~$0.10/GB-month above it) |
| Lambda, EventBridge, CloudWatch, DynamoDB storage and reads | — | $0 (always free) |
| **Total** | **~$0.06** | **~$0.28** — meets the < $0.50 target |

DynamoDB writes, not Fargate, are the largest line: the full table is reloaded every run.

Set a **$2 AWS Budgets alert before task T4.1.** If a month ever exceeds $2, something is misconfigured — almost certainly a NAT Gateway or a load balancer.

---

## 3. Repo layout and conventions

```
movie-recommender/
├── README.md                  # architecture diagram + `make demo` first
├── IMPLEMENTATION.md    # this file
├── Makefile
├── pyproject.toml
├── Dockerfile                 # ONE image: local, CI, and Fargate
├── Dockerfile.api             # slim image for the Lambda API
├── .env.example
├── config/
│   └── default.yaml           # thresholds, K, model params
├── src/movierec/
│   ├── config.py
│   ├── storage/
│   │   ├── base.py            # ArtifactStore ABC
│   │   ├── local.py           # LocalStore  → ./artifacts/
│   │   └── s3.py              # S3Store
│   ├── embedders/
│   │   ├── base.py            # BaseEmbedder ABC
│   │   ├── tfidf.py
│   │   └── word2vec.py
│   ├── data/
│   │   ├── ingest.py          # Kaggle download + load
│   │   └── quality.py         # gates
│   ├── index/
│   │   ├── neighbours.py      # top-K
│   │   └── titles.py          # title index build + fuzzy search
│   ├── eval/
│   │   └── metrics.py
│   ├── pipeline.py            # batch entrypoint
│   └── api/
│       ├── app.py             # Flask
│       └── static/index.html
├── infra/                     # Terraform
├── tests/
├── data/sample/movies_5k.csv  # committed, for `make demo`
└── docs/
    ├── schema.md              # written by T1.1
    └── comparison.md          # written by T2.5
```

### Conventions

- Python 3.12. Dependencies in `pyproject.toml`, no `requirements.txt`.
- Config precedence: env var → `config/default.yaml` → code default. Never hardcode a threshold in a module.
- `STORAGE_BACKEND=local|s3` selects the `ArtifactStore`. Nothing outside `storage/` may import `boto3`.
- Artifact paths: `artifacts/{method}/{run_date}/{filename}` where `run_date` is `YYYY-MM-DD`. Identical for local and S3.
- Every module gets a test. Coverage is not a target; *the quality gates and the metrics must be tested* because those are the parts that would fail silently.
- Commit messages follow the plan: `feat: tfidf-embedder-implementation`, `docs: embedding-comparison-report`.
- One commit per task, on `main`. This is a solo repo; branches add ceremony without benefit, and a clean linear history reads better to a reviewer.

### Dataset

`asaniczka/tmdb-movies-dataset-2023-930k-movies` on Kaggle. Kaggle lists it as updated daily, but as measured that does not reach the catalog: the 2026-10-07 file had 15,753 more raw rows than Sep 14, yet the catalog was the same 77,281 ids with identical vote_count and documents (§9 2026-10-07). New rows all fail the filters and existing rows are not refreshed, so the weekly refresh currently re-proves the pipeline rather than changing recommendations. The slug's 930K is the publication-time count and the file now carries about 1.5M rows.

---

## 4. Phase 1 — Data + TF-IDF, all local (Week 1, Sep 8–14)

No AWS in this phase. Everything runs on the laptop against `LocalStore`.

### T1.1 — Repo skeleton and schema truth
**Depends on:** nothing
**Commit:** `feat: repo-skeleton-and-schema`

Create the layout in §3 (empty modules are fine). Download the dataset once by hand. Then write `docs/schema.md` recording, from the actual file:

- every column name and dtype
- row count
- null rate for `overview`, `title`, `genres`, `keywords`, `release_date`, `poster_path`
- whether a franchise/collection column exists (affects T2.4)
- distribution of `vote_count`: count of rows at ≥ 0, 5, 10, 50, 100

Build `data/sample/movies_5k.csv` by sampling 5,000 rows stratified on `vote_count` decile, and commit it. Everything downstream must run on this sample with zero credentials.

**Done when:** `docs/schema.md` exists with real numbers, and `wc -l data/sample/movies_5k.csv` returns 5001.

---

### T1.2 — `ArtifactStore` and `BaseEmbedder`
**Depends on:** T1.1
**Commit:** `feat: base-embedder-and-storage`

```python
class ArtifactStore(ABC):
    def put_bytes(self, path: str, data: bytes) -> None: ...
    def get_bytes(self, path: str) -> bytes: ...
    def put_json(self, path: str, obj: dict) -> None: ...
    def get_json(self, path: str) -> dict: ...
    def exists(self, path: str) -> bool: ...
    def list(self, prefix: str) -> list[str]: ...

class BaseEmbedder(ABC):
    name: str                                  # "tfidf" | "word2vec"
    def fit(self, texts: list[str]) -> None: ...
    def transform(self, texts: list[str]): ...     # (n, d), sparse or dense
    def save(self, store: ArtifactStore, run_date: str) -> None: ...
    @classmethod
    def load(cls, store: ArtifactStore, run_date: str) -> "BaseEmbedder": ...
```

Implement `LocalStore` now; leave `S3Store` as a stub that raises `NotImplementedError`. Write `tests/test_storage.py` against `LocalStore` with `tmp_path`.

**Done when:** `pytest tests/test_storage.py` passes.

---

### T1.3 — Ingest and text preparation
**Depends on:** T1.2
**Commit:** `feat: movie-dataset-setup`

`data/ingest.py`:
- `download(dest)` — Kaggle API, skip if the file is under 24 h old
- `load(path) -> DataFrame` — dtype-explicit read
- `prepare(df) -> DataFrame` — build the `document` column and apply the catalog filter

**Document text** is `title + tagline + overview + genres`, joined with a space and lowercased. Genres are included because they carry signal the overview often omits.

⚠️ **Methodological note that Phase 2 depends on:** if you include `keywords` in the document, you cannot then evaluate with keyword overlap — the model would have seen the labels. Resolve it this way and do not change it later:

- **`document` = title + tagline + overview + genres** (no keywords)
- **`keywords` is reserved as the held-out evaluation label**

**Catalog filter** (configurable, defaults in `config/default.yaml`):

```yaml
catalog:
  min_overview_chars: 40
  min_vote_count: 10
  exclude_adult: true
  status: ["Released"]
```

Report both counts in every run: rows ingested (~1.5M) and rows in the catalog (77,281). **Ingesting 1.5M and serving a curated subset is the correct design, and the gap between the two numbers is a data-quality talking point, not something to hide.**

**Done when:** `python -m movierec.data.ingest --input data/sample/movies_5k.csv --dry-run` prints ingested and catalog counts.

---

### T1.4 — `TfidfEmbedder`
**Depends on:** T1.3
**Commit:** `feat: tfidf-embedder-implementation`

Wrap `sklearn.feature_extraction.text.TfidfVectorizer`. Starting parameters in config:

```yaml
tfidf:
  max_features: 50000
  ngram_range: [1, 2]
  min_df: 3
  max_df: 0.6
  sublinear_tf: true
  stop_words: english
```

`save()` writes the fitted vectorizer (joblib) and the document matrix (`scipy.sparse.save_npz`) plus a `row_index.parquet` mapping matrix row → `movie_id`. **The row index is not optional** — without it, a matrix is unusable a week later.

**Done when:** `pytest tests/test_tfidf.py` passes, including a round-trip `save()` → `load()` → identical `transform()` output.

---

### T1.5 — Top-K neighbours
**Depends on:** T1.4
**Commit:** `feat: topk-neighbour-index`

`index/neighbours.py`: cosine similarity in **chunks of 5,000 query rows** against the full matrix, keeping the top K+1 and dropping self-matches. A full 200K × 200K dense similarity matrix is 160 GB; chunking is what makes this fit in 4 GB.

Output: `neighbours.parquet` with `movie_id, rank, neighbour_id, score`.

**Done when:** on the 5K sample, `make demo-tfidf` produces `artifacts/tfidf/{today}/neighbours.parquet`, and a manual spot check of 3 well-known movies looks sane. Record those 3 in `docs/comparison.md` as a scratch note.

---

### T1.6 — Data quality gates
**Depends on:** T1.5
**Commit:** `feat: data-quality-rules`

`data/quality.py`. Each gate returns pass/fail plus the observed value. **Failing gates abort the run before anything is written to S3 or DynamoDB.** A pipeline that publishes bad data is worse than one that stops.

Thresholds below are provisional; set real values Sep 18 from the full-catalog run.
```yaml
quality:
  row_count_min: 1300000
  row_count_drift_pct: 15        # first run has no baseline — gate skips with "no previous run"
  catalog_count_min: 70000
  null_rate_max:
    title: 0.001
    overview: 0.60               # expected to be high; the filter handles it
  duplicate_id_rate_max: 0.0
  embedding_nan_rate_max: 0.0
  zero_vector_rate_max: 0.02     # inert until Word2Vec lands in Phase 2
```

Write `quality_report.json` next to the metrics on every run, pass or fail.

**Done when:** `pytest tests/test_quality.py` passes, with a test per gate that deliberately feeds it failing data. **These tests matter more than any others in the repo** — an untested gate is a gate that will wave the bad run through.

---

### Sunday, Week 1 — `feat: tfidf-production-ready`
Run the full pipeline on the complete dataset locally. Record wall time and peak RSS in `docs/schema.md`. If peak RSS exceeds 4 GB, lower `max_features` and note it — the Fargate task size in §7 assumes 4 GiB.

---

## 5. Phase 2 — Word2Vec and evaluation (Week 2, Sep 21–27)

### T2.1 — `Word2VecEmbedder`
**Depends on:** T1.5
**Commit:** `feat: word2vec-embedder-class`

Use `gensim`. **Start with `glove-wiki-gigaword-100` (~130 MB), not `GoogleNews-vectors-negative300` (3.6 GB).** The GoogleNews model will not fit in the Fargate task alongside the corpus, and downloading it weekly is wasteful. If the comparison later shows the larger model is meaningfully better, that becomes a documented trade-off rather than a default.

Document vector = **IDF-weighted mean** of its word vectors, reusing the IDF weights from the fitted TF-IDF. A plain mean drowns the signal in stopword-adjacent words, and the weighted version is a stronger baseline for an honest comparison. L2-normalise, then store as `float16` — halves the artifact to ~23 MB for the full run directory at 77,281 rows (15.5 MB matrix.npy plus neighbours.parquet, embedder.json, row_index.parquet).
**Done when:** `pytest tests/test_word2vec.py` passes, including OOV handling (a document where no token is in the vocabulary must return a zero vector, not raise).

---

### T2.2 — Comparison harness
**Depends on:** T2.1
**Commit:** `feat: embedding-comparison-framework`

`eval/metrics.py` runs any list of embedders over the same catalog and produces one row per method. This is the file that makes the project a comparison rather than two scripts.

**Done when:** `python -m movierec.eval --compare --sample` runs both embedders over the same catalog and writes one row per method to `artifacts/comparison/{run_date}/comparison.json`, each row carrying `method`, `catalog_size`, `fit_seconds`, `peak_rss_mb`, `artifact_mb`, `oov_rate`, `median_idf_fallback_rate`. `pytest tests/test_metrics.py` passes.

---

### T2.3 — Metrics
**Depends on:** T2.2
**Commit:** `feat: retrieval-accuracy-measurement`

Three metrics, evaluated on a fixed sample of **2,000 query movies** with `vote_count ≥ 50`, seeded so the number is reproducible across runs.

**Primary — Keyword Jaccard@10.** Mean over queries of the mean Jaccard similarity between the query's keyword set and each of its top-10 neighbours' keyword sets. This is a genuine held-out metric: keywords are human-curated on TMDB and were deliberately excluded from the document text in T1.3.

**Secondary — Genre Precision@10.** Fraction of top-10 neighbours sharing at least one genre with the query. Genres *are* in the document text, so this measures whether the model learned what it was shown. Report it, but never lead with it.

**Tertiary — latency.** p50 and p95 for a single top-10 lookup, plus total fit time and artifact size on disk.

Also record a **random baseline** for both accuracy metrics. A number without a floor is not a result, and "TF-IDF scores 0.31" means nothing until the reader knows random scores 0.04.

**Done when:** `python -m movierec.eval --sample` writes `metrics.json` containing all three methods (tfidf, word2vec, random) × three metrics.

---

### T2.4 — Franchise recall, if available
**Depends on:** T2.3, and on T1.1 confirming a collection/franchise column exists
**Commit:** `feat: franchise-recall-metric`

If the dataset has collection membership, add **Recall@10 over franchise mates** — for a query in a collection with *n* other members, what fraction appear in the top 10. This is the only metric here with unambiguous ground truth, so it is worth the extra task.

If the column does not exist, skip this task and record the skip in §9. Do not fabricate franchise labels from title prefixes; the false-positive rate is high enough to make the metric meaningless.

---

### Sunday, Week 2 — `docs: embedding-comparison-report`
Write `docs/comparison.md`: the metrics table, two charts (accuracy by method, latency vs. accuracy), and a **decision paragraph** naming which embedder ships as default and why. A comparison that does not end in a decision is an unfinished comparison.

---

## 6. Phase 3 — Tuning and quality gates (Week 3, Sep 22–28)
***Dropped Sep 15 — two Sundays lost and Phase 1 slipped a week.
Prerequisites §8 named this as the droppable work. Two items are not
tuning and survive: T3.5 quality gates → T1.6 (Fri Sep 18, thresholds
set Sep 18 after the full run); the Week 3 Sunday runbook →
Phase 4, T4.7.***

### T3.1 / T3.2 — TF-IDF sweep
**Commits:** `feat: tfidf-parameter-analysis`, `feat: tfidf-optimization`

Sweep `max_features` ∈ {10K, 25K, 50K, 100K} × `ngram_range` ∈ {(1,1), (1,2)}. Eight runs on a 50K-movie subsample. Record every run in `docs/comparison.md`, including the ones that lost — a table where every row improved on the last is a table nobody believes. Promote the winner into `config/default.yaml`.

### T3.3 / T3.4 — Word2Vec sweep
**Commits:** `feat: word2vec-parameter-analysis`, `feat: word2vec-optimization`

Compare: pretrained GloVe-100 vs. GloVe-300 vs. a Word2Vec trained from scratch on the movie corpus. Domain-trained embeddings often beat general-purpose ones on a narrow corpus — if that happens here it is the most interesting finding in the project, so give it its own paragraph.

### T3.5 — Data quality gates
**Depends on:** T3.4
**Commit:** `feat: data-quality-rules`

`data/quality.py`. Each gate returns pass/fail plus the observed value. **Failing gates abort the run before anything is written to S3 or DynamoDB.** A pipeline that publishes bad data is worse than one that stops.

```yaml
quality:
  row_count_min: 800000
  row_count_drift_pct: 15        # vs. previous run
  catalog_count_min: 50000
  null_rate_max:
    title: 0.001
    overview: 0.60               # expected to be high; the filter handles it
  duplicate_id_rate_max: 0.0
  embedding_nan_rate_max: 0.0
  zero_vector_rate_max: 0.02     # word2vec OOV documents
```

Write `quality_report.json` next to the metrics on every run, pass or fail.

**Done when:** `pytest tests/test_quality.py` passes, with a test per gate that deliberately feeds it failing data. **These tests matter more than any others in the repo** — an untested gate is a gate that will wave the bad run through.

### Sunday, Week 3 — `docs: production-deployment-guide`
Write the runbook: how to trigger a run manually, how to roll back `current.json`, what each alert means, what to do when a gate fails.

---

## 7. Phase 4 — AWS (Sep 28 – Oct 23)

**Revised schedule (2026-10-08).** T4.1–T4.3 are done. Priority is a live
T4.4 site before the Oct 15 career fair; automation and documentation follow.

| Date | Budget | Work |
|---|---|---|
| Fri Oct 9 | 30 min | T4.4a: title index + Flask API (`/api/search`, `/api/similar`) with tests; local run on port 8000 |
| Mon Oct 12 | 30 min | T4.4a finish: minimal page (search, poster grid, method toggle); `Dockerfile.api` with the Mangum adapter, tested locally |
| Tue Oct 13 | 30 min | T4.4b: push arm64 API image; Terraform Lambda + IAM role + Function URL (reserved concurrency capped); Done-when check |
| Wed Oct 14 | 30 min | Buffer for T4.4. If done: verify the live site, nothing new built |
| Sat Oct 17 | 30–45 min | T4.5 schedule + alerting |
| Sun Oct 18 | 30–45 min | Check the 02:00 scheduled run; T4.6 `make demo` + README; Week 5 summary + HANDOFF |
| Mon–Fri Oct 19–23 | 15 min/day | T4.6 finish, T4.7 runbook, deferred cleanups. **Deadline Fri Oct 23** |

**Before T4.1:** create the AWS account (this project uses the **Free plan** — see §9 2026-09-28; the Free plan ends 2027-03-10, after which the account must be upgraded to Paid to keep running), enable MFA on root, create an IAM admin user, and set a **$2 budget alert**.

### T4.1 — Terraform base
**Commit:** `feat: terraform-base-infra`

`infra/` defines, in `us-east-1`:

- S3 bucket, versioning on, lifecycle rule expiring `artifacts/*` after 60 days
- DynamoDB table `movies`, PK `movie_id` (String), **on-demand** billing
- ECR repositories for both images, with a lifecycle policy keeping the last 5 tags
- IAM task role scoped to that one bucket and that one table — no wildcards
- Subnets, security group with egress only

**Done when:** `terraform plan` is clean, and `terraform apply && terraform destroy` round-trips without manual console cleanup.

⚠️ Do not create: NAT Gateway, ALB, VPC endpoints, Elastic IP. Any of these alone costs more than the whole rest of the project.

### T4.2 — `S3Store` and the DynamoDB writer
**Commit:** `feat: s3-store-and-dynamo-writer`

Implement `S3Store` against the same tests `LocalStore` passes — the test module should be parametrised over both backends, which is the whole point of the abstraction.

DynamoDB item shape:

```json
{
  "movie_id": "27205",
  "title": "Inception", "year": 2010,
  "poster_path": "/edv5CZvWj09upOsy2Y6IwDhK8bt.jpg",
  "vote_average": 8.4,
  "overview_short": "first 200 chars…",
  "genres": ["Action", "Science Fiction"],
  "similar_tfidf":    [{"id": "155", "score": 0.41}, "… 10 total"],
  "similar_word2vec": [{"id": "1124", "score": 0.88}, "… 10 total"]
}
```

Both methods live in the same item so the site can switch embedders with no rewrite. Load with `BatchWriteItem` (25 per call) and exponential backoff on throttling.

Posters render from `https://image.tmdb.org/t/p/w200{poster_path}` — no API key, no TMDB call at request time.

### T4.3 — Batch container
**Commit:** `feat: fargate-batch-task`

`Dockerfile` builds an **arm64** image whose entrypoint is `timeout 45m python -m movierec.pipeline`; `run_date` defaults to today in UTC, and the timeout bounds a hung task because ECS has none. The identical image must run locally with `STORAGE_BACKEND=local`. Terraform adds the ECS cluster, task definition (ARM64/Graviton via `runtime_platform`, 2 vCPU / 4 GiB, default 20 GB ephemeral), and CloudWatch log group (14-day retention). Base image python:3.12-slim, matching the local venv.

**Done when:** a manual `aws ecs run-task` completes, and S3 plus DynamoDB both contain the run.

### T4.4 — API
**Commit:** `feat: flask-embedder-integration`

`Dockerfile.api` → Lambda container image (arm64, like the batch image) → Function URL. Flask via Mangum.

Split in two, one commit at the end of T4.4b: **T4.4a** (local only, $0 beyond
a few DynamoDB reads): title index, Flask app, page, tests, local run on port
8000. Flask and rapidfuzz (later Mangum and the adapter) go in an optional
`[api]` extra in `pyproject.toml`, so the batch image does not grow. The
default method comes from `pipeline.default_method` in config, not from
`current.json`, so the API needs no S3 access at runtime. The title index is
built by a plain `build_title_index()` plus a CLI and **bundled in the API
image** for now; safe while the dataset is frozen (§9 2026-10-07), because the
catalog cannot drift from DynamoDB. After Oct 15 it moves to the pipeline:
each run writes `catalog/{run_date}/titles.json.gz` to S3 and the Lambda loads
the run `current.json` points to at cold start, so search always matches
DynamoDB and rollback covers it.
**T4.4b**: image, Terraform, Function URL. The Function URL is public with no
auth, so the Lambda gets a **reserved concurrency cap** (about 5) to bound
Lambda and DynamoDB read cost if the URL is abused. Mangum serves ASGI and
Flask is WSGI, so a WSGI→ASGI wrapper is needed; choose it in T4.4b.

- `GET /api/search?q=incep` → title suggestions. `rapidfuzz.process.extract` over the title index loaded from `titles.json.gz` at cold start (~100–200K entries, a few MB — this is why the API is a container image rather than a zip).
- `GET /api/similar/{movie_id}?method=tfidf` → one DynamoDB `GetItem`, hydrate the 10 neighbour IDs with `BatchGetItem`, return with display fields.
- `GET /` → `static/index.html`: a search box, a suggestion list, a result grid, and a method toggle.

Ship the **method toggle in the UI.** Letting an interviewer flip between TF-IDF and Word2Vec on the live site and see the results change is worth more than the comparison report they will not open.

**Done when:** searching "inception" on the Function URL returns 10 posters in under 2 seconds warm.

### T4.5 — Schedule and alerting
**Commit:** `feat: weekly-refresh-automation`

EventBridge Scheduler, `cron(0 2 ? * SUN *)`, target `ecs:RunTask` with `assignPublicIp: ENABLED`. SNS topic → email, subscribed to a CloudWatch alarm on task failures and on the `quality_gate_failed` metric.

**Done when:** a manually triggered schedule runs the task end to end, and a deliberately failing gate produces an email.

### T4.6 — `make demo` and README
**Commit:** `docs: final-documentation`

`make demo` must, on a clean clone with no AWS credentials and no Kaggle key:

```
pip install -e . && \
python -m movierec.pipeline --input data/sample/movies_5k.csv --storage local --config config/sample.yaml && \
python -m movierec.api --local
```

and open a working site on `localhost:8000` in under two minutes.

**This target is the single highest-leverage thing in the repo.** Most portfolio projects cannot be run by the person evaluating them. Yours can.

README order: one-sentence description → architecture diagram → `make demo` → results table → cost breakdown → AWS deployment. Installation instructions go last; nobody is installing this before deciding whether they care.

---

### T4.7 — Production runbook
**Depends on:** T4.5
**Commit:** `docs: production-deployment-guide`

`docs/runbook.md`: how to trigger a run manually, how to roll back
`current.json`, what each alert means, what to do when a gate fails.

**Done when:** a reader who has never seen the project can trigger a
run and roll back a bad one using only this file.

---

## 8. Interview material this produces

Keep these in the README under "Engineering notes" — reviewers read that section, and it is where the reasoning lives that a bullet point cannot carry.

- **Rightsizing.** v1 ran a 6-node Dataproc cluster for a gigabyte of text at $30–50/month. v2 does the same work in a 20-minute Fargate task for under $1. The story is that you profiled it and found the workload fit in 4 GB. Cost-consciousness is a screened-for data engineering skill that almost no student candidate can demonstrate.
- **Held-out evaluation.** Keywords were deliberately withheld from the document text so they could serve as labels. Most content-based recommender projects evaluate on features the model was trained on and report an impressive meaningless number.
- **Quality gates with teeth.** The pipeline refuses to publish when the data looks wrong, and there is a test for every gate.
- **Atomic promotion.** `current.json` gives one-line rollback and doubles as the embedder switch.
- **Reproducibility.** Terraform for infrastructure, one Docker image for local and cloud, `make demo` for anyone with a laptop.

---

## 9. Deviations from this spec

Append here whenever reality differs. Date, task ID, what changed, why (one line per day).

- 2026-09-14 — T1.1 done. Source 1,495,113 rows not 930K. Catalog after
  §4 filters = 77,281 (guide said 100-200K); vote_count>=10 is the binding
  filter. Keywords kept as held-out label: 85.5% coverage at >=50, median 5,
  random-baseline Jaccard 0.0027. No collection column → T2.4 skipped.
  Python 3.12.5 local, container base moved to 3.12-slim. Whitespace
  collapsed on sample write; T1.3 prepare() must match.
- 2026-09-15 — T1.2 done (9 tests, LocalStore + S3Store stub). Phase 3
  dropped: two Sundays unavailable (Sep 20, Oct 4) and Phase 1 slipped a
  week. Surviving items — T3.5 quality gates → T1.6 (Fri Sep 18); Week 3
  runbook → T4.7. Phase dates shifted: P1 Sep 14–19, P2 Sep 21–27,
  P4 Sep 28–Oct 5. Gate floors set from real data: row_count_min 800K →
  1.3M, catalog_count_min 50K → 70K.
- 2026-09-16 — T1.3 done. Sample catalog = 262 of 5,000 rows (decile
  stratification + min_vote_count 10). Correct behaviour, but thin for
  T4.6 `make demo`; revisit there, possibly with a separate demo sample.
- 2026-09-18 — T1.5 done (+ Makefile, which §3 listed but no task created).
  Full-catalog run: 1,495,113 ingested, 77,281 catalog, 772,810 neighbour
  rows. Peak RSS 6.27 GiB, over §7's 4 GiB assumption. Profiled per step:
  neighbours 6.06, ingest 3.53, embedder 3.07. usecols trim (24 → 13
  columns) cut ingest to 2.85 but left the overall peak unchanged. Real
  cause: a 5,000 × 77,281 float64 similarity block, 3.09 GB alone. Fixed by
  casting to float32 before the matmul and chunk_size 5000 → 2000 — full
  chain now 3.05 GiB and 112s, down from 127s. §7 stays at 4 GiB; no model
  change. Timeline extended to Oct 12; Phase 3 stays dropped.
- 2026-09-21 — T1.6 done. 24 quality tests, suite at 59. Raw file has
  1,356 duplicate IDs (458 exact, 898 differing); catalog had 0 only by
  luck of the filters. Explicit dedup added to prepare() after the filters,
  keeping max vote_count, original order preserved. duplicate_id_rate gate
  runs on the catalog, threshold 0.0. quality block was missing from
  default.yaml, added from §4. quality_report.json at
  artifacts/quality/{run_date}/.
- 2026-09-22 — T2.1 done. 18 new tests, suite at 77 (was 59). §5 artifact
  estimate corrected to ~15.5 MB. Median-IDF fallback is 53% on the 262-row
  sample, from min_df: 3. Should be far lower on the full catalog — VERIFY
  IN T2.2. If it stays high, the IDF weighting is barely doing anything.
  Hyphenated tokens split by the shared TF-IDF tokeniser (sci-fi → sci, fi).
  Measured on 20K docs: 4,681 forms, 1,795 in GloVe, top is "year-old" at
  505, most compositional. Under 1% of tokens, kept as-is. Hyphen-aware
  tokeniser is a T2.2 variant only if Word2Vec underperforms unexplainably.
- 2026-09-23 — T2.2 done. §5 had no Done-when; added. Suite at 96 (was
  77). compute_neighbours crashed on dense float16 (.tocsr/.todense are
  sparse-only); dense branch added, sparse path unchanged, 3 tests verified
  against the old code. BaseEmbedder gained coverage_stats() (None by
  default) so the runner stays method-agnostic; fit() now takes movie_ids.
  psutil added: ru_maxrss is process-lifetime, so per-method peak RSS needs
  a sampler (10ms). Full catalog, 77,281 rows, 148s: tfidf peak 2,969 MB /
  artifact 34.2 MB / fit 4.6s; word2vec peak 2,028 MB / artifact 23.1 MB /
  fit 16.7s / oov 1.4%. /usr/bin/time agrees within 0.4%. Median-IDF
  fallback RESOLVED: 4.3% full-catalog vs 52.6% on the 262-row sample —
  min_df: 3 was the sample artifact, the weighting is doing real work.
  §5's ~600 MB corrected to the measured ~23 MB. Word2Vec fit_seconds is
  mostly GloVe load; will shrink when Phase 4 sets a mmap model_path.
  Same-day sample and full runs share {method}/{run_date}/ and overwrite —
  worked around manually, run_date semantics deferred to Phase 4.
- 2026-09-24 — T2.3 done. 21 new tests, suite at 117 (was 96). Metrics
  score an existing run, never refit: `python -m movierec.eval` now scores
  (--run-date, default latest on disk); T2.2's refit moved behind
  --compare. Query set is min(2000, eligible); --sample gives 98 and
  warns. Queries with no keywords are SKIPPED, not zeroed (1,710 of 2,000
  scored). Latency = dict lookup of a precomputed list, ~100 ns for every
  method. Full 2026-09-23 run: keyword Jaccard@10 tfidf 0.0376, word2vec
  0.0241, random 0.0025 (T1.1: 0.0027); genre P@10 0.717 / 0.728 / 0.391.
  Written to comparison/{run_date}/metrics.json.
- 2026-09-25 — Buffer day; no task. Phase 4 toolchain installed: Docker
  28.0.4, AWS CLI 2.37.3, Terraform 1.13.3 (logged here as 1.16.4; see
  2026-09-28). Homebrew could not build awscli or terraform — Command Line Tools too old for source builds on Sonoma (23.6.0). 
  Both installed from official binaries instead: AWS CLI via
  AWSCLIV2.pkg, Terraform from releases.hashicorp.com to /usr/local/bin.
  Terraform upgrades are therefore a manual re-download, not `brew upgrade`.
  Homebrew also no longer carries terraform in core (HashiCorp licence
  change); hashicorp/tap exists but hits the same compiler wall. IAM user
  movierec-dev created with AdministratorAccess (deferred to Oct.12); aws configure set to us-east-1. Spend $0.00.
  Deferred: Command Line Tools update; Fargate is amd64 so T4.3 builds need
  --platform linux/amd64 on Apple Silicon.
- 2026-09-27 — Comparison report written to `docs/comparison.md`:
  table, two charts, decision paragraph. TF-IDF ships as default
  (Keyword Jaccard@10 0.0376 vs 0.0241, 15× the 0.0025 random floor;
  also faster to fit, 11 MB larger on disk). §5's second chart, latency
  vs accuracy, is degenerate, it was replaced with cost vs accuracy (fit_seconds and artifact_mb against Jaccard). 
  T1.5 spot check backfilled nine days late; §5's Done-when
  asks for the 262-row sample but Inception/Godfather/Toy Story are not
  in a 262-row catalog, so the three were taken from the 2026-09-23
  full-catalog run and the heading updated to match. Inception's
  neighbours are visibly weaker than the other two — noted in the report
  rather than hidden. §3 line 155 still says comparison.md is "written
  by T2.5"; no such task exists. `docs/week3_summary.md` written,
  HANDOFF.md "Where I am right now" rewritten. Phase 2 closed.
- 2026-09-28 - T4.1 split into 4.1a (S3/DynamoDB/ECR, plan only) and 4.1b (IAM/network, apply+destroy). There is still one commit, made at the end of 4.1b.
  Terraform was 1.13.3, not the 1.16.4 logged on 09-25, so `~> 1.16`
  failed init. Cause: /usr/local/bin/terraform itself was 1.13.3 and the
  only terraform on PATH (no Homebrew copy exists), so the 09-25 download
  was the wrong release or was misrecorded; which one is unknown.
  Replaced with 1.16.4 today. S3 lifecycle: §7's `artifacts/*` became the
  prefix `artifacts/` (S3 filters take no globs). Noncurrent-version
  expiry after 7 days and abort-incomplete-multipart-upload after 1 day go
  beyond §7, which asks only for 60-day expiry.
- 2026-09-28  T4.1a done: `terraform plan` → 9 to add; forbidden-resource grep empty.
  T4.1b (IAM task role, default-VPC data sources, egress-only SG, apply && destroy, single commit) scheduled for 2026-09-29. ECS execution
  role deferred to T4.3 with the task definition. Account stays on the Free plan, not §7's Paid plan; §7 updated. 
  Setting TF-IDF as default in config/default.yaml moved to T4.3, where pipeline.py first reads it.
  Versioned-bucket expiry leaves zero-byte delete markers under artifacts/; negligible cost, accepted.
- 2026-09-29  T4.1b done; plan showed 13 to add; apply and destroy round-tripped with no console cleanup; subnets are plural, not §7's singular; T4.1 complete.
- 2026-09-30  T4.2 split into 4.2a (S3Store, tests parametrised over both backends with moto, real-bucket smoke test) and 4.2b (DynamoDB writer). There is one commit, at the end of 4.2b. Also note that the suite uses moto rather than real AWS (see below).
- 2026-10-01   Oct 1 skipped; T4.2b moved to Oct 2; the revised schedule (table above) keeps Oct 12 as buffer.  
- 2026-10-02  T4.2b done: storage/dynamo.py (build_items + DynamoWriter), 18 tests, suite at 144. §7 T4.2 has no Done-when; used the T4.2b brief (pytest green + size measurement). The catalog is not persisted under artifacts/, so items were built from ingest.load+prepare on data/raw (77,281 rows, ids identical to the 2026-09-23 row_index); T4.3 pipeline must hold the catalog in memory or save it. Full-catalog items: max 968 B, avg 779 B, all under 1 KB, so 1 WCU each (~77K WCU per full load, about $0.05 on-demand). Missing attrs are left out: year 26, poster_path 641, genres 770. New dynamo block in default.yaml (max_retries 8, base_delay 0.1, full jitter); env MOVIEREC_DYNAMO_MAX_RETRIES / _BASE_DELAY override. The 25-item batch and 400 KB limits are service limits, kept as module constants. A catalog movie with no neighbours for a method raises instead of being written partially. year and genres are derived from release_date[:4] and a ", " split.
- 2026-10-06  Oct 5 skipped, T4.3 split into 4.3a (local container) and 4.3b (AWS run), with one commit at the end of 4.3b. "state list = 18 entries = 13 managed + 5 data sources; plan counts managed only". T4.3a done, tests 144 → 157. storage/factory.py get_store(): STORAGE_BACKEND and MOVIEREC_S3_BUCKET; precedence CLI > env > --config overlay > default.yaml > code (the overlay reaches the blocks pipeline.py passes down; get_store() and DynamoWriter's retry settings read default.yaml directly). default.yaml gains pipeline.default_method: tfidf (from 09-28), storage {backend, bucket: null}, dynamo.table_name: movies. Sample gates: the 1.3M/70K floors fail the 5K sample (5,000 / 262) and no code path ran the gates before pipeline.py; config/sample.yaml overlay (row_count_min 5000, catalog_count_min 250), passed with --config and recorded as config_overlay in quality_report.json, is the one exception to CLAUDE.md's "thresholds live in default.yaml"; §7 T4.6 command updated. Drift gate compares against the latest earlier quality/ report in the same store, so sample and full runs must not share one; local artifacts/ holds sample runs only. Catalog persisted to catalog/{run_date}/catalog.parquet. gate_null_rate returned numpy bool_, which crashed enforce() on its first real call; cast to float, regression test added. Embedding gates run per method (tfidf./word2vec.<gate>), after fitting and before any write. run_date defaults to today in UTC. Container config path: pip install -e . in the image, so modules resolve /app/config as on a laptop. The amd64 image hung importing scipy.stats under emulation (18 min, never logged a line). The 09-25 "Fargate is amd64" premise was wrong: Fargate runs ARM64 (Graviton), so we switched to ARM64 Fargate, which removes emulation and is about 20% cheaper; native arm64 build, §7 T4.3 updated. Entrypoint wrapped in timeout 45m because ECS has no task timeout (a hung run exits 124). Image 1.03 GB uncompressed / ~239 MB compressed (amd64 measurement; arm64 is 1.06 GB). Unpinned deps can add ~230 MB ECR layers per rebuild, so pinning is a cost issue too, not just reproducibility. Found for 4.3b: S3Store keys have no artifacts/ prefix, so the S3 lifecycle rule (prefix artifacts/) matches nothing yet. T4.3b steps 5–6 done (no commit yet). Kaggle issued a new-style KAGGLE_API_TOKEN, so the two username/key parameters were replaced by one, /movierec/kaggle_api_token (SecureString, aws/ssm key, $0), injected by the task definition's secrets; kaggle 2.2.4 checks KAGGLE_API_TOKEN before ~/.kaggle/access_token, which in turn takes precedence over KAGGLE_USERNAME/KAGGLE_KEY. The placeholder uses write-only value_wo, not the briefed value + ignore_changes: `value` is computed, so a refresh would read the decrypted token into tfstate; proven with a probe value and a refresh-only apply (0 hits in state). Only reliable key test: `kaggle datasets list --mine` showing shikristin/movie-recommendation-database-encoded; public datasets list without auth, and an invalid key returns "No datasets found", not 401. v1 repo's hardcoded key confirmed dead; scrub that file after Oct 12. GloVe (glove-100d.kv + .vectors.npy, 171 MB) uploaded to s3://<bucket>/models/; MOVIEREC_WORD2VEC_MODEL_PATH=s3://... is downloaded to /tmp via storage/s3.py download_with_sidecars (boto3 stays in storage/), 4 moto tests, suite 161. S3 lifecycle option A: the dead artifacts/ rule replaced by one rule per run prefix (tfidf/, word2vec/, catalog/, comparison/, quality/; 60 d current, 7 d noncurrent) plus a bucket-wide abort-multipart rule; current.json and models/ match no rule, so they and their versions never expire; §3 path symmetry kept. Rollback window ~60 days. ECS: cluster (Container Insights off), /ecs/movierec at 14 days, execution role (AmazonECSTaskExecutionRolePolicy + ssm:GetParameters on the one parameter), task definition movierec-batch:1 ARM64 2048/4096, env STORAGE_BACKEND=s3, MOVIEREC_S3_BUCKET, MOVIEREC_WORD2VEC_MODEL_PATH, AWS_DEFAULT_REGION, no --config. Ephemeral storage left at the 20 GB default, not §7's 30 GB (§7 updated). State: 20 managed resources + 6 data sources. *.tfplan added to .gitignore; plan files deleted after each apply.
- 2026-10-07  T4.3b step 7 done. Image rebuilt (s3.py and Dockerfile were edited after the 10-06 build; the dependency layer was cached, so no new ~230 MB layer) and pushed to ECR as t4.3-2026-10-07, the default of the Terraform var batch_image_tag, which the task definition's image tag reads. Compressed size 246.5 MB (12 layers), under the 500 MB ECR free allowance shared by both repos, leaving ~253 MB for the T4.4 API image. BuildKit's default provenance attestation turned that push into 3 ECR entries (an OCI image index + the arm64 image + an attestation manifest); with the T4.1 keep-last-5 lifecycle rule (tagStatus any), a later push could expire a tagged index's child and leave a tag that cannot be pulled. Fixed by building without attestations, no lifecycle change: Makefile `image` (docker buildx build --provenance=false --sbom=false) and `push-image TAG=...` (ECR login, tag, push; repo URL from terraform output). Re-pushed: the tag is now a plain arm64 image manifest (same config digest, every layer already present, no extra storage); the first push's index and attestation remain untagged and age out first under keep-last-5. On the containerd image store, `docker image inspect .Size` reports compressed size (~247 MB); `docker images` shows 1.06 GB unpacked. Step 8, first Fargate run: OOM-killed (exit 137, OutOfMemoryError) about 35 s into step 4. Kaggle token auth, the S3 GloVe download, ingest (1,510,866 raw rows; Kaggle's file grew 15,753 since Sep 14), gates and both fits all worked first; only quality/2026-10-07/quality_report.json was written. Reproduced locally in the same image at --memory=4g. Cause: prepare() copied the whole raw frame and built `document` for all ~1.5M rows before filtering to the 77K catalog; anonymous memory went 893 -> 2,849 MiB for a 66 MB catalog, and `del raw` + gc + malloc_trim recovered only ~100 MB (freed string memory is not returned by the allocator), so step 4 started at ~3.0 GiB and its ~1.2 GiB TF-IDF chunk spike crossed 4 GiB. September's "3.05 GiB fits" (09-18) was TF-IDF alone, run step by step on macOS, never the full pipeline.py chain under a Linux memory limit. Fix: prepare() applies the vote_count/status/adult filters first, collapses whitespace and applies the overview-length filter on the survivors, and builds `document` for catalog rows only; on the full Sep 14 file old vs new give identical frames (assert_frame_equal; id and document hashes match) and ids identical to the 09-23 row_index. index.chunk_size 2000 -> 1000 (09-18 set 5000 -> 2000): step 4's TF-IDF block is the run's peak and grows with the catalog, and 2000 left ~0.5 GiB of headroom. Full 8-step run at --memory=4g, peak anonymous memory: before the fix OOM at 4,096 MiB; prepare fix + chunk 2000 3.44-3.48 GiB; + chunk 1000 2.58 GiB (step 1 1,336 MiB, step 3 1,873, step 4 2,647); step 4 110.7 s vs 112.8 s locally; neighbours.parquet identical for both methods at 1000 vs 2000 (772,810 rows each, assert_frame_equal exact). memory.peak reads 4,096 even on passing runs because reclaimable file cache fills the cgroup; anon is the OOM-relevant number. Image rebuilt and re-pushed as t4.3-2026-10-07 (new src/ and config/ layers only; ECR still ~246.5 MB). Fargate rerun (task fc423002, us-east-1c): exit 0, all 8 steps, pipeline 5 min 29 s (task RUNNING 5 min 55 s): ingest 16.8 s (Kaggle download included), gates 0.1, fit 19.2 (S3 GloVe download included), neighbours 189.1, evaluate 4.2, write 5.7, DynamoDB 93.8 (77,281 items, 3,092 batches, 0 retries), current.json 0.1. All 10 gates passed, config_overlay null, 1,510,866 ingested / 77,281 catalog; metrics identical to 09-23 (Jaccard 0.0376 / 0.0241, genre P@10 0.717 / 0.728). Task role had every permission the run used (s3 Put/Get/List, dynamodb BatchWriteItem): no IAM change. Bucket root holds only the five run prefixes, models/ and current.json. Cost about $0.06: Fargate ~$0.009, DynamoDB writes ~$0.05, two COUNT scans used for verification ~$0.002 (full-table reads, should have been flagged first). Peak memory is not observable on Fargate without Container Insights (no per-task metrics for a standalone task), so the local --memory=4g measurement, 2.58 GiB, stands. Timeout stays 45m; 15m (~2.7x measured) proposed after a few weekly runs. Frozen dataset: the Oct catalog is the same 77,281 ids as 09-23 (0 entered, 0 left; row order differs), with identical vote_count and document for every movie, so the 15,753 new raw rows all fail the filters and existing rows are not refreshed; §3's claim that daily updates make the weekly refresh meaningful does not hold for this dataset as measured, corrected. §2 cost table replaced with measured numbers: run ~6 min not ~20, DynamoDB ~$0.05/run is the largest line not $0, total ~$0.28/month, meets < $0.50; the 10-02/HANDOFF "revisit §2 after real runs" item is resolved. T4.3 done.
- 2026-10-08  Timeline revised (school and job-search load). Deadline Oct 12 → Oct 23. Availability: Fri Oct 9 30 min; Mon–Wed Oct 12–14 30 min/day; Sat–Sun Oct 17–18 long sessions; weekdays from Oct 19. No work Oct 8, Oct 10–11, Oct 15–16. Priority: a live T4.4 site before the Oct 15 career fair; T4.5 → Oct 17, T4.6 → Oct 18, T4.7 and deferred cleanups → Oct 19–23. T4.4 split into 4.4a (local) / 4.4b (AWS) with one commit. T4.4 gains a reserved-concurrency cap on the public Function URL and an arm64 API image. Header, §7 dates and the §7 revised-schedule table updated. Items deferred "until after Oct 12" (v1 key scrub, movierec-dev least privilege, DynamoDB skip-if-unchanged, dependency pinning, 15m timeout) now mean after the T4.x tasks, Oct 19–23 or later.
- 2026-10-09  T4.4 decisions before T4.4a: local API port back to 8000 (the other project's container that held it was stopped; the 10-08 note about 8001 is withdrawn, §7 and HANDOFF updated). API dependencies in an optional `[api]` extra. Default method read from config (`pipeline.default_method`), not `current.json`. Title index: plain `build_title_index()` + CLI, bundled in the API image for the Oct 15 fair; moves to pipeline + S3 (`catalog/{run_date}/titles.json.gz`, loaded via `current.json` at cold start) after Oct 15, because a bundled index can drift from DynamoDB once the catalog changes.
