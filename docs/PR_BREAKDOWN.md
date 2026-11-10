# Deep Search migration: pull request breakdown

Maps the phases in [MIGRATION_PLAN.md](MIGRATION_PLAN.md) to a sequence of reviewable pull requests.
Merge them in order; each PR leaves the app working.

| # | Branch suffix | Phase | Scope | Depends on |
|---|---|---|---|---|
| PR-1 | `pr1-platform-verify` | 0 | `scripts/verify_platform.py`, `docs/platform-verification.md`. Resolves assumptions A1–A13. No product code. | — |
| PR-2 | `pr2-aws-uc-infra` | 1 | `infra/aws/*.tf`, `infra/databricks/*.tf`: bucket, KMS, IAM role, storage credential, external location, catalog, volumes, grants. | PR-1 |
| PR-3 | `pr3-graph-skeleton` | 2 | `agent/deep_search/` state, schemas, config, deps, graph with stub nodes; `agent/agent.py` and `runtime/adapter.py` rewired; `interrupt()` plan review; unit tests for routing and HITL. | — (parallel with PR-2) |
| PR-4 | `pr4-llm-nodes` | 3 | Prompts ported from the recipe, structured-output helper, plan/outline/query/synthesis/critic/composer nodes, plan-tag renderer and golden tests. | PR-3 |
| PR-5 | `pr5-search-evidence` | 4 | `SearchProvider`, Tavily MCP and HTTP providers, fake provider, source registry, fact extraction, quote verification, budgets, `agent/mcps.py`. | PR-3 |
| PR-6 | `pr6-persistence` | 5 | Records, IDs, layout, object store (UC volume + local), writer, recorder, redaction, manifest; node wiring; reconstruction script. | PR-3, PR-5 |
| PR-7 | `pr7-data-platform` | 6 | `platform/` bundle: bronze/silver tables, index-source tables, chunker, job. | PR-2, PR-6 |
| PR-8 | `pr8-retrieval` | 7 | `scripts/bootstrap_ai_search.py`, retriever, ranking, eligibility rules, `memory_retriever` node, refresh trigger. | PR-4, PR-7 |
| PR-9 | `pr9-ui` | 8 | Plan-review card, research timeline, report view in `ui/` and `runtime/ui.py`; route and adapter tests. | PR-3, PR-5 |
| PR-10 | `pr10-eval-hardening` | 9 | MLflow eval dataset and scorers, load and failure tests, budget tuning, runbooks. | PR-8 |
| PR-11 | `pr11-docs-cutover` | 10 and M.7 | README and AGENTS.md updates, `app.yaml` and env cutover, removal of sample tools from the deep-search graph. | PR-10 |

## Notes

- PR-2 and PR-3 can run in parallel. PR-4 and PR-5 can run in parallel once PR-3 lands.
- PR-7 and PR-8 need a dev workspace; their integration tests are marked `databricks` and run outside CI.
- If Phase 0 fails an assumption, PR-1 records the fallback chosen, and the affected PR cites it in its description.
- Each PR lists the assumptions it verifies or relies on, so reviewers can check them against `docs/platform-verification.md`.
