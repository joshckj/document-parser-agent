"""
Template LangGraph chatbot module.

A template demonstrating the standard graph pattern:
- StateGraph with load_memory and agent nodes
- AsyncPostgresStore for long-term memory (preferences, profile)
- AsyncPostgresSaver checkpointer for conversation persistence (prod)
- Langfuse prompt management integration

Related files:
- app/template/graph/template_graph.py: Main graph definition
"""

from app.template.graph import get_template_graph, graph
from app.utils.db_pool import close_all_pools as close_connection_pool
from app.utils.store import close_store as close_template_store, get_store as get_template_store

__all__ = [
    "get_template_graph",
    "graph",
    "close_connection_pool",
    "close_template_store",
    "get_template_store",
]
