# The job image and how it runs: one image, one task definition, one container per step (ADR-010).
resource "aws_ecr_repository" "job" {
  name                 = "${local.name}-job"
  image_tag_mutability = "MUTABLE" # CI moves the `prod` tag; every image is also tagged with its commit
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "job" {
  repository = aws_ecr_repository.job.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the newest 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_ecs_cluster" "jobs" {
  name = "${local.name}-jobs"
  setting {
    name  = "containerInsights"
    value = "disabled" # paid metrics; the job records and logs already say what happened
  }
}

resource "aws_cloudwatch_log_group" "jobs" {
  name              = "/${local.name}/jobs"
  retention_in_days = 30
}

# Public subnets of the default VPC, outbound only: no NAT gateway (~$33/month), no inbound access (ADR-010).
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_security_group" "jobs" {
  name        = "${local.name}-jobs"
  description = "Job tasks: no inbound, HTTPS/HTTP out (feeds, OpenAI, AWS APIs)"
  vpc_id      = data.aws_vpc.default.id
  egress {
    description = "HTTPS out"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    description = "HTTP out (some feeds and archive.org redirects)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

locals {
  ssm_openai = "arn:aws:ssm:${var.region}:${local.account}:parameter/${local.name}/openai_api_key"
}

# Execution role: what ECS needs to start the task (pull the image, write logs, read the secret).
resource "aws_iam_role" "execution" {
  name = "${local.name}-task-execution"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["ssm:GetParameters"], Resource = [local.ssm_openai] }]
  })
}

# Task role: what the job itself may touch — the private bucket and the site's data/ folder, nothing else.
resource "aws_iam_role" "task" {
  name = "${local.name}-task"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "task" {
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [aws_s3_bucket.private.arn] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${aws_s3_bucket.private.arn}/*"] },
      { Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${aws_s3_bucket.site.arn}/data/*"] },
    ]
  })
}

resource "aws_ecs_task_definition" "job" {
  family                   = "${local.name}-job"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64" # Graviton: ≈ 20 % cheaper; the same arch as the maintainer's Mac
  }
  container_definitions = jsonencode([{
    name        = "job"
    image       = "${aws_ecr_repository.job.repository_url}:${var.image_tag}"
    essential   = true
    stopTimeout = 120 # ADR-012: SIGTERM → the step ends, uploads nothing, releases its lease; SIGKILL only after 2 min
    command     = ["list"]
    environment = [
      { name = "BASEDNEWS_STATE", value = "s3://${aws_s3_bucket.private.bucket}/prod" },
      { name = "BASEDNEWS_SITE_BUCKET", value = "s3://${aws_s3_bucket.site.bucket}/data" },
      { name = "BASEDNEWS_PUBLISH_POSITIONS", value = var.publish_positions ? "1" : "0" },
      { name = "AWS_REGION", value = var.region },
    ]
    secrets = [{ name = "OPENAI_API_KEY", valueFrom = local.ssm_openai }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.jobs.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "job"
      }
    }
  }])
}
