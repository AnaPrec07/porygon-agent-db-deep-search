variable "region" {
  description = "AWS region. Must match the Databricks workspace region."
  type        = string
}

variable "env" {
  description = "Environment name, for example dev, staging or prod."
  type        = string
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name, for example acme-deep-search-prod-us-east-1."
  type        = string
}

variable "audit_bucket_name" {
  description = "Separate bucket that receives CloudTrail data-event logs."
  type        = string
}

variable "databricks_uc_master_role_arn" {
  description = <<-EOT
    Unity Catalog master role that assumes the storage role. Use the value Databricks documents for your
    cloud. Verify it against the storage credential's unity_catalog_iam_arn output before production.
  EOT
  type        = string
  default     = "arn:aws:iam::414351767826:role/unity-catalog-prod-UCMasterRole-14S5ZJVKOTYTL"
}

variable "databricks_external_id" {
  description = <<-EOT
    External ID that the Databricks storage credential generates. Two-phase apply:
    1. Apply with the default placeholder "0000" to create the role and bucket.
    2. Create the storage credential (infra/databricks), read its external_id output, set this variable,
       and apply this module again to replace the trust-policy placeholder.
  EOT
  type        = string
  default     = "0000"
}

variable "terraform_principal_arns" {
  description = "IAM principal ARNs allowed to administer the bucket (for example the Terraform execution role)."
  type        = list(string)
  default     = []
}

variable "break_glass_role_arns" {
  description = "Emergency admin roles allowed to access the bucket directly. Keep this list short."
  type        = list(string)
  default     = []
}

variable "kms_admin_role_arns" {
  description = "Roles allowed to administer the KMS key (not to use it for data)."
  type        = list(string)
  default     = []
}

variable "source_retention_days" {
  description = "Days before fetched source text under landing/artifacts/sources/ expires (plan §F.9, L2)."
  type        = number
  default     = 365
}

variable "record_noncurrent_retention_days" {
  description = "Days that noncurrent object versions under landing/ are kept (plan §F.9, L3)."
  type        = number
  default     = 30
}

variable "audit_retention_days" {
  description = "Days that CloudTrail logs are kept in the audit bucket."
  type        = number
  default     = 365
}

variable "enable_cloudtrail_data_events" {
  description = "Whether to record S3 object-level events for the deep-search bucket."
  type        = bool
  default     = true
}
