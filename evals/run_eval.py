"""Run the deep-search evaluation with MLflow GenAI (plan §K.6).

    uv run python evals/run_eval.py                 # stub nodes (no model); tests the graph and the scorers
    uv run python evals/run_eval.py --limit 5      # first five topics only
    uv run python evals/run_eval.py --gate         # exit 1 when a release gate fails

Stub runs prove the wiring. Real quality numbers need the LLM-backed deps, which this script builds when
DEEP_SEARCH_WORKER_MODEL is set and the workspace is authenticated. See docs/runbooks/evaluation.md.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.harness import load_dataset, run_topic  # noqa: E402
from evals.scorers import DETERMINISTIC, gate_failures  # noqa: E402


def _make_scorers():
    from mlflow.genai import scorer

    return [scorer(name=name)(fn) for name, fn in DETERMINISTIC.items()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--experiment", default="deep-search-eval")
    args = parser.parse_args(argv)

    import mlflow
    import mlflow.genai

    rows = load_dataset()
    if args.limit:
        rows = rows[: args.limit]

    def predict(topic: str, max_iterations: int = 5, **_: object) -> dict:
        return run_topic(topic, max_iterations=max_iterations)

    data = [
        {"inputs": {"topic": r["topic"], "max_iterations": 5},
         "expectations": {"expected_aspects": r["expected_aspects"], "min_research_goals": r["min_research_goals"]}}
        for r in rows
    ]
    mlflow.set_experiment(args.experiment)
    result = mlflow.genai.evaluate(data=data, scorers=_make_scorers(), predict_fn=predict)
    metrics = {k: v for k, v in result.metrics.items() if k.endswith("/mean")}
    means = {k.removesuffix("/mean"): float(v) for k, v in metrics.items()}
    for name, value in sorted(means.items()):
        print(f"{name}: {value:.3f}")
    failures = gate_failures(means)
    if failures:
        print("gate failures: " + ", ".join(failures))
        return 1 if args.gate else 0
    print("all gates passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
