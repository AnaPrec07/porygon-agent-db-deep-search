# Evaluation

## What is measured

`evals/datasets/deep_search_eval_v1.jsonl` has 30 unique topics (10 static, 10 slow, 10 volatile) and 5 repeats
that test memory reuse. The scorers in `evals/scorers.py` are deterministic:

| Scorer | Gate | Meaning |
|---|---|---|
| `citation_validity` | 100% | No unresolved citation tags, and no citation pointed at an unknown source |
| `citations_supported` | 100% | Every cited source has a verified fact from the same run |
| `plan_format` | 100% | Every plan line has a valid tag, and there are at least five research goals |
| `report_present` | 100% | The run reached `done` with a report |
| `loop_bounded` | 100% | Iterations do not exceed the cap |
| `aspect_mention_rate` | 80% | Share of expected aspects that appear in the report. A proxy for coverage, not a judgement |

The LLM claim-support judge in plan §K.6 is not in this repo yet. It needs a live model and a labelled sample.

## Running

- Stub run, no model. It checks wiring and the scorers. Every topic scores 0 on `aspect_mention_rate`, and the gates
  fail. That is the expected result: a stub is not a report.

  ```bash
  uv run python evals/run_eval.py --limit 5
  ```

- Real run. Set the worker and critic models and authenticate the workspace. Then run the full set and read the
  metrics in the MLflow experiment `deep-search-eval`:

  ```bash
  DEEP_SEARCH_WORKER_MODEL=<model> uv run python evals/run_eval.py --gate
  ```

## Budget tuning (not yet done)

The budgets are `DEEP_SEARCH_MAX_SEARCH_ITERATIONS`, `DEEP_SEARCH_QUERIES_PER_GOAL`, `DEEP_SEARCH_MAX_TOTAL_SEARCHES`,
`DEEP_SEARCH_MAX_EXTRACT_URLS_PER_PASS` and `DEEP_SEARCH_MAX_SOURCE_CHARS`. Their defaults are guesses. Tune them only
from a real run:

1. Run the full dataset with the current defaults. Record, per topic, the iterations used, searches, extractions,
   the aspect rate and the citation counts. The trace events carry the iteration count.
2. Lower `DEEP_SEARCH_MAX_SEARCH_ITERATIONS` while the aspect rate holds. Stop at the first drop of more than 5 points.
3. Lower the search budget to the 90th percentile of the searches the passing runs used.
4. Record the chosen values, the data, and the date in this runbook before changing the defaults.

Until this is done, the defaults are untested guesses and should not be treated as tuned.
