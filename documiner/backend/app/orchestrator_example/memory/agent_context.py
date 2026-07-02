"""
Per-invocation context schema for the orchestrator Full Deep Agent.

Passed via `context=` at ainvoke/astream time so every tool and the dynamic
system prompt can read user identity, permissions, and memory without state
threading.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class OrchestratorAgentContext:
    """Per-invocation runtime context for the orchestrator deep agent."""

    user_id: str = ""
    role: str = ""

    # RBAC permissions forwarded from OrchestratorState.
    permissions: Optional[Dict[str, Any]] = None

    # Long-term memory loaded by load_user_memory_node.
    memories: Optional[List[Dict[str, Any]]] = None
    preferences: Optional[List[str]] = None
    profile: Optional[Dict[str, Any]] = None

    # Pre-computed embedding from cache check (reused by text_to_sql subagent).
    prompt_embedding: Optional[List[float]] = None

    # Existing configs forwarded for update_chart / update_map requests.
    current_chart_config: Optional[Dict[str, Any]] = None
    current_map_config: Optional[Dict[str, Any]] = None

    session_id: Optional[str] = None
