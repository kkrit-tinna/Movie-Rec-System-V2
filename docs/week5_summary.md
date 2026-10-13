# Week 5 Summary — Oct 5 – Oct 11, 2026

**Planned:** T4.3 (batch container) Mon–Tue, T4.4 (API) Wed–Thu, T4.5 Fri,
T4.6 Sat, T4.7 Sun, with Oct 12 as buffer and deadline.
**Delivered:** T4.3 in full (`feat: fargate-batch-task`), the first real
Fargate run, and T4.4a part 1 (local API, uncommitted). Oct 5 and Oct 8
skipped; no work Oct 10–11.
**Net position:** The batch pipeline runs end to end on AWS for about
$0.06 a run. The deadline moved to **Fri Oct 23**; the priority is a live
T4.4 site before the Oct 15 career fair. 205 tests.

---

## Done

### T4.3a — the container, locally (Tue Oct 6)
- `storage/factory.py` `get_store()` (`STORAGE_BACKEND`, `MOVIEREC_S3_BUCKET`);
  precedence CLI > env > `--config` overlay > `default.yaml` > code
- `pipeline.py` runs §2's 8 steps; gates fail before any write; the catalog
  is saved to `catalog/{run_date}/`; `current.json` is written last;
  `run_date` defaults to UTC
- `config/sample.yaml` overlay (5,000 / 250 floors), passed explicitly and
  recorded in `quality_report.json`, so a sample run can never pass for production
- `gate_null_rate` returned a numpy bool, which would have crashed the first real run
- **ARM64, not amd64.** The amd64 image hung importing `scipy.stats` under
  emulation. "Fargate is amd64" (Sep 25) was never true; Fargate runs Graviton,
  about 20% cheaper and with no emulation
- Entrypoint `timeout 45m`: ECS has no task timeout, so a hung task would bill
  about $2/day

### T4.3b — AWS (Tue Oct 6 night – Wed Oct 7)
- Kaggle now issues a single `KAGGLE_API_TOKEN`; stored in SSM SecureString
  through write-only `value_wo`, proven absent from tfstate
- GloVe in `s3://<bucket>/models/`, downloaded at runtime
- S3 lifecycle fixed: S3Store keys had no `artifacts/` prefix, so the old rule
  matched nothing. One rule per run prefix now; `current.json` and `models/`
  never expire
- ECS cluster, execution role (one SSM ARN), 14-day log group, task definition
  (ARM64, 2 vCPU / 4 GiB, 20 GB disk)
- ECR: built without BuildKit attestations (`make image`), because each push
  otherwise created three entries and keep-last-5 could break a tag
- **First run OOM-killed** at step 4. `prepare()` built documents for all
  1.5M rows before filtering, and about 2 GB was never returned to the OS.
  Filtering first gave byte-identical output; `chunk_size` 2000 → 1000 gave
  identical neighbours. Peak 3.44 → **2.58 GiB** of 4
- **Second run: exit 0 in 5.5 min.** All 10 gates passed, 77,281 items in
  DynamoDB, metrics identical to Sep 23, task role needed no IAM change.
  Cost $0.06, confirmed on the bill

### Timeline revision (Thu Oct 8)
School and job-search load. Deadline Oct 12 → Oct 23; T4.4 before the fair,
T4.5–T4.7 after. Logged in §9 and the §7 schedule table.

### T4.4a part 1 (Fri Oct 9, uncommitted)
- `[api]` optional extra (flask, rapidfuzz); port 8000 freed and used
- `index/titles.py`: `build_title_index()` + CLI; 77,281 titles, bundled in the
  API image for the fair, moving to pipeline + S3 after Oct 15
- DynamoDB reader in `storage/dynamo.py` (`get_item`, `batch_get` with
  ≤100 keys per call, retries, requested order kept)
- Flask API in v1's response shapes: `/api/health`, `/api/search`,
  `/api/similar/<id>?method=`; default method from config
- Search puts exact title matches first. 49 catalog titles are under 2
  characters (M, X, Z, …) and are now findable; fuzzy matching (WRatio,
  about 80 ms) only starts at 2 characters

---

## Spec changes
- Fargate architecture ARM64; disk 20 GB, not 30; timeout entrypoint
- One Kaggle token parameter instead of username/key
- Per-prefix lifecycle rules
- §3's "daily updates make the weekly refresh meaningful" corrected: the
  catalog is **frozen** (same 77,281 ids, vote counts and documents as Sep 23)
- §2 cost table now measured: ~6 min and ~$0.28/month; DynamoDB writes are
  the largest line, meeting the < $0.50 target
- Deadline Oct 23; T4.4 split a/b with a reserved-concurrency cap on the
  public Function URL; slim API image decided

---

## Deferred
- **Mon Oct 12:** real-table run, lazy pandas imports + guard test, slim
  `Dockerfile.api`, the page (may slip to Wed)
- Search ranking tuning ("IO" for "inception"; "Dark Night" vs "The Dark Knight")
- After Oct 15: title index into pipeline + S3; skip the DynamoDB load when
  nothing changed (~85% of per-run cost); timeout 45m → 15m; dependency
  pinning; remove the dead Kaggle key from the public v1 repo;
  least-privilege `movierec-dev`

---

## Recurring lessons from this week

**Measure the claim, including the ones in the spec.** Three "facts" failed
when measured: Fargate being amd64, September's "3.05 GiB fits" (TF-IDF
alone on macOS, never the full chain under a Linux limit), and the dataset's
daily updates making a weekly refresh meaningful. Each was cheap to check
and expensive to discover in production.

**A passing test can test nothing.** Kaggle's client lists public data
anonymously and answers an invalid key with "No datasets found", so three
"the key works" checks proved nothing. The only valid test was one that
needed a login and returned something only the owner has.

**Fail-safe beats fail-dangerous.** The lifecycle choice (an unruled prefix
keeps files rather than losing `current.json`), the timeout, the
concurrency cap and the explicit sample overlay were all chosen by asking
what happens when someone gets it wrong.

---

## Week 6 starts here
**Mon Oct 12 – Wed Oct 14 (30 min/day):** finish T4.4: the site goes live
on a Function URL before the Oct 15 career fair. Wednesday is buffer.
**Sat Oct 17 – Sun Oct 18:** T4.5 schedule + alerting, then T4.6 `make demo`
+ README, then the Week 6 summary.
