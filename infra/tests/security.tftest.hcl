mock_provider "aws" {}

variables {
  aws_account_id = "123456789012"
}

run "security_contract" {
  command = plan
  assert {
    condition     = alltrue([for r in aws_apigatewayv2_route.data : r.authorization_type == "AWS_IAM"])
    error_message = "Every route must require IAM authorization."
  }
  assert {
    condition     = alltrue([for b in aws_s3_bucket_public_access_block.data : b.block_public_acls && b.block_public_policy && b.ignore_public_acls && b.restrict_public_buckets])
    error_message = "Both buckets must block every form of public access."
  }
  assert {
    condition     = aws_s3_bucket_versioning.uploads.versioning_configuration[0].status == "Enabled"
    error_message = "Worker input identity requires versioned uploads."
  }
  assert {
    condition     = aws_lambda_function.app["worker"].timeout < 60
    error_message = "Processing lease must outlive Lambda execution."
  }
  assert {
    condition     = aws_lambda_function_event_invoke_config.worker.maximum_retry_attempts == 2
    error_message = "Transient failures must retain asynchronous retries."
  }
  assert {
    condition     = !aws_s3_bucket.data["uploads"].force_destroy && !aws_s3_bucket.data["results"].force_destroy
    error_message = "Deleting stored data must be explicitly opted into."
  }
}
