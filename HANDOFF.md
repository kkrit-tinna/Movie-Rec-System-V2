# Project handoff brief

*Paste this as the first message in the new account, with the three attached files.*

---

I'm continuing a project from a previous Claude conversation. Here's the context, the decisions already made, and where I am. Please read the three attached files before responding.

**Attached:**
- `IMPLEMENTATION_GUIDE.md` — the task-by-task spec (source of truth)
- `PREREQUISITES.md` — setup checklist
- `CLOUD_PRIMER.md` — cloud fundamentals + interview prep

---

## Who I am

Master's student in Data Science at Northeastern, graduating Winter 2026. Currently on a data science co-op. Searching for a Spring 2027 internship, primarily targeting **Data Engineer** roles, Boston area or remote. I need visa sponsorship, so I'm applying broadly and early.

**I'm a complete beginner with cloud computing.** I followed a step-by-step GCP guide once without understanding what I was doing. Assume no cloud knowledge unless I demonstrate it.

**Time budget:** 15 min/day Mon–Fri, 30–45 min Sunday. This constraint is real and drives most scoping decisions.

---

## The project

A content-based movie recommender. Version 1 exists: TF-IDF embeddings, a Flask website, and a weekly job on GCP Dataproc/Spark. The GCP free trial has ended, so I'm rebuilding.

**User story:** visitor types a movie name, gets 10 similar movies with poster, year, rating, and a short overview.

**Timeline:** 4-week upgrade, Sep 8 – Oct 5, 2026.

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

**6. GloVe-100, not GoogleNews-300.**
The 3.6 GB model won't fit alongside the corpus in a 4 GiB Fargate task. Week 3 tests whether a larger or domain-trained model earns its cost.

**7. Public subnet with a security group, not private + NAT Gateway.**
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

## Where I am right now

*Last updated: Sunday, Sep 13, 2026 — end of Week 1*

**Status:** setup complete, no code written yet. Starting T1.1 on Monday Sep 14.

**Schedule:** about one week behind the original plan. Week 1 went to prerequisites and an unplanned cleanup of five old projects instead of T1.1–T1.5. Phase 1 shifts to Week 2; Prerequisites §8 already identifies Phase 3 tuning as the work to drop if the slip compounds.

**Ready:**
- AWS account `419741995620`, IAM user `kristin-admin`, region target `us-east-1`
- $120 credits, $0.00 spent, $2 budget with alerts, Free plan ends Mar 10 2027
- Kaggle token reissued after a leaked key was found in the v1 repo; now at `~/.kaggle/kaggle.json`
- Python 3.11+, git, 95 GB free disk
- `movie-rec-system-v2` cloned locally, containing `IMPLEMENTATION_GUIDE.md`, `HANDOFF.md`, `docs/v1_review.md`, `notes/`
- Claude Code tested against the repo

**Not yet installed** (not needed until Phase 4): Docker, AWS CLI, Terraform.

**Check these before the first Claude Code session:**
1. Delete `+ keywords` from the first line of T1.3's "Document text" — it contradicts the note directly below and would make Keyword Jaccard circular
2. conda `base` auto-activates in every shell; the guide assumes plain `python3 -m venv`

**First action Monday:** `.gitignore` as the very first file in the very first commit, including `notes/`. Then T1.1.

**Reference:** `docs/v1_review.md` holds what carries over from v1 — cleaning rules for T1.3, frontend and API contract for T4.4.

---

## How I want you to work with me

- The implementation guide is the source of truth. One `T#.#` task per session. Don't skip ahead of a task's dependencies.
- If reality contradicts the spec — a column is missing, a count is off — update the guide and log it under §9 Deviations rather than working around it silently.
- Explain cloud concepts when they come up. I'd rather understand the thing than have it work.
- Push back if I'm over-engineering. That's the mistake that produced v1.
- Flag anything that could cost money before I run it.
