"""Object paths relative to the store root (plan §F.2). Paths are deterministic for a given session and step."""

from __future__ import annotations

RECORD_VERSION = "v1"


def record_path(record_type: str, day: str, session_id: str, filename: str) -> str:
    return f"records/{RECORD_VERSION}/{record_type}/dt={day}/{session_id}/{filename}"


def report_path(day: str, session_id: str, response_id: str, version: int) -> str:
    return f"reports/{RECORD_VERSION}/dt={day}/{session_id}/report-{response_id}-v{version:04d}.md"


def session_prefix(session_id: str) -> str:
    return f"records/{RECORD_VERSION}/"
