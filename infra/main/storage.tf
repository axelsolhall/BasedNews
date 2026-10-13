# The private bucket: the hot SQLite file (db/), step records (runs/), the Parquet lake (lake/), the lock (db/LOCK).
resource "aws_s3_bucket" "private" {
  bucket = "${local.name}-private-${local.account}"
}

resource "aws_s3_bucket_versioning" "private" {
  bucket = aws_s3_bucket.private.id
  versioning_configuration { status = "Enabled" } # earlier database versions are the backup (A55)
}

resource "aws_s3_bucket_server_side_encryption_configuration" "private" {
  bucket = aws_s3_bucket.private.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "private" {
  bucket                  = aws_s3_bucket.private.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "private" {
  bucket = aws_s3_bucket.private.id
  rule {
    id     = "database-versions"
    status = "Enabled"
    filter { prefix = "db/" }
    noncurrent_version_expiration { # every step uploads (≈ 100 versions a day × 0.13 GB): keep about one day
      noncurrent_days           = 1
      newer_noncurrent_versions = 24
    }
  }
  rule {
    id     = "step-records"
    status = "Enabled"
    filter { prefix = "runs/" }
    expiration { days = 400 }
    noncurrent_version_expiration { noncurrent_days = 7 }
  }
  rule {
    id     = "lake"
    status = "Enabled"
    filter { prefix = "lake/" }
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
  rule {
    id     = "abort-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 2 }
  }
}

# The site bucket: private, read only by CloudFront (OAC). The export job writes data/, CI writes everything else.
resource "aws_s3_bucket" "site" {
  bucket = "${local.name}-site-${local.account}"
}

resource "aws_s3_bucket_versioning" "site" {
  bucket = aws_s3_bucket.site.id
  versioning_configuration { status = "Enabled" } # a bad deploy can be rolled back
}

resource "aws_s3_bucket_lifecycle_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    id     = "old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 30 }
    abort_incomplete_multipart_upload { days_after_initiation = 2 }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "CloudFrontReadOnly"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.site.arn}/*"
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.site.arn } }
    }]
  })
}
