# T4.4b: the API. Lambda container image (arm64) behind a public Function
# URL. Not in a VPC: it reaches DynamoDB over the public endpoint, so no NAT
# or VPC endpoint is needed -- see §2.
#
# No reserved concurrency by default: the account's Lambda limit is 10, and
# AWS refuses a reservation that leaves fewer than 10 unreserved, so the
# account limit is the ceiling. 0 is the kill switch (§9 2026-10-12).

data "aws_iam_policy_document" "api_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${var.project}-api"
  assume_role_policy = data.aws_iam_policy_document.api_assume.json
}

# Read-only on the one table, logs to the one group. ECR pull is granted here
# rather than on the repository: for a same-account image either side may
# grant it, and with neither Lambda adds a repository policy itself, outside
# Terraform.
data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ReadMoviesTable"
    actions   = ["dynamodb:GetItem", "dynamodb:BatchGetItem"]
    resources = [aws_dynamodb_table.movies.arn]
  }

  # ":*" is the log-stream part of the ARN, within this one group.
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }

  statement {
    sid       = "PullApiImage"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [aws_ecr_repository.this["api"].arn]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "${var.project}-api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# Created before the function, so Lambda never creates it without retention.
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.project}-api"
  retention_in_days = 14
}

# Lambda resolves the tag to a digest at deploy: re-pushing the same tag does
# not update the function. Every rebuild gets a new api_image_tag.
resource "aws_lambda_function" "api" {
  function_name = "${var.project}-api"
  role          = aws_iam_role.api.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.this["api"].repository_url}:${var.api_image_tag}"
  architectures = ["arm64"]
  memory_size   = 512
  timeout       = 10

  reserved_concurrent_executions = var.api_reserved_concurrency

  # Only movierec's own variables: Lambda sets AWS_REGION and the other
  # reserved names itself and rejects them here. MOVIEREC_TITLES_PATH is set
  # in the image (Dockerfile.api).
  environment {
    variables = {
      MOVIEREC_DYNAMO_TABLE = aws_dynamodb_table.movies.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api]
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"
}

# A public URL needs both statements (AWS, since October 2025); only the
# console and SAM add them automatically.
resource "aws_lambda_permission" "api_url" {
  statement_id           = "FunctionURLAllowPublicAccess"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "api_url_invoke" {
  statement_id             = "FunctionURLInvokeAllowPublicAccess"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.api.function_name
  principal                = "*"
  invoked_via_function_url = true
}
