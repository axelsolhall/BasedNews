output "site_url" {
  value = "https://${aws_cloudfront_distribution.site.domain_name}"
}

# Copy these six into GitHub → Settings → Secrets and variables → Actions → Variables (not secrets; none is secret).
output "github_variables" {
  value = {
    AWS_DEPLOY_ROLE_ARN = aws_iam_role.deploy.arn
    AWS_PLAN_ROLE_ARN   = aws_iam_role.plan.arn
    ECR_REPOSITORY      = aws_ecr_repository.job.repository_url
    SITE_BUCKET         = aws_s3_bucket.site.bucket
    CF_DISTRIBUTION_ID  = aws_cloudfront_distribution.site.id
    TF_STATE_BUCKET     = "basednews-tfstate-${local.account}"
  }
}

output "state_url" {
  description = "BASEDNEWS_STATE for push-db (docs/DEPLOY.md step 3)"
  value       = "s3://${aws_s3_bucket.private.bucket}/prod"
}

output "state_machines" {
  value = { for k, m in aws_sfn_state_machine.job : k => m.arn }
}
