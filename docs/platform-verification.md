# Platform verification (Phase 0)

Resolves the assumptions A1–A13 from [MIGRATION_PLAN.md](MIGRATION_PLAN.md) §L.2 before dependent work starts.
Run the automated part with:

```bash
uv run python scripts/verify_platform.py --json-out verify-results.json
```

Exit code is 1 if any automated check FAILs. SKIP means a prerequisite (usually workspace credentials or an
environment variable below) was missing. MANUAL rows have a procedure in this document.

## Status

Recorded from the PR-1 environment (no Databricks credentials available, so no live results yet):

| ID | Status | Notes |
|---|---|---|
| A7 | **PASS (offline)** | Plan §G.5 was wrong. `RerankerConfig` takes `model` and `parameters=RerankerConfigRerankerParameters(columns_to_rerank=...)`, not `columns_to_rerank` directly. Corrected for PR-8. |
| A6 (signature) | **PASS (offline)** | `query_index` accepts `query_type`, `filters_json`, `reranker`, `score_threshold`. Filter *semantics* are still manual (§A6). |
| A9 | **PASS (offline)** | `mlflow.get_active_trace_id`, `mlflow.genai.evaluate`, `mlflow.genai.scorer` exist in mlflow 3.18.0. |
| A1 | SKIP | Needs workspace auth. |
| A1b | SKIP | Needs `DEEP_SEARCH_WORKER_MODEL` and auth. |
| A2 | SKIP | Needs `DEEP_SEARCH_TAVILY_MCP_URL` and auth. |
| A3 | SKIP | Needs `DEEP_SEARCH_VERIFY_VOLUME` and auth. |
| A13 | SKIP | Needs auth. |
| A4, A5, A6 (semantics), A8, A10, A11, A12 | MANUAL | See sections below. |

Live results must be filled in from a workspace run before PR-3 and later merge. Record the date, workspace,
and the JSON output here.

## Environment for live checks

| Variable | Used by | Example |
|---|---|---|
| `DATABRICKS_HOST` / `DATABRICKS_CONFIG_PROFILE` | all live checks | `https://<workspace>` |
| `DEEP_SEARCH_WORKER_MODEL` | A1b | `system.ai.<model>` |
| `DEEP_SEARCH_TAVILY_MCP_URL` | A2 | `https://<workspace>/api/2.0/mcp/external/tavily_mcp` |
| `DEEP_SEARCH_VERIFY_VOLUME` | A3 | `/Volumes/deep_search/landing/scratch` (create the scratch volume first; the script writes and deletes files there) |
| `DEEP_SEARCH_EMBEDDING_ENDPOINT` | A13 | defaults to `databricks-gte-large-en` |

Use an OAuth profile or a personal token in your shell only. Do not put credentials in the repo.

## Automated checks

### A1 — system.ai model services
`GET /api/2.1/unity-catalog/model-services?parent=schemas/system.ai` lists chat services. Pick one frontier model for
worker and critic, and one smaller model for extraction. A1b then makes a live structured-output call (`json_schema`)
to confirm the chosen model supports it. A failed structured-output call disqualifies that model.

### A2 — Tavily MCP tool names and schemas
Lists the tools on the external MCP proxy. Record the exact tool names and argument names in the JSON output.
PR-5 must use those names; they are not assumed.

### A3 — Files API conflict and latency
Uploads with `overwrite=False` twice to the same path. Records the error type and code for the second attempt
(expected: an `AlreadyExists`-class error, status 409). Then measures 50 uploads of 100 KB. This is from the local
machine; repeat from a Databricks App in PR-6, because the plan's latency assumption is about app-side calls.

### A13 — Embedding endpoint
Confirms the embedding endpoint is ready. If the name differs in the workspace, set `DEEP_SEARCH_EMBEDDING_ENDPOINT`
and record the working name.

### A6 (signature) and A7, A9 — offline
Run without a workspace. Already recorded above.

## Manual checks

### §A4 — Delta Sync over a pipeline materialized view
Create a scratch schema, a materialized view with Change Data Feed enabled on its base table, and a Delta Sync
TRIGGERED index on a STANDARD endpoint. Confirm `sync` succeeds. **If it fails**, use the plan's default: a plain
table written by `MERGE` (fallback already in §L.5). Creates billable compute; delete afterwards.

### §A5 — Delete propagation
In the scratch index source table, delete one row, run `sync`, and query for the deleted primary key. Record whether
it is still returned. This decides whether deletion tooling (§F.9) can rely on Delta CDF alone.

### §A6 — Filter semantics
On the seeded scratch index, record the result of:
- a list value as `IN` (`{"access_scope": ["tenant:t", "actor:a"]}`),
- `>=` on an integer column,
- boolean equality,
- a timestamp comparison (expected to be unsupported; if so, use `*_epoch_days` integers, as the plan already does).

### §A8 — Job as app resource and run_now
Deploy a throwaway app with a job resource. From the app's service principal, call `jobs.run_now` on a no-op job.
Record whether it succeeds with `CAN_MANAGE_RUN` only.

### §A10 — Managed location and volume prefixes
Create a catalog with managed location `s3://<bucket>/uc-managed/` and external volumes on `landing/records/` and
`landing/artifacts/` under the same external location. Confirm all three can coexist, and that writes to one do not
affect the others. Requires the PR-2 infrastructure.

### §A11 — Concurrent invocations per session
Run `agentbricks dev`. Send two invocations with the same `session_id` concurrently. Record whether the runtime
queues them, rejects the second, or runs them in parallel. The graph design (§F.7) depends on this.

### §A12 — SSE-KMS on UC-written Delta files
After PR-2, write a Delta table through the external location. In AWS, check the object's encryption
(`x-amz-server-side-encryption: aws:kms` and the expected key ID). If UC does not apply the bucket default, set
encryption on the external location or storage credential as the plan's §F.9 notes.

## Fallbacks

If an assumption fails, apply the fallback in plan §L.5 and record the choice in the Status table above.
