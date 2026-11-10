"""Run the graph headless for one topic: auto-approve the plan, return the state the scorers read."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.deep_search.config import DeepSearchConfig
from agent.deep_search.deps import Deps
from agent.deep_search.graph import build_graph

DATASET = Path(__file__).parent / "datasets" / "deep_search_eval_v1.jsonl"


def load_dataset(path: Path = DATASET) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_topic(
    topic: str,
    *,
    deps_factory: Callable[[DeepSearchConfig], Deps] = Deps.stub,
    max_iterations: int | None = None,
    checkpointer: Any | None = None,
) -> dict[str, Any]:
    config = DeepSearchConfig.from_env({})
    if max_iterations is not None:
        config = replace(config, max_search_iterations=max_iterations)
    graph = build_graph(deps_factory(config)).compile(checkpointer=checkpointer or InMemorySaver())
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke({"messages": [{"role": "user", "content": topic}]}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    values = graph.get_state(cfg).values
    return {
        "phase": values.get("phase"),
        "research_plan": values.get("research_plan", ""),
        "final_cited_report": values.get("final_cited_report", ""),
        "final_report_with_citations": values.get("final_report_with_citations", ""),
        "citation_stats": values.get("citation_stats", {}),
        "sources": values.get("sources", {}),
        "facts": values.get("facts", []),
        "iterations": values.get("iteration", 0),
        "max_iterations": config.max_search_iterations,
        "budget": values.get("budget", {}),
        "errors": values.get("errors", []),
    }
