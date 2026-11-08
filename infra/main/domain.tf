# The site's own domain (2026-11-08); its DNS is at Cloudflare (Cloudflare Registrar needs Cloudflare's DNS), the site
# stays on CloudFront. Two applies, so nothing waits on DNS:
#   1. domain = "basednews.info"  → an ACM certificate (us-east-1: CloudFront's region); output cloudflare_records lists
#      the records to add at Cloudflare, all "DNS only" (grey cloud): the certificate check and the site itself.
#   2. domain_ready = true        → waits for the certificate to be issued, then serves apex + www on CloudFront.
#      The *.cloudfront.net address keeps working.
# With domain = "" (the default) none of this exists.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
  default_tags { tags = { project = "basednews", managed_by = "terraform", stack = "main" } }
}

locals {
  domain_on = var.domain != ""
  cert_on   = local.domain_on && var.domain_ready
  hosts     = local.domain_on ? [var.domain, "www.${var.domain}"] : []
}

resource "aws_acm_certificate" "site" {
  count                     = local.domain_on ? 1 : 0
  provider                  = aws.us_east_1
  domain_name               = var.domain
  subject_alternative_names = ["www.${var.domain}"]
  validation_method         = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_acm_certificate_validation" "site" {
  count           = local.cert_on ? 1 : 0
  provider        = aws.us_east_1
  certificate_arn = aws_acm_certificate.site[0].arn
}

output "cloudflare_records" {
  description = "add at Cloudflare → DNS, proxy off (DNS only); then domain_ready = true"
  value = local.domain_on ? concat(
    [for o in aws_acm_certificate.site[0].domain_validation_options :
    { type = o.resource_record_type, name = trimsuffix(o.resource_record_name, "."), content = trimsuffix(o.resource_record_value, ".") }],
    [for h in local.hosts : { type = "CNAME", name = h, content = aws_cloudfront_distribution.site.domain_name }]
  ) : []
}
