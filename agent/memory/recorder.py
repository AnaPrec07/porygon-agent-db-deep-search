"""Node instrumentation: after each node, write its trace events, new sources and facts, and at session
transitions the session record, final response and manifest.

Writes happen after the node returns, before the graph checkpoints the step. Each node run writes into files
named from (session, node, sequence), so replaying the same step after a crash produces the same paths.
Interrupts (``GraphBubbleUp``) pass through untouched. Failures are written as error events and re-raised.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langgraph.errors import GraphBubbleUp

from agent.deep_search.citations import CITE_PATTERN
from agent.memory.ids import sha256_hex, stable_id
from agent.memory.layout import record_path, report_path
from agent.memory.records import (
    CitedSource,
    FactRecord,
    FinalResponseRecord,
    ManifestFile,
    ResearchSessionRecord,
    SessionManifest,
    SourceRecord,
    TraceEventRecord,
)
from agent.memory.redaction import redact_text
from agent.memory.writer import PersistenceError, PersistenceWriter

logger = logging.getLogger(__name__)

PHASE_TO_STATUS = {
    "planning": "planning",
    "awaiting_approval": "awaiting_approval",
    "researching": "researching",
    "composing": "composing",
    "done": "completed",
    "cancelled": "cancelled",
    "failed": "failed",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _day() -> str:
    return datetime.now(UTC).date().isoformat()


class Persistence:
    def __init__(self, writer: PersistenceWriter, *, strict: bool = True) -> None:
        self._writer = writer
        self._strict = strict

    def wrap_nodes(self, nodes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]]) -> dict[str, Any]:
        return {name: self._instrument(name, fn) for name, fn in nodes.items()}

    def _instrument(self, name: str, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable[..., Any]:
        def node(state: dict[str, Any]) -> dict[str, Any]:
            seq = int(state.get("sequence", 0))
            try:
                update = fn(state)
            except GraphBubbleUp:
                raise
            except Exception as error:
                self._best_effort_failure(state, name, seq, error)
                raise
            try:
                extra = self._record(name, state, update, seq)
            except PersistenceError as error:
                if self._strict:
                    raise
                logger.error("persistence failed after %s: %s", name, error)
                extra = {"errors": [{"stage": "persistence", "message": redact_text(str(error))}]}
            return _merge(update, extra)

        node.__name__ = name
        return node

    # ----- record construction -----

    def _record(self, name: str, state: dict[str, Any], update: dict[str, Any], seq: int) -> dict[str, Any]:
        merged = {**state, **update}
        session_id = merged.get("research_session_id")
        if not session_id:
            return {"sequence": seq + 1}
        day = merged.get("session_day") or _day()
        now = _now()
        written: list[dict[str, Any]] = []
        node_run = stable_id(session_id, name, str(seq))

        trace = self._trace_events(name, state, update, session_id, seq, now)
        if trace:
            written.append(
                self._writer.write_jsonl(
                    record_path("trace_event", day, session_id, f"{node_run}.jsonl"), "trace_event", trace
                ).as_dict()
            )

        sources = self._new_sources(state, merged, session_id)
        if sources:
            written.append(
                self._writer.write_jsonl(
                    record_path("source", day, session_id, f"{node_run}.jsonl"), "source", sources
                ).as_dict()
            )

        facts = self._new_facts(state, merged, session_id)
        if facts:
            written.append(
                self._writer.write_jsonl(
                    record_path("fact", day, session_id, f"{node_run}.jsonl"), "fact", facts
                ).as_dict()
            )

        phase = update.get("phase")
        if phase is not None or "research_session_id" in update:
            written.append(self._session_record(merged, session_id, day, seq, now, phase))

        response_ids: list[str] = []
        if phase == "done" and merged.get("final_report_with_citations"):
            response, files = self._final_response(merged, session_id, day, now)
            written.extend(files)
            response_ids.append(response.response_id)

        if phase in ("done", "cancelled"):
            prior = list(merged.get("persisted_files") or [])
            manifest = SessionManifest(
                research_session_id=session_id,
                manifest_version=seq + 1,
                status=PHASE_TO_STATUS[phase],
                committed_at=now,
                files=[
                    ManifestFile(
                        path=item["path"],
                        record_type=item["record_type"],
                        records=item["records"],
                        sha256=item["sha256"],
                        bytes=item["bytes"],
                    )
                    for item in [*prior, *written]
                ],
                response_ids=list(merged.get("response_ids") or []) + response_ids,
            )
            self._writer.write_json(
                record_path("session_manifest", day, session_id, f"manifest-v{seq + 1:06d}.json"),
                "session_manifest",
                manifest,
            )

        extra: dict[str, Any] = {"sequence": seq + 1}
        if "session_day" not in merged or merged.get("session_day") != day:
            extra["session_day"] = day
        if written:
            extra["persisted_files"] = written
        if response_ids:
            extra["response_ids"] = response_ids
        return extra

    def _trace_events(
        self, name: str, state: dict[str, Any], update: dict[str, Any], session_id: str, seq: int, now: str
    ) -> list[TraceEventRecord]:
        events: list[dict[str, Any]] = [{"event_type": "node_completed", "action": f"{name} completed"}]
        if name == "intake" and update.get("research_topic"):
            events.append({"event_type": "session_started", "action": redact_text(update["research_topic"], 200)})
        if update.get("research_plan"):
            events.append({"event_type": "plan_generated", "action": f"revision {update.get('plan_revisions', 0)}"})
        if name == "plan_review" and update.get("phase") in ("researching", "cancelled"):
            events.append({"event_type": "plan_decision", "action": update["phase"]})
        if update.get("research_evaluation"):
            evaluation = update["research_evaluation"]
            events.append(
                {
                    "event_type": "evaluation",
                    "action": evaluation.get("grade"),
                    "decision_summary": redact_text(evaluation.get("comment"), 1000),
                }
            )
        if update.get("final_cited_report"):
            events.append({"event_type": "report_composed"})
        if update.get("citation_stats"):
            events.append({"event_type": "citations_validated", "action": str(update["citation_stats"])})
        for error in update.get("errors") or []:
            events.append(
                {"event_type": "error", "status": "error", "error": redact_text(str(error.get("message", "")))}
            )
        records: list[TraceEventRecord] = []
        for index, event in enumerate(events):
            records.append(
                TraceEventRecord(
                    trace_id=stable_id(session_id, name, str(seq), str(index)),
                    research_session_id=session_id,
                    sequence=seq * 1000 + index,
                    agent_name=name,
                    event_type=event["event_type"],
                    timestamp=now,
                    status=event.get("status", "ok"),
                    action=redact_text(event.get("action"), 200),
                    decision_summary=event.get("decision_summary"),
                    error=event.get("error"),
                )
            )
        return records

    def _new_sources(self, state: dict[str, Any], merged: dict[str, Any], session_id: str) -> list[SourceRecord]:
        old = set((state.get("sources") or {}).keys())
        records: list[SourceRecord] = []
        for short_id, info in (merged.get("sources") or {}).items():
            if short_id in old:
                continue
            records.append(
                SourceRecord(
                    source_id=stable_id("source", info["canonical_url"]),
                    short_id=short_id,
                    research_session_id=session_id,
                    url=info["url"],
                    canonical_url=info["canonical_url"],
                    domain=info.get("domain", ""),
                    title=info.get("title", "") or info.get("domain", ""),
                    published_at=info.get("published_at"),
                    queries=list(info.get("queries", [])),
                )
            )
        return records

    def _new_facts(self, state: dict[str, Any], merged: dict[str, Any], session_id: str) -> list[FactRecord]:
        old = {f["fact_id"] for f in (state.get("facts") or [])}
        records: list[FactRecord] = []
        for fact in merged.get("facts") or []:
            if fact["fact_id"] in old:
                continue
            records.append(
                FactRecord(
                    fact_id=fact["fact_id"],
                    canonical_fact_key=fact["canonical_fact_key"],
                    status=fact.get("status", "active"),
                    research_session_id=session_id,
                    research_topic=merged.get("research_topic", ""),
                    goal_id=fact["goal_id"],
                    fact_kind=fact.get("fact_kind", "source_fact"),
                    fact_text=fact["fact_text"],
                    source_id=fact["source_id"],
                    source_url=fact["source_url"],
                    source_title=fact.get("source_title", ""),
                    quote=fact["quote"],
                    quote_match=fact["quote_match"],
                    time_sensitivity=fact.get("time_sensitivity", "static"),
                    evidence_quality=fact.get("evidence_quality", "medium"),
                )
            )
        return records

    def _session_record(
        self, merged: dict[str, Any], session_id: str, day: str, seq: int, now: str, phase: str | None
    ) -> dict[str, Any]:
        phase_value = phase or merged.get("phase", "planning")
        record = ResearchSessionRecord(
            research_session_id=session_id,
            session_version=seq + 1,
            research_topic=merged.get("research_topic", ""),
            user_query=merged.get("research_topic", ""),
            research_plan=merged.get("research_plan", ""),
            status=PHASE_TO_STATUS.get(phase_value, "planning"),
            iterations_completed=int(merged.get("iteration", 0) or 0),
            final_grade=(merged.get("research_evaluation") or {}).get("grade"),
            updated_at=now,
        )
        return self._writer.write_json(
            record_path("research_session", day, session_id, f"session-v{seq + 1:06d}.json"),
            "research_session",
            record,
        ).as_dict()

    def _final_response(
        self, merged: dict[str, Any], session_id: str, day: str, now: str
    ) -> tuple[FinalResponseRecord, list[dict[str, Any]]]:
        text = str(merged["final_report_with_citations"])
        response_id = stable_id(session_id, "response")
        sources = merged.get("sources") or {}
        cited_ids: list[str] = []
        for match in CITE_PATTERN.finditer(str(merged.get("final_cited_report", ""))):
            short_id = match.group(1)
            if short_id in sources and short_id not in cited_ids:
                cited_ids.append(short_id)
        cited = [
            CitedSource(
                short_id=sid,
                source_id=stable_id("source", sources[sid]["canonical_url"]),
                url=sources[sid]["url"],
                title=sources[sid].get("title", ""),
            )
            for sid in cited_ids
        ]
        supporting = [
            f["fact_id"]
            for f in merged.get("facts") or []
            if f.get("status") == "active" and f.get("source_id") in cited_ids
        ]
        data = text.encode("utf-8")
        report = self._writer.write_bytes(report_path(day, session_id, response_id, 1), "report", data)
        response = FinalResponseRecord(
            response_id=response_id,
            research_session_id=session_id,
            user_query=merged.get("research_topic", ""),
            research_topic=merged.get("research_topic", ""),
            research_plan=merged.get("research_plan", ""),
            final_response=text,
            final_response_sha256=sha256_hex(data),
            report_path=report.path,
            cited_sources=cited,
            supporting_fact_ids=supporting,
            generated_at=now,
            citation_stats=dict(merged.get("citation_stats") or {}),
        )
        response_file = self._writer.write_json(
            record_path("final_response", day, session_id, f"{response_id}-v0001.json"),
            "final_response",
            response,
        )
        return response, [report.as_dict(), response_file.as_dict()]

    # ----- failures -----

    def _best_effort_failure(self, state: dict[str, Any], name: str, seq: int, error: Exception) -> None:
        session_id = state.get("research_session_id")
        if not session_id:
            return
        try:
            day = state.get("session_day") or _day()
            now = _now()
            event = TraceEventRecord(
                trace_id=stable_id(session_id, name, str(seq), "error"),
                research_session_id=session_id,
                sequence=seq * 1000 + 999,
                agent_name=name,
                event_type="error",
                timestamp=now,
                status="error",
                error=redact_text(f"{type(error).__name__}: {error}"),
            )
            self._writer.write_jsonl(
                record_path("trace_event", day, session_id, f"{stable_id(session_id, name, str(seq))}-error.jsonl"),
                "trace_event",
                [event],
            )
            self._session_record({**state, "phase": "failed"}, session_id, day, seq, now, "failed")
        except Exception as write_error:  # noqa: BLE001 - never mask the original failure
            logger.error("could not record failure for %s: %s", name, write_error)


def _merge(update: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = {**update, **extra}
    if "errors" in update and "errors" in extra:
        merged["errors"] = [*update["errors"], *extra["errors"]]
    return merged
