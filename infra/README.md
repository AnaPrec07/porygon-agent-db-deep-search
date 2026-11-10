# Deep Search infrastructure (PR-2)

Terraform for the AWS bucket and the Unity Catalog objects that sit on it. Plan references: §F.1, §F.9, §M.5, §M.6.

- `aws/`: KMS key, data bucket (versioned, SSE-KMS, public access blocked, TLS-only, principal allow-list),
  lifecycle rules limited to `landing/`, the Unity Catalog storage role, and CloudTrail S3 data events.
- `databricks/`: storage credential, two external locations, the `deep_search` catalog (managed storage under
  `uc-managed/`), schemas `landing`, `bronze` and `memory`, external volumes `records` and `artifacts`, and grants.

Nothing here holds AWS keys or Databricks tokens. Authentication comes from the environment.

## Apply order (two phases for the trust policy)

The storage role's trust policy needs an external ID that only exists after the storage credential is created.

```bash
# 1. AWS: create bucket, KMS key, and role with the placeholder external ID "0000".
cd infra/aws
terraform init
terraform apply -var-file=dev.tfvars            # tfvars stays local; it is git-ignored

# 2. Databricks: create the credential, external locations, catalog, volumes, grants.
cd ../databricks
terraform init
terraform apply -var-file=dev.tfvars \
  -var "uc_storage_role_arn=$(cd ../aws && terraform output -raw uc_storage_role_arn)" \
  -var "aws_bucket_name=$(cd ../aws && terraform output -raw bucket_name)"

# 3. Put the credential's external ID into the AWS trust policy and re-apply.
EXT_ID=$(terraform output -raw storage_credential_external_id)
cd ../aws
terraform apply -var-file=dev.tfvars -var "databricks_external_id=${EXT_ID}"
```

`app_service_principal_id` is needed in step 2. Databricks Apps creates that service principal on the first
deploy of the app. Run step 2 once with a placeholder, deploy the app, then re-apply with the real ID.

Credentials for the Databricks provider: set `DATABRICKS_HOST` and use OAuth (`databricks auth login`) or
`DATABRICKS_CONFIG_PROFILE`. Do not add tokens to `.tf` or `.tfvars` files.

## Checks that must pass before the first production apply

| Item | Why | Where it is verified |
|---|---|---|
| `databricks_uc_master_role_arn` equals the storage credential's `unity_catalog_iam_arn` | The default is a documented example value. A wrong principal breaks storage validation. | `terraform output unity_catalog_iam_arn` in `databricks/` |
| Two external locations instead of the single `deep_search_landing` in plan §M.6 | Avoids overlap between volume paths and catalog managed storage. Deviation from the plan. | PR-1 A10 (`docs/platform-verification.md` §A10) |
| Lifecycle rules and bucket policy do not block the storage role | The principal allow-list (`aws:PrincipalArn`) must include the role. The role is listed by default. | Write and read a test file through the external volume as the app SP |
| Databricks storage-role policy template | The inline policy follows the documented minimum. Databricks updates its template over time. | Compare with the current "S3 external location manual" guide before prod |
| Provider attribute names (`aws_iam_role`, `external_id`, `unity_catalog_iam_arn`, `storage_root`, `volume_type`, `storage_location`) | Written from the provider docs but not validated: this environment cannot reach the Terraform registry. | Run `terraform init && terraform validate` in each directory |

## Not in this PR

- Access to the Tavily connection (`USE CONNECTION`) is created outside this module (PR-5).
- The app's volume resources and environment variables are set in `app.yaml` (PR-6).
- Pipeline, job and AI Search resources live in `platform/` (PR-7) and `scripts/bootstrap_ai_search.py` (PR-8).
- No dev or prod tfvars are committed. Example variables are listed in the variable descriptions.
