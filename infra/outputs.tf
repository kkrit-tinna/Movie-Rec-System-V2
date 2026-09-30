output "artifacts_bucket_name" {
  value = aws_s3_bucket.artifacts.bucket
}

output "movies_table_name" {
  value = aws_dynamodb_table.movies.name
}

output "batch_repository_url" {
  value = aws_ecr_repository.this["batch"].repository_url
}

output "api_repository_url" {
  value = aws_ecr_repository.this["api"].repository_url
}

output "batch_task_role_arn" {
  value = aws_iam_role.task.arn
}

output "batch_security_group_id" {
  value = aws_security_group.batch.id
}

output "default_subnet_ids" {
  value = data.aws_subnets.default.ids
}
