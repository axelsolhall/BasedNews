# GitHub Actions without stored keys (OIDC). Two narrow roles; neither can change infrastructure (A57):
#   deploy — main branch only: push the image, upload the site (never delete its data/), invalidate the cache;
#   plan   — pull requests: read-only, for `terraform plan`; no access to the private bucket's objects or the secrets.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  gh_aud = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
}

resource "aws_iam_role" "deploy" {
  name = "${local.name}-github-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = merge(local.gh_aud, { "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:ref:refs/heads/main" })
      }
    }]
  })
}

resource "aws_iam_role_policy" "deploy" {
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = ["*"] },
      { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
        "ecr:GetDownloadUrlForLayer", "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart"],
      Resource = [aws_ecr_repository.job.arn] },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [aws_s3_bucket.site.arn] },
      { Effect = "Allow", Action = ["s3:PutObject", "s3:DeleteObject"], Resource = ["${aws_s3_bucket.site.arn}/*"] },
      { Effect = "Deny", Action = ["s3:PutObject", "s3:DeleteObject"], Resource = ["${aws_s3_bucket.site.arn}/data/*"] },
      { Effect = "Allow", Action = ["cloudfront:CreateInvalidation"], Resource = [aws_cloudfront_distribution.site.arn] },
    ]
  })
}

resource "aws_iam_role" "plan" {
  name = "${local.name}-github-plan"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = merge(local.gh_aud, { "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:pull_request" })
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "plan_readonly" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "plan_limits" {
  role = aws_iam_role.plan.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Deny", Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = ["${aws_s3_bucket.private.arn}/*"] },
      { Effect = "Deny", Action = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
      Resource = ["arn:aws:ssm:${var.region}:${local.account}:parameter/${local.name}/*"] },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["arn:aws:s3:::basednews-tfstate-${local.account}/main.tfstate"] },
    ]
  })
}
