"""Deterministic scorers (plan §K.6). Plain functions: each takes (inputs, outputs, expectations) and returns a
bool or a float. The MLflow wrappers are added in run_eval.py so this module imports without mlflow.

The LLM claim-support judge (plan §K.6 scorer 2) is not here. It needs a live model and is run separately.
"""

from __future__ import annotations

import re
from typing import Any

from agent.deep_search.citations import CITE_PATTERN
from agent.deep_search.research.pipeline import PLAN_LINE


def report_present(inputs: dict, outputs: dict, expectations: dict) -> bool:
    return outputs.get("phase") == "done" and bool(outputs.get("final_report_with_citations", "").strip())


def citation_validity(inputs: dict, outputs: dict, expectations: dict) -> bool:
    """No unresolved tags remain, and no tag was dropped for pointing at an unknown source."""
    text = outputs.get("final_report_with_citations", "")
    stats = outputs.get("citation_stats", {})
    return not CITE_PATTERN.search(text) and stats.get("dropped", 0) == 0


def citations_supported(inputs: dict, outputs: dict, expectations: dict) -> bool:
    """Every source that the report cites has at least one verified fact from this run."""
    cited = {m.group(1) for m in CITE_PATTERN.finditer(outputs.get("final_cited_report", ""))}
    if not cited:
        return True
    backed = {f.get("source_id") for f in outputs.get("facts", []) if f.get("status") == "active"}
    return cited <= backed


def plan_format(inputs: dict, outputs: dict, expectations: dict) -> bool:
    lines = [line.strip() for line in outputs.get("research_plan", "").splitlines() if line.strip()]
    if not lines or not all(PLAN_LINE.match(line) for line in lines):
        return False
    research = sum(1 for line in lines if line.startswith("* [RESEARCH]"))
    return research >= expectations.get("min_research_goals", 5)


def loop_bounded(inputs: dict, outputs: dict, expectations: dict) -> bool:
    return outputs.get("iterations", 0) <= outputs.get("max_iterations", 5)


def aspect_mention_rate(inputs: dict, outputs: dict, expectations: dict) -> float:
    """Share of expected aspects that appear in the report text. A cheap proxy for coverage, not a judgement."""
    aspects = expectations.get("expected_aspects", [])
    if not aspects:
        return 1.0
    text = outputs.get("final_report_with_citations", "").lower()
    hits = sum(1 for aspect in aspects if re.search(re.escape(aspect.lower()), text))
    return round(hits / len(aspects), 3)


DETERMINISTIC = {
    "report_present": report_present,
    "citation_validity": citation_validity,
    "citations_supported": citations_supported,
    "plan_format": plan_format,
    "loop_bounded": loop_bounded,
    "aspect_mention_rate": aspect_mention_rate,
}

# Release gates (plan §K.6). Citation validity is 100 percent; the others start as floors and are tuned from data.
GATES = {
    "citation_validity": 1.0,
    "citations_supported": 1.0,
    "plan_format": 1.0,
    "report_present": 1.0,
    "loop_bounded": 1.0,
    "aspect_mention_rate": 0.8,
}


def gate_failures(means: dict[str, float]) -> list[str]:
    return [name for name, floor in GATES.items() if name in means and means[name] < floor]
