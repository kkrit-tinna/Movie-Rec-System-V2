# Week 4 Summary — Sep 28 – Oct 4, 2026

**Planned:** T4.1 (Terraform base) and T4.2 (S3Store + DynamoDB writer),
then a start on T4.3.
**Delivered:** T4.1 and T4.2 in full, each split into two halves and
landed as one commit. Oct 1 skipped, so T4.3 moved to Mon Oct 5.
**Net position:** Infrastructure is live in us-east-1 and both storage
backends are proven against real AWS. 144 tests. Spend $0.01, none of it
infrastructure. One day of slack used; Oct 12 is still the deadline, with
Oct 12 itself as buffer.

---

## Done

### T4.1a — S3, DynamoDB, ECR (Mon)
- Split T4.1 into 4.1a (storage, plan only) and 4.1b (IAM, network,
  apply/destroy), logged in §9 before any code
- `.gitignore` covers `.terraform/`, `*.tfstate*`, `*.tfvars`;
  `.terraform.lock.hcl` is committed to pin the provider (aws 6.66.0)
- Bucket named with the account ID, versioned, all public access blocked;
  DynamoDB `movies` on-demand; two ECR repos keeping the last 5 images
- `terraform plan` → 9 to add; forbidden-resource grep empty

**The lifecycle gap.** §7's "expire artifacts after 60 days" does nothing
useful on a versioned bucket: expiry only adds a delete marker, and the old
version stays billed forever. Added noncurrent-version expiry (7 days) and
incomplete-upload cleanup (1 day), scoped to `artifacts/` only, so
`current.json`'s version history, the rollback mechanism, is never expired.

**The Terraform version was never what we thought.** `terraform init`
failed against `required_version ~> 1.16`. `which -a` found a single
binary, and it was 1.13.3. The "1.16.4" in §9 had most likely been copied
from Terraform's own out-of-date notice ("the latest version is 1.16.4"),
not from the installed version. Reinstalled 1.16.4 from the official zip;
deleted a 772 MB scratch copy Claude Code had used to validate around the
mismatch.

### T4.1b — IAM, network, first apply (Tue)
- Task role trusted only by `ecs-tasks.amazonaws.com`; inline policy with
  three statements: ListBucket on the bucket, Get/PutObject on `bucket/*`,
  four named DynamoDB actions on the table. No `*` actions, no
  DeleteObject
- Default VPC read through data sources, never `aws_default_vpc`, so
  destroy cannot touch it. Six public default subnets, one per AZ
- Security group with an explicit egress rule and no ingress
- `plan` 13 → `apply` 13 added → `destroy` 13 destroyed;
  `terraform state list` empty; default VPC untouched
- First run from the repo root failed with "No configuration files".
  Terraform reads `.tf` files and writes state in the current directory,
  so every command now runs from `infra/` or with `-chdir=infra`
- Committed: `feat: terraform-base-infra`

### T4.2a — S3Store (Wed)
- `S3Store(bucket, client=None)` implements all six ArtifactStore methods
- A missing key raises `FileNotFoundError` on both backends, with the
  botocore error kept as the cause
- `exists()` returns False only on a 404 and re-raises a 403. Without
  `ListBucket`, S3 returns 403 for missing keys; swallowing that would
  silently disable the drift gate ("no previous run") after an IAM mistake
- `list()` uses the paginator; a 1,001-key test confirms one raw call
  stops at 1,000
- Tests parametrised over local and S3 with moto, never real AWS:
  `test_storage.py` 9 → 18, suite 117 → 126
- Infrastructure re-applied and now stays up. Smoke test against the real
  bucket passed: put/get/list/404, plus a delete marker observed after `rm`

**Billing check.** September total $0.01, all from one Cost Explorer API
call; infrastructure $0.00; credits $119.99. A Secrets Manager line at
$0.00 turned out to be noise: `list-secrets` returned `[]`.

### Thu — skipped
No session. T4.2b moved to Friday; schedule revised and logged.

### T4.2b — DynamoDB writer (Fri)
- `build_items()` is pure: Decimal not float, NaN and missing values left
  out, exactly 10 neighbours per method; a movie with no neighbours
  raises
- `DynamoWriter` sends BatchWriteItem in 25s and retries `UnprocessedItems`
  with full-jitter exponential backoff; it gives up after `max_retries`
  with a count of unwritten items. Settings live in `config/default.yaml`
- 18 new tests, including fake-client retry and give-up cases. Suite: 144
- Smoke test: 3 real items (Inception, The Godfather, Toy Story) written,
  read back with numeric scores, deleted
- Committed: `feat: s3-store-and-dynamo-writer`

**The item-size measurement.** Full catalog: max 968 bytes, average 779,
so every item is 1 write unit. A full 77,281-item load is about $0.05 per
run, roughly $0.20/month weekly. That makes it the largest cost in the
project, and it contradicts §2's "DynamoDB $0 (always free)": the
always-free tier covers storage and provisioned capacity, not on-demand
writes. Provisioned mode at the free 25 WCU would take about 52 minutes
per load, more than twice the batch window, so on-demand stays.

---

## Spec changes

- **T4.1 and T4.2 each split in two**, one commit per task, logged in §9
  the day each split was made
- **Free plan, not §7's Paid plan.** The old parenthetical ("the account
  will not close when credits run out") contradicted the Free plan;
  rewritten. The Free plan ends Mar 10, 2027
- **Terraform 1.16.4 corrected** in §9, HANDOFF and the Week 3 summary
  (it was 1.13.3 until Sep 28)
- **Lifecycle rule extended** beyond §7: noncurrent-version and
  multipart-upload expiry. `artifacts/*` became the prefix `artifacts/`,
  since S3 prefixes take no globs
- **"Default VPC public subnet" → "subnets"** in §7
- **S3Store's unused `prefix` argument dropped**; §3 makes paths identical
  on both backends
- **§3's boto3 rule clarified**: it applies to `src/`, not to tests
- **T4.2 had no Done-when**; used "tests green + item size measured"
- **§2's DynamoDB cost line is wrong**; measured ~$0.05/run, correction
  pending the cost-target review

---

## Deferred

**T4.3 now carries:** `pipeline.py` (still empty); a STORAGE_BACKEND
factory, since none exists and the CLIs create LocalStore directly;
TF-IDF set as the default in `config/default.yaml`, moved from Sep 28;
keeping or saving the catalog, which the writer needs but isn't in
`artifacts/`; the ECS execution role, cluster, task definition and log
group; the first real test of the task role's IAM policy, since the smoke
tests ran as `movierec-dev` with AdministratorAccess; and
`--platform linux/amd64` builds.

**Cost target:** HANDOFF says under $0.50/month and Claude Code quoted $1.
Decide after a week of real runs, then fix §2.

**Accepted, not fixing:** zero-byte delete markers under `artifacts/`;
`exists()` and `get_bytes()` behave differently on folder paths across
backends, and no caller passes a folder.

**Still open:** least-privilege IAM for `movierec-dev`, until after
Oct 12; dependency pinning; `run_date` semantics; the 262-row demo
catalog (T4.6).

---

## Recurring lesson from this week

**A recorded number is not a measured number.** "Terraform 1.16.4" sat in
§9 for three days, and it was the version Terraform recommended, not the
one installed. On Friday a stale HANDOFF told Claude Code the stack was
destroyed when it had been live since Wednesday. Week 3's lesson was that
the log is not the artifact. This week the log was also wrong about the
environment, and the fix was the same: run the command (`which -a`,
`terraform version`, `describe-table`) instead of trusting the note.

**A second lesson: the same command depends on where it runs.** This week
four "errors" were location bugs, not code bugs: `terraform plan` from the
wrong directory, `-chdir=infra` from inside `infra/`, a DynamoDB console
showing zero tables because it was in Ohio, and a `get-item` that looked
truncated because the CLI had piped it through `less`. Directory, region,
account and pager are all part of a command, even though none of them
appear in it.

**A fact worth keeping for interviews:** the most expensive thing in the
project so far is checking the bill. One Cost Explorer API call cost
$0.01; all 13 resources together cost $0.00.

---

## Week 5 starts here

**Mon Oct 5 – Tue Oct 6:** T4.3, the batch container (split a/b).
**Wed Oct 7 – Thu Oct 8:** T4.4, the API.
**Fri Oct 9:** T4.5, schedule and alerting.
**Sat Oct 10:** T4.6, `make demo` and README.
**Sun Oct 11:** T4.7, runbook and Week 5 summary.
**Mon Oct 12:** buffer.

The first full pipeline run in T4.3 is the first real DynamoDB load:
about 77K write units, roughly $0.05. Check the bill the next morning
against that estimate.