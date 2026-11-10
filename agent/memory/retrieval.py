"""Retrieval over the AI Search indexes, scoped to the caller (plan §G.5).

Every query is hybrid and filtered on ``access_scope`` IN the caller's two values. Queries run in parallel. A
failed query is recorded in the brief and does not stop research (plan §K.7).
ASSUMPTION A6 (plan §L.2, verify in PR-1 check A6): list values in dict filters mean IN on standard endpoints.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from agent.memory.ranking import Policy, combined_score, dedupe, diversify, eligibility, is_stale
from agent.memory.scope import Scope

logger = logging.getLogger(__name__)

FACT_COLUMNS = [
    "canonical_fact_key", "fact_text", "source_url", "source_title", "research_topic", "evidence_quality",
    "time_sensitivity", "status", "fact_kind", "last_verified_at", "latest_fact_id", "supporting_quote",
]
RESPONSE_COLUMNS = ["chunk_id", "response_id", "chunk_text", "research_topic", "generated_at", "is_latest_version"]
ACTIVITY_COLUMNS = ["activity_id", "research_session_id", "agent_name", "summary_text", "research_topic", "status"]
MAX_QUERIES = 6


class IndexClient(Protocol):
    def query(
        self, index: str, columns: list[str], query_text: str, filters: dict[str, Any], num_results: int
    ) -> list[dict[str, Any]]: ...


class SdkIndexClient:
    """Calls ``query_index`` through the Databricks SDK with the app's identity (no client secret)."""

    def __init__(self, workspace: Any) -> None:
        self._w = workspace

    def query(
        self, index: str, columns: list[str], query_text: str, filters: dict[str, Any], num_results: int
    ) -> list[dict[str, Any]]:
        response = self._w.vector_search_indexes.query_index(
            index_name=index,
            columns=columns,
            query_text=query_text,
            query_type="HYBRID",
            filters_json=json.dumps(filters),
            num_results=num_results,
        )
        names = [c.name for c in (response.manifest.columns or [])]
        rows = response.result.data_array or [] if response.result else []
        out = []
        for values in rows:
            row = dict(zip(names, values))
            row["score"] = float(row.get("score") or 0.0)
            out.append(row)
        return out


class MemoryRetriever:
    def __init__(
        self,
        client: IndexClient,
        indexes: dict[str, str],
        scope: Scope,
        policy: Policy | None = None,
        *,
        now: datetime | None = None,
        workers: int = 4,
    ) -> None:
        self._client = client
        self._indexes = indexes  # keys: facts, responses, activity
        self._scope = scope
        self._policy = policy or Policy()
        self._now = now
        self._workers = workers

    def _clock(self) -> datetime:
        return self._now or datetime.now(UTC)

    def retrieve(self, topic: str, queries: list[str] | None = None) -> dict[str, Any]:
        texts = [topic, *(queries or [])]
        seen: set[str] = set()
        unique = []
        for text in texts:
            key = text.strip().casefold()
            if key and key not in seen:
                seen.add(key)
                unique.append(text.strip())
        unique = unique[:MAX_QUERIES]
        scope_filter = {"access_scope": self._scope.filter_values()}
        jobs = []
        for text in unique:
            jobs.append(("facts", self._indexes["facts"], FACT_COLUMNS,
                         {**scope_filter, "status": "active", "fact_kind": "source_fact"}, text))
            jobs.append(("responses", self._indexes["responses"], RESPONSE_COLUMNS,
                         {**scope_filter, "is_latest_version": True}, text))
            jobs.append(("activity", self._indexes["activity"], ACTIVITY_COLUMNS, scope_filter, text))

        def run(job):
            kind, index, columns, filters, text = job
            try:
                return kind, self._client.query(index, columns, text, filters, self._policy.top_k * 2), None
            except Exception as error:  # noqa: BLE001 - one failed query must not stop research
                return kind, [], f"{kind}: {type(error).__name__}: {error}"

        results: dict[str, list[dict[str, Any]]] = {"facts": [], "responses": [], "activity": []}
        errors: list[dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            for kind, rows, error in pool.map(run, jobs):
                results[kind].extend(rows)
                if error:
                    errors.append({"stage": "memory", "message": error})
                    logger.warning("memory query failed: %s", error)

        now = self._clock()
        eligible: list[dict[str, Any]] = []
        stale_ids: list[str] = []
        for row in results["facts"]:
            reason = eligibility(row, now, self._policy)
            if reason == "stale":
                stale_ids.append(row["latest_fact_id"])
            if reason is None:
                eligible.append({**row, "canonical_fact_key": row["canonical_fact_key"],
                                 "score": combined_score(row, row["score"], now)})
        facts = diversify(dedupe(eligible), self._policy.max_results_per_domain)[: self._policy.top_k]

        return {
            "scope": self._scope.access_scope,
            "facts": [
                {
                    "fact_text": f["fact_text"],
                    "source_title": f.get("source_title", ""),
                    "source_url": f.get("source_url", ""),
                    "last_verified_at": f.get("last_verified_at"),
                    "evidence_quality": f.get("evidence_quality"),
                    "canonical_fact_key": f["canonical_fact_key"],
                    "latest_fact_id": f.get("latest_fact_id"),
                    "score": round(f["score"], 4),
                }
                for f in facts
            ],
            "prior_responses": _unique_rows(results["responses"], "chunk_id", 5),
            "prior_activity": _unique_rows(results["activity"], "activity_id", 5),
            "stale_fact_ids": sorted(set(stale_ids)),
            "errors": errors,
        }


def _unique_rows(rows: list[dict[str, Any]], key: str, limit: int) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: r.get("score", 0.0), reverse=True):
        best.setdefault(row[key], row)
    return list(best.values())[:limit]
