terraform {
  required_version = ">= 1.6.0"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.50"
    }
  }
}

# Authentication comes from the environment (DATABRICKS_HOST plus OAuth or a profile).
# Do not add tokens to these files.
provider "databricks" {}
