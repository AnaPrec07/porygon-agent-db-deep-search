data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  name       = "deep-search-${var.env}"

  # Principals that may touch the bucket directly. Unity Catalog reaches it through the storage role.
  allowed_bucket_principals = concat(
    [aws_iam_role.uc_storage.arn],
    var.break_glass_role_arns,
    var.terraform_principal_arns,
  )
}

# ---------------------------------------------------------------------------
# KMS key for SSE-KMS on the bucket
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "kms" {
  statement {
    sid       = "AccountRootAdministration"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid       = "KeyAdministrators"
    actions   = ["kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*", "kms:Put*", "kms:Update*", "kms:Revoke*", "kms:Disable*", "kms:Get*", "kms:Delete*", "kms:TagResource", "kms:UntagResource", "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = var.kms_admin_role_arns
    }
  }

  # Unity Catalog storage role needs data-key operations so SSE-KMS objects can be read and written.
  statement {
    sid       = "UnityCatalogStorageRoleDataKeys"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.uc_storage.arn]
    }
  }
}

resource "aws_kms_key" "data" {
  description             = "Deep Search ${var.env} data bucket"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.kms.json
}

resource "aws_kms_alias" "data" {
  name          = "alias/${local.name}-data"
  target_key_id = aws_kms_key.data.key_id
}

# ---------------------------------------------------------------------------
# Data bucket
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "data" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

# Lifecycle rules apply only under landing/. uc-managed/ holds Delta data and must never expire.
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  depends_on = [aws_s3_bucket_versioning.data]

  rule {
    id     = "records-tiering"
    status = "Enabled"
    filter {
      prefix = "landing/records/"
    }
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 180
      storage_class = "GLACIER_IR"
    }
  }

  rule {
    id     = "sources-expire"
    status = "Enabled"
    filter {
      prefix = "landing/artifacts/sources/"
    }
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    expiration {
      days = var.source_retention_days
    }
  }

  rule {
    id     = "landing-noncurrent-and-multipart"
    status = "Enabled"
    filter {
      prefix = "landing/"
    }
    noncurrent_version_expiration {
      noncurrent_days = var.record_noncurrent_retention_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "DenyPrincipalsOutsideAllowList"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "ArnNotLike"
      variable = "aws:PrincipalArn"
      values   = local.allowed_bucket_principals
    }
  }
}

resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id
  policy = data.aws_iam_policy_document.bucket.json

  depends_on = [aws_s3_bucket_public_access_block.data]
}

# ---------------------------------------------------------------------------
# Unity Catalog storage role
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "uc_trust" {
  statement {
    sid     = "UnityCatalogAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [var.databricks_uc_master_role_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.databricks_external_id]
    }
  }

  # Unity Catalog requires the role to assume itself for credential validation.
  statement {
    sid     = "SelfAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:role/${local.name}-uc-storage"]
    }
  }
}

resource "aws_iam_role" "uc_storage" {
  name               = "${local.name}-uc-storage"
  assume_role_policy = data.aws_iam_policy_document.uc_trust.json
  description        = "Unity Catalog storage credential role for the deep-search data bucket"
}

data "aws_iam_policy_document" "uc_storage" {
  statement {
    sid       = "BucketAccess"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:GetLifecycleConfiguration"]
    resources = [aws_s3_bucket.data.arn]
  }

  statement {
    sid       = "ObjectAccess"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:GetObjectVersion", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
    resources = ["${aws_s3_bucket.data.arn}/*"]
  }

  statement {
    sid       = "DataKeyUse"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = [aws_kms_key.data.arn]
  }
}

resource "aws_iam_role_policy" "uc_storage" {
  name   = "${local.name}-uc-storage-access"
  role   = aws_iam_role.uc_storage.id
  policy = data.aws_iam_policy_document.uc_storage.json
}

# ---------------------------------------------------------------------------
# CloudTrail S3 data events (plan §F.1)
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "audit" {
  bucket = var.audit_bucket_name
}

resource "aws_s3_bucket_public_access_block" "audit" {
  bucket                  = aws_s3_bucket.audit.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SSE-S3 rather than the data KMS key: the CloudTrail service principal is not in the data key policy,
# and CloudTrail writes to SSE-KMS buckets require key access it does not have here.
resource "aws_s3_bucket_server_side_encryption_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id

  rule {
    id     = "expire-audit-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = var.audit_retention_days
    }
  }
}

data "aws_iam_policy_document" "audit" {
  statement {
    sid       = "CloudTrailAclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.audit.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
  }

  statement {
    sid       = "CloudTrailWrite"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.audit.arn}/AWSLogs/${local.account_id}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }
}

resource "aws_s3_bucket_policy" "audit" {
  bucket = aws_s3_bucket.audit.id
  policy = data.aws_iam_policy_document.audit.json
}

resource "aws_cloudtrail" "data_events" {
  count = var.enable_cloudtrail_data_events ? 1 : 0

  name                          = "${local.name}-s3-data-events"
  s3_bucket_name                = aws_s3_bucket.audit.id
  include_global_service_events = false
  is_multi_region_trail         = false

  event_selector {
    read_write_type           = "All"
    include_management_events = false

    data_resource {
      type   = "AWS::S3::Object"
      values = ["${aws_s3_bucket.data.arn}/"]
    }
  }

  depends_on = [aws_s3_bucket_policy.audit]
}
