"""Create the AI Search endpoint and the three Delta Sync indexes. Idempotent: existing objects are left as they are.

    uv run python scripts/bootstrap_ai_search.py --catalog deep_search --dry-run
    uv run python scripts/bootstrap_ai_search.py --catalog deep_search

Creating an endpoint and indexes costs money. Run with --dry-run first. The embedding model is the managed
databricks-gte-large-en endpoint (plan §ADR-12).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

EMBEDDING_MODEL = "databricks-gte-large-en"
ENDPOINT = "deep-search-memory"

FACT_COLUMNS = [
    "canonical_fact_key", "embedding_text", "fact_text", "research_topic", "source_url", "source_title",
    "evidence_quality", "evidence_quality_rank", "fact_kind", "time_sensitivity", "status",
    "observed_in_sessions", "latest_fact_id", "supporting_quote", "last_verified_at", "tenant_id", "access_scope",
]
RESPONSE_COLUMNS = [
    "chunk_id", "chunk_index", "chunk_heading", "chunk_text", "response_id", "response_version",
    "is_latest_version", "research_topic", "generated_at", "access_scope",
]
ACTIVITY_COLUMNS = [
    "activity_id", "research_session_id", "agent_name", "research_topic", "summary_text", "status", "access_scope",
]


@dataclass(frozen=True)
class IndexSpec:
    name: str
    source_table: str
    primary_key: str
    embedding_source_column: str
    columns_to_sync: list[str]


def index_specs(catalog: str) -> list[IndexSpec]:
    return [
        IndexSpec(f"{catalog}.memory.facts_idx", f"{catalog}.memory.facts_index_src", "canonical_fact_key",
                  "embedding_text", FACT_COLUMNS),
        IndexSpec(f"{catalog}.memory.responses_idx", f"{catalog}.memory.response_chunks_index_src", "chunk_id",
                  "chunk_text", RESPONSE_COLUMNS),
        IndexSpec(f"{catalog}.memory.activity_idx", f"{catalog}.memory.activity_summaries_index_src", "activity_id",
                  "summary_text", ACTIVITY_COLUMNS),
    ]


def bootstrap(client, catalog: str, *, endpoint: str = ENDPOINT, dry_run: bool = False) -> list[str]:
    """Create what is missing. Returns one line per action taken or planned."""
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.vectorsearch import (
        DeltaSyncVectorIndexSpecRequest,
        EmbeddingSourceColumn,
        EndpointType,
        PipelineType,
        VectorIndexType,
    )

    actions: list[str] = []
    try:
        client.vector_search_endpoints.get_endpoint(endpoint_name=endpoint)
        actions.append(f"endpoint {endpoint}: exists")
    except NotFound:
        actions.append(f"endpoint {endpoint}: create STANDARD")
        if not dry_run:
            client.vector_search_endpoints.create_endpoint_and_wait(name=endpoint, endpoint_type=EndpointType.STANDARD)

    for spec in index_specs(catalog):
        try:
            client.vector_search_indexes.get_index(index_name=spec.name)
            actions.append(f"index {spec.name}: exists")
            continue
        except NotFound:
            actions.append(f"index {spec.name}: create Delta Sync from {spec.source_table}")
        if dry_run:
            continue
        client.vector_search_indexes.create_index(
            name=spec.name,
            endpoint_name=endpoint,
            primary_key=spec.primary_key,
            index_type=VectorIndexType.DELTA_SYNC,
            delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
                source_table=spec.source_table,
                pipeline_type=PipelineType.TRIGGERED,
                embedding_source_columns=[
                    EmbeddingSourceColumn(name=spec.embedding_source_column, embedding_model_endpoint_name=EMBEDDING_MODEL)
                ],
                columns_to_sync=spec.columns_to_sync,
            ),
        )
    return actions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="deep_search")
    parser.add_argument("--endpoint", default=ENDPOINT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    from databricks.sdk import WorkspaceClient

    for line in bootstrap(WorkspaceClient(), args.catalog, endpoint=args.endpoint, dry_run=args.dry_run):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
