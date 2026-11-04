variable "aws_region" {
  type        = string
  description = "Explicit deployment region; demo proposal uses us-east-1."
  default     = "us-east-1"
}

variable "aws_account_id" {
  type        = string
  description = "Approved destination account; provider rejects other accounts."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Supply a 12-digit AWS account ID."
  }
}

variable "name_prefix" {
  type    = string
  default = "ali-data-api"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.name_prefix))
    error_message = "Use 3-20 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "environment" {
  type    = string
  default = "demo"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,9}$", var.environment))
    error_message = "Use 2-10 lowercase letters, digits or hyphens."
  }
}

variable "alarm_actions" {
  type        = list(string)
  default     = []
  description = "Optional existing SNS topic ARNs; alarms have no notifications until configured."
}

variable "allow_destroy_data" {
  type        = bool
  default     = false
  description = "Explicit opt-in to empty ONLY this project's buckets during terraform destroy."
}
