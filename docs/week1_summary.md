# Week 1 Summary — Sep 8–13, 2026

**Planned:** T1.1 through T1.5, plus a full-catalog pipeline run on Sunday.
**Delivered:** zero code. All prerequisites, a v1 code review, and an unplanned cleanup of five old projects.
**Net position:** Phase 1 slips by about one week. Absorbable — Prerequisites §8 already names Phase 3 tuning as the droppable work.

---

## Done

### AWS setup (Prerequisites §1 — complete)
- Root MFA enabled; root now used only for billing changes
- IAM access to billing activated
- IAM admin user `kristin-admin` created with `AdministratorAccess`, MFA enabled, sign-in URL saved
- Free Tier and CloudWatch billing alerts on
- $2 monthly cost budget created via the credit activity → **$120 credits** (base $100 + $20 earned)
- Calendar reminder set for early Feb 2027: upgrade to Paid, or the site goes offline
- Free plan ends **Mar 10, 2027** (179 days remaining as of Sep 13)

**Sunday billing check:** $0.00 spend. S3 and Secrets Manager appear in Cost Explorer at $0.00 — usage reporting only, no charge.

### Security
- **Kaggle API key found hardcoded** in `weekly_movie_processing_spark.py` in the v1 repo, exposed in git history
- Old token expired, new one issued, placed at `~/.kaggle/kaggle.json` with `chmod 600`
- Credential scans run across all four archived repos — no other live secrets found

### v1 code review (`docs/v1_review.md`)
- Spark TF-IDF embedder **cannot be ported**: wrong library, and it includes `keywords` in the document text, which would make the Keyword Jaccard metric circular
- v1 similarity code also unusable: covers only the top 5,000 movies, and assumes a movie always ranks first among its own matches
- **Reusable:** cleaning rules (year 1900–present, runtime 1–360) for T1.3; Kaggle largest-CSV download logic; `index.html` and the three-endpoint API contract for T4.4
- Lessons logged: keying on title instead of `movie_id`, silent data loss from blanket `dropna()`, API source code that existed only inside Cloud Run

### Repo and disk cleanup (unplanned)
| Project | Outcome |
|---|---|
| `movie_recommender` (v1) | Archived on GitHub, moved to `~/Archive` |
| `Crawler` | Not mine — moved to Trash |
| `Sentiment_Analysis` | Abandoned interactive rebase recovered; work committed and pushed; old repo archived; empty v2 placeholder repo created with `upgrade.md` in it |
| `Fraud_Detection_ML_Pipeline` | Kept live and unarchived; README rescoped to classical ML only, torch promise removed |
| `Torch_ML_Pipeline` | **Was local-only with no remote** — new repo created and pushed |

- Removed 2 conda environments (`fasttext_env`, `test`) and cleared conda/pip package caches
- Removed ~7 stale venvs, including a bare venv sitting at `~/mov`
- **Disk: 95 GB free** of 460 GB — well above the 25 GB Prerequisites §4 requires

### v2 repo
- `movie-rec-system-v2` created public and cloned to `~/Desktop/Personal Projects/movie-rec-system-v2`
- Contains `IMPLEMENTATION_GUIDE.md`, `HANDOFF.md`, `docs/v1_review.md`, and `notes/` for the private setup files
- **No commits yet** — correct. `.gitignore` must be the first file in the first commit (Prerequisites §7)
- Claude Code tested against the repo; reads the guide correctly

---

## Deferred

**Before Phase 4 (late Sep):** Docker Desktop, AWS CLI + access keys + `aws configure`, Terraform, and the 30-minute console tour (§6).

**Any weekday:** the decisions note (§5) — bucket name, repo URL, alert email. Nothing in Week 1 needs it.

**Low priority:** fuller README edits on the PyTorch fraud project (unmeasured benchmark tables, "production ready" claims, RF/XGBoost equivalence framing). Also `Used_Car_Price`, the one project folder not yet triaged.

**In a week:** delete `~/Sentiment_Analysis-backup-20260913` and `~/Archive/movie rec upgrade` once nothing is missing.

---

## Open items to verify before T1.1

1. **`IMPLEMENTATION_GUIDE.md` filename** — was copied as `implementation.md`; confirm the rename took
2. **T1.3 keywords contradiction** — the "Document text" line still lists `keywords` while the note below says to exclude them. Delete `+ keywords` from the first line
3. **conda base is auto-activating** — the guide assumes plain `python3 -m venv`. Either run `conda config --set auto_activate_base false`, or tell Claude Code you're on conda

---

## Recurring lesson from this week

Three separate repos overstated what they contained: v1's README claims a similarity score as "accuracy," the fraud project promised a `Torch_training/` folder that was never added, and the PyTorch project publishes a benchmark table against numbers that were never measured. All three are fixable in minutes and all three would be exposed by one interview question. v2's evaluation should report only what it measures.

**A second lesson, learned the hard way:** a venv got committed to the sentiment repo because `.gitignore` didn't exist yet. That is exactly the failure Prerequisites §7 exists to prevent.

---

## Week 2 starts here

**Monday:** T1.1 — repo skeleton, `.gitignore` as the first file, manual Kaggle download, `docs/schema.md` from real data, and a committed 5,000-row stratified sample. Done when `wc -l data/sample/movies_5k.csv` returns 5001.

Then T1.2 through T1.5 across the week, with the full-catalog run on Sunday Sep 20 — plus the standing Sunday routine of a weekly summary and a HANDOFF refresh.
