"""Graph state. Values are JSON-serializable so the Session Store checkpointer can persist them."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class DeepSearchState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    research_session_id: str
    research_topic: str
    phase: str  # planning | awaiting_approval | researching | composing | done | cancelled
    research_plan: str
    plan_feedback: str
    plan_revisions: int
    report_sections: str
    section_research_findings: str
    research_evaluation: dict[str, Any]
    iteration: int
    sources: dict[str, dict[str, Any]]
    url_to_short_id: dict[str, str]
    final_cited_report: str
    final_report_with_citations: str
    citation_stats: dict[str, int]
    errors: Annotated[list[dict[str, Any]], operator.add]
