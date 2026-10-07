data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.project}-artifacts-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Run folders written by pipeline.py. S3Store keys mirror LocalStore paths
# (§3), so these are top-level prefixes, not artifacts/... A new run folder
# needs adding here; a missing one only costs storage, never deletes.
locals {
  run_prefixes = ["tfidf/", "word2vec/", "catalog/", "comparison/", "quality/"]
}

# One expiry rule per run folder. current.json and models/ match no rule, so
# they and all their noncurrent versions are kept forever: current.json's
# version history is the rollback mechanism. Rollback therefore reaches back
# ~60 days (+7 for the noncurrent copy of an expired run).
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  dynamic "rule" {
    for_each = local.run_prefixes

    content {
      id     = "expire-${trimsuffix(rule.value, "/")}"
      status = "Enabled"

      filter {
        prefix = rule.value
      }

      expiration {
        days = 60
      }

      noncurrent_version_expiration {
        noncurrent_days = 7
      }
    }
  }

  # Bucket-wide, but it only ever touches unfinished multipart uploads.
  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.artifacts]
}
