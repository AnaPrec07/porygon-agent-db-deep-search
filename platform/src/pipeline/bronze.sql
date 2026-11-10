-- Bronze: raw, append-only streaming tables read from the app's record files (plan §G.2).
-- Each record type is a folder under v1/. Rescued data keeps fields the schema does not know yet (plan §F.6).
-- ASSUMPTION A-PLAT-1 (verify on first run): read_files accepts a ** glob and the ${records_volume} configuration key.

CREATE OR REFRESH STREAMING TABLE bronze.research_session_raw AS
SELECT *, _metadata.file_modification_time AS ingested_at, _metadata.file_path AS source_file
FROM STREAM read_files('${records_volume}/v1/research_session/**/*.json',
  format => 'json', rescuedDataColumn => '_rescued_data');

CREATE OR REFRESH STREAMING TABLE bronze.trace_event_raw AS
SELECT *, _metadata.file_modification_time AS ingested_at, _metadata.file_path AS source_file
FROM STREAM read_files('${records_volume}/v1/trace_event/**/*.jsonl',
  format => 'json', rescuedDataColumn => '_rescued_data');

CREATE OR REFRESH STREAMING TABLE bronze.source_raw AS
SELECT *, _metadata.file_modification_time AS ingested_at, _metadata.file_path AS source_file
FROM STREAM read_files('${records_volume}/v1/source/**/*.jsonl',
  format => 'json', rescuedDataColumn => '_rescued_data');

CREATE OR REFRESH STREAMING TABLE bronze.fact_raw AS
SELECT *, _metadata.file_modification_time AS ingested_at, _metadata.file_path AS source_file
FROM STREAM read_files('${records_volume}/v1/fact/**/*.jsonl',
  format => 'json', rescuedDataColumn => '_rescued_data');

CREATE OR REFRESH STREAMING TABLE bronze.final_response_raw AS
SELECT *, _metadata.file_modification_time AS ingested_at, _metadata.file_path AS source_file
FROM STREAM read_files('${records_volume}/v1/final_response/**/*.json',
  format => 'json', rescuedDataColumn => '_rescued_data');

CREATE OR REFRESH STREAMING TABLE bronze.session_manifest_raw AS
SELECT *, _metadata.file_modification_time AS ingested_at, _metadata.file_path AS source_file
FROM STREAM read_files('${records_volume}/v1/session_manifest/**/*.json',
  format => 'json', rescuedDataColumn => '_rescued_data');
