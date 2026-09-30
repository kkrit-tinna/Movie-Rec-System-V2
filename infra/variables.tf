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
