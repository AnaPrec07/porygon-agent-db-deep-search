-- Index source for the facts index (plan §G.2). One row per canonical_fact_key, from active source facts.
-- Built as a MERGE into a plain Delta table with Change Data Feed on, which AI Search Delta Sync requires
-- (plan §ADR-11, default path in §G.2). ${catalog} is replaced by the job.
--
-- NOT SAFE TO INDEX YET: records do not carry tenant_id, actor_id or visibility (plan §E, §ADR-21). access_scope
-- below is a placeholder that makes every row 'unscoped'. Retrieval in PR-8 must not run against an index built
-- from this table until scoping lands.

CREATE TABLE IF NOT EXISTS ${catalog}.memory.facts_index_src (
  canonical_fact_key STRING NOT NULL,
  embedding_text STRING,
  fact_text STRING,
  research_topic STRING,
  source_url STRING,
  source_title STRING,
  evidence_quality STRING,
  evidence_quality_rank INT,
  fact_kind STRING,
  time_sensitivity STRING,
  status STRING,
  observed_in_sessions ARRAY<STRING>,
  latest_fact_id STRING,
  supporting_quote STRING,
  access_scope STRING
)
TBLPROPERTIES (delta.enableChangeDataFeed = true);

MERGE INTO ${catalog}.memory.facts_index_src AS t
USING (
  SELECT
    canonical_fact_key,
    max_by(fact_text, ingested_at) AS fact_text,
    concat(max_by(fact_text, ingested_at), ' - ', max_by(source_title, ingested_at)) AS embedding_text,
    max_by(research_topic, ingested_at) AS research_topic,
    max_by(source_url, ingested_at) AS source_url,
    max_by(source_title, ingested_at) AS source_title,
    max_by(evidence_quality, ingested_at) AS evidence_quality,
    CASE max_by(evidence_quality, ingested_at)
      WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END AS evidence_quality_rank,
    max_by(fact_kind, ingested_at) AS fact_kind,
    max_by(time_sensitivity, ingested_at) AS time_sensitivity,
    'active' AS status,
    collect_set(research_session_id) AS observed_in_sessions,
    max_by(fact_id, ingested_at) AS latest_fact_id,
    max_by(quote, ingested_at) AS supporting_quote,
    'unscoped' AS access_scope
  FROM ${catalog}.memory.facts
  WHERE status = 'active' AND fact_kind = 'source_fact'
  GROUP BY canonical_fact_key
) AS s
ON t.canonical_fact_key = s.canonical_fact_key
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;
