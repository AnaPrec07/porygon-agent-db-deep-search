"""LLM-backed implementations of the stub callables in deps.py (PR-4).

Covered: plan generation, outline, evaluation, and report composition.
Not covered until PR-5, because each needs search results: query planning, findings synthesis, deliverable
building, and the research and refinement steps. Those stay as stubs in deps.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent.deep_search.llm import structured, text_of
from agent.deep_search.prompts import (
    CITATION_RULE_NO_SOURCES,
    CITATION_RULE_WITH_SOURCES,
    COMPOSER_SYSTEM,
    EVALUATOR_SYSTEM,
    OUTLINE_SYSTEM,
    PLAN_SYSTEM,
)
from agent.deep_search.schemas import Feedback, ReportOutline, ResearchPlan

StateView = dict[str, Any]


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _prior_research(state: StateView) -> str:
    brief = state.get("memory_brief") or {}
    lines = [
        f"- {fact['fact_text']} (source: {fact.get('source_title') or fact.get('source_url', '')}, "
        f"last verified {fact.get('last_verified_at') or 'unknown'})"
        for fact in brief.get("facts", [])
    ]
    lines += [f"- Earlier research on this topic: {row['summary_text']}" for row in brief.get("prior_activity", [])]
    return "\n".join(lines) or "(none)"


def plan_with_llm(llm: Any, state: StateView) -> str:
    previous = state.get("research_plan") or "(none)"
    feedback = (state.get("plan_feedback") or "").strip() or "(none)"
    human = (
        f"Research topic: {state.get('research_topic', '')}\n\n"
        f"Previous plan:\n{previous}\n\n"
        f"User feedback:\n{feedback}\n\n"
        f"Prior research (verified, may be outdated):\n{_prior_research(state)}\n\n"
        f"Current date: {_today()}"
    )
    plan = structured(llm, ResearchPlan, PLAN_SYSTEM, human)
    return plan.render()


def outline_with_llm(llm: Any, state: StateView) -> str:
    human = (
        f"Research topic: {state.get('research_topic', '')}\n\n"
        f"Approved research plan:\n{state.get('research_plan', '')}"
    )
    return structured(llm, ReportOutline, OUTLINE_SYSTEM, human).render()


def evaluate_with_llm(llm: Any, state: StateView) -> dict[str, Any]:
    human = (
        f"Research topic: {state.get('research_topic', '')}\n\n"
        f"Research findings:\n{state.get('section_research_findings', '')}\n\n"
        f"Current date: {_today()}"
    )
    feedback = structured(llm, Feedback, EVALUATOR_SYSTEM, human)
    if feedback.grade == "pass":
        feedback = feedback.model_copy(update={"follow_up_queries": None})
    return feedback.model_dump()


def compose_with_llm(llm: Any, state: StateView) -> str:
    sources = state.get("sources") or {}
    rule = CITATION_RULE_WITH_SOURCES if sources else CITATION_RULE_NO_SOURCES
    source_lines = "\n".join(
        f"- {sid}: {info.get('title', '')} ({info.get('url', '')})" for sid, info in sources.items()
    ) or "(none)"
    system = COMPOSER_SYSTEM.format(citation_rule=rule)
    human = (
        f"Research plan:\n{state.get('research_plan', '')}\n\n"
        f"Research findings:\n{state.get('section_research_findings', '')}\n\n"
        f"Citation sources:\n{source_lines}\n\n"
        f"Report structure:\n{state.get('report_sections', '')}"
    )
    response = llm.invoke([("system", system), ("human", human)])
    text = text_of(response)
    if not text:
        raise ValueError("composer returned no text")
    return text
