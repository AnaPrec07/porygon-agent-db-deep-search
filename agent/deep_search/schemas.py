"""Structured-output models, ported from the ADK recipe's app/agent.py (field names unchanged)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    """A specific search query for web search."""

    search_query: str = Field(description="A highly specific and targeted query for web search.")


class Feedback(BaseModel):
    """Evaluation feedback on research quality."""

    grade: Literal["pass", "fail"] = Field(
        description="Evaluation result. 'pass' if the research is sufficient, 'fail' if it needs revision."
    )
    comment: str = Field(
        description="Detailed explanation of the evaluation, highlighting strengths and/or weaknesses of the research."
    )
    follow_up_queries: list[SearchQuery] | None = Field(
        default=None,
        description="Specific follow-up search queries that fix research gaps. Null or empty if the grade is 'pass'.",
    )
