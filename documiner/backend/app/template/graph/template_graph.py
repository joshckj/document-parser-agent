"""
Template LangGraph workflow.

A chatbot template using StateGraph with:
- Load memory node for user context (preferences, profile, permissions)
- Agent node using create_agent with tool execution loop
- AsyncPostgresSaver checkpointer for conversation persistence (prod)
- AsyncPostgresStore for long-term memory
- Langfuse observability integration

Related files:
- app/template/agents/template_agent.py: Agent factory
- app/template/nodes/load_memory.py: Memory loading node
- app/template/nodes/run_agent.py: Agent execution node
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.store.base import BaseStore
from langchain_core.messages import AIMessage

from app.core.config import Settings
from app.template.agents import create_template_agent
from app.template.memory import TemplateState
from app.template.nodes import load_memory_node, run_template_agent_node
from app.utils.store import get_store, close_store
from app.utils.db_pool import get_async_connection_pool
from app.orchestrator.nodes.guardrail_node import guardrail_node

logger = logging.getLogger(__name__)
settings = Settings()

# Singleton instances
_graph_instance = None


# -------------------------
# Store management (use centralized utility)
# -------------------------

async def get_template_store() -> BaseStore:
    """Get or create the Postgres-backed LangGraph Store."""
    return await get_store()


async def close_template_store() -> None:
    """Close the store context manager if opened."""
    await close_store()


# -------------------------
# Graph construction
# -------------------------

async def create_template_graph():
    """Create and compile the Template graph.
    
    Returns a compiled LangGraph with:
    - load_memory node: loads user context from store
    - template_agent node: runs the agent with tools
    - Checkpointer (prod) for conversation persistence
    """
    # Get store for memory operations
    store = await get_template_store()
    
    # Create the agent with store for memory tools
    agent = create_template_agent(settings, store=store)
    
    # Build the StateGraph
    builder = StateGraph(TemplateState)
    
    # Define node wrappers to inject dependencies
    async def _load_memory(state: TemplateState):
        return await load_memory_node(state, store=store)
    
    async def _guardrail(state: TemplateState, config):
        res = await guardrail_node(state, config)
        # If the guardrail caught an out-of-bounds topic, return it directly as an AI message
        if res and "error" in res:
            # Tag the message explicitly so the router can detect it reliably
            return {"messages": [AIMessage(content=res["error"], name="guardrail_rejection")]}
        return {}
    
    async def _run_agent(state: TemplateState, config):
        return await run_template_agent_node(state, config, agent=agent, store=store)
    
    def route_from_guardrail(state: TemplateState) -> str:
        messages = state.get("messages", []) if isinstance(state, dict) else getattr(state, "messages", [])
        # Check for our explicitly tagged guardrail rejection message
        if messages and isinstance(messages[-1], AIMessage) and getattr(messages[-1], "name", "") == "guardrail_rejection":
            return END
        return "template_agent"
    
    # Add nodes
    builder.add_node("load_memory", _load_memory)
    builder.add_node("guardrail", _guardrail)
    builder.add_node("template_agent", _run_agent)
    
    # Add edges
    builder.add_edge(START, "load_memory")
    builder.add_edge("load_memory", "guardrail")
    builder.add_conditional_edges(
        "guardrail",
        route_from_guardrail,
        {
            "template_agent": "template_agent",
            END: END
        }
    )
    builder.add_edge("template_agent", END)
    
    # Conditional compilation based on environment
    fast_env = settings.fast_env.lower()
    
    if fast_env in {"prod", "localprod", "production"}:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        
        pool = await get_async_connection_pool()
        checkpointer = AsyncPostgresSaver(pool)
        
        try:
            await checkpointer.setup()
            logger.info("Template checkpointer setup completed successfully")
        except Exception as e:
            logger.warning(f"Template checkpointer setup warning (may already exist): {e}")
        
        graph = builder.compile(checkpointer=checkpointer)
        logger.info("Template graph compiled with AsyncPostgresSaver and memory store")
    else:
        graph = builder.compile()
        logger.info(f"Template graph compiled without checkpointer (dev mode: {settings.fast_env})")
    
    graph.name = "TemplateAgent"
    return graph


async def get_template_graph():
    """Get or create the Template graph instance."""
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = await create_template_graph()
    return _graph_instance


# Sync entrypoint for LangGraph Studio
def get_template_graph_sync():
    """Synchronous entrypoint for LangGraph Studio.
    
    Safe because Studio is NOT running inside an event loop.
    """
    return asyncio.run(get_template_graph())


# Module-level graph for LangGraph Studio (dev mode only)
graph = None
if settings.fast_env.lower() not in ["prod", "localprod", "production"]:
    try:
        graph = get_template_graph_sync()
        logger.info("Template graph initialized at module level")
    except Exception as e:
        logger.warning(f"Failed to init Template graph at module level: {e}")
        graph = None
