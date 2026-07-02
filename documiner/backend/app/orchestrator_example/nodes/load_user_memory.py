"""
Load long-term memory (profile/preferences/permissions) for the Orchestrator workflow.

This node loads memory ONCE at the orchestrator level and passes it to all subgraphs
via the shared BaseAgentState.

Related files:
    - app/utils/store.py: Store implementation
    - app/orchestrator/memory/state.py: Unified state definition
"""

from __future__ import annotations

import logging
from app.utils.time_utils import get_singapore_time_iso
from typing import Any, Dict

from langchain_core.runnables.config import RunnableConfig
from langgraph.store.base import BaseStore
from langgraph.types import StreamWriter

from app.utils.store import get_store
from app.orchestrator.memory.state import OrchestratorState
from app.utils.rbac import derive_permissions
from app.utils.namespace import make_namespace
from app.orchestrator.nodes.auto_dream import maybe_fire_auto_dream

logger = logging.getLogger(__name__)


async def load_user_memory_node(
    state: OrchestratorState,
    config: RunnableConfig,
    writer: StreamWriter,
) -> Dict[str, Any]:
    """Populate long-term memory fields in orchestrator state.
    
    This memory is loaded ONCE and automatically shared with all subgraphs
    via BaseAgentState inheritance.

    - Preferences: semantic search using the current user prompt.
    - Profile: deterministic get of key 'profile'.
    - Permissions: derived from RBAC role (read-only).
    """

    user_id = state.get("user_id")
    role = state.get("role")
    user_prompt = state.get("user_prompt") or ""

    if not user_id or not role:
        # Endpoint should always provide these; avoid blowing up mid-graph.
        logger.warning("Missing user_id or role in state, skipping memory load")
        return {}

    thread_id = (config.get("configurable") or {}).get("thread_id", "")

    # Fire auto dream in background — non-blocking, checks 12h interval internally
    # NOTE: auto_dream is skipped for now since QA Postgres may not support PG Vector extension yet.
    # Once it is available, run python -m app.setup_memory_store in backend app container and uncomment the line below to enable auto_dream
    maybe_fire_auto_dream(user_id, thread_id)

    # Already loaded this session — checkpointer persists state between turns
    if state.get("preferences") is not None:
        return {}

    writer({"event": "memory-loading", "payload": {"time": get_singapore_time_iso()}})

    store: BaseStore = await get_store()

    # Preferences: semantic search, falling back to recency order if vLLM is unavailable
    try:
        pref_items = await store.asearch(
            make_namespace("preferences", user_id),
            query=user_prompt,
            limit=10,
        )
    except Exception as e:
        logger.warning("Semantic preference search failed (vLLM down?), falling back to recency order: %s", e)
        try:
            pref_items = await store.asearch(make_namespace("preferences", user_id), limit=10)
        except Exception as e2:
            logger.warning("Preference fallback search also failed: %s", e2)
            pref_items = []

    memories = [
        {
            "id": item.key,
            "category": (item.value or {}).get("category", "general"),
            "content": (item.value or {}).get("content", ""),
            "score": getattr(item, "score", None),
        }
        for item in pref_items
    ]
    
    # Simple list of strings for general agents
    preferences = [m["content"] for m in memories]

    # Profile: deterministic (hardcoded per user request)
    profile = {
        "user_id": user_id,
        "role": role,
    }

    permissions = state.get("permissions") or derive_permissions(role)

    writer({
        "event": "memory-loaded",
        "payload": {
            "profile": profile,
            "preferences": preferences,
            "role": role,
            "has_profile": bool(profile),
            "preference_count": len(preferences),
        },
    })

    return {
        "memories": memories,
        "preferences": preferences,
        "profile": profile,
        "permissions": permissions,
    }
