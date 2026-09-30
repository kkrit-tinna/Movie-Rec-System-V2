data "aws_iam_policy_document" "task_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = "${var.project}-batch-task"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

# Scoped to the one bucket and the one table. No s3:DeleteObject: the
# ArtifactStore interface has no delete.
data "aws_iam_policy_document" "task" {
  statement {
    sid       = "ListArtifactsBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn]
  }

  statement {
    sid       = "ReadWriteArtifactObjects"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
  }

  statement {
    sid = "WriteMoviesTable"
    actions = [
      "dynamodb:BatchWriteItem",
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:DescribeTable",
    ]
    resources = [aws_dynamodb_table.movies.arn]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "${var.project}-batch-task"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}
