output "storage_credential_name" {
  value = databricks_storage_credential.deep_search.name
}

output "storage_credential_external_id" {
  description = "Paste this into infra/aws variable databricks_external_id, then re-apply infra/aws."
  value       = databricks_storage_credential.deep_search.aws_iam_role[0].external_id
}

output "unity_catalog_iam_arn" {
  description = "Compare with infra/aws variable databricks_uc_master_role_arn before production."
  value       = databricks_storage_credential.deep_search.aws_iam_role[0].unity_catalog_iam_arn
}

output "catalog_name" {
  value = databricks_catalog.deep_search.name
}

output "records_volume_path" {
  description = "Value for the app's DEEP_SEARCH_RECORDS_VOLUME (PR-6)."
  value       = "/Volumes/${databricks_catalog.deep_search.name}/${databricks_schema.landing.name}/${databricks_volume.records.name}"
}

output "artifacts_volume_path" {
  description = "Value for the app's DEEP_SEARCH_ARTIFACTS_VOLUME (PR-6)."
  value       = "/Volumes/${databricks_catalog.deep_search.name}/${databricks_schema.landing.name}/${databricks_volume.artifacts.name}"
}
