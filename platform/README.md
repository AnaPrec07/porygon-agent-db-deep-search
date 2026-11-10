# Deep Search data platform (PR-7)

Databricks Asset Bundle for the memory pipeline and refresh job. Plan references: §G.2, §G.4, §J Phase 6.

```
platform/
  databricks.yml            bundle, variables, targets (dev, prod)
  resources/pipeline.yml    Lakeflow pipeline deep_search_memory_ingest (serverless, triggered)
  resources/job.yml         job deep_search_memory_refresh: ingest -> build index sources -> sync indexes
  src/pipeline/bronze.sql   raw streaming tables over the app's record folders (read_files STREAM)
  src/pipeline/silver.sql   curated tables with AUTO CDC keyed on record ids
  src/sql/facts_index_src.sql   facts index source: MERGE with Change Data Feed on
  src/jobs/build_index_sources.py  response chunks and activity summaries, then MERGE
  src/jobs/sync_indexes.py  Delta Sync for the three indexes, with a bounded wait
  src/common/              pure functions: chunking and activity summaries (tested without Spark)
```

Catalog, schemas, volumes and grants are created by `infra/databricks` (PR-2). The bundle does not redefine them.

## Commands

```bash
cd platform
databricks bundle validate -t dev
databricks bundle deploy -t dev -var pipeline_service_principal_id=<application-id>
databricks bundle run deep_search_memory_refresh -t dev
```

## Known gaps

- **Scoping is resolved in PR-8.** Records carry `tenant_id`, `actor_id`, `visibility` and `access_scope` (schema 1.1).
  The index sources take `access_scope` from the records. Records written before 1.1 have an empty scope and are never
  returned by retrieval.
- **Activity summaries are thin.** Trace events do not carry queries, source domains or fact counts, so the summary
  text reports the topic, status and critic comment. Richer summaries need the trace fields added in a later PR.
- **Raw page text is not stored**, so no content artifacts are ingested (see PR-6).

## Assumptions to verify on first deploy

| ID | Assumption | Where |
|---|---|---|
| A-PLAT-1 | `read_files` accepts `**` globs and `${records_volume}` substitutes in SQL pipelines | `bronze.sql` |
| A-PLAT-2 | The `CREATE FLOW ... AS AUTO CDC INTO ... STORED AS SCD TYPE 1` syntax is accepted | `silver.sql` |
| A-PLAT-3 | `VectorIndexStatus.ready` is false while a requested sync runs | `sync_indexes.py` |
| A-PLAT-4 | Serverless Python tasks can import the sibling `common` package | `build_index_sources.py` |

The Databricks CLI was not available in the build environment, so `bundle validate` has not been run. Run it before
the first deploy.
