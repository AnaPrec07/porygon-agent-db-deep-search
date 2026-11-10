"""Run configuration for the deep-search graph, read from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class DeepSearchConfig:
    worker_model: str | None = None
    critic_model: str | None = None
    extraction_model: str | None = None
    max_search_iterations: int = 5
    queries_per_goal: int = 5
    max_sources_per_query: int = 5
    max_total_searches: int = 60
    max_extract_urls_per_pass: int = 40
    max_source_chars: int = 20_000
    search_provider: str | None = None  # tavily_mcp | tavily_http | None (research stays stubbed)
    tavily_mcp_url: str | None = None
    tavily_search_tool: str = "tavily_search"
    tavily_extract_tool: str = "tavily_extract"
    object_store: str | None = None  # local:<dir> | uc_volume | None (persistence disabled)
    records_volume: str | None = None  # /Volumes/<catalog>/<schema>/<volume> for uc_volume
    persistence_strict: bool = True
    tenant_id: str | None = None
    default_visibility: str = "private"  # private | tenant
    memory_enabled: bool = True
    facts_index: str = "deep_search.memory.facts_idx"
    responses_index: str = "deep_search.memory.responses_idx"
    activity_index: str = "deep_search.memory.activity_idx"
    refresh_job_id: int | None = None
    refresh_debounce_s: float = 120.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DeepSearchConfig:
        source = os.environ if env is None else env

        def integer(name: str, default: int, minimum: int = 1) -> int:
            raw = source.get(name, str(default))
            try:
                value = int(raw)
            except ValueError as error:
                raise ValueError(f"{name} must be an integer, got {raw!r}") from error
            if value < minimum:
                raise ValueError(f"{name} must be at least {minimum}")
            return value

        provider = (source.get("DEEP_SEARCH_SEARCH_PROVIDER") or "").strip() or None
        if provider not in (None, "tavily_mcp", "tavily_http"):
            raise ValueError("DEEP_SEARCH_SEARCH_PROVIDER must be tavily_mcp, tavily_http, or unset")
        return cls(
            worker_model=source.get("DEEP_SEARCH_WORKER_MODEL") or None,
            critic_model=source.get("DEEP_SEARCH_CRITIC_MODEL") or None,
            extraction_model=source.get("DEEP_SEARCH_EXTRACTION_MODEL") or None,
            max_search_iterations=integer("DEEP_SEARCH_MAX_SEARCH_ITERATIONS", 5),
            queries_per_goal=integer("DEEP_SEARCH_QUERIES_PER_GOAL", 5),
            max_sources_per_query=integer("DEEP_SEARCH_MAX_SOURCES_PER_QUERY", 5),
            max_total_searches=integer("DEEP_SEARCH_MAX_TOTAL_SEARCHES", 60),
            max_extract_urls_per_pass=integer("DEEP_SEARCH_MAX_EXTRACT_URLS_PER_PASS", 40),
            max_source_chars=integer("DEEP_SEARCH_MAX_SOURCE_CHARS", 20_000),
            search_provider=provider,
            tavily_mcp_url=source.get("DEEP_SEARCH_TAVILY_MCP_URL") or None,
            tavily_search_tool=source.get("DEEP_SEARCH_TAVILY_SEARCH_TOOL") or "tavily_search",
            tavily_extract_tool=source.get("DEEP_SEARCH_TAVILY_EXTRACT_TOOL") or "tavily_extract",
            object_store=(source.get("DEEP_SEARCH_OBJECT_STORE") or "").strip() or None,
            records_volume=source.get("DEEP_SEARCH_RECORDS_VOLUME") or None,
            persistence_strict=source.get("DEEP_SEARCH_PERSISTENCE_STRICT", "true").strip().lower() != "false",
            tenant_id=(source.get("DEEP_SEARCH_TENANT_ID") or "").strip() or None,
            default_visibility=(source.get("DEEP_SEARCH_DEFAULT_VISIBILITY") or "private").strip(),
            memory_enabled=source.get("DEEP_SEARCH_MEMORY_ENABLED", "true").strip().lower() != "false",
            facts_index=source.get("DEEP_SEARCH_AI_SEARCH_FACTS_INDEX") or "deep_search.memory.facts_idx",
            responses_index=source.get("DEEP_SEARCH_AI_SEARCH_RESPONSES_INDEX") or "deep_search.memory.responses_idx",
            activity_index=source.get("DEEP_SEARCH_AI_SEARCH_ACTIVITY_INDEX") or "deep_search.memory.activity_idx",
            refresh_job_id=int(source["DEEP_SEARCH_REFRESH_JOB_ID"]) if source.get("DEEP_SEARCH_REFRESH_JOB_ID") else None,
            refresh_debounce_s=float(source.get("DEEP_SEARCH_REFRESH_DEBOUNCE_S", "120")),
        )
