# T4.5: one SNS topic, email only. Three things publish to it:
#   - the gate alarm (CloudWatch alarm on a log metric filter)
#   - the API traffic alarm (CloudWatch alarm on AWS/Lambda Invocations)
#   - the task-failure rule (EventBridge rule on ECS task state changes)
# Task failure is a rule, not an alarm: Container Insights is off (ecs.tf),
# so ECS publishes no task-failure metric to alarm on.
#
# A failed gate sends two emails: the pipeline logs quality_gate_failed (gate
# alarm) and then exits 1 (task-failure rule). Same incident, two views.

resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
}

# Stays PendingConfirmation until the link in AWS's email is clicked.
resource "aws_sns_topic_subscription" "alert_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# A custom topic policy replaces the default one, so the first statement
# restates the default owner access. The other two let the alarms and the
# rule publish, each scoped to this account and to their own ARNs.
data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid = "OwnerDefaultAccess"
    actions = [
      "SNS:GetTopicAttributes",
      "SNS:SetTopicAttributes",
      "SNS:AddPermission",
      "SNS:RemovePermission",
      "SNS:DeleteTopic",
      "SNS:Subscribe",
      "SNS:ListSubscriptionsByTopic",
      "SNS:Publish",
    ]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceOwner"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  statement {
    sid       = "CloudWatchAlarmsPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values = [
        aws_cloudwatch_metric_alarm.quality_gate_failed.arn,
        aws_cloudwatch_metric_alarm.api_invocations.arn,
      ]
    }
  }

  statement {
    sid       = "EventBridgeTaskFailurePublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.batch_task_failed.arn]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

# Gate alarm. pipeline.py logs the stable token quality_gate_failed when a
# gate aborts the run. No default value, so the metric only has data points
# on a failure and missing data reads as OK.
resource "aws_cloudwatch_log_metric_filter" "quality_gate_failed" {
  name           = "${var.project}-quality-gate-failed"
  log_group_name = aws_cloudwatch_log_group.batch.name
  pattern        = "\"quality_gate_failed\""

  metric_transformation {
    name      = "QualityGateFailed"
    namespace = var.project
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "quality_gate_failed" {
  alarm_name          = "${var.project}-quality-gate-failed"
  alarm_description   = "The weekly batch aborted on a data quality gate; nothing was promoted. See /ecs/${var.project}."
  namespace           = var.project
  metric_name         = aws_cloudwatch_log_metric_filter.quality_gate_failed.metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Traffic alarm: the API has no reserved concurrency (§9 2026-10-12), so this
# is the early warning before the $2 budget alert.
resource "aws_cloudwatch_metric_alarm" "api_invocations" {
  alarm_name          = "${var.project}-api-invocations"
  alarm_description   = "API invocations above ${var.api_invocations_alarm_threshold}/hour. Kill switch: reserved concurrency 0 (HANDOFF)."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = aws_lambda_function.api.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.api_invocations_alarm_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Task failure: any batch task that stops with a non-zero exit, or never
# starts. Exit codes:
#   1   = a quality gate failed, or an uncaught exception (crash)
#   124 = `timeout 45m` in the image ENTRYPOINT killed the run
#   137 = SIGKILL, in practice out of memory (§9 2026-10-07)
# A task that fails to start (image pull, ENI, secrets) has no exit code,
# hence the stopCode branch.
resource "aws_cloudwatch_event_rule" "batch_task_failed" {
  name        = "${var.project}-batch-task-failed"
  description = "Batch task stopped with a non-zero exit or failed to start."

  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Task State Change"]
    detail = {
      clusterArn = [aws_ecs_cluster.this.arn]
      lastStatus = ["STOPPED"]
      "$or" = [
        { containers = { exitCode = [{ "anything-but" = 0 }] } },
        { stopCode = ["TaskFailedToStart"] },
      ]
    }
  })
}

# One plain-text line. The template is a quoted JSON string, so SNS sends it
# as text rather than as a JSON object.
resource "aws_cloudwatch_event_target" "batch_task_failed" {
  rule      = aws_cloudwatch_event_rule.batch_task_failed.name
  target_id = "sns-alerts"
  arn       = aws_sns_topic.alerts.arn

  input_transformer {
    input_paths = {
      exitCode = "$.detail.containers[0].exitCode"
      stopCode = "$.detail.stopCode"
      reason   = "$.detail.stoppedReason"
      task     = "$.detail.taskArn"
    }
    input_template = "\"movierec batch task failed: exit code <exitCode>, stopCode <stopCode>, reason <reason>, task <task>\""
  }
}
