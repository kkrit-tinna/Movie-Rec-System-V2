# Week 3 Summary — Sep 21–27, 2026

**Planned:** T1.6, then T2.1–T2.3, T2.4 skipped, and the embedding
comparison report.
**Delivered:** all of it, plus the Phase 4 toolchain a day early.
**Net position:** Phases 1 and 2 are complete. TF-IDF is the chosen
default. Phase 4 starts Monday on schedule, with Oct 12 still the deadline.

---

## Done

### T1.6 — quality gates (Mon)
- 8 gates, thresholds from config, each with a deliberately failing test
- `enforce()` writes `quality_report.json` before raising, so a failed run
  still leaves evidence
- Drift gate skips cleanly with no previous run

**The duplicate-ID finding.** The raw file has 1,356 duplicate IDs (458
exact rows, 898 differing versions). The catalog had 0 — but by luck of the
`vote_count` filters, not by design. Added an explicit dedup in `prepare()`
after the filters, keeping the highest `vote_count`, and moved the gate to
the catalog with the threshold left at 0.0. Suite: 59.

### T2.1 — Word2Vec embedder (Tue)
- GloVe 100d, IDF-weighted mean, L2-normalised, float16
- `idf` injected as a plain dict — no import of `TfidfEmbedder`, so the two
  stay interchangeable
- Unigrams only; GloVe cannot match TF-IDF's bigrams
- Model source reads from config, which is the same knob Phase 4 needs

**Lost an hour to certificates.** The gensim download failed with "connect
to the Internet and retry" while `curl` returned 200. The real cause was the
python.org macOS build shipping no CA bundle. Fixed with
`Install Certificates.command` under sudo. Laptop-only; the Debian container
is unaffected. Suite: 77.

### T2.2 — comparison harness (Wed)
- Runner takes a list of embedders; a stub third method proves no runner
  change is needed
- `BaseEmbedder` gained `coverage_stats()` so the runner stays
  method-agnostic
- `psutil` samples RSS every 10ms per method, since `ru_maxrss` is
  process-lifetime

**`neighbours.py` was sparse-only.** `.tocsr()` and `.todense()` fail
outright on Word2Vec's dense float16. Branched, sparse path untouched, tests
verified against the old code. Full catalog in 148s: tfidf 2,969 MB peak /
34.2 MB artifact; word2vec 2,028 MB / 23.1 MB. Suite: 96.

### T2.3 — metrics (Thu)
- Keyword Jaccard@10, Genre P@10, latency, seeded random baseline
- Metrics score an existing run rather than refitting — iteration went from
  148s to seconds
- Empty-keyword queries skipped, not scored zero; the rule is recorded in
  the JSON

Random came out at 0.0025 against T1.1's 0.0027, and keyword coverage at
exactly 85.5% — three independent confirmations the metric measures what it
claims. Suite: 117.

### Fri — Phase 4 toolchain (buffer day)
Docker 28.0.4, AWS CLI 2.37.3, Terraform 1.13.3 (first logged as 1.16.4;
upgraded to 1.16.4 on Sep 28). Homebrew could not build
either CLI — Command Line Tools too old for source builds on Sonoma — so
both came from official binaries. IAM user `movierec-dev` created,
`aws configure` set to us-east-1, spend $0.00.

### Sun — comparison report
`docs/comparison.md`: table, two charts, decision paragraph. **TF-IDF
ships** — 0.0376 against Word2Vec's 0.0241 on the held-out metric, 15× the
random floor, and faster to fit. The 11 MB disk disadvantage is negligible.

---

## Spec changes

- **T2.2 had no Done-when**; wrote one, then amended it when the refit moved
  behind `--compare`
- **T2.3's Done-when** said "five methods-columns" for three methods;
  corrected
- **§5's ~600 MB artifact estimate** was for 300-dim at 930K rows; measured
  23.1 MB
- **Chart 2 substituted**: latency vs accuracy is degenerate at 8.3e-05 for
  both methods, because serving is a precomputed key lookup. Replaced with
  cost vs accuracy
- **§9 Sep 21 entry backfilled** — T1.6 was never logged on the day

---

## Deferred

**Phase 4:** `pipeline.py` still empty. `run_date` semantics — same-day
sample and full runs share a folder and overwrite; worked around manually.
UTC vs local. Dependency pinning. Least-privilege IAM to replace
`AdministratorAccess`.

**T4.3:** Fargate is amd64 and the laptop is arm64, so builds need
`--platform linux/amd64`.

**T4.6:** the 262-row demo catalog, still thin.

**Closed with evidence, not deferred:** the hyphenated-token split
(`sci-fi` → `sci`, `fi`) is under 1% of tokens and mostly compositional.
Reopens only if Word2Vec underperforms for unexplained reasons — which,
after this week, it does not.

**Environment:** Command Line Tools update; Terraform upgrades are a manual
re-download, not `brew upgrade`.

---

## Recurring lesson from this week

**Sample-scale numbers are not small versions of real numbers.** The
median-IDF fallback rate was 52.6% on 262 rows and 4.3% on 77,281 —
`min_df: 3` was starving the vocabulary at sample size. A whole evening's
worry, resolved by running the thing at full scale. Same shape as Week 2's
keyword panic.

**A second lesson: the log is not the artifact.** §9 recorded that §5's
artifact estimate had been corrected; §5 still said ~600 MB. The T1.5 spot
check was ticked off twice and the heading sat empty for nine days. Both
were invisible in summaries and obvious on `grep`. Week 2's lesson was that
reading the diff catches what the summary misses — this week's is that the
same applies to documentation, including documentation claiming something
was documented.

---

## Week 4 starts here

**Monday Sep 28:** T4.1 — Terraform base. The first task that spends money;
`terraform apply` is where an estimate becomes a bill.

**Through Oct 11:** Phase 4, with Sat Oct 10 and Sun Oct 11 as the final
long blocks. Oct 12 is buffer.

Set TF-IDF as the default method in `config/default.yaml` before the
pipeline entry point gets written, or the decision exists only in prose.