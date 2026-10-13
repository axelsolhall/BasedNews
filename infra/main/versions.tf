# BasedNews production (ADR-010): a static site on S3 + CloudFront, scheduled container jobs on ECS Fargate run by
# Step Functions, the SQLite file and the Parquet lake in a private bucket, alarms by email, a spending kill switch.
#   AWS_PROFILE=basednews terraform -chdir=infra/main init -backend-config="bucket=<bootstrap's state_bucket output>"
#   AWS_PROFILE=basednews terraform -chdir=infra/main plan
# Applied by the maintainer, never by CI (A57); CI only plans on pull requests and deploys the image and the site.
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.7" }
  }
  backend "s3" { # bucket is passed at init (-backend-config), so the account id stays out of the public repo
    key          = "main.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
  default_tags { tags = { project = "basednews", managed_by = "terraform", stack = "main" } }
}

data "aws_caller_identity" "me" {}

locals {
  name    = "basednews"
  account = data.aws_caller_identity.me.account_id
}
