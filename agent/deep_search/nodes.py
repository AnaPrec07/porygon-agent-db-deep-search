"""Graph nodes. Names match the ADK recipe's agent names, which the UI keys off (plan P10)."""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from agent.deep_search.citations import replace_citations
from agent.deep_search.deps import Deps
from agent.deep_search.schemas import Feedback
from agent.deep_search.state import DeepSearchState

DECISIONS = ("approve", "revise", "cancel")
PLAN_REVIEW_AGENT = "interactive_planner_agent"


def _last_user_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) == "human":
            return str(message.content).strip()
    raise ValueError("no user message in state")


def _view(state: DeepSearchState) -> dict[str, Any]:
    return dict(state)


def make_nodes(deps: Deps) -> dict[str, Any]:
    """Return node functions bound to ``deps``."""

    def intake(state: DeepSearchState) -> dict[str, Any]:
        text = _last_user_text(state.get("messages", []))
        if state.get("phase") == "awaiting_approval":
            # Chat text while a plan is waiting for approval is feedback, not a new topic.
            return {"plan_feedback": text, "phase": "planning"}
        return {
            "research_topic": text,
            "research_session_id": str(uuid.uuid4()),
            "phase": "planning",
            "research_plan": "",
            "plan_feedback": "",
            "plan_revisions": 0,
            "iteration": 0,
        }

    def plan_generator(state: DeepSearchState) -> dict[str, Any]:
        plan = deps.plan(_view(state))
        revisions = state.get("plan_revisions", 0) + (1 if state.get("research_plan") else 0)
        return {
            "research_plan": plan,
            "plan_revisions": revisions,
            "plan_feedback": "",
            "phase": "awaiting_approval",
            "messages": [AIMessage(content=plan, name=PLAN_REVIEW_AGENT)],
        }

    def plan_review(state: DeepSearchState) -> dict[str, Any]:
        decision = interrupt(
            {
                "type": "plan_review",
                "agent_name": PLAN_REVIEW_AGENT,
                "plan": state.get("research_plan", ""),
                "revisions": state.get("plan_revisions", 0),
            }
        )
        if not isinstance(decision, dict) or decision.get("decision") not in DECISIONS:
            raise ValueError(f"plan_review expects {{'decision': one of {DECISIONS}}}, got {decision!r}")
        choice = decision["decision"]
        if choice == "approve":
            return {"phase": "researching"}
        if choice == "revise":
            return {"plan_feedback": str(decision.get("feedback", "")).strip(), "phase": "planning"}
        return {"phase": "cancelled"}

    def section_planner(state: DeepSearchState) -> dict[str, Any]:
        return {"report_sections": deps.outline(_view(state))}

    def section_researcher(state: DeepSearchState) -> dict[str, Any]:
        return {"section_research_findings": deps.research(_view(state)), "iteration": 0}

    def research_evaluator(state: DeepSearchState) -> dict[str, Any]:
        feedback = Feedback.model_validate(deps.evaluate(_view(state)))
        return {
            "research_evaluation": feedback.model_dump(),
            "iteration": state.get("iteration", 0) + 1,
        }

    def enhanced_search_executor(state: DeepSearchState) -> dict[str, Any]:
        return {"section_research_findings": deps.refine(_view(state))}

    def report_composer_with_citations(state: DeepSearchState) -> dict[str, Any]:
        return {"final_cited_report": deps.compose(_view(state)), "phase": "composing"}

    def citation_processor(state: DeepSearchState) -> dict[str, Any]:
        text, stats = replace_citations(state.get("final_cited_report", ""), state.get("sources", {}))
        return {
            "final_report_with_citations": text,
            "citation_stats": stats,
            "messages": [AIMessage(content=text)],
        }

    def response_finalizer(state: DeepSearchState) -> dict[str, Any]:
        return {"phase": "done"}

    def finalize_cancelled(state: DeepSearchState) -> dict[str, Any]:
        return {
            "phase": "cancelled",
            "messages": [AIMessage(content="Research cancelled. Nothing was researched.")],
        }

    return {
        "intake": intake,
        "plan_generator": plan_generator,
        "plan_review": plan_review,
        "section_planner": section_planner,
        "section_researcher": section_researcher,
        "research_evaluator": research_evaluator,
        "enhanced_search_executor": enhanced_search_executor,
        "report_composer_with_citations": report_composer_with_citations,
        "citation_processor": citation_processor,
        "response_finalizer": response_finalizer,
        "finalize_cancelled": finalize_cancelled,
    }
