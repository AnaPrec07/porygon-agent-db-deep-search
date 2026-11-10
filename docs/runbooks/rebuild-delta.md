# Rebuild the Delta tables from the record files

Use this after the Delta tables are lost or corrupted, or after a schema change. The record files are the source of
truth, so the tables can be rebuilt from them.

## Steps

1. Stop the refresh job so no run overlaps the rebuild: pause `deep-search-memory-refresh-<target>`.
2. Full refresh the pipeline, which re-reads every record file from the start:

   ```bash
   cd platform
   databricks bundle run deep_search_memory_ingest -t <target> --full-refresh-all
   ```

3. Run the refresh job once to rebuild the index sources and sync the indexes:

   ```bash
   databricks bundle run deep_search_memory_refresh -t <target>
   ```

4. Compare row counts with the manifests. For each session, `session_manifests` should list the files the store
   holds for that session. A missing manifest means the session is partial and is not expected to match.
5. Resume the job schedule.

## Notes

- Index-source tables are rebuilt by MERGE, so the step is idempotent. The index sync is a full sync after a rebuild.
- Records written before schema 1.1 have an empty `access_scope`. They are present in Delta but never returned by
  retrieval. This is intentional; see the PR-8 description.
