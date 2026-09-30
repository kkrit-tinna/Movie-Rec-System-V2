# Data sources only, never aws_default_vpc / aws_default_subnet, so that
# terraform destroy can never touch the default VPC.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# No inline rules. Egress is a separate rule resource; there is no ingress.
resource "aws_security_group" "batch" {
  name        = "${var.project}-batch"
  description = "Batch task: egress only, no ingress"
  vpc_id      = data.aws_vpc.default.id
}

# Public egress reaches Kaggle, S3, DynamoDB and ECR without a NAT gateway
# or VPC endpoints.
resource "aws_vpc_security_group_egress_rule" "batch_all" {
  security_group_id = aws_security_group.batch.id
  description       = "All outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
