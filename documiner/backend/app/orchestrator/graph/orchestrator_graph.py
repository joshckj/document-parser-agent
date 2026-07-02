"""
Documiner orchestrator StateGraph.

A thin wrapper around the deep agent:

    START -> agent -> finalize -> END

`agent`    runs the router deep agent (which may delegate to the extractor).
`finalize` expands render_blocks(<ref>) keywords into <<TABLE>>...<<END>> markers.

The outer graph is compiled WITHOUT a checkpointer: the frontend sends the full
message history on every /chat call, so outer-graph persistence would duplicate
messages. Per-session state the extractor needs (the cached OCR JSON) travels
via state["session_id"] -> the deep agent's thread_id. Exposed as a
process-level singleton via get_graph().
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from orchestrator.memory.state import OrchestratorState
from orchestrator.nodes.orchestrator_agent_node import orchestrator_agent_node
from orchestrator.nodes.finalize import finalize_node

logger = logging.getLogger(__name__)


def create_graph():
    builder = StateGraph(OrchestratorState)
    builder.add_node("agent", orchestrator_agent_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "agent")
    builder.add_edge("agent", "finalize")
    builder.add_edge("finalize", END)

    compiled = builder.compile()
    compiled.name = "DocuminerOrchestrator"
    logger.info("Documiner orchestrator StateGraph compiled")
    return compiled


_graph_instance = None


def get_graph():
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = create_graph()
    return _graph_instance
