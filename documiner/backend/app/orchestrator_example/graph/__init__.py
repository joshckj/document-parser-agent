"""Orchestrator graph module."""

import logging

from . import orchestrator_graph as _orchestrator_graph
from .orchestrator_graph import (
    get_orchestrator_graph,
    get_orchestrator_graph_sync,
    graph,
)
from app.utils.db_pool import close_async_connection_pool as close_connection_pool

logger = logging.getLogger(__name__)


async def reset_orchestrator_graph(*, close_pool: bool = False) -> None:
    """Reset the cached Orchestrator LangGraph instance.

    This is used to recover from transient infrastructure failures (e.g. Postgres
    restarts) where the compiled graph's checkpointer may hold stale connections.
    """

    _orchestrator_graph._graph_instance = None
    _orchestrator_graph.graph = None

    if close_pool:
        try:
            await close_connection_pool()
        except Exception as e:
            logger.warning(f"Failed to close async connection pool during reset: {e}")


__all__ = [
    "get_orchestrator_graph",
    "get_orchestrator_graph_sync",
    "reset_orchestrator_graph",
    "close_connection_pool",
    "graph",
]
