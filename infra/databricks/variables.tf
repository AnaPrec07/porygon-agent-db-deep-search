variable "env" {
  description = "Environment name, for example dev, staging or prod."
  type        = string
}

variable "aws_bucket_name" {
  description = "Data bucket created by infra/aws (output bucket_name)."
  type        = string
}

variable "uc_storage_role_arn" {
  description = "IAM role for the storage credential (output uc_storage_role_arn from infra/aws)."
  type        = string
}

variable "catalog_name" {
  description = "Unity Catalog catalog for deep-search data."
  type        = string
  default     = "deep_search"
}

variable "app_service_principal_id" {
  description = <<-EOT
    Application ID of the Databricks App's service principal. Databricks Apps creates it when the app is
    first deployed, so this is set after that first deploy.
  EOT
  type        = string
}

variable "pipeline_service_principal_id" {
  description = "Application ID of the service principal that runs the memory pipeline and refresh job."
  type        = string
}

variable "platform_admins_group" {
  description = "Account group that administers the storage objects and external volumes."
  type        = string
}

variable "tavily_connection_name" {
  description = "Unity Catalog connection used by the app for web search. Created outside this module (PR-5)."
  type        = string
  default     = "tavily_mcp"
}
