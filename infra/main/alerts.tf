# Failure → email: a failed or timed-out execution of any job (ADR-010 "failure → fix").
resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ADR-012: an email means "it did not heal itself". The hourly refresh retries a failed step, takes over a dead job's
# lease and rolls back a bad image, so one failure is no news: the alarm is "no successful refresh for 3 hours". Daily
# jobs already retried inside their run, so a failed daily execution is worth an email.
resource "aws_cloudwatch_metric_alarm" "job_failed" {
  for_each            = { for k, v in aws_sfn_state_machine.job : k => v if k != "refresh" }
  alarm_name          = "${local.name}-${each.key}-failed"
  alarm_description   = "A ${each.key} run failed even after its retry: see its step record in runs/ and the logs in /basednews/jobs"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = each.value.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "refresh_unhealthy" {
  alarm_name          = "${local.name}-refresh-unhealthy"
  alarm_description   = "No successful refresh for 3 hours, after retries, lease takeover and rollback: it did not heal itself. Runbook: docs/DEPLOY.md 'When an alarm email comes'."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsSucceeded"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.job["refresh"].arn }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  # no runs at all is unhealthy too — while the schedule is on; switched off, missing runs are expected (no false alarm)
  treat_missing_data = var.refresh_enabled ? "breaching" : "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn] # and an all-clear when it recovers
}

# Kill switch (A48): if the month's AWS cost passes killswitch_usd, every schedule is switched off by a small Lambda.
# The $10 budget made in the console stays the early email warning; this second budget is the brake (2 budgets free).
resource "aws_sns_topic" "killswitch" {
  name = "${local.name}-killswitch"
}

resource "aws_sns_topic_policy" "killswitch" {
  arn = aws_sns_topic.killswitch.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Sid = "BudgetsPublish", Effect = "Allow", Principal = { Service = "budgets.amazonaws.com" },
    Action = "SNS:Publish", Resource = aws_sns_topic.killswitch.arn, Condition = { StringEquals = { "aws:SourceAccount" = local.account } } }]
  })
}

resource "aws_budgets_budget" "killswitch" {
  name         = "${local.name}-killswitch"
  budget_type  = "COST"
  limit_amount = tostring(var.killswitch_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_sns_topic_arns  = [aws_sns_topic.killswitch.arn]
    subscriber_email_addresses = var.alert_email == "" ? [] : [var.alert_email]
  }
}

data "archive_file" "killswitch" {
  type        = "zip"
  source_file = "${path.module}/killswitch/handler.py"
  output_path = "${path.module}/.build/killswitch.zip"
}

resource "aws_lambda_function" "killswitch" {
  function_name    = "${local.name}-killswitch"
  role             = aws_iam_role.killswitch.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.killswitch.output_path
  source_code_hash = data.archive_file.killswitch.output_base64sha256
  timeout          = 60
  architectures    = ["arm64"]
  environment { variables = { GROUP = aws_scheduler_schedule_group.jobs.name } }
}

resource "aws_cloudwatch_log_group" "killswitch" {
  name              = "/aws/lambda/${aws_lambda_function.killswitch.function_name}"
  retention_in_days = 90
}

resource "aws_sns_topic_subscription" "killswitch" {
  topic_arn = aws_sns_topic.killswitch.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.killswitch.arn
}

resource "aws_lambda_permission" "killswitch" {
  statement_id  = "AllowSnsInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.killswitch.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.killswitch.arn
}

resource "aws_iam_role" "killswitch" {
  name = "${local.name}-killswitch"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "killswitch" {
  role = aws_iam_role.killswitch.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["scheduler:ListSchedules"], Resource = ["*"] },
      { Effect = "Allow", Action = ["scheduler:GetSchedule", "scheduler:UpdateSchedule"],
      Resource = ["arn:aws:scheduler:${var.region}:${local.account}:schedule/${aws_scheduler_schedule_group.jobs.name}/*"] },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.scheduler.arn] },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = ["${aws_cloudwatch_log_group.killswitch.arn}:*"] },
    ]
  })
}
