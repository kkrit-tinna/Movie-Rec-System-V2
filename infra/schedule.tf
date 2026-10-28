# T4.5: the weekly batch run. EventBridge Scheduler calls ecs:RunTask
# directly, with the same network settings as the manual run (public default
# subnet, public IP, no NAT -- see §2).

# Assumable only by EventBridge Scheduler acting for this account.
data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.project}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

# RunTask on any revision of the batch family, in this cluster only, and
# PassRole on exactly the two roles the task definition names.
data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "RunBatchTask"
    actions   = ["ecs:RunTask"]
    resources = ["${aws_ecs_task_definition.batch.arn_without_revision}:*"]

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.this.arn]
    }
  }

  statement {
    sid       = "PassBatchRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${var.project}-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

# Sundays 02:00 New York time, DST-aware. Sun Nov 1 2026 is the night DST
# ends, so the first run fires at 07:00 UTC (EST), not 06:00.
resource "aws_scheduler_schedule" "weekly_batch" {
  name                         = "${var.project}-weekly-batch"
  schedule_expression          = "cron(0 2 ? * SUN *)"
  schedule_expression_timezone = "America/New_York"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.scheduler.arn

    # No retries: each retry is a second paid run (~$0.06, mostly DynamoDB
    # writes). A failed run is reported by alerts.tf and rerun by hand.
    retry_policy {
      maximum_retry_attempts = 0
    }

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.batch.arn
      launch_type         = "FARGATE"
      task_count          = 1

      network_configuration {
        subnets          = data.aws_subnets.default.ids
        security_groups  = [aws_security_group.batch.id]
        assign_public_ip = true
      }
    }
  }
}
