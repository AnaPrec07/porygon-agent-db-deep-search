"""Templated activity summaries for the activity index (plan §E.5). Deterministic, so re-runs produce the same text."""

from __future__ import annotations


def summarize_activity(
    agent_name: str,
    topic: str,
    queries: list[str],
    source_domains: list[str],
    fact_count: int,
    high_quality_count: int,
    status: str,
    critic_comment: str | None = None,
) -> str:
    domains = ", ".join(sorted(set(d for d in source_domains if d))[:10]) or "none"
    query_text = "; ".join(q for q in queries[:10] if q) or "no queries"
    parts = [
        f"{agent_name} ran {len(queries)} queries on '{topic}' ({query_text}).",
        f"Sources: {domains}.",
        f"Facts: {fact_count} ({high_quality_count} high quality).",
        f"Status: {status}.",
    ]
    if critic_comment:
        parts.append(f"Critic: {critic_comment.strip()[:300]}")
    return " ".join(parts)
