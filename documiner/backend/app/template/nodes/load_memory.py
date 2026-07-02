"""
Load memory node - prepares memory context before agent invocation.

This is a preprocessing node that loads user context from the LangGraph Store:
- Preferences: semantically searched based on user prompt
- Profile: deterministic get by user_id
- Permissions: derived from role via RBAC

Related files:
- app/template/memory/state.py: State definition where memory is stored
"""

from typing import Any, Dict
import logging

from langgraph.store.base import BaseStore

from app.utils.rbac import derive_permissions
from app.utils.namespace import make_namespace
from app.template.memory import TemplateState

logger = logging.getLogger(__name__)


async def load_memory_node(
    state: TemplateState,
    *,
    store: BaseStore,
) -> Dict[str, Any]:
    """Load user memories from the store and update state.
    
    Args:
        state: Current workflow state
        store: LangGraph Store for memory operations
        
    Returns:
        State update dictionary with loaded memory data
    """
    
    user_id = (state.get("user_id") or "").strip()
    role = (state.get("role") or "").strip()
    user_prompt = str(state["messages"][-1].content) if state.get("messages") else ""

    logger.info(f"Loading memory for user: {user_id}")

    # Preferences: semantic search based on user prompt
    preferences = []
    try:
        pref_items = await store.asearch(
            make_namespace("preferences", user_id),
            query=user_prompt,
            limit=10,
        )
        preferences = [
            {
                "id": item.key,
                "category": (item.value or {}).get("category", "general"),
                "content": (item.value or {}).get("content", ""),
                "score": getattr(item, "score", None),
            }
            for item in pref_items
        ]
    except TypeError:
        # Some store implementations support asearch(namespace, limit=...) only
        try:
            pref_items = await store.asearch(make_namespace("preferences", user_id), limit=10)
            preferences = [
                {
                    "id": item.key,
                    "category": (item.value or {}).get("category", "general"),
                    "content": (item.value or {}).get("content", ""),
                    "score": getattr(item, "score", None),
                }
                for item in pref_items
            ]
        except Exception as e:
            logger.warning(f"Failed to load preferences: {e}")
    except Exception as e:
        logger.warning(f"Failed to load preferences: {e}")

    # Profile: deterministic get
    profile = None
    try:
        profile_item = await store.aget(make_namespace("profile", user_id), "profile")
        profile = getattr(profile_item, "value", None)
    except Exception as e:
        logger.debug(f"No profile found for user {user_id}: {e}")
        profile = None

    # Permissions: preserve upstream context when present, fallback to role-derived
    permissions = state.get("permissions") or derive_permissions(role)

    logger.info(
        f"Memory loaded - preferences: {len(preferences)}, "
        f"has_profile: {profile is not None}, role: {role}"
    )

    return {
        "user_id": user_id,
        "role": role,
        "user_prompt": user_prompt,
        "preferences": preferences,
        "profile": profile,
        "permissions": permissions,
    }
