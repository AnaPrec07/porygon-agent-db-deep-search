"""Injectable dependencies for graph nodes.

PR-3 provides deterministic stubs so the graph can run and be tested without a model or a search provider.
PR-4 replaces the planning, outline, research, evaluation, refinement and composition callables with LLM calls.
Every callable receives a snapshot of graph state (a plain dict) and returns plain values.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.deep_search.config import DeepSearchConfig

StateView = dict[str, Any]


def _stub_plan(state: StateView) -> str:
    topic = state.get("research_topic", "")
    lines = [
        f"* [RESEARCH] Analyze the key aspects of {topic}",
        f"* [RESEARCH] Identify the main sources of information on {topic}",
        f"* [RESEARCH] Investigate recent developments in {topic}",
        f"* [RESEARCH] Assess the implications of {topic}",
        f"* [RESEARCH] Compare the main viewpoints on {topic}",
        f"* [DELIVERABLE][IMPLIED] Create a summary report on {topic}",
    ]
    feedback = (state.get("plan_feedback") or "").strip()
    if feedback:
        lines.insert(5, f"* [RESEARCH][NEW] {feedback}")
    return "\n".join(lines)


def _stub_outline(state: StateView) -> str:
    topic = state.get("research_topic", "")
    return (
        f"# Background\nWhat {topic} is and why it matters.\n\n"
        f"# Key findings\nThe main results for {topic}.\n\n"
        f"# Implications\nWhat the findings mean in practice.\n\n"
        f"# Open questions\nWhat remains unresolved about {topic}."
    )


def _stub_research(state: StateView) -> str:
    return f"Stub findings for: {state.get('research_topic', '')}."


def _stub_evaluate(state: StateView) -> dict[str, Any]:
    return {"grade": "pass", "comment": "Stub evaluator accepts the findings.", "follow_up_queries": None}


def _stub_refine(state: StateView) -> str:
    # Merge, never replace: earlier findings stay in place (plan requirement P8).
    return f"{state.get('section_research_findings', '')}\n\nStub refinement pass."


def _stub_compose(state: StateView) -> str:
    return f"## Report\n\nStub report for {state.get('research_topic', '')}.\n"


@dataclass
class Deps:
    config: DeepSearchConfig
    plan: Callable[[StateView], str]
    outline: Callable[[StateView], str]
    research: Callable[[StateView], str]
    evaluate: Callable[[StateView], dict[str, Any]]
    refine: Callable[[StateView], str]
    compose: Callable[[StateView], str]

    @classmethod
    def stub(cls, config: DeepSearchConfig | None = None) -> Deps:
        return cls(
            config=config or DeepSearchConfig.from_env(),
            plan=_stub_plan,
            outline=_stub_outline,
            research=_stub_research,
            evaluate=_stub_evaluate,
            refine=_stub_refine,
            compose=_stub_compose,
        )
