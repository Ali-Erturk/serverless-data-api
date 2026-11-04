resource "aws_apigatewayv2_api" "data" {
  name          = local.name
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.data.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.app["api"].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 10000
}

resource "aws_apigatewayv2_route" "data" {
  for_each           = toset(["POST /uploads", "GET /datasets/{id}", "GET /datasets/{id}/download"])
  api_id             = aws_apigatewayv2_api.data.id
  route_key          = each.key
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "AWS_IAM"
}

resource "aws_cloudwatch_log_group" "access" {
  name              = "/aws/apigateway/${local.name}"
  retention_in_days = 14
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.data.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 5
    throttling_rate_limit  = 2
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({
      request_id = "$context.requestId", route = "$context.routeKey",
      status     = "$context.status", integration_error = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowHTTPAPI"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app["api"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.data.execution_arn}/*/*"
}

resource "aws_iam_policy" "client" {
  name = "${local.name}-invoke"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Action = ["execute-api:Invoke"]
      Resource = [
        "${aws_apigatewayv2_api.data.execution_arn}/*/POST/uploads",
        "${aws_apigatewayv2_api.data.execution_arn}/*/GET/datasets/*"
      ]
    }]
  })
}
