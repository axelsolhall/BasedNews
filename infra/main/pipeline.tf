# Jobs as Step Functions state machines: one state per step, each a Fargate task of the same image running
# `python -m pipeline.job <step> --run-id <execution name>`. A failed step stops the execution; its record is in
# s3://<private>/prod/runs/<execution name>/<step>.json and its logs in /basednews/jobs (docs/DEPLOY.md).
locals {
  # all rolling, nothing tied to calendar weeks: new story pairs are judged daily as stories grow (A58)
  jobs = {
    refresh     = { steps = ["ingest", "normalize", "embed", "stories"], cron = "cron(5 * * * ? *)", enabled = var.refresh_enabled }
    feeds       = { steps = ["sources"], cron = "cron(30 3 * * ? *)", enabled = var.refresh_enabled }
    judge       = { steps = ["measure", "export"], cron = "cron(35 4 * * ? *)", enabled = var.judge_enabled }
    maintenance = { steps = ["retention"], cron = "cron(35 5 * * ? *)", enabled = var.maintenance_enabled }
  }
  # per-step extras: the paid step carries its budget (A48); retention deletes only in production (A52)
  # everything the definitions reference, in one place (so a test can render them with stand-ins)
  sfn_refs = {
    cluster      = aws_ecs_cluster.jobs.arn
    task_family  = aws_ecs_task_definition.job.family
    subnets      = data.aws_subnets.default.ids
    sg           = aws_security_group.jobs.id
    distribution = aws_cloudfront_distribution.site.id
  }
  step_env  = { measure = [{ Name = "BASEDNEWS_RUN_BUDGET_USD", Value = var.daily_budget_usd }] }
  step_args = { retention = ["--apply"] }

  task_state = { for step in distinct(flatten([for j in local.jobs : j.steps])) : step => {
    Type           = "Task"
    Resource       = "arn:aws:states:::ecs:runTask.sync"
    TimeoutSeconds = 5400
    Parameters = {
      LaunchType     = "FARGATE"
      Cluster        = local.sfn_refs.cluster
      TaskDefinition = local.sfn_refs.task_family # the newest active revision
      NetworkConfiguration = {
        AwsvpcConfiguration = { Subnets = local.sfn_refs.subnets, SecurityGroups = [local.sfn_refs.sg], AssignPublicIp = "ENABLED" }
      }
      Overrides = {
        ContainerOverrides = [merge(
          { Name = "job", "Command.$" = "States.Array('${step}', '--run-id', $$.Execution.Name${join("", [for a in lookup(local.step_args, step, []) : ", '${a}'"])})" },
          contains(keys(local.step_env), step) ? { Environment = local.step_env[step] } : {}
        )]
      }
    }
    # ADR-012: a failed step is tried once more after 2 min. The lease (pipeline/job/storage.py) lets the retry take over
    # the lock of a crashed attempt within 5 min. Not the paid step: a retry would start a second daily budget (A48).
    Retry = concat(
      [{ ErrorEquals = ["ECS.AmazonECSException"], IntervalSeconds = 60, MaxAttempts = 2, BackoffRate = 2 }],
      step == "measure" ? [] : [{ ErrorEquals = ["States.TaskFailed"], IntervalSeconds = 120, MaxAttempts = 1 }]
    )
  } }
}

locals {
  sfn_definition = { for job, j in local.jobs : job => {
    Comment = "BasedNews ${job}: ${join(" → ", j.steps)}"
    StartAt = j.steps[0]
    States = merge(
      { for i, step in j.steps : step => merge(local.task_state[step], # separate merges keep End a boolean
        i < length(j.steps) - 1 ? { Next = j.steps[i + 1] } : {},
        i == length(j.steps) - 1 && contains(j.steps, "export") ? { Next = "invalidate" } : {},
        i == length(j.steps) - 1 && !contains(j.steps, "export") ? { End = true } : {})
      },
      contains(j.steps, "export") ? {
        invalidate = {
          Type     = "Task"
          Resource = "arn:aws:states:::aws-sdk:cloudfront:createInvalidation"
          Parameters = {
            DistributionId = local.sfn_refs.distribution
            InvalidationBatch = {
              "CallerReference.$" = "$$.Execution.Name"
              Paths               = { Quantity = 1, Items = ["/data/*"] }
            }
          }
          End = true
        }
      } : {}
    )
  } }
}

resource "aws_sfn_state_machine" "job" {
  for_each   = local.jobs
  name       = "${local.name}-${each.key}"
  role_arn   = aws_iam_role.sfn.arn
  definition = jsonencode(local.sfn_definition[each.key])
}

resource "aws_iam_role" "sfn" {
  name = "${local.name}-sfn"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "sfn" {
  role = aws_iam_role.sfn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecs:RunTask"], Resource = ["arn:aws:ecs:${var.region}:${local.account}:task-definition/${aws_ecs_task_definition.job.family}:*"] },
      { Effect = "Allow", Action = ["ecs:StopTask", "ecs:DescribeTasks"], Resource = ["*"],
      Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.jobs.arn } } },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn] },
      { Effect = "Allow", Action = ["events:PutTargets", "events:PutRule", "events:DescribeRule"],
      Resource = ["arn:aws:events:${var.region}:${local.account}:rule/StepFunctionsGetEventsForECSTaskRule"] },
      { Effect = "Allow", Action = ["cloudfront:CreateInvalidation"], Resource = [aws_cloudfront_distribution.site.arn] },
    ]
  })
}

# EventBridge Scheduler starts the jobs. Created switched off; the kill switch can switch them off again.
resource "aws_scheduler_schedule_group" "jobs" {
  name = local.name
}

resource "aws_scheduler_schedule" "job" {
  for_each                     = local.jobs
  name                         = each.key
  group_name                   = aws_scheduler_schedule_group.jobs.name
  schedule_expression          = each.value.cron
  schedule_expression_timezone = "UTC"
  state                        = each.value.enabled ? "ENABLED" : "DISABLED"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_sfn_state_machine.job[each.key].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{}"
    retry_policy { maximum_retry_attempts = 0 } # a missed hour is caught up by the next one
  }
}

resource "aws_iam_role" "scheduler" {
  name = "${local.name}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = local.account } } }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["states:StartExecution"], Resource = [for m in aws_sfn_state_machine.job : m.arn] }]
  })
}
