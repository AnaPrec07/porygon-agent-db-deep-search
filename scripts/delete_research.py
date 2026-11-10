"""Delete one research session's record files (plan §F.9). Dry run by default.

    uv run python scripts/delete_research.py --root ./deep_search_data --session <id>          # lists files
    uv run python scripts/delete_research.py --root ./deep_search_data --session <id> --yes    # deletes

Steps, in order:
1. Write an audit record under records/v1/deletion_request/ (kept, never deleted by this script).
2. Delete every record and report file that names the session. The audit record is excluded.
3. Print the Delta statements that remove the session from the curated and index-source tables. Run them in the
   pipeline's catalog, then run the refresh job so the indexes drop the rows (Change Data Feed propagates deletes).
Verify with `scripts/reconstruct_session.py` (it should report no files) and with a retrieval query.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from agent.memory.layout import RECORD_VERSION
from agent.memory.object_store import LocalObjectStore, ObjectStore, UCVolumeObjectStore

DELTA_TABLES = [
    "bronze.research_session_raw", "bronze.trace_event_raw", "bronze.source_raw", "bronze.fact_raw",
    "bronze.final_response_raw", "bronze.session_manifest_raw",
    "memory.research_sessions", "memory.trace_events", "memory.sources", "memory.facts",
    "memory.final_responses", "memory.session_manifests",
    "memory.facts_index_src", "memory.response_chunks_index_src", "memory.activity_summaries_index_src",
]


def session_files(store: ObjectStore, session_id: str) -> list[str]:
    candidates = store.list_files(f"records/{RECORD_VERSION}/") + store.list_files(f"reports/{RECORD_VERSION}/")
    return sorted(p for p in candidates if f"/{session_id}/" in p and "/deletion_request/" not in p)


def delete_session(store: ObjectStore, session_id: str, *, apply: bool, catalog: str = "deep_search") -> dict:
    files = session_files(store, session_id)
    summary: dict = {"session": session_id, "files": files, "applied": apply}
    if not apply:
        return summary
    day = datetime.now(UTC).date().isoformat()
    audit = {
        "record_type": "deletion_request",
        "research_session_id": session_id,
        "requested_at": datetime.now(UTC).isoformat(),
        "files_deleted": len(files),
    }
    store.create(
        f"records/{RECORD_VERSION}/deletion_request/dt={day}/{session_id}.json",
        json.dumps(audit, sort_keys=True).encode("utf-8"),
    )
    for path in files:
        store.delete(path)
    summary["delta_statements"] = [
        f"DELETE FROM {catalog}.{table} WHERE research_session_id = '{session_id}';" for table in DELTA_TABLES
    ]
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--root")
    source.add_argument("--volume")
    parser.add_argument("--session", required=True)
    parser.add_argument("--catalog", default="deep_search")
    parser.add_argument("--yes", action="store_true", help="actually delete (default is a dry run)")
    args = parser.parse_args(argv)
    if args.root:
        store: ObjectStore = LocalObjectStore(args.root)
    else:
        from databricks.sdk import WorkspaceClient

        store = UCVolumeObjectStore(WorkspaceClient(), args.volume)
    result = delete_session(store, args.session, apply=args.yes, catalog=args.catalog)
    print(json.dumps(result, indent=2))
    if not result["files"]:
        print(f"no files found for session {args.session}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
