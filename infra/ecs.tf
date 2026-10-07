# T4.3: the weekly batch task. Fargate in a public default subnet with a
# public IP (assignPublicIp at run-task time), no NAT -- see §2.

resource "aws_ecs_cluster" "this" {
  name = var.project

  # Explicitly off: Container Insights is billed per metric.
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "batch" {
  name              = "/ecs/${var.project}"
  retention_in_days = 14
}

# Execution role: used by ECS itself to pull the image, ship logs and fetch
# the Kaggle token before the container starts. Distinct from the task role
# (iam.tf), which is what the pipeline code runs as.
resource "aws_iam_role" "execution" {
  name               = "${var.project}-batch-execution"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The token is a SecureString under the AWS-managed aws/ssm key, which SSM
# decrypts on the caller's behalf: no kms:Decrypt grant is needed.
data "aws_iam_policy_document" "execution_ssm" {
  statement {
    sid       = "ReadKaggleToken"
    actions   = ["ssm:GetParameters"]
    resources = [aws_ssm_parameter.kaggle_api_token.arn]
  }
}

resource "aws_iam_role_policy" "execution_ssm" {
  name   = "${var.project}-batch-execution-ssm"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_ssm.json
}

# 2 vCPU / 4 GiB on ARM64 (Graviton). Ephemeral storage is left at the 20 GB
# default (§7 said 30; the raw CSV plus zip is ~1-2 GB). No command: the image
# ENTRYPOINT is `timeout 45m python -m movierec.pipeline`, run_date defaults
# to today in UTC, and production uses default.yaml floors (no --config).
resource "aws_ecs_task_definition" "batch" {
  family                   = "${var.project}-batch"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "2048"
  memory                   = "4096"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name      = "batch"
    image     = "${aws_ecr_repository.this["batch"].repository_url}:${var.batch_image_tag}"
    essential = true

    environment = [
      { name = "STORAGE_BACKEND", value = "s3" },
      { name = "MOVIEREC_S3_BUCKET", value = aws_s3_bucket.artifacts.bucket },
      { name = "MOVIEREC_WORD2VEC_MODEL_PATH", value = "s3://${aws_s3_bucket.artifacts.bucket}/models/glove-100d.kv" },
      # boto3 needs a region; set explicitly rather than rely on the agent.
      { name = "AWS_DEFAULT_REGION", value = var.region },
    ]

    secrets = [
      { name = "KAGGLE_API_TOKEN", valueFrom = aws_ssm_parameter.kaggle_api_token.arn },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "batch"
      }
    }
  }])
}
