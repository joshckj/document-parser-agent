"""
State definitions for the Template LangGraph workflow.

Defines `TemplateState` (extends `MessagesState`) and `TemplateContext`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from langgraph.graph import MessagesState


@dataclass
class TemplateContext:
    """Runtime context passed via config['context']."""
    user_id: str
    session_id: Optional[str] = None
    langfuse_handler: Optional[Any] = None


class TemplateState(MessagesState):
    """State for the Template workflow.
    
    Inherits from MessagesState which provides:
    - messages: Annotated[List[BaseMessage], add_messages]
    
    Memory fields loaded from LangGraph Store:
    - user_id: User identifier
    - role: User role for RBAC
    - user_prompt: Extracted user prompt from last message
    - preferences: Semantically searched user preferences
    - profile: User profile data
    - permissions: Derived permissions from role
    """
    user_id: Optional[str]
    role: Optional[str]
    user_prompt: Optional[str]
    preferences: Optional[List[Dict[str, Any]]]
    profile: Optional[Dict[str, Any]]
    permissions: Optional[Dict[str, Any]]
