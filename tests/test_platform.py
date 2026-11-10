"""PR-7 tests: bundle structure, pure transforms, and the index sync wait. No Spark, no workspace.

The platform/ folder is loaded by file path because a top-level package named ``platform`` would collide
with the standard library module of the same name.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform"


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, PLATFORM / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


chunking = _load("pf_chunking", "src/common/chunking.py")
activity = _load("pf_activity", "src/common/activity.py")
sync = _load("pf_sync", "src/jobs/sync_indexes.py")


# ---------- bundle structure ----------


def _yaml(path: str):
    return yaml.safe_load((PLATFORM / path).read_text(encoding="utf-8"))


def test_bundle_and_resources_parse_and_reference_existing_files():
    bundle = _yaml("databricks.yml")
    assert bundle["bundle"]["name"] == "deep_search_platform"
    assert {"dev", "prod"} <= set(bundle["targets"])

    pipeline = _yaml("resources/pipeline.yml")["resources"]["pipelines"]["deep_search_memory_ingest"]
    for library in pipeline["libraries"]:
        assert (PLATFORM / "resources" / library["file"]["path"]).resolve().exists()

    job = _yaml("resources/job.yml")["resources"]["jobs"]["deep_search_memory_refresh"]
    keys = [task["task_key"] for task in job["tasks"]]
    assert keys == ["ingest", "build_index_sources", "sync_indexes"]
    for task in job["tasks"]:
        if "spark_python_task" in task:
            assert (PLATFORM / "resources" / task["spark_python_task"]["python_file"]).resolve().exists()
    assert job["max_concurrent_runs"] == 1
    assert job["queue"]["enabled"] is True


def test_every_bronze_table_reads_a_record_folder_the_app_writes():
    from agent.memory.layout import RECORD_VERSION  # noqa: F401 - documents the v1 root used in SQL

    bronze = (PLATFORM / "src/pipeline/bronze.sql").read_text(encoding="utf-8")
    folders = set(re.findall(r"v1/([a-z_]+)/", bronze))
    written = {
        "research_session", "trace_event", "source", "fact", "final_response", "session_manifest",
    }
    assert folders == written


def test_every_silver_flow_keys_on_the_record_identity():
    silver = (PLATFORM / "src/pipeline/silver.sql").read_text(encoding="utf-8")
    flows = re.findall(r"CREATE FLOW (\w+) AS AUTO CDC INTO ([\w.]+)\s+FROM STREAM [\w.]+\s+KEYS \(([^)]+)\)", silver)
    targets = {target: keys for _, target, keys in flows}
    assert targets["memory.research_sessions"] == "research_session_id"
    assert targets["memory.trace_events"] == "trace_id"
    assert targets["memory.facts"] == "fact_id"
    assert targets["memory.final_responses"] == "response_id, response_version"


def test_facts_index_sql_keeps_the_create_statement():
    from importlib import util

    spec = util.spec_from_file_location("bi", PLATFORM / "src/jobs/build_index_sources.py")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sql = (PLATFORM / "src/sql/facts_index_src.sql").read_text(encoding="utf-8")
    statements = module.split_statements(sql)
    assert statements[0].startswith("CREATE TABLE IF NOT EXISTS")
    assert statements[1].startswith("MERGE INTO")
    assert all(not s.startswith("--") for s in statements)


# ---------- chunking ----------


REPORT = """# Solar storage outlook

Battery prices fell in 2024 and demand grew across three regions.

# Costs

Installers report steady demand.

Prices per kilowatt hour fell further in the second half.

# Suppliers

Three producers control most refined output.
"""


def test_chunk_zero_is_the_abstract():
    chunks = chunking.chunk_report(REPORT)
    assert chunks[0].index == 0 and chunks[0].heading == "abstract"
    assert chunks[0].text.startswith("Battery prices fell in 2024")


def test_chunks_follow_headings_and_are_indexed_in_order():
    chunks = chunking.chunk_report(REPORT)
    assert [c.heading for c in chunks] == ["abstract", "Costs", "Suppliers"]
    assert [c.index for c in chunks] == [0, 1, 2]
    assert "Installers report steady demand." in chunks[1].text


def test_long_sections_split_on_paragraphs_under_the_limit():
    body = "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(20))
    chunks = chunking.chunk_report(f"# Long\n\n{body}", max_chars=600)
    sections = [c for c in chunks if c.heading == "Long"]
    assert len(sections) > 1
    assert all(len(c.text) <= 600 for c in sections)


def test_empty_report_has_no_chunks():
    assert chunking.chunk_report("   ") == []


# ---------- activity summaries ----------


def test_summary_is_deterministic_and_includes_critic_comment():
    text = activity.summarize_activity(
        agent_name="section_researcher",
        topic="grid storage",
        queries=["battery costs", "lithium supply"],
        source_domains=["b.example", "a.example", "a.example"],
        fact_count=4,
        high_quality_count=2,
        status="ok",
        critic_comment="Missing pricing data.",
    )
    assert text == activity.summarize_activity(
        "section_researcher", "grid storage", ["battery costs", "lithium supply"],
        ["b.example", "a.example", "a.example"], 4, 2, "ok", "Missing pricing data.",
    )
    assert "section_researcher ran 2 queries" in text
    assert "a.example, b.example" in text
    assert "Critic: Missing pricing data." in text


# ---------- index sync ----------


class _Status:
    def __init__(self, ready):
        self.ready = ready


class _FakeIndexes:
    def __init__(self, ready_after: dict[str, int]):
        self.ready_after = ready_after
        self.synced: list[str] = []
        self.polls: dict[str, int] = {}

    def sync_index(self, index_name):
        self.synced.append(index_name)

    def get_index(self, name):
        self.polls[name] = self.polls.get(name, 0) + 1
        ready = self.polls[name] > self.ready_after.get(name, 0)
        return type("I", (), {"status": _Status(ready)})()


class _FakeClient:
    def __init__(self, indexes):
        self.vector_search_indexes = indexes


def test_sync_requests_every_index_and_waits_until_ready():
    indexes = _FakeIndexes({"c.s.facts_idx": 2, "c.s.responses_idx": 0})
    sleeps: list[float] = []
    sync.sync_and_wait(
        _FakeClient(indexes), ["c.s.facts_idx", "c.s.responses_idx"], poll_s=1, sleep=sleeps.append
    )
    assert indexes.synced == ["c.s.facts_idx", "c.s.responses_idx"]
    assert indexes.polls["c.s.facts_idx"] == 3
    assert sleeps == [1, 1]


def test_sync_times_out_with_the_pending_indexes():
    indexes = _FakeIndexes({"c.s.facts_idx": 10**6})
    ticks = iter([0.0, 0.0, 1000.0])
    with pytest.raises(sync.SyncTimeout, match="facts_idx"):
        sync.sync_and_wait(
            _FakeClient(indexes), ["c.s.facts_idx"], timeout_s=900, poll_s=1,
            sleep=lambda s: None, clock=lambda: next(ticks),
        )
