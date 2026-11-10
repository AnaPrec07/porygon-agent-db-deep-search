-- Silver: curated tables. Idempotent by key: a re-run or a duplicate file never creates a second row (plan §E.9).
-- AUTO CDC keeps the row with the highest SEQUENCE BY value per key.
-- ASSUMPTION A-PLAT-2 (verify on first run): the AUTO CDC flow syntax below is accepted by this workspace's runtime.

CREATE OR REFRESH STREAMING TABLE memory.research_sessions;
CREATE FLOW research_sessions_flow AS AUTO CDC INTO memory.research_sessions
FROM STREAM bronze.research_session_raw
KEYS (research_session_id)
SEQUENCE BY session_version
COLUMNS * EXCEPT (_rescued_data, source_file, ingested_at)
STORED AS SCD TYPE 1;

CREATE OR REFRESH STREAMING TABLE memory.trace_events;
CREATE FLOW trace_events_flow AS AUTO CDC INTO memory.trace_events
FROM STREAM bronze.trace_event_raw
KEYS (trace_id)
SEQUENCE BY sequence
COLUMNS * EXCEPT (_rescued_data, source_file, ingested_at)
STORED AS SCD TYPE 1;

CREATE OR REFRESH STREAMING TABLE memory.sources;
CREATE FLOW sources_flow AS AUTO CDC INTO memory.sources
FROM STREAM bronze.source_raw
KEYS (source_id, research_session_id)
SEQUENCE BY ingested_at
COLUMNS * EXCEPT (_rescued_data, source_file, ingested_at)
STORED AS SCD TYPE 1;

CREATE OR REFRESH STREAMING TABLE memory.facts;
CREATE FLOW facts_flow AS AUTO CDC INTO memory.facts
FROM STREAM bronze.fact_raw
KEYS (fact_id)
SEQUENCE BY ingested_at
COLUMNS * EXCEPT (_rescued_data, source_file, ingested_at)
STORED AS SCD TYPE 1;

CREATE OR REFRESH STREAMING TABLE memory.final_responses;
CREATE FLOW final_responses_flow AS AUTO CDC INTO memory.final_responses
FROM STREAM bronze.final_response_raw
KEYS (response_id, response_version)
SEQUENCE BY ingested_at
COLUMNS * EXCEPT (_rescued_data, source_file, ingested_at)
STORED AS SCD TYPE 1;

CREATE OR REFRESH STREAMING TABLE memory.session_manifests AS
SELECT * EXCEPT (_rescued_data, source_file, ingested_at)
FROM STREAM bronze.session_manifest_raw;
