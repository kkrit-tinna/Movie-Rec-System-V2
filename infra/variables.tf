variable "region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for resources."
  type        = string
  default     = "movierec"
}

variable "batch_image_tag" {
  description = "Tag of the movierec-batch ECR image the task definition runs."
  type        = string
  default     = "t4.3-2026-10-07"
}
