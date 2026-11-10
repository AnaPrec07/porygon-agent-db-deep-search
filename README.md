# Deep Search on Databricks

A research agent that plans with you, researches the web, checks every claim against its source, and writes a
cited report. It runs on Databricks Apps with LangGraph. Research memory is stored in your S3 bucket through Unity
Catalog and searched with Databricks AI Search.

It is a migration of Google's ADK deep-search recipe. The plan is in [docs/MIGRATION_PLAN.md](docs/MIGRATION_PLAN.md),
and the pull request breakdown is in [docs/PR_BREAKDOWN.md](docs/PR_BREAKDOWN.md).

## How it works

1. **Plan.** The agent drafts a research plan. Past research on the topic is used as context. The graph pauses
   until you approve, revise, or cancel. Chat text sent while the plan is waiting is treated as a revision.
2. **Research.** For each research goal the agent writes search queries and runs them through Tavily. It extracts
   facts from each page and keeps only facts whose quote is found in the page text. The rest are dropped and counted.
3. **Evaluate and refine.** A critic grades the findings. If they fall short, the agent runs follow-up searches and
   merges the new facts. It stops on a pass or at the iteration cap.
4. **Report.** The composer writes the report with `<cite>` tags. Each tag becomes a link to its source. Tags that
   point at an unknown source are removed.
5. **Persist.** Every step writes immutable records to the store. Each completed session has a manifest with a
   checksum for every file. A later session can retrieve this research.

## Layout

| Path | What it is |
|---|---|
| `agent/agent.py` | Builds the graph from configuration. Keeps the runtime entry points (`run_agent`, `recovery_input`). |
| `agent/deep_search/` | Graph state, nodes, routing, prompts, LLM nodes, citations, configuration. |
| `agent/deep_search/research/` | Search providers (Tavily MCP and HTTP), quote verification, source registry, research pipeline. |
| `agent/memory/` | Records, scope, object stores, writer, recorder, manifest reconstruction, retrieval, ranking, refresh trigger. |
| `runtime/` | `DurableAgentServer` wiring, the event adapter, and the demo UI routes. |
| `ui/` | The chat UI, with the plan review card and the research timeline. |
| `platform/` | Databricks Asset Bundle: the memory pipeline, the refresh job, and the index-source builders. |
| `infra/` | Terraform for the S3 bucket, KMS key, storage role, Unity Catalog objects and grants. |
| `scripts/` | `verify_platform.py`, `bootstrap_ai_search.py`, `reconstruct_session.py`, `delete_research.py`. |
| `evals/` | Evaluation dataset, deterministic scorers with release gates, and the MLflow runner. |
| `docs/` | Migration plan, PR breakdown, platform verification, and runbooks. |

## Run locally

```bash
uv sync
uv run pytest                 # unit tests; the live model test is skipped without a workspace
agentbricks dev               # then open http://localhost:8000
```

Without a worker model, the graph runs with stub nodes and logs a warning. Local development uses
`app.agentbricksdev.yaml`, which writes records to `./.deep_search_data` and turns retrieval off.

## Use the API

Every request needs a UUID `id`, which is the idempotency key. Keep one `session_id` for a conversation.

Start a topic:

```json
{
  "id": "11111111-1111-4111-8111-111111111111",
  "session_id": "22222222-2222-4222-8222-222222222222",
  "input": {
    "messages": [{"role": "user", "content": "Current pricing of grid-scale battery storage"}]
  }
}
```

The response includes an `interrupt` with `type: plan_review` and the plan. Resume it with a new `id` and the same
`session_id`:

```json
{
  "id": "33333333-3333-4333-8333-333333333333",
  "session_id": "22222222-2222-4222-8222-222222222222",
  "input": {"resume": {"decision": "approve"}}
}
```

`decision` is `approve`, `revise` (add `"feedback": "..."`), or `cancel`. Chat text sent without `resume` is
treated as revision feedback while a plan is waiting.

## Configuration

Set these in the deployment environment. Anything not set is either off or uses the default shown.

| Variable | Default | Purpose |
|---|---|---|
| `DEEP_SEARCH_WORKER_MODEL` | unset | Model for planning, outline and synthesis. Unset runs stub nodes. |
| `DEEP_SEARCH_CRITIC_MODEL` | worker | Model for evaluation and composition. |
| `DEEP_SEARCH_EXTRACTION_MODEL` | worker | Model for fact extraction. |
| `DEEP_SEARCH_SEARCH_PROVIDER` | unset | `tavily_mcp` or `tavily_http`. Unset leaves research stubbed. |
| `DEEP_SEARCH_TAVILY_MCP_URL` | unset | `https://<workspace>/api/2.0/mcp/external/<connection>`. Required for `tavily_mcp`. |
| `TAVILY_API_KEY` | unset | Required for `tavily_http`. Use a secret resource, never a literal value. |
| `DEEP_SEARCH_MAX_SEARCH_ITERATIONS` | `5` | Refinement passes. |
| `DEEP_SEARCH_QUERIES_PER_GOAL` | `5` | Queries per research goal. Untuned (see the evaluation runbook). |
| `DEEP_SEARCH_MAX_TOTAL_SEARCHES` | `60` | Search budget per run. Untuned. |
| `DEEP_SEARCH_OBJECT_STORE` | unset | `uc_volume` or `local:<dir>`. Unset disables persistence. |
| `DEEP_SEARCH_RECORDS_VOLUME` | unset | Volume path for `uc_volume`, for example `/Volumes/deep_search/landing/records`. |
| `DEEP_SEARCH_PERSISTENCE_STRICT` | `true` | Fail the run if a record cannot be written. |
| `DEEP_SEARCH_TENANT_ID` | unset | Required when persistence is on. Scopes records and retrieval. |
| `DEEP_SEARCH_DEFAULT_VISIBILITY` | `private` | `private` (the actor only) or `tenant` (everyone in the tenant). |
| `DEEP_SEARCH_MEMORY_ENABLED` | `true` | Retrieval of prior research. Needs a tenant. |
| `DEEP_SEARCH_AI_SEARCH_*_INDEX` | `deep_search.memory.*_idx` | Facts, responses and activity indexes. |
| `DEEP_SEARCH_REFRESH_JOB_ID` | unset | Job to trigger after a session completes. |
| `DEEP_SEARCH_REFRESH_DEBOUNCE_S` | `120` | Minimum gap between refresh triggers. |

## Deploy

Order matters. Each step depends on the one before it.

1. **Infrastructure.** `infra/README.md`: bucket, key, storage role, Unity Catalog objects and grants. Confirm the
   two checks listed there before the first production apply.
2. **Platform.** `platform/README.md`: validate and deploy the bundle, then run the refresh job once.
3. **Search.** `uv run python scripts/bootstrap_ai_search.py --dry-run`, then without `--dry-run`. This creates
   billable endpoints and indexes.
4. **App.** `agentbricks deploy <name> --source .`, with the variables from the table above set for the environment.
   The app's service principal needs the grants in `infra/databricks`, plus `CAN_MANAGE_RUN` on the refresh job.

## Operate

- Reconstruct what a session did: [docs/runbooks/reconstruct-session.md](docs/runbooks/reconstruct-session.md)
- Delete a session on request: [docs/runbooks/delete-research.md](docs/runbooks/delete-research.md)
- Rebuild the Delta tables: [docs/runbooks/rebuild-delta.md](docs/runbooks/rebuild-delta.md)
- Persistence and refresh failures: [docs/runbooks/persistence-and-refresh.md](docs/runbooks/persistence-and-refresh.md)
- Evaluation and budget tuning: [docs/runbooks/evaluation.md](docs/runbooks/evaluation.md)

## Status and limits

- **Not yet verified against a workspace.** Several platform assumptions are listed in `platform/README.md`
  (A-PLAT-1 to 4). The Tavily MCP tool names are listed in `docs/platform-verification.md` (A2).
- **Prior research is context, not citations.** Remembered facts can shape the plan, but they are never cited.
- **Budget defaults are untested.** Tune them with a real run, following the evaluation runbook.
- **Quality numbers are not yet measured.** The evaluation harness runs and its gates are defined. A real run has
  not been done.
- **The Agent Bricks memory pane** in the chat UI still shows the template's memory store. The deep-search graph does
  not use that store.
