"""Research pipeline (PR-5): plan queries, search, extract and verify facts, then synthesize findings.

Findings are built deterministically from verified facts, so every line of the findings can be traced to a
source id. The model writes only the DELIVERABLE goals, and it does so from those findings, never from memory.
Refinement reuses the same path with the evaluator's follow-up queries and merges the results: facts are never
dropped (plan P8).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from agent.deep_search.config import DeepSearchConfig
from agent.deep_search.llm import StructuredOutputError, structured, text_of
from agent.deep_search.prompts import DELIVERABLE_SYSTEM, EXTRACTION_SYSTEM, QUERY_SYSTEM
from agent.deep_search.research.evidence import verify_quote
from agent.deep_search.research.providers import SearchError, SearchProvider
from agent.deep_search.research.sources import canonical_url, content_key, register_hits
from agent.deep_search.schemas import ExtractedFacts, QueryPlan

PLAN_LINE = re.compile(r"^\*\s*\[(RESEARCH|DELIVERABLE)\](?:\[[A-Z]+\])*\s+(.+)$")
GENERAL_GOAL = "general"
MAX_FACTS_PER_PAGE = 12


@dataclass(frozen=True)
class PlanGoalRef:
    id: str
    task_type: str  # RESEARCH | DELIVERABLE
    text: str


@dataclass
class ResearchServices:
    search: SearchProvider
    query_llm: Any
    extract_llm: Any
    deliver_llm: Any
    config: DeepSearchConfig


def parse_plan_goals(plan: str) -> list[PlanGoalRef]:
    """Read goal ids from the rendered plan: g1, g2, ... for RESEARCH and d1, d2, ... for DELIVERABLE."""
    goals: list[PlanGoalRef] = []
    counts = {"RESEARCH": 0, "DELIVERABLE": 0}
    prefix = {"RESEARCH": "g", "DELIVERABLE": "d"}
    for line in plan.splitlines():
        match = PLAN_LINE.match(line.strip())
        if not match:
            continue
        task_type = match.group(1)
        counts[task_type] += 1
        goals.append(PlanGoalRef(f"{prefix[task_type]}{counts[task_type]}", task_type, match.group(2).strip()))
    return goals


def plan_queries(llm: Any, topic: str, goals: list[PlanGoalRef], per_goal: int) -> list[tuple[str, str]]:
    """Ask the model for queries per research goal. Returns (goal_id, query) pairs, deduplicated."""
    if not goals:
        return []
    listing = "\n".join(f"- {g.id}: {g.text}" for g in goals)
    result = structured(
        llm,
        QueryPlan,
        QUERY_SYSTEM.format(n=per_goal),
        f"Research topic: {topic}\n\nResearch goals:\n{listing}",
    )
    known = {g.id for g in goals}
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in result.goals:
        if entry.goal_id not in known:
            continue
        for query in entry.queries[:per_goal]:
            text = query.search_query.strip()
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                pairs.append((entry.goal_id, text))
    return pairs


def render_findings(goals: list[PlanGoalRef], facts: list[dict[str, Any]]) -> str:
    """Findings grouped by research goal. Each fact carries the cite tag for its source."""
    active = [f for f in facts if f.get("status") == "active"]
    lines: list[str] = []
    research = [g for g in goals if g.task_type == "RESEARCH"]
    for goal in research:
        lines.append(f"## {goal.text}")
        goal_facts = [f for f in active if f["goal_id"] == goal.id]
        if not goal_facts:
            lines.append("No verified facts were found for this goal.")
        for fact in goal_facts:
            lines.append(f'- {fact["fact_text"]} <cite source="{fact["source_id"]}" />')
        lines.append("")
    known = {g.id for g in research}
    extra = [f for f in active if f["goal_id"] not in known]
    if extra:
        lines.append("## Additional findings")
        for fact in extra:
            lines.append(f'- {fact["fact_text"]} <cite source="{fact["source_id"]}" />')
        lines.append("")
    return "\n".join(lines).strip()


def build_deliverables(llm: Any, deliverables: list[PlanGoalRef], findings: str) -> str:
    sections: list[str] = []
    for goal in deliverables:
        response = llm.invoke(
            [
                ("system", DELIVERABLE_SYSTEM),
                ("human", f"Deliverable: {goal.text}\n\nResearch findings:\n{findings}"),
            ]
        )
        text = text_of(response)
        if text:
            sections.append(f"## {goal.text}\n\n{text}")
    return "\n\n".join(sections)


def _extract_facts(
    services: ResearchServices,
    topic: str,
    goals: list[PlanGoalRef],
    page_text: str,
) -> tuple[list[Any], str]:
    snippet = page_text[: services.config.max_source_chars]
    listing = "\n".join(f"- {g.id}: {g.text}" for g in goals if g.task_type == "RESEARCH") or "(none)"
    result = structured(
        services.extract_llm,
        ExtractedFacts,
        EXTRACTION_SYSTEM,
        f"Research topic: {topic}\n\nResearch goals:\n{listing}\n\nPage text:\n{snippet}",
    )
    return list(result.facts)[:MAX_FACTS_PER_PAGE], snippet



def _gather(
    state: dict[str, Any],
    queries: list[tuple[str, str]],
    goals: list[PlanGoalRef],
    services: ResearchServices,
) -> dict[str, Any]:
    cfg = services.config
    topic = state.get("research_topic", "")
    session_id = state.get("research_session_id") or str(uuid.uuid4())
    sources: dict[str, dict[str, Any]] = dict(state.get("sources") or {})
    url_to_id: dict[str, str] = dict(state.get("url_to_short_id") or {})
    facts: list[dict[str, Any]] = list(state.get("facts") or [])
    extracted: set[str] = set(state.get("extracted_urls") or [])
    budget = dict(state.get("budget") or {"searches": 0, "extracts": 0})
    stats = dict(state.get("research_stats") or {})
    errors: list[dict[str, Any]] = []
    research_ids = [g.id for g in goals if g.task_type == "RESEARCH"]
    known_goal_ids = set(research_ids)

    candidates: list[tuple[str, str]] = []  # (short_id, url) not yet extracted
    queued: set[str] = set()
    for _goal_id, query in queries:
        if budget["searches"] >= cfg.max_total_searches:
            errors.append({"stage": "search", "message": "search budget exhausted", "query": query})
            break
        budget["searches"] += 1
        try:
            hits = services.search.search(query, max_results=cfg.max_sources_per_query)
        except SearchError as error:
            errors.append({"stage": "search", "message": str(error), "query": query})
            continue
        for short_id, url in register_hits(sources, url_to_id, hits, query):
            key = canonical_url(url)
            if key not in extracted and key not in queued:
                queued.add(key)
                candidates.append((short_id, url))
    candidates = candidates[: cfg.max_extract_urls_per_pass]

    pages: dict[str, str] = {}
    if candidates:
        urls = [url for _, url in candidates]
        budget["extracts"] += len(urls)
        try:
            for page in services.search.extract(urls):
                pages[canonical_url(page.url)] = page.text
        except SearchError as error:
            errors.append({"stage": "extract", "message": str(error)})

    fact_keys = {f["canonical_fact_key"] for f in facts}
    dropped = int(stats.get("unverified_quotes_dropped", 0))
    for short_id, url in candidates:
        key = canonical_url(url)
        text = pages.get(key)
        if not text:
            errors.append({"stage": "extract", "message": "no page text returned", "url": url})
            continue
        extracted.add(key)
        try:
            extracted_facts, snippet = _extract_facts(services, topic, goals, text)
        except StructuredOutputError as error:
            errors.append({"stage": "extract", "message": str(error), "url": url})
            continue
        for item in extracted_facts:
            check = verify_quote(item.quote, snippet)
            if not check.verified:
                dropped += 1
                continue
            fact_key = content_key(item.fact_text, url)
            if fact_key in fact_keys:
                continue
            fact_keys.add(fact_key)
            goal_id = item.goal_id if item.goal_id in known_goal_ids else (research_ids[0] if research_ids else GENERAL_GOAL)
            facts.append(
                {
                    "fact_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{session_id}:{fact_key}")),
                    "canonical_fact_key": fact_key,
                    "goal_id": goal_id,
                    "fact_kind": "source_fact",
                    "fact_text": item.fact_text.strip(),
                    "quote": item.quote.strip(),
                    "quote_match": check.method,
                    "source_id": short_id,
                    "source_url": url,
                    "source_title": sources[short_id]["title"],
                    "time_sensitivity": item.time_sensitivity,
                    "evidence_quality": "high" if check.method in ("exact", "normalized") else "medium",
                    "status": "active",
                }
            )
    stats["unverified_quotes_dropped"] = dropped

    findings = render_findings(goals, facts)
    deliverable_goals = [g for g in goals if g.task_type == "DELIVERABLE"]
    deliverables = build_deliverables(services.deliver_llm, deliverable_goals, findings)
    combined = f"{findings}\n\n{deliverables}".strip() if deliverables else findings

    return {
        "section_research_findings": combined,
        "sources": sources,
        "url_to_short_id": url_to_id,
        "facts": facts,
        "extracted_urls": sorted(extracted),
        "budget": budget,
        "research_stats": stats,
        "errors": errors,
    }


def run_research(state: dict[str, Any], services: ResearchServices) -> dict[str, Any]:
    """First research pass: one query plan per research goal, then search, extraction and synthesis."""
    goals = parse_plan_goals(state.get("research_plan", ""))
    research_goals = [g for g in goals if g.task_type == "RESEARCH"]
    queries = plan_queries(services.query_llm, state.get("research_topic", ""), research_goals, services.config.queries_per_goal)
    return _gather(state, queries, goals, services)


def run_refine(state: dict[str, Any], services: ResearchServices) -> dict[str, Any]:
    """Refinement pass: run the evaluator's follow-up queries and merge the new facts into the existing set."""
    goals = parse_plan_goals(state.get("research_plan", ""))
    evaluation = state.get("research_evaluation") or {}
    follow_ups = [q["search_query"].strip() for q in (evaluation.get("follow_up_queries") or []) if q.get("search_query")]
    research_goals = [g for g in goals if g.task_type == "RESEARCH"]
    fallback_goal = research_goals[0].id if research_goals else GENERAL_GOAL
    queries = [(fallback_goal, q) for q in follow_ups[: services.config.queries_per_goal * 2]]
    return _gather(state, queries, goals, services)
