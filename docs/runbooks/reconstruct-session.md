# Reconstruct a research session

Use this when someone asks what the agent did, why it answered as it did, or whether a delivered report matches
the stored one.

## Steps

1. Get the `research_session_id` (from the chat's session list, the trace in MLflow, or a support ticket).
2. Run the reconstruction against the record store:

   ```bash
   uv run python scripts/reconstruct_session.py --volume /Volumes/deep_search/landing/records --session <id> --out ./out
   ```

   For a local store use `--root <dir>` in place of `--volume`.
3. Read `out/timeline.md` for the ordered steps, `out/session.json` for the session record and file list, and
   `out/report.md` for the final text.

## Reading the result

- `report_verified=True`: the stored report matches its SHA-256. This is the text the user received.
- `complete=False`: the session did not commit a manifest. It stopped before completion (see the status in the
  output). The timeline still shows what was recorded.
- Non-zero exit with "checksum mismatch": a stored file has changed since it was written. Do not use the output.
  Open an incident and keep the store as it is.
