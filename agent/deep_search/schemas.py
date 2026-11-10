"""Structured-output models and renderers.

``Feedback`` and ``SearchQuery`` are ported from the ADK recipe (field names unchanged). ``ResearchPlan`` and
``ReportOutline`` are new: they let the model return structured data, and the renderers reproduce the tagged
markdown the recipe's UI and prompts expect.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


TaskType = Literal["RESEARCH", "DELIVERABLE"]
PlanTag = Literal["MODIFIED", "NEW", "IMPLIED"]


class PlanGoal(BaseModel):
    task_type: TaskType
    tags: list[PlanTag] = Field(default_factory=list)
    text: str = Field(min_length=3, description="Action-oriented goal, starting with a verb.")

    def render(self) -> str:
        tags = "".join(f"[{tag}]" for tag in self.tags)
        return f"* [{self.task_type}]{tags} {self.text.strip()}"


class ResearchPlan(BaseModel):
    goals: list[PlanGoal] = Field(min_length=1)

    @model_validator(mode="after")
    def _requires_research(self) -> ResearchPlan:
        if not any(goal.task_type == "RESEARCH" for goal in self.goals):
            raise ValueError("a plan needs at least one RESEARCH goal")
        return self

    def render(self) -> str:
        return "\n".join(goal.render() for goal in self.goals)


class OutlineSection(BaseModel):
    title: str = Field(min_length=2)
    overview: str = Field(min_length=3, description="What this section covers, in one or two sentences.")


class ReportOutline(BaseModel):
    sections: list[OutlineSection] = Field(min_length=4, max_length=6)

    def render(self) -> str:
        return "\n\n".join(f"# {s.title.strip()}\n{s.overview.strip()}" for s in self.sections)
