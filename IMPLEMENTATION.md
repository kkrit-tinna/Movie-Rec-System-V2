# Movie Recommender v2 — Implementation Guide (AWS)

**Status:** spec for a 5-week upgrade, Sep 8 – Oct 12, 2026
**Budget:** 15 min/day Mon–Fri; the 30–45 min long-session budget applies
to Sun Sep 27, Sat Oct 10, and Sun Oct 11 only.
**Audience:** the author, and Claude Code
**Schedule:** Phase 1 slipped one week; Phase 3 dropped Sep 15 and stays
dropped — the extra week is buffer for Phase 4, not a Phase 3 reinstatement.
No work Saturdays except Sat Oct 10. No work Sun Sep 20 or Sun Oct 4.
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
   Scheduler   ───► │  2 vCPU / 4 GiB, ~20 min     │
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

| Line | Monthly |
|---|---|
| Fargate — 4 runs × ~20 min × 2 vCPU / 4 GiB | ~$0.15 |
| S3 — ~3 GB + requests | ~$0.08 |
| ECR — image above the 500 MB free allowance | ~$0.10 |
| DynamoDB, Lambda, EventBridge, CloudWatch | $0 (always free) |
| **Total** | **< $0.50** |

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

`asaniczka/tmdb-movies-dataset-2023-930k-movies` on Kaggle. It is **updated daily**, which is what makes a weekly refresh honest rather than decorative. The slug's 930K is the publication-time count and the file now carries about 1.5M rows.

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

Document vector = **IDF-weighted mean** of its word vectors, reusing the IDF weights from the fitted TF-IDF. A plain mean drowns the signal in stopword-adjacent words, and the weighted version is a stronger baseline for an honest comparison. L2-normalise, then store as `float16` — halves the artifact to ~600 MB at 930K rows with no measurable effect on ranking.

**Done when:** `pytest tests/test_word2vec.py` passes, including OOV handling (a document where no token is in the vocabulary must return a zero vector, not raise).

---

### T2.2 — Comparison harness
**Depends on:** T2.1
**Commit:** `feat: embedding-comparison-framework`

`eval/metrics.py` runs any list of embedders over the same catalog and produces one row per method. This is the file that makes the project a comparison rather than two scripts.

---

### T2.3 — Metrics
**Depends on:** T2.2
**Commit:** `feat: retrieval-accuracy-measurement`

Three metrics, evaluated on a fixed sample of **2,000 query movies** with `vote_count ≥ 50`, seeded so the number is reproducible across runs.

**Primary — Keyword Jaccard@10.** Mean over queries of the mean Jaccard similarity between the query's keyword set and each of its top-10 neighbours' keyword sets. This is a genuine held-out metric: keywords are human-curated on TMDB and were deliberately excluded from the document text in T1.3.

**Secondary — Genre Precision@10.** Fraction of top-10 neighbours sharing at least one genre with the query. Genres *are* in the document text, so this measures whether the model learned what it was shown. Report it, but never lead with it.

**Tertiary — latency.** p50 and p95 for a single top-10 lookup, plus total fit time and artifact size on disk.

Also record a **random baseline** for both accuracy metrics. A number without a floor is not a result, and "TF-IDF scores 0.31" means nothing until the reader knows random scores 0.04.

**Done when:** `python -m movierec.eval --sample` writes `metrics.json` containing all five methods-columns (tfidf, word2vec, random) × three metrics.

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

## 7. Phase 4 — AWS (Week 4, Sep 28 – Oct 11)

**Before T4.1:** create the AWS account choosing the **Paid plan** (credits still apply; the account will not close when they run out), enable MFA on root, create an IAM admin user, and set a **$2 budget alert**.

### T4.1 — Terraform base
**Commit:** `feat: terraform-base-infra`

`infra/` defines, in `us-east-1`:

- S3 bucket, versioning on, lifecycle rule expiring `artifacts/*` after 60 days
- DynamoDB table `movies`, PK `movie_id` (String), **on-demand** billing
- ECR repositories for both images, with a lifecycle policy keeping the last 5 tags
- IAM task role scoped to that one bucket and that one table — no wildcards
- Default VPC public subnet, security group with egress only

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

`Dockerfile` builds an image that runs `python -m movierec.pipeline --run-date $(date +%F)`. The identical image must run locally with `STORAGE_BACKEND=local`. Terraform adds the ECS cluster, task definition (2 vCPU / 4 GiB, 30 GB ephemeral), and CloudWatch log group. Base image python:3.12-slim, matching the local venv.

**Done when:** a manual `aws ecs run-task` completes, and S3 plus DynamoDB both contain the run.

### T4.4 — API
**Commit:** `feat: flask-embedder-integration`

`Dockerfile.api` → Lambda container image → Function URL. Flask via Mangum.

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
python -m movierec.pipeline --input data/sample/movies_5k.csv --storage local && \
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
- 2026-09-22 — T2.1 done. 18 new tests, suite at 77 (was 59). §5 artifact
  estimate corrected to ~15.5 MB. Median-IDF fallback is 53% on the 262-row
  sample, from min_df: 3. Should be far lower on the full catalog — VERIFY
  IN T2.2. If it stays high, the IDF weighting is barely doing anything.
  Hyphenated tokens split by the shared TF-IDF tokeniser (sci-fi → sci, fi).
  Measured on 20K docs: 4,681 forms, 1,795 in GloVe, top is "year-old" at
  505, most compositional. Under 1% of tokens, kept as-is. Hyphen-aware
  tokeniser is a T2.2 variant only if Word2Vec underperforms unexplainably.