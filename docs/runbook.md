# movierec production runbook

For the operator of this deployment (the project owner, or a Claude Code session working for them). It assumes AWS credentials for the account and a checkout of this repo.

A weekly batch task loads recommendations into DynamoDB, and the site is a Lambda function that reads them.

## Before you run anything

Run every command from the repo root. You need:

- **The AWS CLI and Terraform.** Known good: AWS CLI 2.37.3, Terraform 1.16.4.
- **AWS credentials for the account.** Ask the project owner for access. You need ECS, EventBridge Scheduler, CloudWatch Logs, S3 (read and write), DynamoDB (read and batch write), Lambda, ECR and SSM access, plus `iam:PassRole` on the batch and scheduler roles.
- **The Terraform state,** which is local: `infra/terraform.tfstate`, not in git, kept by the project owner. Copy it to that path, and run `terraform -chdir=infra init` if `output` complains. Without it, see "No Terraform state" below.

Set these once per terminal, and stop if the check fails:

```bash
unset AWS_REGION
export AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity
export BUCKET=$(terraform -chdir=infra output -raw artifacts_bucket_name)
: "${BUCKET:?terraform output failed, BUCKET is empty}"
```

Use `AWS_DEFAULT_REGION`: boto3 ignores `AWS_REGION`, and the CLI would prefer it.

**No Terraform state?** Find the values with the AWS CLI. The bucket is named `movierec-artifacts-<account-id>`:

```bash
aws s3 ls
aws lambda get-function-url-config --function-name movierec-api --query FunctionUrl --output text
```

Run `export BUCKET=<bucket-name>`, and use the normal route in section 2, which doesn't need Terraform.

**What has been tested on real AWS:** the schedule flow and the failing-gate alerts (2026-10-28), and `movierec.reload --dry-run` (2026-10-31). **Not tested on real AWS:** a real reload (tested against a mock only), disabling the schedule, and the first-deploy order.

## 1. What runs, and when

| | |
|---|---|
| Schedule | `movierec-weekly-batch`, Sundays 02:00 New York time, no retries |
| Task | ECS Fargate, cluster `movierec`, ~6.5 min, ~$0.06 per run (most of it DynamoDB writes) |
| Steps | Download from Kaggle, quality gates, build embeddings, neighbours, evaluate, write to S3, load DynamoDB, write `current.json` |
| Logs | CloudWatch log group `/ecs/movierec`, kept 14 days |
| Site | Lambda function `movierec-api`; it reads DynamoDB, not S3 |

`current.json` in S3 records the last published run. Nothing reads it.

**Daylight saving:** 02:00 New York is 07:00 UTC in winter (EST) and 06:00 UTC in summer (EDT). On a DST-change night, check the logs at the UTC time. On the November night, the run fires at 07:00 UTC. On the March night, 02:00 doesn't exist in New York, so confirm a run happened and trigger one if it didn't.

## 2. Trigger a run now

A run costs ~$0.06 and **writes to the live table**, so it undoes any rollback. The run date (`YYYY-MM-DD`) is the UTC date when the task starts, so an evening run in New York gets tomorrow's date.

Don't start a run while another is going. This should print `None`:

```bash
aws ecs list-tasks --cluster movierec --desired-status RUNNING --query 'taskArns[0]' --output text
```

### Normal way: a one-time schedule

EventBridge Scheduler has no "run now". This creates a one-time schedule with the weekly schedule's live target. It deletes itself after firing. This block is as run on 2026-10-28.

```bash
aws scheduler get-schedule --name movierec-weekly-batch --query Target --output json > target.json

RUN_AT=$(TZ=America/New_York date -v+5M +%Y-%m-%dT%H:%M:00)

aws scheduler create-schedule \
  --name movierec-manual-run \
  --schedule-expression "at($RUN_AT)" \
  --schedule-expression-timezone America/New_York \
  --flexible-time-window Mode=OFF \
  --action-after-completion DELETE \
  --target "file://$PWD/target.json"

rm target.json
```

`date -v` is macOS. It sets a time about 5 minutes from now, on the whole minute. On Linux, use:

```bash
RUN_AT=$(TZ=America/New_York date -d '+5 min' +%Y-%m-%dT%H:%M:00)
```

The command returns no task ID, and no task exists until the schedule fires, so wait about 6 minutes before checking.

If the name is already taken, an earlier one-time schedule hasn't fired or been deleted yet. Check that no run is in progress (below), then delete it and rerun the whole block:

```bash
aws scheduler delete-schedule --name movierec-manual-run
```

### Fallback: `aws ecs run-task`

Use this if the schedule route fails. It uses your own permissions and starts straight away.

```bash
SUBNETS=$(aws scheduler get-schedule --name movierec-weekly-batch --query 'Target.EcsParameters.NetworkConfiguration.awsvpcConfiguration.Subnets' --output json)
SG=$(aws scheduler get-schedule --name movierec-weekly-batch --query 'Target.EcsParameters.NetworkConfiguration.awsvpcConfiguration.SecurityGroups[0]' --output text)
TASK_DEF=$(aws scheduler get-schedule --name movierec-weekly-batch --query 'Target.EcsParameters.TaskDefinitionArn' --output text)

aws ecs run-task \
  --cluster movierec \
  --launch-type FARGATE \
  --task-definition "$TASK_DEF" \
  --network-configuration "{\"awsvpcConfiguration\":{\"subnets\":$SUBNETS,\"securityGroups\":[\"$SG\"],\"assignPublicIp\":\"ENABLED\"}}" \
  --query 'tasks[0].taskArn' --output text
```

Alternative for the first three lines, if you have the Terraform state:

```bash
SUBNETS=$(terraform -chdir=infra output -json default_subnet_ids)
SG=$(terraform -chdir=infra output -raw batch_security_group_id)
TASK_DEF=$(terraform -chdir=infra output -raw batch_task_definition_arn)
```

### Check that it worked

Set the run date and get the running task's ID:

```bash
RUN_DATE=$(date -u +%F)
TASK_ARN=$(aws ecs list-tasks --cluster movierec --desired-status RUNNING --query 'taskArns[0]' --output text)
echo "$TASK_ARN"
```

If that prints `None` about a minute after `RUN_AT`, the task has already stopped or never started. List stopped tasks and set `TASK_ARN` by hand, choosing the one whose `started` time (from `describe-tasks` below) is near `RUN_AT`. ECS keeps stopped tasks for only about an hour; after that, use the logs. A task that fails to start also sends an alert email (section 4).

```bash
aws ecs list-tasks --cluster movierec --desired-status STOPPED
TASK_ARN=<task-arn>
```

Follow the logs. This command doesn't exit by itself, so press Ctrl+C once the run finishes, after about 6.5 minutes. The last line is `run <date> complete`.

```bash
aws logs tail /ecs/movierec --since 1h --follow
```

Then read the exit code, once `status` is `STOPPED`. `0` means success.

```bash
aws ecs describe-tasks --cluster movierec --tasks "$TASK_ARN" \
  --query 'tasks[0].{status:lastStatus,started:startedAt,exit:containers[0].exitCode,stopCode:stopCode,reason:stoppedReason}'
```

Check the artifacts:

```bash
aws s3 ls "s3://$BUCKET/catalog/$RUN_DATE/"
aws s3 cp "s3://$BUCKET/quality/$RUN_DATE/quality_report.json" -
aws s3 cp "s3://$BUCKET/current.json" -
```

The `catalog` folder should list `catalog.parquet`, and every gate in the report should show `"passed": true`. `current.json` should be `{"run_date": "<the run date>", "method": "tfidf"}`. TF-IDF is the site's default method; GloVe is also loaded and is available through the page's toggle.

## 3. Roll back

Serving reads DynamoDB, which holds the last loaded run, so rolling back means reloading the table from an earlier run's S3 artifacts. Moving `current.json` by hand changes nothing on the site.

You need Python 3.12, and "Before you run anything" done in this terminal. `pip install -e .` includes everything the command needs. If `.venv` already exists, skip the `python3.12 -m venv` and `pip install` lines, but always run `source` and the exports.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
export STORAGE_BACKEND=s3
export MOVIEREC_S3_BUCKET=$BUCKET
```

**1. Check that nothing will overwrite the rollback.** The first command should print `None`, and the second should fail with `ResourceNotFoundException`. If a run is going, wait for it to finish. If a one-time schedule is pending, delete it (section 2).

```bash
aws ecs list-tasks --cluster movierec --desired-status RUNNING --query 'taskArns[0]' --output text
aws scheduler get-schedule --name movierec-manual-run
```

**See which runs exist.**

```bash
aws s3 ls "s3://$BUCKET/catalog/"
```

Run folders expire after 60 days, so that's the rollback window. Runs include manual ones, so dates aren't only Sundays. Pick the latest date from before the problem started. The tool can only reject failed or incomplete runs, so judging "good" is up to you. If the date you want isn't listed, it has expired, so pick another.

**2. Dry-run.** This writes nothing. It refuses a run with missing artifacts or a failed or missing quality report, and exits non-zero with a `refused:` message.

```bash
python -m movierec.reload --run-date <run-date> --dry-run
```

Expect the date, the item and WCU counts (77,281 of each on every run so far), and `dry run: nothing written`.

**3. Reload.** This writes to the live table without asking for confirmation, and costs ~$0.05. The pipeline's load step takes about 90 s on Fargate; from a laptop it may take longer. **A real reload has only been tested against a mock, so watch the output.** Stopping it with Ctrl+C, or a write error, is a partial failure (see Limits), so rerun it. A `refused:` message or anything else unexpected means stop and contact the project owner.

```bash
python -m movierec.reload --run-date <run-date>
```

Success prints `DynamoDB movies: {'items_written': ..., 'batches': ..., 'retries': ...}`, with `items_written` matching the dry run's count, then `current.json -> <run-date>, tfidf`.

**4. Verify.** Items don't store a run date, and the catalog has been frozen, so the table can't show which run it came from. The evidence is `items_written` above plus `current.json`, which moves only after every write succeeds.

```bash
aws s3 cp "s3://$BUCKET/current.json" -
aws dynamodb get-item --table-name movies --key '{"movie_id":{"S":"27205"}}' --query 'Item.title'
FUNCTION_URL=$(aws lambda get-function-url-config --function-name movierec-api --query FunctionUrl --output text)
curl -s "${FUNCTION_URL}api/similar/27205" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['found'], len(d['recommendations']))"
```

`current.json` should show `<run-date>`, the item should be Inception, and the last line should print `True 10`. If you rolled back because of a specific wrong film, also check that film on the site.

**5. Decide about the next scheduled run.** The next Sunday 02:00 New York run reloads the table from the latest data and undoes the rollback. If the problem is in the code or the upstream data and could return, disable the schedule now, even if that run is only hours away. If it was a one-off, leave the schedule on.

This hasn't been tested on real AWS. The update needs the full definition:

```bash
aws scheduler get-schedule --name movierec-weekly-batch --query Target --output json > target.json

aws scheduler update-schedule \
  --name movierec-weekly-batch \
  --state DISABLED \
  --schedule-expression "cron(0 2 ? * SUN *)" \
  --schedule-expression-timezone America/New_York \
  --flexible-time-window Mode=OFF \
  --target "file://$PWD/target.json"

rm target.json
aws scheduler get-schedule --name movierec-weekly-batch --query State --output text
```

The last command should print `DISABLED`. To turn it back on, run the whole block again with `--state ENABLED`. Remember to do that, since nothing reminds you and no runs happen until then. Terraform's configuration doesn't set this state, so expect a later `terraform apply` to put the schedule back to enabled; read the plan.

**Limits**

- **No deletes.** A reload overwrites items but doesn't delete items missing from the older run, so a film added since then stays in the table. That's harmless while the catalog is frozen, as it has been so far.
- **Partial failure.** If a write fails partway, the command exits with an error, the table holds a mix of two runs, and `current.json` doesn't move. The site keeps working. Run the same reload again, since it overwrites every item.

## 4. What each alert means

All alerts are emails from the SNS topic `movierec-alerts`, sent to the address in Terraform's `alert_email`. They fire once, and no email is sent on recovery.

### Gate alarm: `movierec-quality-gate-failed`

A quality gate stopped the batch run. Nothing was loaded, the site is still serving the previous run, and `current.json` didn't move. It arrives about 6 minutes after the failure. Go to section 5.

### Task-failure email: "movierec batch task failed: exit code …"

The batch task stopped badly. There are no retries, so nothing happens until you act. **A failed gate sends two emails, this one and the gate alarm, for the same incident.**

| Exit code | Meaning | What to do |
|---|---|---|
| `1` | Gate failure or crash | Search the logs (below). If you find `quality_gate_failed`, go to section 5. Otherwise read the traceback, fix it, and rerun. |
| `124` | The 45-minute timeout killed a hung run (a normal run is ~6.5 min) | Find the last `step` line in the logs to see where it hung, then rerun once. If it hangs again, investigate that step. |
| `137` | Out of memory, in practice | Peak was 2.58 GiB of 4 at 77K films, at the neighbours step. Check whether the catalog grew. Lower `index.chunk_size` in `config/default.yaml` (this needs a new image, section 6), or raise the task's memory in Terraform. |
| `TaskFailedToStart` (no exit code) | Image pull, network or secrets problem | Read `reason` in the email. Check the image tag and the SSM parameter (commands below), then rerun. |

```bash
aws logs tail /ecs/movierec --since 24h --filter-pattern quality_gate_failed
aws logs tail /ecs/movierec --since 24h --filter-pattern ERROR
aws ecr list-images --repository-name movierec-batch
aws ssm get-parameter --name /movierec/kaggle_api_token --query Parameter.Version
```

If the run died during the DynamoDB step, the table may hold a mix of two runs. Rerun it (section 2) or reload the last good run (section 3).

### Invocations alarm: `movierec-api-invocations`

The API had more than 1,000 requests in an hour, which is far above normal. Lambda's free tier absorbs a lot, so this is an early warning, not an outage. Check whether it's real traffic or abuse:

```bash
aws logs tail /aws/lambda/movierec-api --since 1h
```

If it's abuse, or the $2 budget alert has also fired, use the kill switch. It throttles every request, so **the site goes down**.

```bash
aws lambda put-function-concurrency --function-name movierec-api --reserved-concurrent-executions 0
```

Undo it:

```bash
aws lambda delete-function-concurrency --function-name movierec-api
```

Either command makes Terraform drift until `api_reserved_concurrency` matches (`0` while the switch is on, `null` after). Don't raise the account's Lambda concurrency limit without setting a cap in the same change.

### Test the alerts

This deliberately fails the gates on the small sample and costs ~$0.01. Expect two emails within about 6 minutes. Only the quality report is written, so run it before any real run on the same date, and the real run overwrites that report.

```bash
SUBNETS=$(aws scheduler get-schedule --name movierec-weekly-batch --query 'Target.EcsParameters.NetworkConfiguration.awsvpcConfiguration.Subnets' --output json)
SG=$(aws scheduler get-schedule --name movierec-weekly-batch --query 'Target.EcsParameters.NetworkConfiguration.awsvpcConfiguration.SecurityGroups[0]' --output text)
TASK_DEF=$(aws scheduler get-schedule --name movierec-weekly-batch --query 'Target.EcsParameters.TaskDefinitionArn' --output text)

aws ecs run-task \
  --cluster movierec \
  --launch-type FARGATE \
  --task-definition "$TASK_DEF" \
  --network-configuration "{\"awsvpcConfiguration\":{\"subnets\":$SUBNETS,\"securityGroups\":[\"$SG\"],\"assignPublicIp\":\"ENABLED\"}}" \
  --overrides '{"containerOverrides":[{"name":"batch","command":["--input","data/sample/movies_5k.csv","--skip-dynamo"]}]}'
```

## 5. When a gate fails

The site is safe: a failed gate stops the run before anything is loaded.

1. **Read the log lines.** Each failed gate logs its name, observed value and threshold.

   ```bash
   aws logs tail /ecs/movierec --since 24h --filter-pattern '"gate"'
   aws s3 cp "s3://$BUCKET/quality/<run-date>/quality_report.json" -
   ```

2. **Check what changed upstream.** Look at the [Kaggle dataset](https://www.kaggle.com/datasets/asaniczka/tmdb-movies-dataset-2023-930k-movies). Common causes are a truncated download, a changed column, or a real jump in rows.
3. **Decide.**
   - **A temporary upstream problem:** wait, then rerun (section 2). A rerun on the same date overwrites the report.
   - **The data really changed and is good:** adjust the threshold in `config/default.yaml`, with a dated line in `IMPLEMENTATION.md` §9 saying why. The config is baked into the image, so this needs a new image (section 6).
   - **The data is bad:** leave the previous run serving. If the next scheduled run would fail too, that's only more emails, since the gates protect the table.

**Never bypass a gate.** Don't remove one, don't override it to get a run through, and don't reload a run that failed one. `movierec.reload` refuses those.

## 6. Deploying and changing images

### Image rules

- **A new tag for every rebuild.** Lambda pins the image's digest at deploy, and ECR tags are mutable. Re-pushing a tag doesn't update the function, and `terraform plan` shows no change.
- **Keep-last-5 counts pushes, not deploys.** ECR keeps the last 5 images per repository. Never push 5 API images without applying, or the image the live function is pinned to can expire.
- **ECR's free allowance is 500 MB,** and about 385 MB was in use on 2026-10-26. Before the next push, pin dependencies and set `FROM` in both Dockerfiles to a digest: an unpinned rebuild can add ~230 MB of new layers. Above the allowance it costs about $0.10/GB-month.
- **Warm the site once after a new image,** before sharing it. The first cold start after a deploy has been ~4 s, against ~1.9 s normally.

### Change an image

This needs Docker. The `make push-*` targets log in to ECR themselves. For the API image:

```bash
make image-api
make push-image-api TAG=<new-tag>
terraform -chdir=infra apply -var api_image_tag=<new-tag>
```

For the batch image:

```bash
make image
make push-image TAG=<new-tag>
terraform -chdir=infra apply -var batch_image_tag=<new-tag>
```

Review the plan before you approve it. The API image needs `artifacts/api/titles.json.gz` (step 4 below). Tag names so far look like `t4.5-2026-10-26`.

To make it stick, update that variable's default in `infra/variables.tf`, because later applies otherwise revert it.

### First deploy

**This order has never been run on a fresh account, so treat it as untested and watch each step.** It costs about $0.28/month once running. "Before you run anything" can't work yet, since there are no outputs. You need Docker, Terraform, the AWS CLI, your own Kaggle API token, and the venv from section 3 for the `python` steps.

1. Set a **$2 AWS Budgets alert** in the AWS console.
2. Create `infra/terraform.tfvars` (gitignored) containing `alert_email = "<your-email>"`.
3. Create the image repositories first, because the Lambda function needs its image to exist:

   ```bash
   terraform -chdir=infra init
   terraform -chdir=infra apply -target='aws_ecr_repository.this'
   ```

4. Build the title index. This needs the raw CSV from [Kaggle](https://www.kaggle.com/datasets/asaniczka/tmdb-movies-dataset-2023-930k-movies) at that path:

   ```bash
   python -m movierec.index.titles --input data/raw/TMDB_movie_dataset_v11.csv
   ```

5. Build and push both images, each with a new tag:

   ```bash
   make image
   make push-image TAG=<batch-tag>
   make image-api
   make push-image-api TAG=<api-tag>
   ```

6. Set `batch_image_tag` and `api_image_tag` in `infra/variables.tf` to those tags, then apply everything:

   ```bash
   terraform -chdir=infra apply
   ```

7. Click the confirmation link in the SNS email, or no alert will reach you.
8. Put the real Kaggle token in SSM. Terraform only creates a placeholder, and never stores the token in state. `read -s` keeps it out of your shell history.

   ```bash
   read -s KAGGLE_TOKEN
   aws ssm put-parameter --name /movierec/kaggle_api_token --type SecureString --overwrite --value "$KAGGLE_TOKEN"
   unset KAGGLE_TOKEN
   ```

9. Upload the GloVe vectors the batch task reads from `s3://<bucket>/models/glove-100d.kv`. Saving writes two files, and both are needed:

   ```bash
   python -c "import gensim.downloader as d; d.load('glove-wiki-gigaword-100').save('glove-100d.kv')"
   export BUCKET=$(terraform -chdir=infra output -raw artifacts_bucket_name)
   aws s3 cp glove-100d.kv "s3://$BUCKET/models/"
   aws s3 cp glove-100d.kv.vectors.npy "s3://$BUCKET/models/"
   rm glove-100d.kv glove-100d.kv.vectors.npy
   ```

10. Trigger a run (section 2) and check it. Until the first run succeeds, the table is empty and the site finds no movies.

## 7. Command-line gotchas

- **A trailing `\` breaks a command pasted onto one line.** Either keep the line breaks or remove every `\`.
- **`file://` paths need the full path, in quotes if it has spaces:** `--target "file://$PWD/target.json"`, not `file://target.json`.
- **`aws logs tail --filter-pattern` needs quotes** around anything with punctuation.
- **Keep `#` comments out of pasted commands.** zsh doesn't treat `#` as a comment by default, and `make` would read the words after it as targets.
- **`terraform output -raw`** is for single values, and **`-json`** is for lists such as the subnets.
