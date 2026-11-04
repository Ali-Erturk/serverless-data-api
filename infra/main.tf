locals {
  name = "${var.name_prefix}-${var.environment}"
  buckets = {
    uploads = "${local.name}-${var.aws_account_id}-${var.aws_region}-uploads"
    results = "${local.name}-${var.aws_account_id}-${var.aws_region}-results"
  }
}

resource "aws_s3_bucket" "data" {
  for_each      = local.buckets
  bucket        = each.value
  force_destroy = var.allow_destroy_data
}

resource "aws_s3_bucket_public_access_block" "data" {
  for_each                = local.buckets
  bucket                  = aws_s3_bucket.data[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.data[each.key].id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.data[each.key].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_policy" "tls" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.data[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport", Effect = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.data[each.key].arn, "${aws_s3_bucket.data[each.key].arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_s3_bucket_versioning" "uploads" {
  bucket = aws_s3_bucket.data["uploads"].id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.data["uploads"].id
  rule {
    id     = "expire-inputs"
    status = "Enabled"
    filter { prefix = "" }
    expiration { days = 1 }
    noncurrent_version_expiration { noncurrent_days = 1 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
  rule {
    id     = "remove-delete-markers"
    status = "Enabled"
    filter { prefix = "" }
    expiration { expired_object_delete_marker = true }
  }
  depends_on = [aws_s3_bucket_versioning.uploads]
}

resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.data["results"].id
  rule {
    id     = "expire-results"
    status = "Enabled"
    filter { prefix = "" }
    expiration { days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

resource "aws_dynamodb_table" "datasets" {
  name         = local.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "dataset_id"
  attribute {
    name = "dataset_id"
    type = "S"
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  server_side_encryption { enabled = true }
}

resource "aws_sqs_queue" "failures" {
  name                      = "${local.name}-failures"
  message_retention_seconds = 604800
  sqs_managed_sse_enabled   = true
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = toset(["api", "worker"])
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = 14
}

resource "aws_iam_role" "lambda" {
  for_each = toset(["api", "worker"])
  name     = "${local.name}-${each.key}"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "lambda" {
  for_each = toset(["api", "worker"])
  role     = aws_iam_role.lambda[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect   = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.lambda[each.key].arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = each.key == "api" ? ["dynamodb:GetItem", "dynamodb:PutItem"] : ["dynamodb:GetItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.datasets.arn
      },
      {
        Effect   = "Allow"
        Action   = each.key == "api" ? ["s3:PutObject"] : ["s3:GetObjectVersion"]
        Resource = "${aws_s3_bucket.data["uploads"].arn}/uploads/*"
      },
      {
        Effect   = "Allow"
        Action   = each.key == "api" ? ["s3:GetObject"] : ["s3:PutObject"]
        Resource = "${aws_s3_bucket.data["results"].arn}/results/*"
      }
      ], each.key == "worker" ? [{
        Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.failures.arn
    }] : [])
  })
}

resource "aws_lambda_function" "app" {
  for_each         = toset(["api", "worker"])
  function_name    = "${local.name}-${each.key}"
  role             = aws_iam_role.lambda[each.key].arn
  runtime          = "python3.12"
  handler          = "data_api.${each.key}.handler"
  architectures    = ["x86_64"]
  filename         = "${path.module}/../build/lambda.zip"
  source_code_hash = filebase64sha256("${path.module}/../build/lambda.zip")
  memory_size      = 256
  timeout          = each.key == "worker" ? 30 : 10
  environment {
    variables = {
      TABLE_NAME    = aws_dynamodb_table.datasets.name
      UPLOAD_BUCKET = aws_s3_bucket.data["uploads"].id
      RESULT_BUCKET = aws_s3_bucket.data["results"].id
    }
  }
  depends_on = [aws_iam_role_policy.lambda]
}

resource "aws_lambda_function_event_invoke_config" "worker" {
  function_name                = aws_lambda_function.app["worker"].function_name
  maximum_event_age_in_seconds = 900
  maximum_retry_attempts       = 2
  destination_config {
    on_failure { destination = aws_sqs_queue.failures.arn }
  }
}

resource "aws_lambda_permission" "s3" {
  statement_id   = "AllowUploadEvents"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.app["worker"].function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.data["uploads"].arn
  source_account = var.aws_account_id
}

resource "aws_s3_bucket_notification" "uploads" {
  bucket = aws_s3_bucket.data["uploads"].id
  lambda_function {
    lambda_function_arn = aws_lambda_function.app["worker"].arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "uploads/"
  }
  depends_on = [aws_lambda_permission.s3, aws_s3_bucket_versioning.uploads, aws_lambda_function_event_invoke_config.worker]
}
