"""PR-3 tests: deep-search graph routing, plan approval (HITL), evaluation loop, and citations.

Hermetic: stub dependencies, in-memory checkpointer, no Databricks auth or model calls.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.deep_search.citations import replace_citations
from agent.deep_search.config import DeepSearchConfig
from agent.deep_search.deps import Deps
from agent.deep_search.graph import build_graph, route_after_evaluation, route_after_plan_review


def _graph(deps: Deps):
    return build_graph(deps).compile(checkpointer=InMemorySaver())


def _config(**overrides) -> DeepSearchConfig:
    return replace(DeepSearchConfig(), **overrides)


def _thread() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


def _interrupts(result) -> list:
    return list(result.get("__interrupt__", []))


# ---------- configuration ----------


def test_config_defaults_and_env_override():
    assert DeepSearchConfig.from_env({}).max_search_iterations == 5
    cfg = DeepSearchConfig.from_env(
        {"DEEP_SEARCH_MAX_SEARCH_ITERATIONS": "3", "DEEP_SEARCH_WORKER_MODEL": "system.ai.x", "DEEP_SEARCH_CRITIC_MODEL": ""}
    )
    assert cfg.max_search_iterations == 3
    assert cfg.worker_model == "system.ai.x"
    assert cfg.critic_model is None


@pytest.mark.parametrize("value", ["0", "-1", "many"])
def test_config_rejects_invalid_iterations(value):
    with pytest.raises(ValueError):
        DeepSearchConfig.from_env({"DEEP_SEARCH_MAX_SEARCH_ITERATIONS": value})


# ---------- routing tables ----------


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("researching", "section_planner"),
        ("planning", "plan_generator"),
        ("cancelled", "finalize_cancelled"),
    ],
)
def test_route_after_plan_review(phase, expected):
    assert route_after_plan_review({"phase": phase}) == expected


@pytest.mark.parametrize(
    "grade,iteration,max_iter,expected",
    [
        ("pass", 1, 5, "report_composer_with_citations"),
        ("fail", 1, 5, "enhanced_search_executor"),
        ("fail", 4, 5, "enhanced_search_executor"),
        ("fail", 5, 5, "report_composer_with_citations"),
        ("fail", 2, 2, "report_composer_with_citations"),
    ],
)
def test_route_after_evaluation(grade, iteration, max_iter, expected):
    state = {"research_evaluation": {"grade": grade}, "iteration": iteration}
    assert route_after_evaluation(state, max_iter) == expected


# ---------- citations (ported behaviour) ----------

SOURCES = {"src-1": {"title": "Alpha", "url": "https://a.example/x", "domain": "a.example"}}


def test_valid_citation_becomes_markdown_link():
    # Tags are written directly after the claim; the rewrite prepends a space, as in the recipe.
    text, stats = replace_citations('Revenue grew<cite source="src-1" />.', SOURCES)
    assert text == "Revenue grew [Alpha](https://a.example/x)."
    assert stats == {"total": 1, "valid": 1, "dropped": 0}


def test_unknown_citation_is_dropped_not_rendered():
    text, stats = replace_citations("Claim<cite source=\"src-9\"/>.", SOURCES)
    assert "src-9" not in text and "](" not in text
    assert stats["dropped"] == 1


def test_space_before_punctuation_is_removed():
    text, _ = replace_citations('Fact <cite source="src-1" /> , and more .', SOURCES)
    assert text.endswith("[Alpha](https://a.example/x), and more.")


def test_domain_used_when_title_missing():
    sources = {"src-2": {"title": "", "url": "https://b.example", "domain": "b.example"}}
    text, _ = replace_citations('<cite source="src-2" />', sources)
    assert text.strip() == "[b.example](https://b.example)"


# ---------- plan approval flow (HITL) ----------


def test_graph_pauses_for_plan_approval():
    graph = _graph(Deps.stub(_config()))
    cfg = _thread()
    result = graph.invoke({"messages": [{"role": "user", "content": "Solar storage costs"}]}, cfg)

    (interrupt,) = _interrupts(result)
    assert interrupt.value["type"] == "plan_review"
    assert interrupt.value["agent_name"] == "interactive_planner_agent"
    assert "Solar storage costs" in interrupt.value["plan"]
    assert "[DELIVERABLE][IMPLIED]" in interrupt.value["plan"]
    # Nothing is researched before approval.
    assert "section_research_findings" not in graph.get_state(cfg).values


def test_approve_runs_to_completion_with_cited_report_shape():
    graph = _graph(Deps.stub(_config()))
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Topic A"}]}, cfg)
    result = graph.invoke(Command(resume={"decision": "approve"}), cfg)

    assert not _interrupts(result)
    state = graph.get_state(cfg).values
    assert state["phase"] == "done"
    assert state["section_research_findings"]
    assert state["final_report_with_citations"].startswith("## Report")
    assert state["citation_stats"] == {"total": 0, "valid": 0, "dropped": 0}


def test_revise_regenerates_plan_with_feedback():
    graph = _graph(Deps.stub(_config()))
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Topic B"}]}, cfg)
    result = graph.invoke(Command(resume={"decision": "revise", "feedback": "add pricing data"}), cfg)

    (interrupt,) = _interrupts(result)
    assert "add pricing data" in interrupt.value["plan"]
    assert interrupt.value["revisions"] == 1
    assert graph.get_state(cfg).values["phase"] == "awaiting_approval"


def test_chat_text_while_awaiting_is_feedback_not_new_topic():
    graph = _graph(Deps.stub(_config()))
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Topic C"}]}, cfg)
    result = graph.invoke({"messages": [{"role": "user", "content": "also cover tariffs"}]}, cfg)

    (interrupt,) = _interrupts(result)
    state = graph.get_state(cfg).values
    assert state["research_topic"] == "Topic C"
    assert "also cover tariffs" in interrupt.value["plan"]


def test_cancel_ends_without_research():
    graph = _graph(Deps.stub(_config()))
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Topic D"}]}, cfg)
    result = graph.invoke(Command(resume={"decision": "cancel"}), cfg)

    assert not _interrupts(result)
    state = graph.get_state(cfg).values
    assert state["phase"] == "cancelled"
    assert "section_research_findings" not in state
    assert "final_cited_report" not in state


def test_invalid_decision_is_rejected():
    graph = _graph(Deps.stub(_config()))
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Topic E"}]}, cfg)
    with pytest.raises(ValueError, match="plan_review expects"):
        graph.invoke(Command(resume={"decision": "maybe"}), cfg)


# ---------- evaluation loop ----------


def _counting_deps(evaluate_grades: list[str], max_iterations: int) -> tuple[Deps, dict]:
    counts = {"evaluate": 0, "refine": 0}
    base = Deps.stub(_config(max_search_iterations=max_iterations))

    def evaluate(state):
        grade = evaluate_grades[min(counts["evaluate"], len(evaluate_grades) - 1)]
        counts["evaluate"] += 1
        return {"grade": grade, "comment": "c", "follow_up_queries": None}

    def refine(state):
        counts["refine"] += 1
        return base.refine(state)

    return replace(base, evaluate=evaluate, refine=refine), counts


def test_pass_on_first_evaluation_skips_refinement():
    deps, counts = _counting_deps(["pass"], max_iterations=5)
    graph = _graph(deps)
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "T"}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    assert counts == {"evaluate": 1, "refine": 0}


def test_fail_loops_until_iteration_cap():
    deps, counts = _counting_deps(["fail"], max_iterations=2)
    graph = _graph(deps)
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "T"}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    # evaluate (fail, iter 1) -> refine -> evaluate (fail, iter 2 = cap) -> compose
    assert counts == {"evaluate": 2, "refine": 1}
    assert graph.get_state(cfg).values["phase"] == "done"


def test_refinement_keeps_earlier_findings():
    deps, _ = _counting_deps(["fail", "pass"], max_iterations=5)
    graph = _graph(deps)
    cfg = _thread()
    graph.invoke({"messages": [{"role": "user", "content": "Topic F"}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    findings = graph.get_state(cfg).values["section_research_findings"]
    assert "Stub findings for: Topic F." in findings
    assert "Stub refinement pass." in findings


# ---------- agent entry point (adapter path) ----------


@pytest.mark.asyncio
async def test_run_agent_pauses_then_completes_through_adapter(monkeypatch):
    from agent import agent as agent_module
    from runtime.adapter import _serialize_events

    monkeypatch.setenv("DEEP_SEARCH_MAX_SEARCH_ITERATIONS", "5")
    session_id = f"test-{uuid.uuid4()}"

    first = [
        e
        async for e in _serialize_events(
            agent_module.run_agent(
                {"messages": [{"role": "user", "content": "Adapter topic"}]},
                session_id=session_id,
                actor=session_id,
            )
        )
    ]
    interrupts = [e for e in first if e["type"] == "interrupt"]
    assert interrupts and interrupts[0]["value"]["type"] == "plan_review"

    second = [
        e
        async for e in _serialize_events(
            agent_module.run_agent(
                Command(resume={"decision": "approve"}),
                session_id=session_id,
                actor=session_id,
            )
        )
    ]
    assert not [e for e in second if e["type"] == "interrupt"]
    messages = [e["message"] for e in second if e["type"] == "message"]
    assert any("Stub report" in str(m.get("content", "")) for m in messages)
