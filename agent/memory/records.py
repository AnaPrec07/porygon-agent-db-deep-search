"""Persisted record models (plan §E). ``extra='forbid'`` keeps writers and readers in step."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

SessionStatus = Literal[
    "planning", "awaiting_approval", "researching", "composing", "completed", "cancelled", "failed"
]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = SCHEMA_VERSION


class ResearchSessionRecord(_Record):
    record_type: Literal["research_session"] = "research_session"
    research_session_id: str
    session_version: int
    conversation_session_id: str | None = None
    research_topic: str = ""
    user_query: str = ""
    research_plan: str = ""
    status: SessionStatus
    iterations_completed: int = 0
    final_grade: str | None = None
    updated_at: str
    error: str | None = None


class TraceEventRecord(_Record):
    record_type: Literal["trace_event"] = "trace_event"
    trace_id: str
    research_session_id: str
    sequence: int
    agent_name: str
    event_type: str
    timestamp: str
    status: Literal["ok", "error"] = "ok"
    action: str | None = None
    decision_summary: str | None = Field(default=None, max_length=1000)
    created_fact_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class SourceRecord(_Record):
    record_type: Literal["source"] = "source"
    source_id: str
    short_id: str
    research_session_id: str
    url: str
    canonical_url: str
    domain: str
    title: str
    published_at: str | None = None
    provider: str = "web"
    queries: list[str] = Field(default_factory=list)
    content_sha256: str | None = None


class FactRecord(_Record):
    record_type: Literal["fact"] = "fact"
    fact_id: str
    canonical_fact_key: str
    fact_version: int = 1
    status: Literal["active", "superseded", "retracted", "disputed"] = "active"
    research_session_id: str
    research_topic: str = ""
    goal_id: str
    fact_kind: Literal["source_fact", "interpretation", "hypothesis"] = "source_fact"
    fact_text: str
    source_id: str
    source_url: str
    source_title: str
    quote: str
    quote_match: str
    time_sensitivity: Literal["static", "slow", "volatile"] = "static"
    evidence_quality: Literal["high", "medium", "low"] = "medium"


class CitedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    short_id: str
    source_id: str
    url: str
    title: str


class FinalResponseRecord(_Record):
    record_type: Literal["final_response"] = "final_response"
    response_id: str
    response_version: int = 1
    research_session_id: str
    user_query: str = ""
    research_topic: str = ""
    research_plan: str = ""
    final_response: str
    final_response_sha256: str
    report_path: str
    cited_sources: list[CitedSource] = Field(default_factory=list)
    supporting_fact_ids: list[str] = Field(default_factory=list)
    generated_at: str
    citation_stats: dict[str, int] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)


class ManifestFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    record_type: str
    records: int
    sha256: str
    bytes: int


class SessionManifest(_Record):
    record_type: Literal["session_manifest"] = "session_manifest"
    research_session_id: str
    manifest_version: int
    status: SessionStatus
    committed_at: str
    files: list[ManifestFile] = Field(default_factory=list)
    response_ids: list[str] = Field(default_factory=list)
