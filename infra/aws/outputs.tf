output "bucket_name" {
  description = "Name of the data bucket."
  value       = aws_s3_bucket.data.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.data.arn
}

output "kms_key_arn" {
  value = aws_kms_key.data.arn
}

output "uc_storage_role_arn" {
  description = "Use as aws_iam_role.role_arn in the Databricks storage credential."
  value       = aws_iam_role.uc_storage.arn
}

output "uc_storage_role_name" {
  value = aws_iam_role.uc_storage.name
}
