"""Graph topology and routing (plan §C.4, §J.0)."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent.deep_search.deps import Deps
from agent.deep_search.nodes import make_nodes
from agent.deep_search.state import DeepSearchState


def route_after_plan_review(state: DeepSearchState) -> str:
    phase = state.get("phase")
    if phase == "researching":
        return "section_planner"
    if phase == "planning":
        return "plan_generator"
    return "finalize_cancelled"


def route_after_evaluation(state: DeepSearchState, max_iterations: int) -> str:
    evaluation = state.get("research_evaluation") or {}
    if evaluation.get("grade") == "pass":
        return "report_composer_with_citations"
    if state.get("iteration", 0) >= max_iterations:
        return "report_composer_with_citations"
    return "enhanced_search_executor"


def build_graph(deps: Deps) -> StateGraph:
    """Return the uncompiled graph. Callers compile it with a checkpointer."""
    builder = StateGraph(DeepSearchState)
    for name, node in make_nodes(deps).items():
        builder.add_node(name, node)

    max_iterations = deps.config.max_search_iterations

    builder.add_edge(START, "intake")
    builder.add_edge("intake", "plan_generator")
    builder.add_edge("plan_generator", "plan_review")
    builder.add_conditional_edges(
        "plan_review",
        route_after_plan_review,
        {
            "section_planner": "section_planner",
            "plan_generator": "plan_generator",
            "finalize_cancelled": "finalize_cancelled",
        },
    )
    builder.add_edge("section_planner", "section_researcher")
    builder.add_edge("section_researcher", "research_evaluator")
    builder.add_conditional_edges(
        "research_evaluator",
        lambda state: route_after_evaluation(state, max_iterations),
        {
            "enhanced_search_executor": "enhanced_search_executor",
            "report_composer_with_citations": "report_composer_with_citations",
        },
    )
    builder.add_edge("enhanced_search_executor", "research_evaluator")
    builder.add_edge("report_composer_with_citations", "citation_processor")
    builder.add_edge("citation_processor", "response_finalizer")
    builder.add_edge("response_finalizer", END)
    builder.add_edge("finalize_cancelled", END)
    return builder
