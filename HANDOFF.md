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

*Last updated: Friday, Oct 2, 2026 — Week 4*

**Status:** Phases 1 and 2 complete. T4.1 (`feat: terraform-base-infra`)
and T4.2 (`feat: s3-store-and-dynamo-writer`) committed. 144 tests
passing. Infrastructure is live in us-east-1 (13 resources) and stays up.
Both stores smoke-tested against real AWS: S3 put/get/list/404, and
DynamoDB write → get-item → delete for 3 movies.

**Spend:** $0.01 total (one Cost Explorer API call); infrastructure
$0.00; credits $119.99. Measured DynamoDB items: max 968 bytes, average
779, so 1 write unit each. A full 77,281-item load is about $0.05 per
run, roughly $0.20/month weekly. Revisit §2's cost target after a week
of real runs.

**Schedule:** deadline **Oct 12**. Oct 1 was skipped; T4.2b moved to
Oct 2.
- Mon Oct 5 – Tue Oct 6: T4.3 batch container (split a/b)
- Wed Oct 7 – Thu Oct 8: T4.4 API
- Fri Oct 9: T4.5 schedule + alerting
- Sat Oct 10: T4.6 `make demo` + README
- Sun Oct 11: T4.7 runbook + Week 4 summary
- Mon Oct 12: buffer
No work Sat Oct 3 or Sun Oct 4.

**The embedder decision** (full report in `docs/comparison.md`):
TF-IDF ships. Keyword Jaccard@10 of 0.0376 against Word2Vec's 0.0241 — a
56% margin, 15× the 0.0025 random floor. Genre P@10 was 0.717 vs 0.728,
too close to separate them and measured on features both models were
trained on. TF-IDF also fits faster (4.62s vs 16.74s); its only
disadvantage is 11 MB more on disk.

**Numbers from the real data** (full detail in `docs/schema.md`):
- 1,495,113 rows ingested; 77,281 in catalog; 772,810 neighbour rows
- Raw file has 1,356 duplicate IDs; `prepare()` dedupes explicitly after
  the filters, keeping max `vote_count`
- Keyword coverage 85.5% at `vote_count >= 50`, median 5. 1,710 of 2,000
  sampled queries have keywords; the rest are skipped, not scored zero
- No collection column, so T2.4 is skipped — Keyword Jaccard@10 is the
  only held-out metric, with no fallback
- Full run, both methods, 148s: tfidf peak 2,969 MB / 34.2 MB artifact;
  word2vec peak 2,028 MB / 23.1 MB. Inside the 4 GiB Fargate budget

**Environment:**
- venv `movie_rec_venv/` on Python 3.12.5; container base
  `python:3.12-slim`
- Docker 28.0.4, AWS CLI 2.37.3, Terraform 1.16.4 (1.13.3 until Sep 28;
  the Sep 25 install was logged wrong). Homebrew could not
  build the CLIs (Command Line Tools too old for source builds on
  Sonoma) — both installed from official binaries, so Terraform upgrades
  are a manual re-download
- AWS: IAM user `movierec-dev`, us-east-1, $0.00 spent, $120 credits,
  $2 budget alert, Free plan ends Mar 10 2027
- GloVe at `~/glove/glove-100d.kv`; `MOVIEREC_WORD2VEC_MODEL_PATH` in
  `.env`
- boto3 and moto added.

**Next: T4.3, which now also carries:**
1. `pipeline.py`, the Fargate entry point (still empty)
2. a STORAGE_BACKEND factory. None exists; the CLIs create LocalStore
   directly
3. TF-IDF set as the default method in `config/default.yaml`
4. keeping or saving the catalog, which isn't written to artifacts/ but
   the DynamoDB writer needs
5. the ECS execution role, cluster, task definition and log group
6. the first real test of the task role's IAM policy; the smoke tests ran
   as movierec-dev with AdministratorAccess
7. `--platform linux/amd64` builds (Fargate is amd64, the laptop is arm64)

**Deferred, with a home:** `movierec/pipeline.py` is empty and gets
written in Phase 4 for the Fargate entry point. `run_date` semantics —
same-day sample and full runs share a folder and overwrite. UTC vs local
`run_date`. Dependency pinning. least-privilege IAM for movierec-dev, until after Oct 12.
Delete markers under artifacts/ accepted at negligible cost. 
Dependency pinning. run_date semantics. The 262-row demo catalog (T4.6). 
Fargate is amd64 and the laptop is arm64, so T4.3 builds need `--platform linux/amd64`. The 262-row demo catalog is revisited at T4.6.

**Reference:** `docs/week3_summary.md` for this week in full, §9 of
`IMPLEMENTATION.md` for every deviation from spec.

---

## How I want you to work with me

- The implementation.md is the source of truth. One `T#.#` task per session. Don't skip ahead of a task's dependencies.
- If reality contradicts the spec — a column is missing, a count is off — update the guide and log it under §9 Deviations rather than working around it silently.
- Explain cloud concepts when they come up. I'd rather understand the thing than have it work.
- Push back if I'm over-engineering. That's the mistake that produced v1.
- Flag anything that could cost money before I run it.
