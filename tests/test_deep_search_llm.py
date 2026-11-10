"""PR-4 tests: LLM-backed nodes, structured output, rendering, and prompt wiring.

Uses a fake chat model, so nothing calls a model endpoint and no workspace auth is needed.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from tests.fakes import FakeChat

from agent import agent as agent_module
from agent.deep_search import llm_nodes
from agent.deep_search.config import DeepSearchConfig
from agent.deep_search.deps import Deps
from agent.deep_search.graph import build_graph
from agent.deep_search.llm import StructuredOutputError, structured, text_of
from agent.deep_search.prompts import CITATION_RULE_NO_SOURCES, CITATION_RULE_WITH_SOURCES
from agent.deep_search.schemas import Feedback, OutlineSection, PlanGoal, ReportOutline, ResearchPlan


def _plan_dict(topic: str = "Solar storage") -> dict:
    return {
        "goals": [
            {"task_type": "RESEARCH", "tags": [], "text": f"Analyze {topic} costs"},
            {"task_type": "RESEARCH", "tags": ["MODIFIED"], "text": f"Identify suppliers of {topic}"},
            {"task_type": "DELIVERABLE", "tags": ["IMPLIED"], "text": "Create a comparison table"},
        ]
    }


def _outline_dict() -> dict:
    return {
        "sections": [
            {"title": f"Section {i}", "overview": f"Overview of part {i}."} for i in range(1, 5)
        ]
    }


PASS = {"grade": "pass", "comment": "Thorough.", "follow_up_queries": None}


# ---------- rendering (golden) ----------


def test_plan_renders_tags_in_recipe_format():
    plan = ResearchPlan(
        goals=[
            PlanGoal(task_type="RESEARCH", text="Analyze market size"),
            PlanGoal(task_type="RESEARCH", tags=["MODIFIED", "NEW"], text="Identify competitors"),
            PlanGoal(task_type="DELIVERABLE", tags=["IMPLIED"], text="Create a summary report"),
        ]
    )
    assert plan.render() == (
        "* [RESEARCH] Analyze market size\n"
        "* [RESEARCH][MODIFIED][NEW] Identify competitors\n"
        "* [DELIVERABLE][IMPLIED] Create a summary report"
    )


def test_plan_requires_a_research_goal():
    with pytest.raises(ValidationError, match="RESEARCH"):
        ResearchPlan(goals=[PlanGoal(task_type="DELIVERABLE", text="Create a table")])


def test_outline_renders_markdown_sections():
    outline = ReportOutline(sections=[OutlineSection(title="Background", overview="Context.") for _ in range(4)])
    assert outline.render().startswith("# Background\nContext.\n\n# Background")


@pytest.mark.parametrize("count", [3, 7])
def test_outline_enforces_four_to_six_sections(count):
    sections = [OutlineSection(title=f"S{i}", overview="Overview.") for i in range(count)]
    with pytest.raises(ValidationError):
        ReportOutline(sections=sections)


# ---------- structured output and text extraction ----------


def test_structured_retries_with_the_validation_error():
    chat = FakeChat(structured_results=[{"goals": []}, _plan_dict()])
    plan = structured(chat, ResearchPlan, "sys", "human")
    assert isinstance(plan, ResearchPlan)
    second_call_messages = chat.calls[1][2]
    assert "invalid" in second_call_messages[-1][1].lower()


def test_structured_gives_up_after_retries():
    chat = FakeChat(structured_results=[{"goals": []}, {"goals": []}])
    with pytest.raises(StructuredOutputError):
        structured(chat, ResearchPlan, "sys", "human", retries=1)


def test_text_of_drops_reasoning_blocks():
    message = AIMessage(
        content=[
            {"type": "reasoning", "reasoning": "private chain of thought"},
            {"type": "text", "text": "Visible answer."},
        ]
    )
    assert text_of(message) == "Visible answer."


# ---------- LLM nodes ----------


def test_plan_prompt_carries_topic_and_feedback():
    chat = FakeChat(structured_results=[_plan_dict()])
    state = {"research_topic": "Grid batteries", "plan_feedback": "add pricing", "research_plan": "old plan"}
    rendered = llm_nodes.plan_with_llm(chat, state)
    assert rendered.startswith("* [RESEARCH]")
    human = chat.calls[0][2][1][1]
    assert "Grid batteries" in human and "add pricing" in human and "old plan" in human


def test_evaluator_pass_clears_follow_up_queries():
    chat = FakeChat(
        structured_results=[
            {"grade": "pass", "comment": "ok", "follow_up_queries": [{"search_query": "extra"}]}
        ]
    )
    result = llm_nodes.evaluate_with_llm(chat, {"research_topic": "T", "section_research_findings": "F"})
    assert result["grade"] == "pass"
    assert result["follow_up_queries"] is None


def test_composer_without_sources_forbids_citation_tags():
    chat = FakeChat(texts=["## Report\n\nBody text."])
    text = llm_nodes.compose_with_llm(chat, {"research_topic": "T", "sources": {}})
    assert text == "## Report\n\nBody text."
    system = chat.calls[0][1][0][1]
    assert CITATION_RULE_NO_SOURCES in system


def test_composer_with_sources_lists_their_ids():
    chat = FakeChat(texts=["Body."])
    sources = {"src-1": {"title": "Alpha", "url": "https://a.example"}}
    llm_nodes.compose_with_llm(chat, {"sources": sources, "section_research_findings": "F"})
    system, human = chat.calls[0][1][0][1], chat.calls[0][1][1][1]
    assert CITATION_RULE_WITH_SOURCES in system
    assert "src-1: Alpha (https://a.example)" in human


def test_composer_rejects_empty_output():
    with pytest.raises(ValueError, match="no text"):
        llm_nodes.compose_with_llm(FakeChat(texts=["   "]), {})


# ---------- graph with LLM dependencies ----------


def _run_to_done(deps: Deps) -> dict:
    graph = build_graph(deps).compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke({"messages": [{"role": "user", "content": "Grid batteries"}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    return graph.get_state(cfg).values


def test_graph_with_llm_deps_runs_to_a_composed_report():
    chat = FakeChat(
        structured_results=[_plan_dict("Grid batteries"), _outline_dict(), PASS],
        texts=["## Report\n\nGrid batteries are growing."],
    )
    config = DeepSearchConfig(max_search_iterations=5)
    state = _run_to_done(Deps.from_models(config, worker=chat, critic=chat))
    assert state["phase"] == "done"
    assert state["final_report_with_citations"] == "## Report\n\nGrid batteries are growing."
    assert state["report_sections"].startswith("# Section 1")
    assert "Analyze Grid batteries costs" in state["research_plan"]


def test_plan_revision_is_sent_to_the_model():
    chat = FakeChat(structured_results=[_plan_dict(), _plan_dict()])
    graph = build_graph(Deps.from_models(DeepSearchConfig(), worker=chat, critic=chat)).compile(
        checkpointer=InMemorySaver()
    )
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke({"messages": [{"role": "user", "content": "Topic"}]}, cfg)
    graph.invoke(Command(resume={"decision": "revise", "feedback": "focus on Europe"}), cfg)
    revised_human = chat.calls[1][2][1][1]
    assert "focus on Europe" in revised_human


# ---------- agent wiring ----------


@pytest.mark.asyncio
async def test_create_agent_graph_without_model_uses_stub_nodes(monkeypatch, caplog):
    monkeypatch.delenv("DEEP_SEARCH_WORKER_MODEL", raising=False)
    graph = await agent_module.create_agent_graph("actor-1")
    assert graph is not None
    assert "stub nodes" in caplog.text


@pytest.mark.asyncio
async def test_create_agent_graph_with_model_wires_llm_nodes(monkeypatch):
    monkeypatch.setenv("DEEP_SEARCH_WORKER_MODEL", "system.ai.test-model")
    monkeypatch.delenv("DEEP_SEARCH_CRITIC_MODEL", raising=False)
    created: list[dict] = []
    fake = FakeChat(structured_results=[_plan_dict("T"), _outline_dict(), PASS], texts=["Report body."])

    def fake_chat(**kwargs):
        created.append(kwargs)
        return fake

    monkeypatch.setattr(agent_module, "_RoutedChatDatabricks", fake_chat)
    monkeypatch.setattr(agent_module, "_app_workspace_client", lambda: object())
    graph = await agent_module.create_agent_graph("actor-1")

    assert [c["endpoint"] for c in created] == ["system.ai.test-model"]  # critic reuses the worker
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke({"messages": [{"role": "user", "content": "T"}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    assert graph.get_state(cfg).values["final_report_with_citations"] == "Report body."


@pytest.mark.asyncio
async def test_ui_model_override_sets_the_worker_model(monkeypatch):
    monkeypatch.delenv("DEEP_SEARCH_WORKER_MODEL", raising=False)
    created: list[dict] = []
    monkeypatch.setattr(agent_module, "_RoutedChatDatabricks", lambda **kw: created.append(kw) or FakeChat())
    monkeypatch.setattr(agent_module, "_app_workspace_client", lambda: object())
    await agent_module.create_agent_graph("actor-1", "system.ai.override")
    assert created[0]["endpoint"] == "system.ai.override"
