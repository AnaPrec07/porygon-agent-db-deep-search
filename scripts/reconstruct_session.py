"""Rebuild a research session from its record files (plan §F.8).

Examples:
    uv run python scripts/reconstruct_session.py --root ./deep_search_data --session <id> --out ./out
    uv run python scripts/reconstruct_session.py --volume /Volumes/deep_search/landing/records --session <id> --out ./out

The volume form needs Databricks authentication. Writes session.json, timeline.md and report.md to --out.
Exit code 1 if a checksum does not match or the session is not found.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.memory.object_store import LocalObjectStore, ObjectStore, UCVolumeObjectStore
from agent.memory.reconstruct import ReconstructError, reconstruct, write_outputs


def _store(args: argparse.Namespace) -> ObjectStore:
    if args.root:
        return LocalObjectStore(args.root)
    from databricks.sdk import WorkspaceClient

    return UCVolumeObjectStore(WorkspaceClient(), args.volume)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--root", help="local object store root (the directory that holds records/ and reports/)")
    source.add_argument("--volume", help="Unity Catalog volume path that holds records/ and reports/")
    parser.add_argument("--session", required=True, help="research_session_id")
    parser.add_argument("--out", required=True, help="directory for session.json, timeline.md and report.md")
    args = parser.parse_args(argv)

    try:
        result = reconstruct(_store(args), args.session)
    except ReconstructError as error:
        print(f"reconstruction failed: {error}", file=sys.stderr)
        return 1
    if result["session"] is None and not result["files_checked"]:
        print(f"no records found for session {args.session}", file=sys.stderr)
        return 1
    write_outputs(result, Path(args.out))
    print(
        f"session={args.session} complete={result['complete']} "
        f"status={result['session']['status'] if result['session'] else 'unknown'} "
        f"report_verified={result['report_sha256_verified']} files={result['files_checked']} out={args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
