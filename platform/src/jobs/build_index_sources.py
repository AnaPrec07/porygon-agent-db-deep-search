"""Build the three index-source tables (plan §G.2). Runs as a serverless Spark job after the pipeline.

- memory.facts_index_src: MERGE from facts (SQL in src/sql/facts_index_src.sql).
- memory.response_chunks_index_src: latest response version chunked in Python, then MERGE.
- memory.activity_summaries_index_src: one templated summary per (session, agent), then MERGE.

Every table has Change Data Feed on, so Delta Sync can read deltas. MERGE keeps re-runs idempotent.
ASSUMPTION A-PLAT-4: the job runs on serverless compute that can import the sibling ``common`` package
(added to sys.path below). Pure logic lives in ``common`` and is covered by tests.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.activity import summarize_activity  # noqa: E402
from common.chunking import chunk_report  # noqa: E402

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"

CHUNK_DDL = """
CREATE TABLE IF NOT EXISTS {catalog}.memory.response_chunks_index_src (
  chunk_id STRING NOT NULL,
  chunk_index INT,
  chunk_heading STRING,
  chunk_text STRING,
  response_id STRING,
  response_version INT,
  is_latest_version BOOLEAN,
  research_topic STRING,
  user_query STRING,
  generated_at STRING,
  tenant_id STRING,
  access_scope STRING
) TBLPROPERTIES (delta.enableChangeDataFeed = true)
"""

ACTIVITY_DDL = """
CREATE TABLE IF NOT EXISTS {catalog}.memory.activity_summaries_index_src (
  activity_id STRING NOT NULL,
  research_session_id STRING,
  agent_name STRING,
  research_topic STRING,
  summary_text STRING,
  fact_count INT,
  status STRING,
  tenant_id STRING,
  access_scope STRING
) TBLPROPERTIES (delta.enableChangeDataFeed = true)
"""


def _merge_from_rows(spark, table: str, key: str, rows: list[dict], columns: list[str]) -> None:
    if not rows:
        return
    temp = f"incoming_{table.split('.')[-1]}"
    spark.createDataFrame(rows, schema=columns).createOrReplaceTempView(temp)
    spark.sql(f"MERGE INTO {table} AS t USING {temp} AS s ON t.{key} = s.{key} "
              "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")


def split_statements(sql: str) -> list[str]:
    """Split a SQL file into statements, dropping -- comment lines first so headers stay with no statement."""
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [statement.strip() for statement in code.split(";") if statement.strip()]


def build_facts_index(spark, catalog: str) -> None:
    sql = (SQL_DIR / "facts_index_src.sql").read_text(encoding="utf-8").replace("${catalog}", catalog)
    for statement in split_statements(sql):
        spark.sql(statement)


def build_response_chunks(spark, catalog: str) -> int:
    spark.sql(CHUNK_DDL.format(catalog=catalog))
    rows = spark.sql(
        f"""
        SELECT response_id, response_version, research_topic, user_query, generated_at, final_response,
               tenant_id, access_scope
        FROM {catalog}.memory.final_responses
        QUALIFY row_number() OVER (PARTITION BY response_id ORDER BY response_version DESC) = 1
        """
    ).collect()
    chunk_rows = []
    for row in rows:
        for chunk in chunk_report(row["final_response"]):
            chunk_rows.append(
                {
                    "chunk_id": f"{row['response_id']}:v{row['response_version']}:{chunk.index}",
                    "chunk_index": chunk.index,
                    "chunk_heading": chunk.heading,
                    "chunk_text": chunk.text,
                    "response_id": row["response_id"],
                    "response_version": int(row["response_version"]),
                    "is_latest_version": True,
                    "research_topic": row["research_topic"],
                    "user_query": row["user_query"],
                    "generated_at": row["generated_at"],
                    "tenant_id": row["tenant_id"],
                    "access_scope": row["access_scope"],
                }
            )
    _merge_from_rows(
        spark,
        f"{catalog}.memory.response_chunks_index_src",
        "chunk_id",
        chunk_rows,
        ["chunk_id", "chunk_index", "chunk_heading", "chunk_text", "response_id", "response_version",
         "is_latest_version", "research_topic", "user_query", "generated_at", "tenant_id", "access_scope"],
    )
    return len(chunk_rows)


def build_activity_summaries(spark, catalog: str) -> int:
    spark.sql(ACTIVITY_DDL.format(catalog=catalog))
    rows = spark.sql(
        f"""
        SELECT t.research_session_id, t.agent_name, s.research_topic, t.event_type, t.status,
               t.action, t.decision_summary, s.tenant_id, s.access_scope
        FROM {catalog}.memory.trace_events t
        LEFT JOIN {catalog}.memory.research_sessions s USING (research_session_id)
        """
    ).collect()
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["research_session_id"], row["agent_name"])
        entry = grouped.setdefault(
            key, {"topic": row["research_topic"] or "", "tenant": row["tenant_id"], "scope": row["access_scope"], "events": []}
        )
        entry["events"].append(row)
    summary_rows = []
    for (session_id, agent), entry in grouped.items():
        events = entry["events"]
        critic = next((e["decision_summary"] for e in events if e["event_type"] == "evaluation"), None)
        status = "error" if any(e["status"] == "error" for e in events) else "ok"
        text = summarize_activity(
            agent_name=agent,
            topic=entry["topic"],
            queries=[],
            source_domains=[],
            fact_count=0,
            high_quality_count=0,
            status=status,
            critic_comment=critic,
        )
        summary_rows.append(
            {
                "activity_id": f"{session_id}:{agent}",
                "research_session_id": session_id,
                "agent_name": agent,
                "research_topic": entry["topic"],
                "summary_text": text,
                "fact_count": 0,
                "status": status,
                "tenant_id": entry["tenant"],
                "access_scope": entry["scope"],
            }
        )
    _merge_from_rows(
        spark,
        f"{catalog}.memory.activity_summaries_index_src",
        "activity_id",
        summary_rows,
        ["activity_id", "research_session_id", "agent_name", "research_topic", "summary_text",
         "fact_count", "status", "tenant_id", "access_scope"],
    )
    return len(summary_rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    args = parser.parse_args(argv)
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    build_facts_index(spark, args.catalog)
    chunks = build_response_chunks(spark, args.catalog)
    summaries = build_activity_summaries(spark, args.catalog)
    print(f"index sources built: response_chunks={chunks} activity_summaries={summaries}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
