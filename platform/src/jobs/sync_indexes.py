"""Trigger Delta Sync for the AI Search indexes and wait until they report ready (plan §G.4).

Run as a job task with ``--index <name>`` per index. Index names are three-part Unity Catalog names.
ASSUMPTION A-PLAT-3 (verify on first run): ``VectorIndexStatus.ready`` becomes true only after the requested sync
has finished. If it stays true during a sync, replace the wait with the indexed row count check.
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger("sync_indexes")


class SyncTimeout(RuntimeError):
    pass


def sync_and_wait(
    client: Any,
    index_names: Sequence[str],
    *,
    timeout_s: float = 900.0,
    poll_s: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    for name in index_names:
        client.vector_search_indexes.sync_index(index_name=name)
        logger.info("requested sync for %s", name)
    deadline = clock() + timeout_s
    pending = set(index_names)
    while pending:
        for name in sorted(pending):
            status = client.vector_search_indexes.get_index(name).status
            if status is not None and status.ready:
                logger.info("%s is ready", name)
                pending.discard(name)
        if not pending:
            return
        if clock() > deadline:
            raise SyncTimeout(f"indexes not ready after {timeout_s:.0f}s: {sorted(pending)}")
        sleep(poll_s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", action="append", required=True, help="three-part index name; repeat per index")
    parser.add_argument("--timeout-s", type=float, default=900.0)
    args = parser.parse_args(argv)
    from databricks.sdk import WorkspaceClient

    sync_and_wait(WorkspaceClient(), args.index, timeout_s=args.timeout_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
