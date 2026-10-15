# Auto-rollback (ADR-012): every failed or timed-out BasedNews execution wakes a small Lambda that moves the `prod` image
# tag back to `prev` once the refresh has failed twice in a row with a newly deployed image (rollback/handler.py), and
# emails why. CI tags the outgoing image `prev` before it pushes a new `prod`.
data "archive_file" "rollback" {
  type        = "zip"
  source_file = "${path.module}/rollback/handler.py"
  output_path = "${path.module}/.build/rollback.zip"
}

resource "aws_lambda_function" "rollback" {
  function_name    = "${local.name}-rollback"
  role             = aws_iam_role.rollback.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.rollback.output_path
  source_code_hash = data.archive_file.rollback.output_base64sha256
  timeout          = 60
  architectures    = ["arm64"]
  environment {
    variables = {
      REPOSITORY   = aws_ecr_repository.job.name
      REFRESH_ARN  = aws_sfn_state_machine.job["refresh"].arn
      TOPIC        = aws_sns_topic.alerts.arn
      MIN_FAILURES = "2"
    }
  }
}

resource "aws_cloudwatch_log_group" "rollback" {
  name              = "/aws/lambda/${aws_lambda_function.rollback.function_name}"
  retention_in_days = 90
}

resource "aws_cloudwatch_event_rule" "execution_failed" {
  name        = "${local.name}-execution-failed"
  description = "A BasedNews job execution failed or timed out: let the rollback Lambda look (ADR-012)"
  event_pattern = jsonencode({
    source        = ["aws.states"]
    "detail-type" = ["Step Functions Execution Status Change"]
    detail = {
      status          = ["FAILED", "TIMED_OUT"]
      stateMachineArn = [for sm in aws_sfn_state_machine.job : sm.arn]
    }
  })
}

resource "aws_cloudwatch_event_target" "rollback" {
  rule = aws_cloudwatch_event_rule.execution_failed.name
  arn  = aws_lambda_function.rollback.arn
}

resource "aws_lambda_permission" "rollback" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rollback.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.execution_failed.arn
}

resource "aws_iam_role" "rollback" {
  name = "${local.name}-rollback"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "rollback" {
  role = aws_iam_role.rollback.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:DescribeImages", "ecr:BatchGetImage", "ecr:PutImage"], Resource = [aws_ecr_repository.job.arn] },
      { Effect = "Allow", Action = ["states:ListExecutions"], Resource = [aws_sfn_state_machine.job["refresh"].arn] },
      { Effect = "Allow", Action = ["sns:Publish"], Resource = [aws_sns_topic.alerts.arn] },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = ["${aws_cloudwatch_log_group.rollback.arn}:*"] },
    ]
  })
}
