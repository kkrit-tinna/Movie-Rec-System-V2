# Week 2 Summary — Sep 14–18, 2026

**Planned:** T1.1 through T1.6, the full-catalog run, and the weekly close.
**Delivered:** T1.1 through T1.5, the full-catalog run, and a memory fix
that kept the Fargate sizing intact. T1.6 carries to Monday.
**Net position:** Phase 1 is complete except the quality gates. The timeline
is extended one week to Oct 12, which puts the project back on plan.

---

## Done

### T1.1 — repo skeleton and schema (Mon)
- `.gitignore` as commit #1, before any data landed
- venv `movie_rec_venv` on Python 3.12.5; container base moved to
  `python:3.12-slim` to match
- `pyproject.toml`, `CLAUDE.md`, `src/movierec/` layout, `config/default.yaml`
- `docs/schema.md` profiled from the real file
- 5,000-row stratified sample committed; `wc -l` returns 5001

**What the data actually looks like:**
- 1,495,113 rows, not the 930K in the dataset slug
- Catalog after the §4 filters: 77,281 (guide estimated 100–200K).
  `vote_count >= 10` is the binding filter
- No collection or franchise column → T2.4 skipped

**The keyword question, settled with numbers.** Keywords are 76% null
catalog-wide, which looked fatal for Keyword Jaccard@10. At
`vote_count >= 50` coverage is 85.5% with a median of 5 keywords per film.
Random-pair baseline Jaccard is 0.0027, so any real signal will be
unmistakable. Keywords stay out of the document text and remain the
held-out label.

### T1.2 — storage and embedder interfaces (Tue)
- `ArtifactStore` with `LocalStore` and an `S3Store` stub
- `BaseEmbedder` ABC
- 9 tests, parametrised over backends so T4.2 can reuse them

### T1.3 — ingest and text preparation (Wed)
- `download()`, `load()` with an explicit dtype map, `prepare()`
- `document` = title + tagline + overview + genres; whitespace collapsed to
  match the sample builder
- 10 tests, including `test_document_never_contains_keywords` — the keyword
  constraint is now enforced by a test, not by discipline
- Sample catalog is 262 rows: correct, but thin for the demo

### T1.4 — TF-IDF embedder (Thu)
- `TfidfEmbedder` with all parameters read from config
- Saves vectorizer, matrix, and `row_index.parquet`; round-trip test
  compares matrices element-wise
- `run_date` is always passed in explicitly — no hidden clock dependency
- `joblib` declared as a direct dependency rather than inherited

### T1.5 — top-K neighbours and the full-catalog run (Fri)
- Chunked cosine similarity, self-matches dropped
- `Makefile` with `demo-tfidf`, which no task had created
- Full run: 1,495,113 ingested → 77,281 catalog → 772,810 neighbour rows

**The memory finding.** The first full run peaked at 6.27 GiB, over the
4 GiB Fargate budget in §7. Profiled per step:

| Step | Peak RSS |
|---|---|
| Ingest | 3.53 GiB |
| Embedder | 3.07 GiB |
| Neighbours | 6.06 GiB |

Trimming ingest to 13 columns with `usecols` cut that step to 2.85 GiB
but left the overall peak unchanged. The real cause was a single
5,000 × 77,281 float64 similarity block — 3.09 GB on its own. Casting to
float32 before the matmul and reducing `chunk_size` from 5,000 to 2,000
brought the full chain to **3.05 GiB in 112s**, down from 127s. No model
change, and §7 stays at 4 GiB.

Test suite: 34 passing.

### Spec changes
- **Phase 3 dropped** (Sep 15): two Sundays unavailable and Phase 1 slipped
  a week. Quality gates moved to T1.6; the production runbook moved to T4.7
- **Timeline extended to Oct 12** (Sep 18): Saturdays are free except
  Oct 10. Long blocks are now Sun Sep 27, Sat Oct 10, and Sun Oct 11.
  Phase 3 is not reinstated; the extra week is buffer for Phase 4
- Gate floors corrected from real data: `row_count_min` 800K → 1.3M,
  `catalog_count_min` 50K → 70K
- §9 converted to one line per day

---

## Deferred

**Monday:** T1.6, the quality gates. Real thresholds can now be set from
tonight's run rather than from estimates.

**Phase 4:** `movierec/pipeline.py` is still empty — the Fargate task will
need a single entry point. UTC versus local for `run_date`, since
EventBridge triggers in UTC. Pinning dependency versions.

**T4.6:** `make demo` on a 262-row catalog may not convince anyone. A
separate small demo sample of recognisable films is the likely fix.

**Possibly T2.2:** adding release year or language as features. The
comparison harness is the right place to test whether it earns its keep.

---

## Open items to verify Monday

1. T1.5's spot check is written into `docs/comparison.md`
2. `HANDOFF.md` "Where I am right now" still says starting T1.1
3. Claude Code has committed on its own three times, with messages that
   drifted from the convention. Decide whether `CLAUDE.md` should say
   "never run git commit"

---

## Recurring lesson from this week

**Every estimate in the guide that met real data was wrong.** The row count
was 60% higher. The catalog was half the lower bound. The Fargate memory
budget was exceeded by 57%. The keyword worry turned out unfounded. And the
obvious memory fix — trimming columns — didn't touch the actual peak.

None of these were caught by reasoning. All were caught by measuring. The
guide is a plan; `docs/schema.md` is what's true.

**A second lesson: reading the diff catches what the summary doesn't.**
The egg-info folder, the empty T1.6 stub, the half-written T4.7, and the
`pipeline.py` that ran in 0.04 seconds were all invisible in a "done"
message and obvious in `git show --stat` or a timing line.

---

## Week 3 starts here

**Monday Sep 21:** T1.6 — quality gates, with a deliberately failing test
per gate.

**Tue–Fri:** T2.1 Word2Vec embedder, T2.2 comparison harness, T2.3
evaluation metrics. T2.4 is skipped, which absorbs the day T1.6 takes.

**Sunday Sep 27:** the embedding comparison report — TF-IDF against
Word2Vec, against the random baseline, on Keyword Jaccard@10.

Watch memory when Word2Vec lands: the embedder is now the tallest step at
3.07 GiB, leaving about 1 GiB of headroom against the Fargate budget.