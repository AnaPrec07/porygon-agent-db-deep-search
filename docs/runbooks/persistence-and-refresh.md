# Persistence and refresh failures

## Symptoms and actions

| Symptom | Likely cause | Action |
|---|---|---|
| Runs fail with `PersistenceError` | Store unavailable or permission denied on the records volume | Check the app's volume grant (`READ VOLUME`, `WRITE VOLUME`) and the volume path in `DEEP_SEARCH_RECORDS_VOLUME`. Retry the run once the store is back. |
| Runs complete, but the log shows `persistence failed` | Non-strict mode is on, so the failure was recorded and the run continued | The session has no complete manifest. Reconstruct it to see what was written. Consider turning strict mode on. |
| Session shows `failed` in the store | A node raised an exception | Read the error trace event in `out/timeline.md` after reconstruction. |
| `refresh trigger failed` in the log | `jobs.run_now` was refused | Check that the app's service principal has `CAN_MANAGE_RUN` on the job. The 30-minute schedule still runs. |
| New research does not show up in memory | Refresh has not run yet, or the index sync is pending | Run the refresh job manually. Check the job's last run and the index's ready status. |
| Retrieval returns no memory at all | Tenant not configured, memory disabled, or the index is unavailable | Check `DEEP_SEARCH_TENANT_ID` and `DEEP_SEARCH_MEMORY_ENABLED`. Check the `errors` list in the memory brief. |

## Strict mode

`DEEP_SEARCH_PERSISTENCE_STRICT` defaults to `true`. Strict mode stops a run when a record cannot be written after
retries, so a delivered answer always has a complete record. Turn it off only for a deliberate trade-off, and record
the decision in the change log.
