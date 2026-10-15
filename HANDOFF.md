# Project handoff brief

*Paste this as the first message in the new account, with the three attached files.*

---

I'm continuing a project from a previous Claude conversation. Here's the context, the decisions already made, and where I am. Please read the three attached files before responding.

**Attached:**
- `IMPLEMENTATION.md` — the task-by-task spec (source of truth)
- `PREREQUISITES.md` — setup checklist
- `CLOUD_PRIMER.md` — cloud fundamentals + interview prep

---

---

## The project

A content-based movie recommender. Version 1 exists: TF-IDF embeddings, a Flask website, and a weekly job on GCP Dataproc/Spark. The GCP free trial has ended, so I'm rebuilding.

**User story:** visitor types a movie name, gets 10 similar movies with poster, year, rating, and a short overview.

---

## Decisions already made — do not relitigate these unless I ask

**1. AWS, not GCP or Azure.**
I priced all three. GCP's always-free tier is actually the most generous and Azure's Container Registry alone costs ~$5/month with no free tier. I chose AWS anyway because it appears in far more Data Engineer postings and I have prior exposure. The cost difference is under $1/month; the career difference is not.

**2. No Spark. No cluster.**
V1 ran a 6-node Dataproc cluster on ~930K movie records at $30–50/month. That's under a gigabyte of text — it fits in 4 GB of RAM. V2 is a single ~20-minute Fargate task. The rightsizing story is deliberate portfolio material, not an embarrassment.

**3. Storage was the wrong framing.**
My original reason for using cloud was that embeddings filled my laptop. But cloud storage is ~$0.023/GB-month — 3 GB costs 7 cents. The real fix was architectural: precompute top-10 neighbours in the batch job, so the big matrices only exist for the 20 minutes the task runs, and the serving layer never loads a matrix at all.

**4. The user types a title, not a free-text query.**
So there's nothing to embed at request time. Serving is a DynamoDB key lookup. This is why the API fits on Lambda.

**5. Keywords are held out as evaluation labels.**
TMDB keyword tags are deliberately excluded from the text the embeddings are built from, so they can serve as genuine ground truth for Keyword Jaccard@10. **This constraint propagates through the whole project** — if keywords get added back into the document text, the primary metric becomes circular.

**6. Public subnet with a security group, not private + NAT Gateway.**
A NAT Gateway is ~$32/month, roughly 60× the rest of the project. The batch task listens on no ports, so there's nothing to reach.

---

## Target architecture

```
EventBridge Scheduler (Sun 02:00)
  └─> ECS Fargate task (2 vCPU / 4 GiB, ~20 min, public subnet, no NAT)
        download Kaggle → quality gates → fit TF-IDF + Word2Vec
        → top-K neighbours → evaluate → S3 artifacts + DynamoDB
        → flip current.json
  └─> CloudWatch logs, SNS email on failure

Lambda (container image) + Function URL
  Flask via Mangum, rapidfuzz title search, DynamoDB lookup
```

Target cost: under $0.50/month. Everything is Terraform.

---

## Where I am right now

*Last updated: Wednesday, Oct 14, 2026 — Week 6*

**Status:** Phases 1 and 2 complete. T4.1–T4.3 committed. **T4.4 done**:
the API was deployed and committed on Oct 12, and the page plus 1024 MB
landed Oct 14 as a separate follow-up commit. **Done-when passed Oct 14**,
checked on a phone and a laptop (§9 2026-10-14). The site is **live and
ready to share** at the fair. Infra is live in us-east-1 (T4.1–T4.3
resources + 7 for the API) and stays up.

- **Function URL:** https://rueyegqfm4fjepd2vbstidhkti0crznq.lambda-url.us-east-1.on.aws/
- **Live image:** `movierec-api:t4.4-2026-10-14b`, digest
  `sha256:7cde4be0fb169c8174383e7e45f51cc36466eb7e4e571ad69584130e9bed782d`
  (`api_image_tag` default). Earlier: `t4.4-2026-10-14` (`f28f426c…`, page
  + memory), `t4.4-2026-10-12` (`f5c52ba9…`, API only). Each is one plain
  arm64 Docker v2 manifest, ~67.2 MB

**How the API is built (§9 2026-10-12):**
- Slim arm64 Lambda image, no batch deps (guarded by
  `tests/test_api_imports.py`). `Dockerfile.api` pins the serving deps;
  build `make image-api`, push `make push-image-api TAG=...` with a **new
  tag every time**, then set `api_image_tag`'s default and apply
- Mangum + a2wsgi `WSGIMiddleware` in `api/lambda_handler.py`; awslambdaric
  entrypoint; title index bundled at `/app/titles.json.gz`
- Search: fuzzy-tail length rule, `api.min_fuzzy_length_ratio: 0.4`
- `infra/lambda.tf`: role (DynamoDB read on `movies`, own logs, ECR pull on
  `movierec-api`), log group (14 d), function (arm64, 1024 MB, 10 s, no VPC,
  no reserved concurrency), public Function URL + its two permissions

**What Oct 14 delivered (§9 2026-10-14):**
- The page, `api/static/index.html`, built fresh: the file was an empty
  skeleton, not v1's page. One self-contained file, textContent only (no
  innerHTML), relative `/api/` URLs. It has a start screen with 4 example
  films (Inception, Toy Story, The Godfather, The Dark Knight), a hero for
  the picked movie with a "Read the full overview on TMDB →" link, and 10
  ranked cards (#1–#10, no % match). Tapping a card makes it the hero, with
  "← back to …". Cards show "Shared genres: …", never keywords
- Toggle "Match by: Similar details" (tfidf, default) / "Similar feel"
  (word2vec), with a "How does this work?" note
- `/api/similar`'s `query` block gained rating, poster_path, overview and
  genres (no extra DynamoDB calls)
- Memory 512 → 1024 MB. Init 1,892 ms (was 4,157); warm search 267–286 ms
  handler / ~0.4 s client (was 627–777 ms / ~0.7 s); `/api/similar` 17–61 ms;
  peak memory 166 MB. Cheaper per search than at 512 MB
- Done-when reading: 10 result cards, with posters wherever the data has a
  `poster_path`. Inception's default view has 9 posters + 1 placeholder
  (Sonic: The Birth of an Icon has none); Similar feel shows 10
- Cold starts tonight: 2.64 s (Init 1,892 ms) after the first deploy, then
  5.39 s (Init 4,881 ms) after the TMDB-link deploy at the same memory.
  **Cause unverified.** Hypothesis: the first pull after deploying a new
  image. Re-check on a later cold start
- Tests: 223

**Spend:** the Oct 7 run cost $0.06, confirmed on the bill. §2's cost table
holds measured numbers: ~$0.28/month weekly, DynamoDB the largest line. The
API adds ~$0 at fair traffic (Lambda, Function URL and logs in the free
tier). ECR is ~316.5 MB of the 500 MB free allowance (API 69.9 MB for 3
images deduplicated, batch ~246.6 MB); each code-only API rebuild adds
~1.4 MB.

**Schedule:** deadline **Fri Oct 23** (§9 2026-10-08; table in §7).
- Mon Oct 12 – Wed Oct 14: T4.4 done
- **Thu Oct 15 (fair):** open the site once before showing it. Cold start
  is 2.6–5.4 s, then warm
- **Next: Sat Oct 17 (30–45 min): T4.5 schedule + alerting, including a
  CloudWatch alarm on the API function's `Invocations`**, emailing via the
  same SNS topic. When you're there, note the first cold start's Init
  Duration (re-checks the 4.9 s hypothesis)
- Sun Oct 18: check the 02:00 scheduled run; T4.6 `make demo` + README;
  Week 6 summary + HANDOFF (Week 5 summary written Oct 9)
- Mon–Fri Oct 19–23 (15 min/day): T4.6 finish, T4.7 runbook, deferred cleanups
- No work: Oct 15–16

**Carried to T4.5 / T4.6 and after Oct 15:**
- Port 8000 is free; T4.6's `make demo` uses `localhost:8000`. Local server
  binds 127.0.0.1 unless `MOVIEREC_API_HOST` is set (0.0.0.0 in a container)
- A local `docker run` of the API image needs `AWS_DEFAULT_REGION`: boto3
  ignores `AWS_REGION`, which Lambda sets alongside it
- Title index is **bundled in the API image** for the fair (safe while the
  dataset is frozen). **After Oct 15:** move it to the pipeline
  (`catalog/{run_date}/titles.json.gz` in S3, loaded via `current.json` at
  cold start) so search can't drift from DynamoDB
- **Concurrency (§9 2026-10-12):** the account's Lambda limit is **10**, and
  AWS refuses a reservation that leaves fewer than 10 unreserved, so §7's
  cap of ~5 can't be set. The account limit is the ceiling;
  `api_reserved_concurrency` stays `null`. Never raise the account limit
  without applying a cap in the same change
- Search, still imperfect: "dark knight" ranks Dark Night (2017) above The
  Dark Knight; for "dark night" The Dark Knight is 10th; "jaws the movie"
  loses Jaws to the length rule (deferred tie-break, below)
- 49 catalog titles are under 2 characters (M, X, Z, 8, 9, $ …): exact
  matches come first for every query, and under 2 characters only exact
  matches are returned

**The embedder decision** (full report in `docs/comparison.md`):
TF-IDF ships. Keyword Jaccard@10 of 0.0376 against Word2Vec's 0.0241 — a
56% margin, 15× the 0.0025 random floor. Genre P@10 was 0.717 vs 0.728,
too close to separate them and measured on features both models were
trained on. TF-IDF also fits faster (4.62s vs 16.74s); its only
disadvantage is 11 MB more on disk.

**Numbers from the real data** (full detail in `docs/schema.md`):
- 1,510,866 rows ingested (Oct 7; 1,495,113 on Sep 14); 77,281 in catalog;
  772,810 neighbour rows
- Raw file has 1,356 duplicate IDs; `prepare()` dedupes explicitly after
  the filters, keeping max `vote_count`
- Keyword coverage 85.5% at `vote_count >= 50`, median 5. 1,710 of 2,000
  sampled queries have keywords; the rest are skipped, not scored zero
- No collection column, so T2.4 is skipped — Keyword Jaccard@10 is the
  only held-out metric, with no fallback
- **Frozen dataset:** the Oct 7 catalog is the same 77,281 ids as Sep 23,
  with identical vote_count and documents. New raw rows all fail the
  filters, so a weekly refresh does not currently change recommendations
  (§3 corrected)
- Full pipeline at `--memory=4g`: peak 2.58 GiB (step 4). On Fargate:
  5.5 min, neighbours 189 s, DynamoDB load 94 s

**Environment:**
- venv `movie_rec_venv/` on Python 3.12.5; container base
  `python:3.12-slim`, built natively as **arm64** (no `--platform` flag):
  `make image`. Fargate runs it on ARM64/Graviton. amd64 under emulation hung importing scipy.stats
- Docker 28.0.4, AWS CLI 2.37.3, Terraform 1.16.4 (1.13.3 until Sep 28;
  the Sep 25 install was logged wrong). Homebrew could not
  build the CLIs (Command Line Tools too old for source builds on
  Sonoma) — both installed from official binaries, so Terraform upgrades
  are a manual re-download
- AWS: IAM user `movierec-dev`, us-east-1, ~$0.07 spent (Oct 7 run), $120 credits,
  $2 budget alert, Free plan ends Mar 10 2027
- GloVe at `~/glove/glove-100d.kv`; `MOVIEREC_WORD2VEC_MODEL_PATH` in
  `.env`
- boto3 and moto added.

**For the T4.7 runbook:** rollback reaches back ~60 days (run folders expire
at 60 d, their noncurrent copies 7 d later). The weekly-failure alarm (T4.5)
is what stops `current.json` from ever pointing at an expired run.
Also: watch peak memory as the catalog grows. Step 4's TF-IDF chunk is the
run's peak (2.58 GiB of 4 at 77K movies, chunk_size 1000); a growing catalog
raises it, and an OOM shows up as exit 137 in `describe-tasks` (§9 2026-10-07).
API (§9 2026-10-12):
- **Kill switch:** `aws lambda put-function-concurrency --function-name
  movierec-api --reserved-concurrent-executions 0` throttles every request
  (always permitted), or set `api_reserved_concurrency = 0` and apply. Undo:
  `aws lambda delete-function-concurrency --function-name movierec-api`, or
  back to `null` and apply. The CLI route drifts from Terraform until the
  variable matches
- **Image tags:** Lambda pins the image digest at deploy and ECR tags are
  mutable. Re-pushing a tag does not update the function and `terraform
  plan` shows no change: every rebuild gets a new tag, applied through
  `api_image_tag`. Same rule for `batch_image_tag`
- **Keep-last-5 counts pushes, not deploys:** never push 5 API images
  without applying, or the image the live function is pinned to can expire

**Timeout tuning:** the entrypoint stays `timeout 45m`; propose 15m
(~2.7× the measured 5.5 min) after a few weekly runs. Needs a rebuild + push.

**Deferred, with a home:** `run_date` semantics — same-day sample and
full runs share a folder and overwrite (run_date is now UTC; full runs
move to S3, local `artifacts/` holds sample runs only). Dependency
pinning — now a cost issue too: each unpinned rebuild can add a ~230 MB
ECR layer against the 500 MB free allowance; includes pinning the
`python:3.12-slim` base image by digest (it ships Python 3.12.15, the venv
3.12.5). Search tie-break by vote_count (needs vote_count in the title
index); do it with the post-Oct 15 move of the index into the pipeline.
Likely also fixes the "jaws the movie" loss. Least-privilege IAM for
movierec-dev, Oct 19–23 or later; with it, import the
`LambdaECRImageRetrievalPolicy` repository policy Lambda added to
`movierec-api` into Terraform and remove the role's PullApiImage statement
if it's confirmed unused. Delete markers under artifacts/
accepted at negligible cost. The 262-row demo catalog is revisited at
T4.6. **Oct 19–23 or later:** skip the DynamoDB load when the catalog and
neighbours are unchanged from the previous run; it is ~85% of the per-run
cost, and with the frozen dataset every run is currently unchanged.
**After Oct 15, with the title-index move:** store the full overview in
each DynamoDB item, so the hero uses it and cards keep `overview_short`
(today it's a hard 200-character cut and the page trims it to a word).
**Cost test first**, locally with `build_items` at no AWS cost: item-size
distribution with the full overview, count over 1 KB, total WCUs for one
full load, against today's 77,281 WCU (~$0.05/run, ~$0.21/month). Decide
on the numbers; pair it with skip-the-load-when-unchanged if writes grow.
Also deferred: a "matched on: …" shared-words explanation, computed in the
pipeline.

**Reference:** `docs/week3_summary.md` and `docs/week4_summary.md`, §9 of
`IMPLEMENTATION.md` for every deviation from spec.

---

## How I want you to work with me

- The implementation.md is the source of truth. One `T#.#` task per session. Don't skip ahead of a task's dependencies.
- If reality contradicts the spec — a column is missing, a count is off — update the guide and log it under §9 Deviations rather than working around it silently.
- Explain cloud concepts when they come up. I'd rather understand the thing than have it work.
- Push back if I'm over-engineering. That's the mistake that produced v1.
- Flag anything that could cost money before I run it.
- Never commit or push. Stop at `git status --short` and the list of files to add; I commit and push myself.
