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

variable "api_image_tag" {
  description = "Tag of the movierec-api ECR image the Lambda runs. Use a new tag per rebuild: Lambda pins the digest at deploy."
  type        = string
  default     = "t4.4-2026-10-14b"
}

variable "api_reserved_concurrency" {
  description = "Reserved concurrency for the API Lambda. null = unreserved (the account limit of 10 is the ceiling); 0 = kill switch, throttles every request."
  type        = number
  default     = null

  validation {
    condition     = var.api_reserved_concurrency == null ? true : (var.api_reserved_concurrency >= 0 && floor(var.api_reserved_concurrency) == var.api_reserved_concurrency)
    error_message = "api_reserved_concurrency must be null or a whole number >= 0 (0 is the kill switch)."
  }
}
