# Project handoff brief

*Paste this as the first message in the new account, with the three attached files.*

---

I'm continuing a project from a previous Claude conversation. Here's the context, the decisions already made, and where I am. Please read the three attached files before responding.

**Attached:**
- `IMPLEMENTATION.md` — the task-by-task spec (source of truth)
- `PREREQUISITES.md` — setup checklist
- `CLOUD_PRIMER.md` — cloud fundamentals + interview prep

---

## Who I am

Master's student in Data Science at Northeastern, graduating Winter 2026. Currently on a data science co-op. Searching for a Spring 2027 internship, primarily targeting **Data Engineer** roles, Boston area or remote. I need visa sponsorship, so I'm applying broadly and early.

**I'm a complete beginner with cloud computing.** I followed a step-by-step GCP guide once without understanding what I was doing. Assume no cloud knowledge unless I demonstrate it.

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

*Last updated: Friday, Sep 18, 2026 — end of Week 2*

**Status:** Phase 1 complete except T1.6 (quality gates). T1.1–T1.5 shipped
and pushed; 34 tests passing. Starting T1.6 on Monday Sep 21.

**Schedule:** timeline extended one week to **Oct 12**. Phase 3 dropped
Sep 15 and stays dropped — the extra week is buffer for Phase 4, not
recovered scope. No work on Saturdays (except Oct 10), Sun Sep 20, or
Sun Oct 4. Long blocks: Sun Sep 27, Sat Oct 10, Sun Oct 11.

- Phase 2: Sep 21–27 (T1.6 Monday, then T2.1–T2.3; T2.4 skipped)
- Phase 4: Sep 28 – Oct 11
- Oct 12: buffer

**Numbers from the real data** (full detail in `docs/schema.md`):
- 1,495,113 rows ingested; 77,281 in catalog; 772,810 neighbour rows
- Keyword coverage 85.5% at `vote_count >= 50`, median 5; random-baseline
  Jaccard 0.0027. Keywords confirmed as the held-out label
- No collection column, so T2.4 franchise recall is skipped — Keyword
  Jaccard@10 is the only held-out metric, with no fallback
- Full-catalog run: **112s, 3.05 GiB peak** — inside the 4 GiB Fargate
  budget after fixing a float64 similarity block that first pushed it to
  6.27 GiB. The embedder is now the tallest step at 3.07 GiB, so Word2Vec
  has about 1 GiB of headroom

**Environment:**
- venv `movie_rec_venv/` on Python 3.12.5; container base is
  `python:3.12-slim` to match
- Claude Code set up with `CLAUDE.md` at the repo root; runs in
  accept-edits mode, one task per session
- AWS: $0.00 spent, $120 credits, $2 budget alert, Free plan ends
  Mar 10 2027. Docker, AWS CLI, Terraform still not installed — not needed
  until Phase 4

**Open items for Monday:**
1. T1.6 — every gate needs a deliberately failing test, including the
   no-baseline skip for `row_count_drift_pct`
2. Confirm the T1.5 spot check is in `docs/comparison.md`
3. Claude Code has committed on its own three times with drifting
   messages — decide whether `CLAUDE.md` should forbid `git commit`

**Deferred, with a home:** `movierec/pipeline.py` is empty and gets written
in Phase 4 for the Fargate entry point; UTC vs local `run_date` and
dependency pinning are Phase 4 decisions; the 262-row demo catalog is
revisited at T4.6.

**Reference:** `docs/week2_summary.md` for this week in full, §9 of the
implementation.md for every deviation from spec.

---

## How I want you to work with me

- The implementation.md is the source of truth. One `T#.#` task per session. Don't skip ahead of a task's dependencies.
- If reality contradicts the spec — a column is missing, a count is off — update the guide and log it under §9 Deviations rather than working around it silently.
- Explain cloud concepts when they come up. I'd rather understand the thing than have it work.
- Push back if I'm over-engineering. That's the mistake that produced v1.
- Flag anything that could cost money before I run it.
