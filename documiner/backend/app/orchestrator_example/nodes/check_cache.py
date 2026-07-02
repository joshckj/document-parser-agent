"""
Cache checking node for Orchestrator workflow.

Caching has moved to the individual subagent level (text_to_sql, charter,
mapper, analyzer) where the cache key is a clean, disambiguated request
rather than the raw user prompt.  This node is kept as a pass-through to
preserve graph structure.
"""

from typing import Dict, Any
from langgraph.types import StreamWriter

from app.orchestrator.memory.state import OrchestratorState


async def check_cache_node(
    state: OrchestratorState,
    writer: StreamWriter,
) -> Dict[str, Any]:
    """Pass-through node — subagent-level caching handles cache logic."""
    if state.get("stage") == "error" or state.get("error"):
        return {"stage": "error"}
    return {"stage": "memory"}

