"""
Orchestrator LangGraph workflow — Phase 3 (Full Deep Agent).

The original 10-node / 7-conditional-edge StateGraph is replaced by a thin
4-node wrapper that:

  START
    → check_cache          (semantic cache hit → END; miss → continue)
    → load_user_memory     (populates profile / preferences / permissions)
    → guardrail            (6-check safety gate; fail → finalize_error)
    → orchestrator_agent   (Full Deep Agent — routes, retrieves, analyzes,
                            visualizes all in one reasoning loop)
    → finalize_success     (caches result, drops temp table, builds answer)
    → END

  guardrail fail path:
    → finalize_error → END

All orchestration logic (intent classification, SQL, RAG, analysis, charts,
maps, memory) now lives inside the deep agent.  The StateGraph is purely
infrastructure: cache, memory load, safety gate, and post-processing.

Related files:
  app/orchestrator/agents/orchestrator_agent.py     — deep agent factory
  app/orchestrator/nodes/orchestrator_agent_node.py — LangGraph node wrapper
  app/orchestrator/nodes/               — check_cache, load_user_memory, etc.
  app/orchestrator/nodes/finalize_*     — post-processing (unchanged)
"""

import asyncio
import logging

from langgraph.graph import END, START, StateGraph

from app.orchestrator.nodes.orchestrator_agent_node import orchestrator_deep_agent_node
from app.orchestrator.memory.state import OrchestratorState
from app.orchestrator.nodes import (
    check_cache_node,
    finalize_error_node,
    finalize_success_node,
    load_user_memory_node,
)
from app.orchestrator.nodes.guardrail_node import guardrail_node
from app.orchestrator.nodes.fast_path_node import fast_path_node, is_fast_path_query
from app.observability.langfuse_client import init_langfuse
from app.utils.db_pool import get_async_connection_pool

from app.core.config import get_settings as _get_settings

logger = logging.getLogger(__name__)
settings = _get_settings()


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------

def _route_from_cache(state: OrchestratorState) -> str:
    if state.get("cached_result"):
        return END
    return "load_user_memory"


def _route_from_guardrail(state: OrchestratorState) -> str:
    error = state.get("error") if isinstance(state, dict) else getattr(state, "error", None)
    if error:
        return "finalize_error"
    if is_fast_path_query(state.get("user_prompt", "")):
        return "fast_path"
    return "orchestrator_agent"


def _route_from_agent(state: OrchestratorState) -> str:
    if state.get("stage") == "error":
        return "finalize_error"
    return "finalize_success"


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------

async def create_orchestrator_graph():
    """Build and compile the Phase 3 orchestrator StateGraph."""

    fast_env = settings.fast_env.lower()

    # graph_checkpointer: used by the outer StateGraph.
    # agent_checkpointer: used by the deep agent internally (always needed).
    #
    # In prod, both share the same Postgres pool.
    # In dev, langgraph dev manages outer-graph persistence itself and rejects
    # a custom checkpointer on the compiled graph, so we leave graph_checkpointer=None
    # and give the deep agent its own MemorySaver.
    graph_checkpointer = None
    agent_checkpointer = None

    if fast_env in {"prod", "localprod"}:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        pool = await get_async_connection_pool()
        pg_checkpointer = AsyncPostgresSaver(pool)
        try:
            logger.info("Setting up orchestrator AsyncPostgresSaver tables...")
            await pg_checkpointer.setup()
            logger.info("Orchestrator AsyncPostgresSaver setup completed")
        except Exception as exc:
            logger.warning("Orchestrator checkpointer setup warning: %s", exc)
        graph_checkpointer = pg_checkpointer
        agent_checkpointer = pg_checkpointer
    else:
        from langgraph.checkpoint.memory import MemorySaver
        agent_checkpointer = MemorySaver()
        logger.info("Orchestrator dev mode: deep agent using MemorySaver, outer graph unmanaged (langgraph dev)")

    # Prime all deep agent singletons with their checkpointer.
    from app.orchestrator.agents.orchestrator_agent import get_orchestrator_agent
    from app.text_to_sql.agents.sql_agent import get_text_to_sql_agent
    from app.orchestrator.agents.analyzer_agent import set_analyzer_checkpointer
    from app.orchestrator.agents.ui_agent import get_ui_agent

    get_orchestrator_agent(checkpointer=agent_checkpointer, settings=settings)
    get_text_to_sql_agent(checkpointer=agent_checkpointer)
    set_analyzer_checkpointer(agent_checkpointer)
    get_ui_agent(checkpointer=agent_checkpointer, settings=settings)

    # Thin StateGraph wrapper ------------------------------------------------
    builder = StateGraph(OrchestratorState)

    builder.add_node("check_cache", check_cache_node)
    builder.add_node("load_user_memory", load_user_memory_node)
    builder.add_node("guardrail", guardrail_node)
    builder.add_node("fast_path", fast_path_node)
    builder.add_node("orchestrator_agent", orchestrator_deep_agent_node)
    builder.add_node("finalize_success", finalize_success_node)
    builder.add_node("finalize_error", finalize_error_node)

    builder.add_edge(START, "check_cache")
    builder.add_conditional_edges(
        "check_cache",
        _route_from_cache,
        {"load_user_memory": "load_user_memory", END: END},
    )
    builder.add_edge("load_user_memory", "guardrail")
    builder.add_conditional_edges(
        "guardrail",
        _route_from_guardrail,
        {
            "fast_path": "fast_path",
            "orchestrator_agent": "orchestrator_agent",
            "finalize_error": "finalize_error",
        },
    )
    builder.add_edge("fast_path", "finalize_success")
    builder.add_conditional_edges(
        "orchestrator_agent",
        _route_from_agent,
        {"finalize_success": "finalize_success", "finalize_error": "finalize_error"},
    )
    builder.add_edge("finalize_success", END)
    builder.add_edge("finalize_error", END)

    if graph_checkpointer is not None:
        compiled = builder.compile(checkpointer=graph_checkpointer)
        logger.info("Orchestrator StateGraph compiled with AsyncPostgresSaver")
    else:
        compiled = builder.compile()
        logger.info("Orchestrator StateGraph compiled (dev mode: %s)", settings.fast_env)

    compiled.name = "Orchestrator"
    return compiled


# ---------------------------------------------------------------------------
# Singleton management (mirrors original interface for copilotkit.py)
# ---------------------------------------------------------------------------

_graph_instance = None


async def get_orchestrator_graph():
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = await create_orchestrator_graph()
    return _graph_instance


def get_orchestrator_graph_sync():
    """Synchronous entrypoint for LangGraph Studio."""
    return asyncio.run(get_orchestrator_graph())


# Module-level graph for LangGraph Studio (dev only)
graph = None
if settings.fast_env.lower() not in {"prod", "production", "localprod"}:
    try:
        graph = get_orchestrator_graph_sync()
        logger.info("LangGraph Studio orchestrator graph initialized at module level")
    except Exception as exc:
        logger.warning("Failed to initialize orchestrator graph at module level: %s", exc)
        graph = None
