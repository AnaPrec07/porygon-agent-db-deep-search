# Delete one research session

Use this for a deletion request, or to remove a session written in error. Deletion is permanent.

## Steps

1. Confirm the session id and the request. Keep the request's reference for the audit record.
2. Dry run. It lists every file that will be removed and changes nothing:

   ```bash
   uv run python scripts/delete_research.py --volume /Volumes/deep_search/landing/records --session <id>
   ```

3. Apply:

   ```bash
   uv run python scripts/delete_research.py --volume /Volumes/deep_search/landing/records --session <id> --yes
   ```

   The script first writes `records/v1/deletion_request/dt=<day>/<id>.json`. That record is never deleted by this script.
4. Run the printed Delta statements in the `deep_search` catalog. They cover the bronze, silver and index-source
   tables. Then run the refresh job so the indexes drop the rows.
5. Check: reconstruction returns no files, and a retrieval for the session's topic no longer returns its facts.

## Notes

- A session deleted from the store but still present in Delta is a partial deletion. Finish step 4 before
  closing the request.
- Records of other sessions that cite the same source URL are not affected. Sources are keyed per session.
