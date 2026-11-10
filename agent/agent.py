import logging
import os
from collections.abc import AsyncGenerator, Callable
from dataclasses import replace
from functools import lru_cache
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks_langchain import ChatDatabricks
from langchain.agents.middleware.human_in_the_loop import HITLResponse
from langgraph.types import Command
from pydantic import TypeAdapter, ValidationError

from agent.deep_search.config import DeepSearchConfig
from agent.deep_search.deps import Deps
from agent.deep_search.graph import build_graph
from agent.deep_search.research.pipeline import ResearchServices
from agent.memory.object_store import LocalObjectStore, UCVolumeObjectStore
from agent.memory.recorder import Persistence
from agent.memory.writer import PersistenceWriter
from databricks_agentkit import workspace_client, workspace_headers
from databricks_agentkit.langgraph import (
    checkpointer,
    configure_tracing,
    start_trace,
    thread_config,
)
from databricks_agentkit.langgraph.session_store import (
    invocation_id_from_metadata,
    invocation_metadata,
)
from databricks_agentkit.runtime.auth import AuthError
from databricks_agentkit.runtime.tool_manifest import resolve_model_service

logger = logging.getLogger(__name__)

MODEL = resolve_model_service() or "system.ai.meta-llama-3-1-8b-instruct"  # consumed by PR-4 LLM nodes

# Template approval gate for the sample tools in agent/tools. The deep-search graph does not use these tools
# (removed in PR-11); the constant stays so the template's tool tests keep their contract.
# Tools that require human approval before they run. Map a tool name to True to allow every decision
# (approve / edit / reject / respond), or to a config dict to restrict them (see HumanInTheLoopMiddleware).
# When a listed tool is about to run, the agent pauses and emits an `interrupt` event; the client
# resumes by sending `resume` with the same session id. Empty this dict to disable approval gating.
REQUIRE_APPROVAL = {"send_message": True}


class _RoutedChatDatabricks(ChatDatabricks):
    """Forward account-host workspace routing to the underlying OpenAI clients."""

    def _get_client_kwargs(self) -> dict[str, Any]:
        kwargs = super()._get_client_kwargs()
        if headers := workspace_headers():
            kwargs["default_headers"] = headers
        return kwargs


@lru_cache(maxsize=1)
def _app_workspace_client() -> WorkspaceClient:
    """The App's own client, shared across turns.

    Constructing a WorkspaceClient resolves auth with blocking network calls (and, locally, a
    `databricks auth token` subprocess). Doing that per turn inside the async graph builder stalls
    every request on the event loop; the SDK refreshes tokens itself, so one client suffices.
    """
    return workspace_client()


def configure() -> None:
    """Wire up global state; call once at server startup (not at import)."""
    _check_databricks_auth()
    configure_tracing()


def _check_databricks_auth() -> None:
    """Fail fast at startup with a clear message if Databricks auth isn't configured.

    Without this, a missing/invalid profile only surfaces on the first model call — as a generic SDK
    error buried in a request traceback. Resolving a WorkspaceClient here validates the same config
    the model client uses, so the failure is immediate and actionable.
    """
    try:
        _app_workspace_client()
    except Exception as e:
        profile = os.getenv("DATABRICKS_CONFIG_PROFILE")
        target = (
            f"profile {profile!r}" if profile else "the DEFAULT profile / DATABRICKS_HOST+TOKEN"
        )
        raise RuntimeError(
            f"Databricks auth is not configured — the agent can't call the model. Tried {target}.\n"
            "Fix one of:\n"
            "  • set DATABRICKS_CONFIG_PROFILE in .env to a profile from `databricks auth profiles`, or\n"
            "  • run `databricks auth login --profile <name>` to create one, or\n"
            "  • set DATABRICKS_HOST and DATABRICKS_TOKEN in .env.\n"
            f"(underlying error: {e})"
        ) from e


def _build_persistence(config: DeepSearchConfig) -> Persistence | None:
    """Return the record writer for the configured store, or None when persistence is disabled."""
    spec = config.object_store
    if spec is None:
        logger.warning("DEEP_SEARCH_OBJECT_STORE is not set; research records are not persisted")
        return None
    if spec.startswith("local:"):
        store: Any = LocalObjectStore(spec[len("local:") :])
    elif spec == "uc_volume":
        if not config.records_volume:
            raise ValueError("DEEP_SEARCH_RECORDS_VOLUME is required when DEEP_SEARCH_OBJECT_STORE=uc_volume")
        store = UCVolumeObjectStore(_app_workspace_client(), config.records_volume)
    else:
        raise ValueError("DEEP_SEARCH_OBJECT_STORE must be local:<dir>, uc_volume, or unset")
    return Persistence(PersistenceWriter(store), strict=config.persistence_strict)


def _build_search_provider(config: DeepSearchConfig, workspace: WorkspaceClient) -> Any | None:
    """Return the configured search provider, or None when none is configured.

    Misconfiguration fails here, at graph build, instead of appearing later as failed searches.
    """
    if config.search_provider is None:
        return None
    if config.search_provider == "tavily_mcp":
        if not config.tavily_mcp_url:
            raise ValueError("DEEP_SEARCH_TAVILY_MCP_URL is required when DEEP_SEARCH_SEARCH_PROVIDER=tavily_mcp")
        from databricks_mcp import DatabricksMCPClient

        from agent.deep_search.research.providers import TavilyMcpProvider

        client = DatabricksMCPClient(server_url=config.tavily_mcp_url, workspace_client=workspace)
        return TavilyMcpProvider(
            client,
            search_tool=config.tavily_search_tool,
            extract_tool=config.tavily_extract_tool,
        )
    from agent.deep_search.research.providers import TavilyHttpProvider

    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise ValueError("TAVILY_API_KEY is required when DEEP_SEARCH_SEARCH_PROVIDER=tavily_http")
    return TavilyHttpProvider(key)


async def create_agent_graph(
    actor: str,
    model: str | None = None,
    *,
    workspace_client_for: Callable[[str], WorkspaceClient] | None = None,
):
    """Build the deep-search graph, compiled with the managed checkpointer.

    With a worker model configured (``DEEP_SEARCH_WORKER_MODEL`` or the UI model override), planning,
    outline, evaluation and composition call the model through the AI Gateway. Without one, the graph
    runs with stub nodes and logs a warning, so a stub run cannot pass as a real one.

    ``actor`` and ``workspace_client_for`` are accepted but not used until PR-8 (memory) and PR-5 (search)
    need them. The UI model override changes the worker model; the critic model keeps its setting.
    """
    del actor, workspace_client_for
    config = DeepSearchConfig.from_env()
    if model:
        config = replace(config, worker_model=model)
    persistence = _build_persistence(config)
    if config.worker_model:
        workspace = _app_workspace_client()
        worker = _RoutedChatDatabricks(
            endpoint=config.worker_model, workspace_client=workspace, use_ai_gateway=True
        )
        if config.critic_model and config.critic_model != config.worker_model:
            critic = _RoutedChatDatabricks(
                endpoint=config.critic_model, workspace_client=workspace, use_ai_gateway=True
            )
        else:
            critic = worker
        extractor = worker
        if config.extraction_model and config.extraction_model != config.worker_model:
            extractor = _RoutedChatDatabricks(
                endpoint=config.extraction_model, workspace_client=workspace, use_ai_gateway=True
            )
        search = _build_search_provider(config, workspace)
        services = None
        if search is None:
            logger.warning("DEEP_SEARCH_SEARCH_PROVIDER is not set; research and refinement use stub nodes")
        else:
            services = ResearchServices(
                search=search,
                query_llm=worker,
                extract_llm=extractor,
                deliver_llm=worker,
                config=config,
            )
        deps = Deps.from_models(config, worker=worker, critic=critic, services=services)
    else:
        logger.warning("DEEP_SEARCH_WORKER_MODEL is not set; the deep-search graph is running with stub nodes")
        deps = Deps.stub(config)
    return build_graph(deps, persistence=persistence).compile(checkpointer=checkpointer())


async def recovery_input(
    agent_input: Any,
    *,
    session_id: str,
    actor: str,
    invocation_id: str,
) -> Any:
    """Return the input that should be passed to ``run_agent`` after worker loss.

    A checkpoint tagged with this invocation means LangGraph can continue by receiving ``None``.
    Otherwise recovery replays the original application input.
    """
    checkpoint = await checkpointer().aget_tuple(thread_config(session_id, actor))
    current_invocation_checkpointed = bool(
        checkpoint and invocation_id_from_metadata(checkpoint.metadata) == invocation_id
    )
    return None if current_invocation_checkpointed else agent_input


async def _validate_approval_resume(graph: Any, config: dict, agent_input: Any) -> None:
    """Reject malformed middleware decisions before LangGraph checkpoints the resume value."""
    if not isinstance(agent_input, Command) or agent_input.resume is None:
        return
    state = await graph.aget_state(config)
    pending = [item for task in state.tasks for item in task.interrupts]
    resume = agent_input.resume
    keyed = isinstance(resume, dict) and any(item.id in resume for item in pending)
    for item in pending:
        if not isinstance(item.value, dict) or "review_configs" not in item.value:
            continue  # Application-defined interrupts keep their native resume contract.
        if keyed and item.id not in resume:
            continue
        response = resume[item.id] if keyed else resume
        try:
            decisions = TypeAdapter(HITLResponse).validate_python(response)["decisions"]
        except ValidationError as error:
            raise AuthError(
                "INVALID_APPROVAL", "Invalid approval decision payload.", 400
            ) from error
        reviews = item.value["review_configs"]
        if len(decisions) != len(reviews) or any(
            decision["type"] not in review["allowed_decisions"]
            for decision, review in zip(decisions, reviews, strict=True)
        ):
            raise AuthError(
                "INVALID_APPROVAL", "Provide one allowed decision per pending tool call.", 400
            )


async def run_agent(
    agent_input: Any,
    *,
    session_id: str,
    actor: str | None = None,
    model: str | None = None,
    invocation_id: str | None = None,
    workspace_client_for: Callable[[str], WorkspaceClient] | None = None,
    graph: Any = None,
) -> AsyncGenerator[Any, None]:
    """Run the agent and yield native LangGraph stream events.

    This is the framework-native entrypoint. It has no dependency on Agent Bricks request or context types,
    so it can be called from another server, a notebook, or a test harness.
    """
    # Each interactive resume builds tools from this request's credentials. Only graph state,
    # never the graph or its credential-bound clients, survives the approval pause.
    actor = actor or session_id
    if graph is None:
        graph = await create_agent_graph(actor, model, workspace_client_for=workspace_client_for)
    config = thread_config(session_id, actor)
    if invocation_id:
        config["metadata"] = invocation_metadata(invocation_id)

    await _validate_approval_resume(graph, config, agent_input)

    last_update: Any = None
    with start_trace(name="invoke", inputs=agent_input, session_id=session_id) as span:
        async for event in graph.astream(
            input=agent_input,
            config=config,
            stream_mode=["updates", "messages"],
            durability="sync",
        ):
            if event[0] == "updates":
                last_update = event[1]
            yield event
        if span is not None and last_update is not None:
            span.set_outputs(last_update)
