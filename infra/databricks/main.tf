locals {
  landing_url  = "s3://${var.aws_bucket_name}/landing/"
  managed_url  = "s3://${var.aws_bucket_name}/uc-managed/"
  records_path = "${local.landing_url}records/"
  artifacts    = "${local.landing_url}artifacts/"
}

# ---------------------------------------------------------------------------
# Storage credential: Unity Catalog assumes the IAM role in infra/aws.
# ---------------------------------------------------------------------------

resource "databricks_storage_credential" "deep_search" {
  name    = "deep_search_s3_${var.env}"
  comment = "Deep Search ${var.env} storage role (see infra/README.md for the two-phase trust setup)"

  aws_iam_role {
    role_arn = var.uc_storage_role_arn
  }
}

# ---------------------------------------------------------------------------
# External locations. They do not overlap: landing/ holds volumes, uc-managed/ holds catalog managed storage.
# The plan's single landing location is split on purpose; PR-1 check A10 confirms this layout.
# ---------------------------------------------------------------------------

resource "databricks_external_location" "landing" {
  name            = "deep_search_landing_${var.env}"
  url             = local.landing_url
  credential_name = databricks_storage_credential.deep_search.name
  comment         = "Immutable record and artifact files written by the app"
}

resource "databricks_external_location" "managed" {
  name            = "deep_search_managed_${var.env}"
  url             = local.managed_url
  credential_name = databricks_storage_credential.deep_search.name
  comment         = "Delta data for the deep_search catalog (never lifecycle-expired)"
}

# ---------------------------------------------------------------------------
# Catalog and schemas
# ---------------------------------------------------------------------------

resource "databricks_catalog" "deep_search" {
  name         = var.catalog_name
  comment      = "Deep Search research memory (${var.env})"
  storage_root = local.managed_url

  depends_on = [databricks_external_location.managed]
}

resource "databricks_schema" "landing" {
  catalog_name = databricks_catalog.deep_search.name
  name         = "landing"
  comment      = "External volumes holding app-written record and artifact files"
}

resource "databricks_schema" "bronze" {
  catalog_name = databricks_catalog.deep_search.name
  name         = "bronze"
  comment      = "Raw streaming tables ingested from landing volumes"
}

resource "databricks_schema" "memory" {
  catalog_name = databricks_catalog.deep_search.name
  name         = "memory"
  comment      = "Curated Delta tables and AI Search index sources"
}

# ---------------------------------------------------------------------------
# External volumes over the landing prefixes
# ---------------------------------------------------------------------------

resource "databricks_volume" "records" {
  catalog_name     = databricks_catalog.deep_search.name
  schema_name      = databricks_schema.landing.name
  name             = "records"
  volume_type      = "EXTERNAL"
  storage_location = "${local.landing_url}records/"
  comment          = "Versioned JSON and JSONL records (plan §F.2)"

  depends_on = [databricks_external_location.landing]
}

resource "databricks_volume" "artifacts" {
  catalog_name     = databricks_catalog.deep_search.name
  schema_name      = databricks_schema.landing.name
  name             = "artifacts"
  volume_type      = "EXTERNAL"
  storage_location = "${local.landing_url}artifacts/"
  comment          = "Content-addressed source text and final report markdown"

  depends_on = [databricks_external_location.landing]
}

# ---------------------------------------------------------------------------
# Grants (plan §M.6 permission matrix). Least privilege: no ALL PRIVILEGES, no MODIFY on landing.
# ---------------------------------------------------------------------------

resource "databricks_grants" "external_location_landing" {
  external_location = databricks_external_location.landing.name

  grant {
    principal  = var.platform_admins_group
    privileges = ["CREATE_EXTERNAL_VOLUME", "READ_FILES", "WRITE_FILES"]
  }
}

resource "databricks_grants" "external_location_managed" {
  external_location = databricks_external_location.managed.name

  grant {
    principal  = var.platform_admins_group
    privileges = ["CREATE_MANAGED_STORAGE", "READ_FILES", "WRITE_FILES"]
  }
}

resource "databricks_grants" "catalog" {
  catalog = databricks_catalog.deep_search.name

  grant {
    principal  = var.app_service_principal_id
    privileges = ["USE_CATALOG"]
  }

  grant {
    principal  = var.pipeline_service_principal_id
    privileges = ["USE_CATALOG"]
  }
}

resource "databricks_grants" "schema_landing" {
  schema = "${databricks_catalog.deep_search.name}.${databricks_schema.landing.name}"

  grant {
    principal  = var.app_service_principal_id
    privileges = ["USE_SCHEMA"]
  }

  grant {
    principal  = var.pipeline_service_principal_id
    privileges = ["USE_SCHEMA"]
  }
}

resource "databricks_grants" "schema_bronze" {
  schema = "${databricks_catalog.deep_search.name}.${databricks_schema.bronze.name}"

  grant {
    principal  = var.pipeline_service_principal_id
    privileges = ["USE_SCHEMA", "CREATE_TABLE", "CREATE_MATERIALIZED_VIEW", "MODIFY", "SELECT"]
  }
}

resource "databricks_grants" "schema_memory" {
  schema = "${databricks_catalog.deep_search.name}.${databricks_schema.memory.name}"

  grant {
    principal  = var.app_service_principal_id
    privileges = ["USE_SCHEMA", "SELECT"]
  }

  grant {
    principal  = var.pipeline_service_principal_id
    privileges = ["USE_SCHEMA", "CREATE_TABLE", "CREATE_MATERIALIZED_VIEW", "MODIFY", "SELECT"]
  }
}

resource "databricks_grants" "volume_records" {
  volume = "${databricks_catalog.deep_search.name}.${databricks_schema.landing.name}.${databricks_volume.records.name}"

  grant {
    principal  = var.app_service_principal_id
    privileges = ["READ_VOLUME", "WRITE_VOLUME"]
  }

  grant {
    principal  = var.pipeline_service_principal_id
    privileges = ["READ_VOLUME"]
  }
}

resource "databricks_grants" "volume_artifacts" {
  volume = "${databricks_catalog.deep_search.name}.${databricks_schema.landing.name}.${databricks_volume.artifacts.name}"

  grant {
    principal  = var.app_service_principal_id
    privileges = ["READ_VOLUME", "WRITE_VOLUME"]
  }
}
