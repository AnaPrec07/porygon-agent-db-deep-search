"""Rebuild a research session from the object store alone (plan §F.8).

Uses the latest committed manifest and checks the SHA-256 of every listed file. Without a manifest (a crash
before commit), it falls back to every file that names the session and reports that the result is partial.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent.memory.ids import sha256_hex
from agent.memory.layout import RECORD_VERSION
from agent.memory.object_store import ObjectStore


class ReconstructError(RuntimeError):
    pass


_MANIFEST = re.compile(r"manifest-v(\d+)\.json$")


def _latest_manifest(store: ObjectStore, session_id: str) -> str | None:
    candidates = [
        p
        for p in store.list_files(f"records/{RECORD_VERSION}/session_manifest/")
        if f"/{session_id}/" in p and _MANIFEST.search(p)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: int(_MANIFEST.search(p).group(1)))


def reconstruct(store: ObjectStore, session_id: str) -> dict[str, Any]:
    manifest_path = _latest_manifest(store, session_id)
    if manifest_path:
        manifest = json.loads(store.read(manifest_path))
        entries = manifest["files"]
        complete = True
    else:
        manifest = None
        listed = [
            p
            for p in store.list_files(f"records/{RECORD_VERSION}/")
            if f"/{session_id}/" in p
        ] + [p for p in store.list_files(f"reports/{RECORD_VERSION}/") if f"/{session_id}/" in p]
        entries = [{"path": p, "record_type": p.split("/")[2] if p.startswith("records/") else "report"} for p in listed]
        complete = False

    buckets: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        data = store.read(entry["path"])
        if "sha256" in entry and sha256_hex(data) != entry["sha256"]:
            raise ReconstructError(f"checksum mismatch for {entry['path']}")
        buckets.setdefault(entry["record_type"], []).append({"path": entry["path"], "data": data})

    sessions = [json.loads(item["data"]) for item in buckets.get("research_session", [])]
    session = max(sessions, key=lambda r: r["session_version"]) if sessions else None

    traces: dict[str, dict[str, Any]] = {}
    for item in buckets.get("trace_event", []):
        for line in item["data"].splitlines():
            if line.strip():
                record = json.loads(line)
                traces[record["trace_id"]] = record
    timeline = sorted(traces.values(), key=lambda r: r["sequence"])

    sources: dict[str, dict[str, Any]] = {}
    for item in buckets.get("source", []):
        for line in item["data"].splitlines():
            if line.strip():
                record = json.loads(line)
                sources[record["source_id"]] = record

    facts: dict[str, dict[str, Any]] = {}
    for item in buckets.get("fact", []):
        for line in item["data"].splitlines():
            if line.strip():
                record = json.loads(line)
                facts[record["fact_id"]] = record

    responses = [json.loads(item["data"]) for item in buckets.get("final_response", [])]
    report_text: str | None = None
    verified_report = None
    if responses:
        latest = responses[-1]
        report_bytes = store.read(latest["report_path"])
        verified_report = sha256_hex(report_bytes) == latest["final_response_sha256"]
        report_text = report_bytes.decode("utf-8")

    return {
        "research_session_id": session_id,
        "complete": complete,
        "manifest_status": manifest["status"] if manifest else None,
        "session": session,
        "timeline": timeline,
        "sources": sorted(sources.values(), key=lambda r: r["short_id"]),
        "facts": sorted(facts.values(), key=lambda r: r["fact_id"]),
        "responses": responses,
        "report": report_text,
        "report_sha256_verified": verified_report,
        "files_checked": len(entries),
    }


def write_outputs(result: dict[str, Any], out_dir: str | Path) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "session.json").write_text(
        json.dumps({k: v for k, v in result.items() if k != "report"}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    lines = [f"# Timeline for {result['research_session_id']}", ""]
    for event in result["timeline"]:
        detail = event.get("action") or event.get("decision_summary") or event.get("error") or ""
        lines.append(f"- {event['sequence']} {event['agent_name']} {event['event_type']} {detail}".rstrip())
    (out / "timeline.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if result["report"] is not None:
        (out / "report.md").write_text(result["report"], encoding="utf-8")
