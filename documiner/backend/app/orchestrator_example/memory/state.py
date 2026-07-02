"""
State definitions for Orchestrator LangGraph workflow.

Defines the `OrchestratorState` which extends `BaseAgentState` with
orchestrator-specific fields like intent, rewritten request, and agent outputs.

Related files:
    - app/orchestrator/memory/base_state.py: Base class
    - app/orchestrator/graph/orchestrator_graph.py: Uses this state
"""

from typing import Any, Dict, List, Literal, Optional, Annotated
from dataclasses import dataclass
from operator import add

from app.orchestrator.memory.base_state import BaseAgentState


@dataclass
class OrchestratorContext:
    """Runtime context for the orchestrator workflow."""
    user_id: str
    role: str
    session_id: Optional[str] = None
    langfuse_handler: Optional[Any] = None


# Intent types from prompt_registry/orchestrator.md
IntentLiteral = Literal[
    "simple",
    "rag",
    "sql_only",
    "sql_analysis",
    "sql_analysis_chart",
    "sql_analysis_map",
    "sql_analysis_visual",
    "update_chart",
    "update_map",
    "memory",
    "unclear",
]


def merge_agent_outputs(left: Optional[Any], right: Optional[Any]) -> Optional[Any]:
    """Reducer for merging parallel agent outputs.
    
    When multiple agents run in parallel (analyzer, charter, mapper),
    this reducer ensures their outputs are properly merged instead of
    one overwriting the other.
    
    Strategy:
    - If left is None, return right
    - If right is None, return left
    - If both exist, right takes precedence (last update wins)
    """
    if right is not None:
        return right
    return left


class OrchestratorState(BaseAgentState):
    """State for the Orchestrator workflow graph.
    
    Inherits from BaseAgentState which provides:
    - messages (from MessagesState)
    - user_prompt, user_id, role
    - preferences, profile, permissions
    - answer, stage, elapsed_time
    
    Adds orchestrator-specific intent routing and agent outputs.
    """
    
    # Orchestrator intent classification
    intent: Optional[IntentLiteral]
    rewritten_request: Optional[str]  # Context-aware query rewrite
    is_correction: Optional[bool]     # User correction flag

    # Orchestrator direct response (for simples, unclear, etc)
    orchestrator_response: Optional[str]
    
    # Caching
    cache_key: Optional[str]
    cached_result: Optional[Dict[str, Any]]
    prompt_embedding: Optional[List[float]]
    
    # Text-to-SQL specific outputs (only text_to_sql subgraph updates these)
    query: Optional[str]
    row_count: Optional[int]
    rows: Optional[List[Dict[str, Any]]]  # Actual query result data
    
    # Temp table for parallel agent execution
    temp_table_name: Optional[str]  # Name of temp table created from query results
    table_context: Optional[str]    # Context string with SQL + sample rows
    
    # Agent-specific outputs (with reducers for parallel execution)
    chart_config: Annotated[Optional[Dict[str, Any]], merge_agent_outputs]
    map_config: Annotated[Optional[Dict[str, Any]], merge_agent_outputs]
    analysis: Annotated[Optional[str], merge_agent_outputs]
    
    # Memory updates
    memory_update: Optional[Dict[str, Any]]
    
    # RAG outputs
    retrieved_docs: Optional[List[str]]
