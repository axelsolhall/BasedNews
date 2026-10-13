# One-time bootstrap (ADR-010): the bucket that holds Terraform's state for infra/main. Local state, run once:
#   AWS_PROFILE=basednews terraform -chdir=infra/bootstrap init
#   AWS_PROFILE=basednews terraform -chdir=infra/bootstrap apply
# Locking uses S3's own lock file (Terraform ≥ 1.10, `use_lockfile`), so no DynamoDB table is needed.
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = "eu-north-1"
  default_tags { tags = { project = "basednews", managed_by = "terraform", stack = "bootstrap" } }
}

data "aws_caller_identity" "me" {}

resource "aws_s3_bucket" "state" {
  bucket = "basednews-tfstate-${data.aws_caller_identity.me.account_id}"
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 90 }
    abort_incomplete_multipart_upload { days_after_initiation = 2 }
  }
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}
