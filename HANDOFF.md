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

*Last updated: Thursday, Oct 8, 2026 — Week 5 (timeline revised)*

**Status:** Phases 1 and 2 complete. T4.1 (`feat: terraform-base-infra`)
and T4.2 (`feat: s3-store-and-dynamo-writer`) committed. **T4.3 done**
(one commit, `feat: fargate-batch-task`). Infrastructure is live in
us-east-1 (20 managed resources + 6 data sources) and stays up. 161 tests.

**What T4.3 delivered:** `pipeline.py` runs all 8 steps; arm64 image
`movierec-batch:t4.3-2026-10-07` in ECR (246.5 MB compressed, a plain image
manifest; build with `make image`, push with `make push-image TAG=...`,
both without BuildKit attestations); ECS cluster `movierec`, task definition
`movierec-batch:1` (ARM64, 2 vCPU / 4 GiB), Kaggle token from SSM, GloVe
from `s3://<bucket>/models/`. The first full Fargate run is live: exit 0 in
5.5 min, S3 run 2026-10-07, 77,281 items in DynamoDB, `current.json` →
tfidf. The first attempt was OOM-killed at step 4; fixed by filtering
before building documents in `prepare()` and `chunk_size` 1000 (peak
2.58 GiB of 4, measured locally at `--memory=4g`; §9 2026-10-07).

**Spend:** today's run about $0.06 (Fargate ~$0.009, DynamoDB writes
~$0.05, COUNT scans ~$0.002). §2's cost table now holds measured numbers:
~$0.28/month weekly, DynamoDB the largest line; the "revisit §2 after real
runs" item is resolved.

**Schedule:** deadline moved **Oct 12 → Fri Oct 23** (§9 2026-10-08;
table in §7). Priority: a live T4.4 site before the **Oct 15 career fair**;
automation and docs after.
- Wed Oct 7: T4.3 closed
- **Next: Fri Oct 9 (30 min): T4.4a** — title index + Flask API
  (`/api/search`, `/api/similar`) with tests, local run on port 8001
- Mon Oct 12 (30 min): T4.4a finish — minimal page (search, posters,
  method toggle); `Dockerfile.api` + Mangum adapter, tested locally
- Tue Oct 13 (30 min): T4.4b — arm64 API image, Terraform Lambda + IAM +
  Function URL (reserved concurrency capped), Done-when check
- Wed Oct 14 (30 min): buffer for T4.4; if done, verify the live site only
- Sat Oct 17: T4.5 schedule + alerting
- Sun Oct 18: check the 02:00 scheduled run; T4.6 `make demo` + README;
  Week 5 summary + HANDOFF
- Mon–Fri Oct 19–23 (15 min/day): T4.6 finish, T4.7 runbook, deferred cleanups
- No work: Oct 8, Oct 10–11, Oct 15–16

**Carried to T4.4 / T4.6:**
- **Port 8000 is taken locally** by another project's container. T4.4's
  local API run and T4.6's `make demo` (`localhost:8000` in §7) will
  collide; pick a port or stop that container first
- The Lambda API image should also be arm64, built with
  `--provenance=false --sbom=false` (one ECR entry per push). It shares
  the 500 MB ECR free allowance: ~253 MB left
- The Function URL is public with no auth: give the Lambda a **reserved
  concurrency cap** (~5) so abuse can't run up Lambda/DynamoDB read cost
- Mangum serves ASGI and Flask is WSGI: a WSGI→ASGI wrapper is needed;
  choose it in T4.4b

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

**Timeout tuning:** the entrypoint stays `timeout 45m`; propose 15m
(~2.7× the measured 5.5 min) after a few weekly runs. Needs a rebuild + push.

**Deferred, with a home:** `run_date` semantics — same-day sample and
full runs share a folder and overwrite (run_date is now UTC; full runs
move to S3, local `artifacts/` holds sample runs only). Dependency
pinning — now a cost issue too: each unpinned rebuild can add a ~230 MB
ECR layer against the 500 MB free allowance. Least-privilege IAM for
movierec-dev, Oct 19–23 or later. Delete markers under artifacts/
accepted at negligible cost. The 262-row demo catalog is revisited at
T4.6. **Oct 19–23 or later:** skip the DynamoDB load when the catalog and
neighbours are unchanged from the previous run; it is ~85% of the per-run
cost, and with the frozen dataset every run is currently unchanged.

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
