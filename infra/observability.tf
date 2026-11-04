locals {
  lambda_alarms = {
    api_errors         = { function = "api", metric = "Errors" }
    worker_errors      = { function = "worker", metric = "Errors" }
    worker_throttles   = { function = "worker", metric = "Throttles" }
    worker_dropped     = { function = "worker", metric = "AsyncEventsDropped" }
    worker_destination = { function = "worker", metric = "DestinationDeliveryFailures" }
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda" {
  for_each            = local.lambda_alarms
  alarm_name          = "${local.name}-${each.key}"
  alarm_description   = "Inspect structured logs and the failure queue; see docs/deployment.md."
  namespace           = "AWS/Lambda"
  metric_name         = each.value.metric
  dimensions          = { FunctionName = aws_lambda_function.app[each.value.function].function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
}

# API exceptions intentionally return a consistent JSON 500. They need their own metric.
resource "aws_cloudwatch_log_metric_filter" "api_errors" {
  name           = "${local.name}-api-errors"
  log_group_name = aws_cloudwatch_log_group.lambda["api"].name
  pattern        = "{ $.event = \"api_error\" }"
  metric_transformation {
    name      = "APIInternalErrors"
    namespace = local.name
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "api_internal" {
  alarm_name          = "${local.name}-api-internal"
  namespace           = local.name
  metric_name         = "APIInternalErrors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "failure_queue" {
  alarm_name          = "${local.name}-failure-queue"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.failures.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
}
