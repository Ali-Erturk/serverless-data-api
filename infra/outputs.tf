output "api_url" { value = aws_apigatewayv2_api.data.api_endpoint }
output "client_policy_arn" { value = aws_iam_policy.client.arn }
output "upload_bucket" { value = aws_s3_bucket.data["uploads"].id }
output "result_bucket" { value = aws_s3_bucket.data["results"].id }
output "table_name" { value = aws_dynamodb_table.datasets.name }
output "failure_queue_url" { value = aws_sqs_queue.failures.url }
output "worker_function" { value = aws_lambda_function.app["worker"].function_name }
