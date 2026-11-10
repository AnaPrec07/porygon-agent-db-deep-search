"""PR-8 tests: scope, eligibility and ranking, scoped retrieval, refresh trigger, bootstrap, and memory in the graph.

Hermetic: fake index client, fake workspace, fake chat model. No network, no workspace.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import NotFound
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.deep_search.config import DeepSearchConfig
from agent.deep_search.deps import Deps
from agent.deep_search.graph import build_graph, route_after_intake
from agent.memory.layout import RECORD_VERSION
from agent.memory.object_store import LocalObjectStore
from agent.memory.ranking import Policy, combined_score, decay, dedupe, diversify, eligibility
from agent.memory.recorder import Persistence
from agent.memory.refresh import RefreshTrigger
from agent.memory.retrieval import MemoryRetriever, SdkIndexClient
from agent.memory.scope import Scope
from agent.memory.writer import PersistenceWriter
from tests.fakes import FakeChat

NOW = datetime(2026, 11, 10, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def _fact(**overrides) -> dict:
    base = {
        "canonical_fact_key": "k1",
        "fact_text": "System costs were near 200 dollars per kilowatt hour.",
        "source_url": "https://news.example/a",
        "source_title": "News",
        "evidence_quality": "high",
        "time_sensitivity": "static",
        "status": "active",
        "fact_kind": "source_fact",
        "last_verified_at": (NOW - timedelta(days=3)).isoformat(),
        "latest_fact_id": "f1",
    }
    base.update(overrides)
    return base


# ---------- scope ----------


def test_scope_strings_and_filter_values():
    private = Scope("t1", "alice", "private")
    shared = Scope("t1", "alice", "tenant")
    assert private.access_scope == "actor:alice"
    assert shared.access_scope == "tenant:t1"
    assert private.filter_values() == ["tenant:t1", "actor:alice"]


@pytest.mark.parametrize("kwargs", [{"tenant_id": "", "actor_id": "a"}, {"tenant_id": "t", "actor_id": ""},
                                    {"tenant_id": "t", "actor_id": "a", "visibility": "public"}])
def test_scope_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        Scope(**kwargs)


# ---------- eligibility and ranking ----------


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({}, None),
        ({"status": "disputed"}, "not_active"),
        ({"fact_kind": "hypothesis"}, "not_source_fact"),
        ({"evidence_quality": "low"}, "quality_below_policy"),
        ({"time_sensitivity": "volatile", "last_verified_at": (NOW - timedelta(days=90)).isoformat()}, "stale"),
    ],
)
def test_eligibility_rules(overrides, expected):
    assert eligibility(_fact(**overrides), NOW, Policy()) == expected


def test_high_only_policy_excludes_medium_quality():
    assert eligibility(_fact(evidence_quality="medium"), NOW, Policy(min_quality="high")) == "quality_below_policy"


def test_decay_halves_at_the_half_life():
    assert decay(None, "static") == 1.0
    assert decay(365.0, "static") == 1.0
    assert decay(365.0, "slow") == pytest.approx(0.5)
    assert decay(30.0, "volatile") == pytest.approx(0.5)


def test_combined_score_weights_quality_and_age():
    fresh = combined_score(_fact(), 1.0, NOW)
    older = combined_score(_fact(last_verified_at=(NOW - timedelta(days=400)).isoformat(), time_sensitivity="slow"), 1.0, NOW)
    medium = combined_score(_fact(evidence_quality="medium"), 1.0, NOW)
    assert fresh == pytest.approx(1.0)
    assert older < fresh and medium == pytest.approx(0.7)


def test_dedupe_keeps_the_best_row_per_key_and_drops_near_duplicates():
    rows = [
        {"canonical_fact_key": "a", "fact_text": "Battery costs fell in 2024 across grids.", "score": 0.6},
        {"canonical_fact_key": "a", "fact_text": "Battery costs fell in 2024 across grids.", "score": 0.9},
        {"canonical_fact_key": "b", "fact_text": "Battery costs fell in 2024 across grids!", "score": 0.8},
        {"canonical_fact_key": "c", "fact_text": "Lithium supply is concentrated.", "score": 0.5},
    ]
    kept = dedupe(rows)
    assert [r["canonical_fact_key"] for r in kept] == ["a", "c"]
    assert kept[0]["score"] == 0.9


def test_diversify_caps_facts_per_domain():
    rows = [{"score": 1.0 - i / 10, "source_url": f"https://www.one.example/{i}"} for i in range(6)]
    rows.append({"score": 0.1, "source_url": "https://two.example/x"})
    chosen = diversify(rows, max_per_domain=4)
    assert sum(1 for r in chosen if "one.example" in r["source_url"]) == 4
    assert any("two.example" in r["source_url"] for r in chosen)


# ---------- scoped retrieval ----------


class FakeIndexClient:
    def __init__(self, results: dict[str, list[dict]], fail: set[str] | None = None) -> None:
        self.results = results
        self.fail = fail or set()
        self.calls: list[tuple[str, dict]] = []

    def query(self, index, columns, query_text, filters, num_results):
        self.calls.append((index, filters))
        if index in self.fail:
            raise RuntimeError("index unavailable")
        return list(self.results.get(index, []))


INDEXES = {"facts": "c.m.facts_idx", "responses": "c.m.responses_idx", "activity": "c.m.activity_idx"}


def test_retrieval_filters_every_index_on_the_callers_scope():
    client = FakeIndexClient({})
    MemoryRetriever(client, INDEXES, Scope("t1", "alice"), now=NOW).retrieve("topic")
    assert client.calls and all(f["access_scope"] == ["tenant:t1", "actor:alice"] for _, f in client.calls)
    fact_filters = [f for index, f in client.calls if index == INDEXES["facts"]][0]
    assert fact_filters["status"] == "active" and fact_filters["fact_kind"] == "source_fact"


def test_retrieval_returns_only_eligible_ranked_facts_and_reports_stale_ids():
    rows = [
        {**_fact(canonical_fact_key="ok", latest_fact_id="f-ok"), "score": 0.9},
        {**_fact(canonical_fact_key="stale", latest_fact_id="f-stale", time_sensitivity="volatile",
                 last_verified_at=(NOW - timedelta(days=120)).isoformat()), "score": 0.95},
        {**_fact(canonical_fact_key="low", latest_fact_id="f-low", evidence_quality="low"), "score": 0.99},
    ]
    client = FakeIndexClient({INDEXES["facts"]: rows})
    brief = MemoryRetriever(client, INDEXES, Scope("t1", "alice"), now=NOW).retrieve("topic")
    assert [f["canonical_fact_key"] for f in brief["facts"]] == ["ok"]
    assert brief["stale_fact_ids"] == ["f-stale"]


def test_a_failed_index_is_reported_and_the_others_still_answer():
    client = FakeIndexClient(
        {INDEXES["facts"]: [{**_fact(), "score": 0.5}]}, fail={INDEXES["responses"]}
    )
    brief = MemoryRetriever(client, INDEXES, Scope("t1", "alice"), now=NOW).retrieve("topic")
    assert len(brief["facts"]) == 1
    assert brief["errors"] and all(e["stage"] == "memory" for e in brief["errors"])
    assert "responses" in brief["errors"][0]["message"]


def test_sdk_client_maps_manifest_columns_and_scores():
    rows = [["k1", "fact text", "0.75"]]
    response = SimpleNamespace(
        manifest=SimpleNamespace(columns=[SimpleNamespace(name="canonical_fact_key"),
                                          SimpleNamespace(name="fact_text"), SimpleNamespace(name="score")]),
        result=SimpleNamespace(data_array=rows),
    )
    captured: dict = {}

    class _Indexes:
        def query_index(self, **kwargs):
            captured.update(kwargs)
            return response

    client = SdkIndexClient(SimpleNamespace(vector_search_indexes=_Indexes()))
    out = client.query("c.m.facts_idx", ["canonical_fact_key"], "q", {"access_scope": ["actor:a"]}, 5)
    assert out == [{"canonical_fact_key": "k1", "fact_text": "fact text", "score": 0.75}]
    assert captured["query_type"] == "HYBRID"
    assert json.loads(captured["filters_json"]) == {"access_scope": ["actor:a"]}


# ---------- refresh trigger ----------


class _Jobs:
    def __init__(self, fail: bool = False) -> None:
        self.runs: list[int] = []
        self.fail = fail

    def run_now(self, job_id):
        if self.fail:
            raise RuntimeError("workspace unavailable")
        self.runs.append(job_id)


def test_refresh_is_debounced_and_failures_do_not_raise():
    clock = iter([0.0, 10.0, 200.0])
    jobs = _Jobs()
    trigger = RefreshTrigger(SimpleNamespace(jobs=jobs), 42, debounce_s=120, clock=lambda: next(clock))
    assert trigger() is True
    assert trigger() is False  # within the debounce window
    assert trigger() is True
    assert jobs.runs == [42, 42]

    failing = RefreshTrigger(SimpleNamespace(jobs=_Jobs(fail=True)), 42, clock=lambda: 0.0)
    assert failing() is False


# ---------- bootstrap ----------


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location("bootstrap_ai_search", ROOT / "scripts/bootstrap_ai_search.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["bootstrap_ai_search"] = module
    spec.loader.exec_module(module)
    return module


class _FakeAISearch:
    def __init__(self, endpoint_exists: bool = False, indexes: set[str] | None = None) -> None:
        self.endpoints = {"deep-search-memory"} if endpoint_exists else set()
        self.indexes = set(indexes or set())
        self.created_endpoints: list[str] = []
        self.created_indexes: list[dict] = []
        self.vector_search_endpoints = self
        self.vector_search_indexes = self

    def get_endpoint(self, endpoint_name):
        if endpoint_name not in self.endpoints:
            raise NotFound("no endpoint")
        return object()

    def create_endpoint_and_wait(self, name, endpoint_type):
        self.endpoints.add(name)
        self.created_endpoints.append(name)

    def get_index(self, index_name):
        if index_name not in self.indexes:
            raise NotFound("no index")
        return object()

    def create_index(self, **kwargs):
        self.indexes.add(kwargs["name"])
        self.created_indexes.append(kwargs)


def test_bootstrap_creates_the_endpoint_and_three_delta_sync_indexes_once():
    bootstrap = _load_bootstrap()
    client = _FakeAISearch()
    actions = bootstrap.bootstrap(client, "deep_search")
    assert client.created_endpoints == ["deep-search-memory"]
    names = [i["name"] for i in client.created_indexes]
    assert names == ["deep_search.memory.facts_idx", "deep_search.memory.responses_idx", "deep_search.memory.activity_idx"]
    facts = client.created_indexes[0]
    assert facts["primary_key"] == "canonical_fact_key"
    assert facts["delta_sync_index_spec"].source_table == "deep_search.memory.facts_index_src"
    assert "access_scope" in facts["delta_sync_index_spec"].columns_to_sync
    assert any("create" in a for a in actions)

    client.created_indexes.clear()
    second = bootstrap.bootstrap(client, "deep_search")
    assert client.created_indexes == [] and client.created_endpoints == ["deep-search-memory"]
    assert all("exists" in a for a in second)


def test_bootstrap_dry_run_changes_nothing():
    bootstrap = _load_bootstrap()
    client = _FakeAISearch()
    actions = bootstrap.bootstrap(client, "deep_search", dry_run=True)
    assert client.created_endpoints == [] and client.created_indexes == []
    assert any("create STANDARD" in a for a in actions)


# ---------- records carry scope; memory reaches the planner ----------


def test_persisted_records_carry_tenant_and_actor_scope(tmp_path):
    store = LocalObjectStore(tmp_path)
    completed = []
    persistence = Persistence(
        PersistenceWriter(store, sleep=lambda s: None),
        scope=Scope("tenant-7", "alice", "private"),
        on_session_complete=lambda: completed.append(True),
    )
    deps = Deps.stub(DeepSearchConfig())
    graph = build_graph(deps, persistence=persistence).compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke({"messages": [{"role": "user", "content": "Scope test"}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)

    session_files = store.list_files(f"records/{RECORD_VERSION}/research_session/")
    record = json.loads(store.read(session_files[-1]))
    assert record["tenant_id"] == "tenant-7"
    assert record["actor_id"] == "alice"
    assert record["access_scope"] == "actor:alice"
    assert completed == [True]  # the refresh hook fires once, after the manifest


def test_new_topic_retrieves_memory_and_revisions_do_not():
    calls: list[str] = []

    def memory(state):
        calls.append(state.get("research_topic", ""))
        return {"scope": "tenant:t", "facts": [{"fact_text": "Prior fact about grids.", "source_title": "Prior",
                                              "last_verified_at": "2026-11-01"}],
                "prior_responses": [], "prior_activity": [], "stale_fact_ids": [], "errors": []}

    plan = {"goals": [{"task_type": "RESEARCH", "tags": [], "text": "Analyze grids"}]}
    outline = {"sections": [{"title": f"S{i}", "overview": "Overview."} for i in range(4)]}
    chat = FakeChat(structured_results=[plan, plan, outline, {"grade": "pass", "comment": "ok"}],
                    texts=["Report."])
    deps = Deps.from_models(DeepSearchConfig(), worker=chat, critic=chat, memory=memory)
    graph = build_graph(deps).compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke({"messages": [{"role": "user", "content": "Grids"}]}, cfg)
    graph.invoke(Command(resume={"decision": "revise", "feedback": "add storage"}), cfg)

    assert calls == ["Grids"]  # the revision did not query memory again
    first_plan_prompt = chat.calls[0][2][1][1]
    assert "Prior fact about grids." in first_plan_prompt


def test_route_after_intake_skips_retrieval_for_revisions():
    assert route_after_intake({"research_plan": ""}) == "memory_retriever"
    assert route_after_intake({"research_plan": "* [RESEARCH] x"}) == "plan_generator"


def test_index_sources_expose_no_unscoped_rows_in_the_bundle_sql():
    sql = (ROOT / "platform/src/sql/facts_index_src.sql").read_text(encoding="utf-8")
    assert "'unscoped'" not in sql and "max_by(access_scope, ingested_at)" in sql
