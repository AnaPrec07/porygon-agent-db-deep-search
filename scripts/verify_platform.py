"""Phase 0 platform verification for the Deep Search migration.

Runs the checks that resolve assumptions A1-A13 in docs/MIGRATION_PLAN.md (section L.2).
Each check reports PASS, FAIL, SKIP (prerequisite missing) or MANUAL (procedure in
docs/platform-verification.md; not automated here because it creates billable or destructive resources).

Usage:
    uv run python scripts/verify_platform.py [--json-out PATH]

Live checks need Databricks authentication (DATABRICKS_HOST plus a token or OAuth profile) and
the optional environment variables documented in docs/platform-verification.md.
Offline checks run without a workspace.
"""

from __future__ import annotations

import argparse
import dataclasses
import inspect
import json
import os
import statistics
import sys
import time
import uuid
from collections.abc import Callable
from io import BytesIO
from typing import Any

PASS, FAIL, SKIP, MANUAL = "PASS", "FAIL", "SKIP", "MANUAL"


@dataclasses.dataclass
class Result:
    id: str
    title: str
    status: str
    evidence: str


class Skip(Exception):
    """Raised when a check's prerequisite is missing; reported as SKIP, not FAIL."""


def _workspace_client() -> Any:
    """Return a WorkspaceClient, or raise Skip with the reason."""
    if not (os.getenv("DATABRICKS_HOST") or os.getenv("DATABRICKS_CONFIG_PROFILE")):
        raise Skip("no DATABRICKS_HOST or DATABRICKS_CONFIG_PROFILE set")
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


# ---------- offline checks ----------


def check_a7_reranker_fields() -> Result:
    from databricks.sdk.service import vectorsearch as vs

    fields = {f.name for f in dataclasses.fields(vs.RerankerConfig)}
    params = {f.name for f in dataclasses.fields(vs.RerankerConfigRerankerParameters)}
    expected = {"model", "parameters"} <= fields and "columns_to_rerank" in params
    evidence = f"RerankerConfig fields={sorted(fields)}; parameters fields={sorted(params)}"
    if expected:
        evidence += (
            ". Plan §G.5 showed RerankerConfig(columns_to_rerank=...); the real shape is "
            "RerankerConfig(model=..., parameters=RerankerConfigRerankerParameters(columns_to_rerank=...)). "
            "Correction recorded for PR-8."
        )
    return Result("A7", "Reranker config fields", PASS if expected else FAIL, evidence)


def check_sdk_query_index_signature() -> Result:
    from databricks.sdk.service.vectorsearch import VectorSearchIndexesAPI

    params = set(inspect.signature(VectorSearchIndexesAPI.query_index).parameters)
    needed = {"query_type", "filters_json", "reranker", "score_threshold", "query_text", "num_results"}
    missing = sorted(needed - params)
    return Result(
        "A6-sig",
        "query_index accepts HYBRID, filters_json, reranker, score_threshold",
        PASS if not missing else FAIL,
        f"missing={missing}" if missing else "all required parameters present",
    )


def check_mlflow_api() -> Result:
    import mlflow
    import mlflow.genai

    have = {
        "mlflow.get_active_trace_id": hasattr(mlflow, "get_active_trace_id"),
        "mlflow.genai.evaluate": hasattr(mlflow.genai, "evaluate"),
        "mlflow.genai.scorer": hasattr(mlflow.genai, "scorer"),
    }
    ok = all(have.values())
    return Result(
        "A9",
        "MLflow trace-id and GenAI evaluation APIs",
        PASS if ok else FAIL,
        f"mlflow {mlflow.__version__}: {have}",
    )


# ---------- live checks (need a workspace) ----------


def check_a1_model_services() -> Result:
    w = _workspace_client()  # raises Skip without credentials
    resp = w.api_client.do(
        "GET",
        "/api/2.1/unity-catalog/model-services",
        query={"parent": "schemas/system.ai"},
    )
    names = [s.get("full_name") or s.get("name") for s in resp.get("model_services", [])]
    chat = [n for n in names if n]
    return Result(
        "A1",
        "system.ai model services listed",
        PASS if chat else FAIL,
        f"{len(chat)} services; first: {chat[:5]}",
    )


def check_a1_structured_output() -> Result:
    model = os.getenv("DEEP_SEARCH_WORKER_MODEL")
    if not model:
        return Result("A1b", "Structured output on chosen worker model", SKIP, "DEEP_SEARCH_WORKER_MODEL not set")
    from databricks_langchain import ChatDatabricks
    from pydantic import BaseModel

    class Probe(BaseModel):
        answer: str
        confidence: float

    llm = ChatDatabricks(endpoint=model, use_ai_gateway=True, workspace_client=_workspace_client())
    out = llm.with_structured_output(Probe, method="json_schema").invoke(
        "Reply with answer='ok' and confidence=0.5."
    )
    ok = isinstance(out, Probe)
    return Result("A1b", "Structured output on chosen worker model", PASS if ok else FAIL, repr(out))


def check_a2_tavily_mcp_tools() -> Result:
    url = os.getenv("DEEP_SEARCH_TAVILY_MCP_URL")
    if not url:
        return Result("A2", "Tavily MCP tool names and schemas", SKIP, "DEEP_SEARCH_TAVILY_MCP_URL not set")
    from databricks_mcp import DatabricksMCPClient

    tools = DatabricksMCPClient(server_url=url, workspace_client=_workspace_client()).list_tools()
    summary = {t.name: sorted((t.inputSchema or {}).get("properties", {})) for t in tools}
    names = set(summary)
    ok = {"tavily_search", "tavily_extract"} <= names or any("search" in n for n in names)
    return Result(
        "A2",
        "Tavily MCP tool names and schemas",
        PASS if ok else FAIL,
        json.dumps(summary, sort_keys=True),
    )


def check_a3_files_conflict_and_latency() -> Result:
    volume = os.getenv("DEEP_SEARCH_VERIFY_VOLUME")  # e.g. /Volumes/deep_search/landing/scratch
    if not volume:
        return Result("A3", "Files API overwrite=False conflict and latency", SKIP, "DEEP_SEARCH_VERIFY_VOLUME not set")
    from databricks.sdk.errors import DatabricksError

    w = _workspace_client()
    path = f"{volume}/verify-{uuid.uuid4().hex}.bin"
    w.files.upload(path, BytesIO(b"first"), overwrite=False)
    conflict = None
    try:
        w.files.upload(path, BytesIO(b"second"), overwrite=False)
    except DatabricksError as e:
        conflict = f"{type(e).__name__} status={getattr(e, 'status_code', None)} error_code={getattr(e, 'error_code', None)}"
    finally:
        w.files.delete(path)
    timings = []
    payload = os.urandom(100 * 1024)
    for i in range(50):
        p = f"{volume}/latency-{uuid.uuid4().hex}.bin"
        t0 = time.perf_counter()
        w.files.upload(p, BytesIO(payload), overwrite=False)
        timings.append((time.perf_counter() - t0) * 1000)
        w.files.delete(p)
    timings.sort()
    p50 = statistics.median(timings)
    p95 = timings[int(0.95 * (len(timings) - 1))]
    status = PASS if conflict else FAIL
    return Result(
        "A3",
        "Files API overwrite=False conflict and latency",
        status,
        f"conflict: {conflict or 'NO ERROR RAISED'}; 50x100KB upload p50={p50:.0f}ms p95={p95:.0f}ms "
        "(measured from this machine, not from a Databricks App; repeat from the app in PR-6)",
    )


def check_a13_embedding_endpoint() -> Result:
    name = os.getenv("DEEP_SEARCH_EMBEDDING_ENDPOINT", "databricks-gte-large-en")
    w = _workspace_client()
    try:
        ep = w.serving_endpoints.get(name)
    except Exception as e:  # noqa: BLE001 - report any failure as evidence
        return Result("A13", "Embedding endpoint available", FAIL, f"{name}: {type(e).__name__}: {e}")
    state = getattr(getattr(ep, "state", None), "ready", None)
    return Result("A13", "Embedding endpoint available", PASS if state else FAIL, f"{name} ready={state}")


# ---------- manual checks (documented, not automated) ----------

MANUAL_CHECKS = [
    ("A4", "Delta Sync over a pipeline materialized view", "creates an index; billable. See platform-verification.md §A4."),
    ("A5", "Delete propagation through Delta Sync", "mutates a source table; needs a scratch schema. See §A5."),
    ("A6", "Filter semantics (list as IN, >= on ints, booleans)", "needs a seeded index. See §A6."),
    ("A8", "Job as app resource and run_now permission", "needs a deployed app. See §A8."),
    ("A10", "Catalog managed location and disjoint volume prefixes", "needs the PR-2 infrastructure. See §A10."),
    ("A11", "Runtime serializes concurrent invocations per session", "needs agentbricks dev running. See §A11."),
    ("A12", "Bucket default SSE-KMS applies to UC-written Delta files", "needs AWS console or CLI access. See §A12."),
]

CHECKS: list[Callable[[], Result]] = [
    check_a7_reranker_fields,
    check_sdk_query_index_signature,
    check_mlflow_api,
    check_a1_model_services,
    check_a1_structured_output,
    check_a2_tavily_mcp_tools,
    check_a3_files_conflict_and_latency,
    check_a13_embedding_endpoint,
]


CHECK_IDS: dict[str, tuple[str, str]] = {
    "check_a1_model_services": ("A1", "system.ai model services listed"),
    "check_a1_structured_output": ("A1b", "Structured output on chosen worker model"),
    "check_a2_tavily_mcp_tools": ("A2", "Tavily MCP tool names and schemas"),
    "check_a3_files_conflict_and_latency": ("A3", "Files API overwrite=False conflict and latency"),
    "check_a13_embedding_endpoint": ("A13", "Embedding endpoint available"),
}


def run_checks(checks: list[Callable[[], Result]] = CHECKS) -> list[Result]:
    results: list[Result] = []
    for check in checks:
        try:
            results.append(check())
        except Skip as e:
            cid, title = CHECK_IDS.get(check.__name__, (check.__name__, check.__name__))
            results.append(Result(cid, title, SKIP, str(e)))
        except Exception as e:  # noqa: BLE001 - a crashed check is a FAIL with its error
            cid, title = CHECK_IDS.get(check.__name__, (check.__name__, check.__name__))
            results.append(Result(cid, title, FAIL, f"{type(e).__name__}: {e}"))
    results.extend(Result(i, t, MANUAL, note) for i, t, note in MANUAL_CHECKS)
    return results


def render(results: list[Result]) -> str:
    lines = [f"{'ID':<8} {'STATUS':<7} {'CHECK':<55} EVIDENCE"]
    for r in results:
        lines.append(f"{r.id:<8} {r.status:<7} {r.title[:55]:<55} {r.evidence}")
    counts = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, SKIP, MANUAL)}
    lines.append("")
    lines.append(" ".join(f"{k}={v}" for k, v in counts.items()))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json-out", help="write results as JSON to this path")
    args = parser.parse_args(argv)
    results = run_checks()
    print(render(results))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump([dataclasses.asdict(r) for r in results], fh, indent=2)
    return 1 if any(r.status == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
