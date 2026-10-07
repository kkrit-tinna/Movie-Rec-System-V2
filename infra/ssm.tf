# Kaggle API token for the batch task, injected by the task definition's
# `secrets` as KAGGLE_API_TOKEN (kaggle 2.2.4 checks it before any other
# credential). SecureString with the AWS-managed aws/ssm key: standard
# parameters and that key are free (Secrets Manager would be $0.40/month).
#
# The placeholder goes in through value_wo, a write-only argument: Terraform
# sends it to AWS but never stores it in state or plans. The real token is set
# out of band with `aws ssm put-parameter --overwrite`. Do not switch to
# `value` + ignore_changes: `value` is computed, so every refresh would read
# the decrypted real token back into terraform.tfstate.
resource "aws_ssm_parameter" "kaggle_api_token" {
  name             = "/${var.project}/kaggle_api_token"
  type             = "SecureString"
  value_wo         = "placeholder"
  value_wo_version = 1
}
