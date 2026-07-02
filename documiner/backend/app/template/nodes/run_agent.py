"""
Run template agent node.

This node wraps the LangChain `create_agent` graph and streams its execution.
Uses raw LangGraph streaming (no custom StreamWriter events).

Related files:
- app/template/agents/template_agent.py: Agent definition
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, cast

from langchain_core.messages import BaseMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.store.base import BaseStore

from app.template.memory import TemplateState
from app.tools.memory_tools import set_memory_tool_context, reset_memory_tool_context

logger = logging.getLogger(__name__)


async def run_template_agent_node(
    state: TemplateState,
    config: RunnableConfig,
    *,
    agent: Any,
    store: BaseStore,
) -> Dict[str, Any]:
    """Invoke the template agent and return final state.
    
    Args:
        state: Current workflow state with messages and memory context
        config: LangGraph runnable config
        agent: The compiled agent from create_agent
        store: LangGraph store for memory operations
        
    Returns:
        State update with final messages from agent
    """

    messages = cast(List[BaseMessage], state.get("messages", []))
    user_id = state.get("user_id", "")
    
    logger.info(f"Running template agent with {len(messages)} messages for user: {user_id}")

    # Set contextvars for memory tool access (same pattern as memory graph)
    tokens = set_memory_tool_context(user_id=user_id, store=store)

    # Stream the agent graph in 'values' mode to get incremental updates
    final_values: Dict[str, Any] = {}

    try:
        async for values in agent.astream(
            {
                "messages": messages,
                # Pass through memory context for agent to use
                **{k: v for k, v in state.items() if k != "messages"}
            },
            config=config,
            stream_mode="values",
        ):
            if values:
                final_values = values
    except Exception as e:
        logger.error(f"Agent execution failed: {e}")
        raise
    finally:
        # Always reset contextvars
        reset_memory_tool_context(tokens)

    logger.info("Template agent completed")

    # Return the agent's final messages to the outer graph
    return {
        "messages": final_values.get("messages", messages),
    }
