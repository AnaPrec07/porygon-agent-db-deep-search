"""PR-10 tests: the evaluation dataset, scorers and gates, restart recovery, concurrent sessions, and deletion.

Hermetic: stub graph, in-memory checkpointer, local object store in a temp directory. Live quality runs are in
docs/runbooks/evaluation.md and are not part of this suite.
"""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.deep_search.config import DeepSearchConfig
from agent.deep_search.deps import Deps
from agent.deep_search.graph import build_graph
from agent.memory.object_store import LocalObjectStore
from agent.memory.recorder import Persistence
from agent.memory.writer import PersistenceWriter
from evals.harness import load_dataset, run_topic
from evals.scorers import (
    aspect_mention_rate,
    citation_validity,
    citations_supported,
    gate_failures,
    loop_bounded,
    plan_format,
    report_present,
)

ROOT = Path(__file__).resolve().parents[1]


# ---------- dataset ----------


def test_dataset_has_thirty_topics_split_by_volatility_plus_repeats():
    rows = load_dataset()
    assert len({r["id"] for r in rows}) == len(rows)
    unique = [r for r in rows if r["repeat_of"] is None]
    assert len(unique) == 30
    counts = {v: sum(1 for r in unique if r["volatility"] == v) for v in ("static", "slow", "volatile")}
    assert counts == {"static": 10, "slow": 10, "volatile": 10}
    assert len({r["topic"] for r in unique}) == 30


def test_repeats_point_at_an_earlier_row_with_the_same_topic():
    rows = {r["id"]: r for r in load_dataset()}
    repeats = [r for r in rows.values() if r["repeat_of"]]
    assert len(repeats) == 5
    for row in repeats:
        original = rows[row["repeat_of"]]
        assert original["topic"] == row["topic"]
        assert original["repeat_of"] is None


def test_every_row_has_aspects_and_a_research_goal_minimum():
    for row in load_dataset():
        assert 2 <= len(row["expected_aspects"]) <= 4
        assert row["min_research_goals"] == 5


# ---------- scorers ----------

GOOD_PLAN = "\n".join(f"* [RESEARCH] Goal {i}" for i in range(5)) + "\n* [DELIVERABLE][IMPLIED] Summary"


def _outputs(**overrides) -> dict:
    base = {
        "phase": "done",
        "research_plan": GOOD_PLAN,
        "final_cited_report": "Claim.",
        "final_report_with_citations": "Claim with [Source](https://a.example).",
        "citation_stats": {"total": 1, "valid": 1, "dropped": 0},
        "facts": [],
        "iterations": 1,
        "max_iterations": 5,
    }
    base.update(overrides)
    return base


def test_report_present_requires_done_and_text():
    assert report_present({}, _outputs(), {})
    assert not report_present({}, _outputs(phase="cancelled"), {})
    assert not report_present({}, _outputs(final_report_with_citations="  "), {})


def test_citation_validity_fails_on_leftover_tags_or_dropped_ids():
    assert citation_validity({}, _outputs(), {})
    assert not citation_validity({}, _outputs(final_report_with_citations='x <cite source="src-1" />'), {})
    assert not citation_validity({}, _outputs(citation_stats={"total": 2, "valid": 1, "dropped": 1}), {})


def test_citations_supported_needs_a_verified_fact_for_every_cited_source():
    outputs = _outputs(final_cited_report='Claim <cite source="src-1" />.',
                       facts=[{"source_id": "src-1", "status": "active"}])
    assert citations_supported({}, outputs, {})
    unsupported = _outputs(final_cited_report='Claim <cite source="src-2" />.', facts=[{"source_id": "src-1", "status": "active"}])
    assert not citations_supported({}, unsupported, {})
    assert citations_supported({}, _outputs(final_cited_report="No cites."), {})


def test_plan_format_checks_tags_and_the_research_minimum():
    assert plan_format({}, _outputs(), {"min_research_goals": 5})
    assert not plan_format({}, _outputs(research_plan="A plain sentence."), {"min_research_goals": 5})
    assert not plan_format({}, _outputs(research_plan="* [RESEARCH] only one"), {"min_research_goals": 5})


def test_loop_bounded_and_aspect_rate():
    assert loop_bounded({}, _outputs(iterations=5, max_iterations=5), {})
    assert not loop_bounded({}, _outputs(iterations=6, max_iterations=5), {})
    text = "This covers pricing and supply chains in detail."
    rate = aspect_mention_rate({}, _outputs(final_report_with_citations=text),
                               {"expected_aspects": ["pricing", "supply", "policy", "storage"]})
    assert rate == 0.5


def test_gate_failures_report_only_measured_metrics_below_their_floor():
    assert gate_failures({"citation_validity": 1.0, "aspect_mention_rate": 0.9}) == []
    assert gate_failures({"citation_validity": 0.97, "aspect_mention_rate": 0.9}) == ["citation_validity"]
    assert gate_failures({}) == []


def test_harness_run_is_done_and_scores_the_plan_on_the_stub_graph():
    row = load_dataset()[0]
    outputs = run_topic(row["topic"])
    assert outputs["phase"] == "done"
    assert plan_format({}, outputs, {"min_research_goals": 5})
    assert loop_bounded({}, outputs, {})


# ---------- restart and concurrency ----------


def _graph(saver):
    return build_graph(Deps.stub(DeepSearchConfig())).compile(checkpointer=saver)


def test_a_restarted_worker_resumes_a_paused_plan_from_the_checkpoint():
    saver = InMemorySaver()
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    first = _graph(saver)
    first.invoke({"messages": [{"role": "user", "content": "Restart topic"}]}, cfg)
    del first  # the worker process is gone; the checkpoint remains

    second = _graph(saver)
    second.invoke(Command(resume={"decision": "approve"}), cfg)
    values = second.get_state(cfg).values
    assert values["phase"] == "done"
    assert values["final_report_with_citations"]


def test_concurrent_sessions_stay_isolated_and_each_commits_a_manifest(tmp_path):
    store = LocalObjectStore(tmp_path)
    writer = PersistenceWriter(store, sleep=lambda s: None)
    persistence = Persistence(writer)

    def run_one(index: int) -> str:
        graph = build_graph(Deps.stub(DeepSearchConfig()), persistence=persistence).compile(checkpointer=InMemorySaver())
        cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
        graph.invoke({"messages": [{"role": "user", "content": f"Topic {index}"}]}, cfg)
        graph.invoke(Command(resume={"decision": "approve"}), cfg)
        return graph.get_state(cfg).values["research_session_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        session_ids = list(pool.map(run_one, range(12)))

    assert len(set(session_ids)) == 12
    manifests = store.list_files("records/v1/session_manifest/")
    assert len(manifests) == 12
    for session_id in session_ids:
        manifest_path = next(m for m in manifests if f"/{session_id}/" in m)
        manifest = json.loads(store.read(manifest_path))
        assert manifest["status"] == "completed"
        for entry in manifest["files"]:
            assert f"/{session_id}/" in entry["path"], entry["path"]


# ---------- deletion ----------


def _load_delete_script():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("delete_research", ROOT / "scripts/delete_research.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["delete_research"] = module
    spec.loader.exec_module(module)
    return module


def _completed_session(store) -> str:
    graph = build_graph(Deps.stub(DeepSearchConfig()), persistence=Persistence(PersistenceWriter(store, sleep=lambda s: None))).compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke({"messages": [{"role": "user", "content": "Delete me"}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    return graph.get_state(cfg).values["research_session_id"]


def test_deletion_dry_run_changes_nothing(tmp_path):
    delete = _load_delete_script()
    store = LocalObjectStore(tmp_path)
    session = _completed_session(store)
    before = store.list_files("")
    result = delete.delete_session(store, session, apply=False)
    assert result["files"] and store.list_files("") == before


def test_deletion_removes_one_session_and_keeps_the_audit_record(tmp_path):
    delete = _load_delete_script()
    store = LocalObjectStore(tmp_path)
    doomed = _completed_session(store)
    kept = _completed_session(store)
    kept_before = [p for p in store.list_files("") if f"/{kept}/" in p]

    result = delete.delete_session(store, doomed, apply=True)

    assert result["applied"] is True
    assert not [p for p in store.list_files("") if f"/{doomed}/" in p and "/deletion_request/" not in p]
    assert [p for p in store.list_files("") if f"/{kept}/" in p] == kept_before
    audit = [p for p in store.list_files("records/v1/deletion_request/") if doomed in p]
    assert len(audit) == 1
    assert json.loads(store.read(audit[0]))["record_type"] == "deletion_request"
    assert any("DELETE FROM deep_search.memory.facts_index_src" in s for s in result["delta_statements"])
