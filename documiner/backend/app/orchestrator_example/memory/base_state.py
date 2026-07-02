"""
Shared base state for orchestrator and all subgraphs.

All agents (orchestrator, text_to_sql, rag, memory, etc.) inherit from this
to ensure automatic state sharing.

Related files:
- app/orchestrator/state.py: Orchestrator-specific state
"""

from typing import Any, Dict, List, Optional
from langgraph.graph import MessagesState


class BaseAgentState(MessagesState):
    """Base state shared by orchestrator and all subgraphs.
    
    Inherits from MessagesState to get:
    - messages: Annotated[List[BaseMessage], add_messages]
    
    All agents (orchestrator, text_to_sql, rag, memory, etc.) inherit from this
    to ensure automatic state sharing.
    """
    
    # Input fields
    user_prompt: str
    user_id: str
    role: str
    
    # Long-term memory
    memories: Optional[List[Dict[str, Any]]] # Full memory objects with IDs (for Memory Agent)
    preferences: Optional[List[str]]         # Simplified content strings (for other agents)
    profile: Optional[Dict[str, Any]]
    permissions: Optional[Dict[str, Any]]
    
    # Output fields (shared by all agents)
    answer: Optional[str]
    rag_response: Optional[str]
    stage: Optional[str]
    elapsed_time: Optional[float]
    error: Optional[str]